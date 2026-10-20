# -*- coding: utf-8 -*-
"""ffmpeg 输出接管与异常保护。

背景
----
ffmpeg 6.1(Linux 发行版常见版本)在输入流异常结束时, 可能陷入
"读失败 -> 打日志 -> 立即重试" 的循环:

* 解复用层 `ff_read_packet()` 对 `FFERROR_REDO` 直接 `continue`, 既不休眠也不计数;
* `avio_feof()` 会清掉 EOF 标记后重新填缓冲, 每次重试都会重新进 http 层;
* http 层发现响应体提前结束, 每次都打印一条
  `Stream ends prematurely at ..., should be ...` (AV_LOG_ERROR) 并返回 EIO。

几秒内即可刷出上百万行日志并把单核打满。录制命令原先让 ffmpeg 的 stdout 直接继承终端
(tmux/pty), 等于把这场日志风暴原样转给终端重绘; 同时 `check_subprocess()` 只在
`process.poll()` 上每秒轮询一次, 对这种风暴没有任何兜底。

本模块做四件事
--------------
1. 接管 stdout/stderr: 按行读取并限流写入 logs/ffmpeg.log, 终端不再被刷屏;
2. 日志风暴保护: 连续若干秒超过阈值行数就 kill 掉进程, 交给外层断流重试;
3. 空输出保护: 开录后超过设定时间仍没有任何字节写入, 视为坏流并 kill;
4. 风暴现场落盘: 触发风暴时把最近若干行写到 logs/ffmpeg_storm.log, 便于定位。

模块不依赖 loguru 等运行时组件, 便于在测试里直接导入。
"""

import collections
import os
import re
import threading
import time

# 正常录制(-loglevel error)几乎没有输出, 超过这个行数/秒即视为异常。
LOG_STORM_LINES_PER_SEC = 300
# 连续 N 秒超阈值才判定风暴, 避免启动瞬间的集中输出被误杀。
LOG_STORM_SECONDS = 2
# 风暴触发时落盘的最后 N 行。
STORM_DUMP_LINES = 200
# 开录 N 秒仍没有任何字节写入 -> 视为坏流(ffmpeg 会一直空转)。
EMPTY_OUTPUT_TIMEOUT = 120
# 正常日志落盘限流, 防止磁盘被异常输出刷爆。
MAX_LOG_LINES_PER_SEC = 50
# logs/ffmpeg.log 超过该大小轮转一次(保留一份 .1)。
LOG_MAX_BYTES = 5 * 1024 * 1024

_LINE_SPLIT = re.compile(rb"[\r\n]+")
_DUMP_LOCK = threading.Lock()


def output_bytes(save_file_path: str) -> int:
    """返回录制目标已写入的字节数。

    分段模式的目标路径形如 `..._%03d.ts`, ffmpeg 按 `%03d` 生成多个文件,
    这里按前缀汇总; 普通模式直接取文件大小。文件/目录不存在时返回 0。
    """
    if "%03d" in save_file_path:
        folder = os.path.dirname(save_file_path) or "."
        prefix = os.path.basename(save_file_path).rsplit("_", 1)[0]
        if not prefix:
            return 0
        try:
            names = os.listdir(folder)
        except OSError:
            return 0
        total = 0
        for name in names:
            if not name.startswith(prefix):
                continue
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                continue
        return total
    try:
        return os.path.getsize(save_file_path)
    except OSError:
        return 0


class FfmpegOutputGuard:
    """接管一个 ffmpeg 进程的输出, 并在日志风暴/长期空输出时终止它。

    用法::

        process = subprocess.Popen(..., stdin=PIPE, stdout=PIPE, stderr=STDOUT)
        guard = FfmpegOutputGuard(process, log_path, storm_path, label=record_name)
        guard.start()
        while process.poll() is None:
            ...
        guard.join()
        if guard.storm: ...
    """

    def __init__(self, process, log_path: str, storm_path: str, label: str = "",
                 detail: str = "",
                 lines_per_sec: int = LOG_STORM_LINES_PER_SEC,
                 storm_seconds: int = LOG_STORM_SECONDS,
                 dump_lines: int = STORM_DUMP_LINES,
                 max_log_lines_per_sec: int = MAX_LOG_LINES_PER_SEC):
        self.process = process
        self.log_path = log_path
        self.storm_path = storm_path
        self.label = label
        self.detail = detail
        self.lines_per_sec = max(1, lines_per_sec)
        self.storm_seconds = max(1, storm_seconds)
        self.max_log_lines_per_sec = max(1, max_log_lines_per_sec)

        self.recent = collections.deque(maxlen=max(1, dump_lines))
        self.total_lines = 0
        self.storm = False
        self.no_output = False

        self._write_window = time.monotonic()
        self._written_window = 0
        self._suppressed = 0
        self._writer = None
        self._thread = threading.Thread(
            target=self._run, name=f"ffmpeg-guard-{label or 'rec'}", daemon=True)

    # ---------- 生命周期 ----------

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float = 3.0) -> None:
        self._thread.join(timeout)

    def mark_no_output(self) -> None:
        """由等待方标记"空输出超时", 与风暴标记互不覆盖。"""
        self.no_output = True

    # ---------- 读取 ----------

    def _run(self) -> None:
        stdout = getattr(self.process, "stdout", None)
        if stdout is None:
            return
        # read1 有一次数据就返回, 适合管道; 没有 read1(如测试替身)则退回 read
        read = getattr(stdout, "read1", None) or stdout.read

        buf = b""
        second_start = time.monotonic()
        second_lines = 0
        over_seconds = 0
        try:
            while True:
                chunk = read(4096)
                if not chunk:
                    break
                buf += chunk
                parts = _LINE_SPLIT.split(buf)
                buf = parts.pop()  # 最后一段可能不完整, 留到下一轮
                for raw in parts:
                    if not raw:
                        continue
                    self.total_lines += 1
                    second_lines += 1
                    # 本秒输出已超阈值时不再逐行处理(风暴已基本确认),
                    # 只计数并等秒边界判定, 避免在风暴里做无谓的解码/落盘
                    if not self.storm and second_lines < self.lines_per_sec:
                        self._handle(raw)

                now = time.monotonic()
                if now - second_start >= 1.0:
                    if second_lines >= self.lines_per_sec:
                        over_seconds += 1
                    else:
                        over_seconds = 0
                    second_start = now
                    second_lines = 0
                    if over_seconds >= self.storm_seconds and not self.storm:
                        self._trigger_storm()
        except (OSError, ValueError):
            pass
        finally:
            if buf and not self.storm:
                self._handle(buf)
            self._flush()

    def _handle(self, raw: bytes) -> None:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            return
        self.recent.append(line)

        now = time.monotonic()
        if now - self._write_window >= 1.0:
            if self._suppressed:
                self._write(f"... ({self._suppressed} lines throttled)")
            self._suppressed = 0
            self._write_window = now
            self._written_window = 0
        if self._written_window < self.max_log_lines_per_sec:
            self._write(line)
            self._written_window += 1
        else:
            self._suppressed += 1

    # ---------- 落盘 ----------

    def _write(self, line: str) -> None:
        try:
            if self._writer is None:
                folder = os.path.dirname(self.log_path)
                if folder:
                    os.makedirs(folder, exist_ok=True)
                try:
                    if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > LOG_MAX_BYTES:
                        os.replace(self.log_path, self.log_path + ".1")
                except OSError:
                    pass
                self._writer = open(self.log_path, "a", encoding="utf-8")
            self._writer.write(line + "\n")
        except OSError:
            self._writer = None

    def _flush(self) -> None:
        writer, self._writer = self._writer, None
        if writer is None:
            return
        try:
            if self._suppressed:
                writer.write(f"... ({self._suppressed} lines throttled)\n")
                self._suppressed = 0
            writer.flush()
            writer.close()
        except OSError:
            pass

    def _trigger_storm(self) -> None:
        self.storm = True
        self._dump()
        try:
            self.process.kill()
        except (OSError, AttributeError):
            pass

    def _dump(self) -> None:
        """把风暴现场(最后若干行)追加到 logs/ffmpeg_storm.log。"""
        try:
            folder = os.path.dirname(self.storm_path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with _DUMP_LOCK:
                with open(self.storm_path, "a", encoding="utf-8") as f:
                    f.write(f"\n===== {stamp} {self.label} =====\n")
                    f.write(f"storm: {self.total_lines} lines total, "
                            f">= {self.lines_per_sec}/s for {self.storm_seconds}s"
                            f"{('; ' + self.detail) if self.detail else ''}\n")
                    for line in self.recent:
                        f.write(line + "\n")
        except OSError:
            pass
