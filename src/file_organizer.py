"""录制文件自动整理: 把录制根目录下散落各子目录的文件收拢、清理空目录、
删掉过短的录像、再归档到指定目录。

设计要点(与 main.py 的配合见 CLAUDE.md):
- **定时**: 后台守护线程按可配置间隔执行, 关闭时线程不启动; 配置由主循环
  读取后通过 `update_settings()` 原子替换, 整理线程只读快照, 不碰 configparser。
- **不碰正在录制的文件**: 进程内 busy 注册表(文件/目录, 引用计数)+ 文件
  mtime 宽限期双重保护。ffmpeg 分段录制会往同一目录里不断创建新文件, 所以
  `check_subprocess()` 注册的是"输出目录"而不是单个文件。
- **时长判断**: 优先 ffprobe, 没有 ffprobe 时退回解析 `ffmpeg -i` 的 stderr;
  都拿不到时长就保留文件(宁可不删)。Windows 发行包只带了 ffmpeg.exe,
  因此 ffmpeg 兜底是必需的。
- **安全默认**: 默认关闭; 删除阈值默认 0(不删除); 归档目标留空则不归档;
  源目录内的归档目标子树不会被扫描/删除, 避免自己搬自己。
"""

import dataclasses
import json
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager

from .utils import logger

# 视频/音频扩展名(小写, 不含点)。分段产物 _%03d.<ext> 也按扩展名匹配;
# .part/.tmp 等中间文件不在集合里, 不会被搬动
VIDEO_EXTENSIONS = frozenset({'ts', 'mkv', 'flv', 'mp4', 'mov', 'avi', 'webm', 'm4v', 'wmv'})
AUDIO_EXTENSIONS = frozenset({'mp3', 'm4a', 'aac', 'flac', 'wav', 'ogg', 'mka'})
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

DEFAULT_LOOP_TICK_SECONDS = 1.0
DEFAULT_INTERVAL_MINUTES = 60
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 7 * 24 * 60  # 7 天
DEFAULT_MIN_DURATION_SECONDS = 0.0
MAX_MIN_DURATION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_GRACE_SECONDS = 120
MAX_GRACE_SECONDS = 24 * 60 * 60
PROBE_TIMEOUT_SECONDS = 30

_DURATION_LINE_RE = re.compile(r'Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)')


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class OrganizerSettings:
    """整理任务的配置快照(由 main.py 每个主循环轮次重建后原子替换)。"""

    enabled: bool = False
    interval_seconds: int = DEFAULT_INTERVAL_MINUTES * 60
    source_dir: str = ''
    target_dir: str = ''
    min_duration_seconds: float = DEFAULT_MIN_DURATION_SECONDS
    grace_seconds: int = DEFAULT_GRACE_SECONDS


@dataclasses.dataclass
class OrganizeReport:
    """一轮整理的统计。"""

    moved_to_root: int = 0
    removed_dirs: int = 0
    deleted_short: int = 0
    archived: int = 0

    @property
    def total(self) -> int:
        return self.moved_to_root + self.removed_dirs + self.deleted_short + self.archived


_settings_lock = threading.Lock()
_settings = OrganizerSettings()
_loop_thread: threading.Thread | None = None
_warned_once: set = set()
_warned_once_lock = threading.Lock()


def _warn_once(key, message: str) -> None:
    with _warned_once_lock:
        if key in _warned_once:
            return
        _warned_once.add(key)
    logger.warning(message)


def _reset_warned_once() -> None:
    """测试用: 清空"只提示一次"的记录。"""
    with _warned_once_lock:
        _warned_once.clear()


def _to_float(raw):
    """把配置值转成 float; 无法转换返回 None(不抛异常)。"""
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def parse_interval_minutes(raw, default: int = DEFAULT_INTERVAL_MINUTES) -> int:
    """整理间隔(分钟): 非法值回退默认, 钳制到 1~10080。"""
    value = _to_float(raw)
    if value is None:
        _warn_once(
            ('interval', str(raw)),
            f'[文件整理] 整理间隔(分钟) 的值无效({raw}), 按默认 {default} 分钟',
        )
        return default
    return int(min(MAX_INTERVAL_MINUTES, max(MIN_INTERVAL_MINUTES, value)))


def parse_min_duration_seconds(raw, default: float = DEFAULT_MIN_DURATION_SECONDS) -> float:
    """删除阈值(秒): 0 = 不删除; 负数按 0, 非法回退默认, 上限 7 天。"""
    value = _to_float(raw)
    if value is None:
        _warn_once(
            ('min_duration', str(raw)),
            f'[文件整理] 删除小于该时长的视频(秒) 的值无效({raw}), 按默认 {default:g} 秒',
        )
        return default
    return min(MAX_MIN_DURATION_SECONDS, max(0.0, value))


def parse_grace_seconds(raw, default: int = DEFAULT_GRACE_SECONDS) -> int:
    """写入宽限期(秒): 文件 mtime 在此范围内视为仍在写入。"""
    value = _to_float(raw)
    if value is None:
        _warn_once(
            ('grace', str(raw)),
            f'[文件整理] 写入宽限期(秒) 的值无效({raw}), 按默认 {default} 秒',
        )
        return default
    return int(min(MAX_GRACE_SECONDS, max(0, value)))


def get_settings() -> OrganizerSettings:
    with _settings_lock:
        return _settings


def update_settings(settings: OrganizerSettings) -> bool:
    """原子替换配置; 返回是否发生了变化。

    第一次读到 enabled=True 时懒启动整理线程, 关闭状态不额外占线程。
    """
    global _settings
    normalized = dataclasses.replace(
        settings,
        interval_seconds=int(min(MAX_INTERVAL_MINUTES * 60,
                                  max(MIN_INTERVAL_MINUTES * 60, settings.interval_seconds))),
        source_dir=(settings.source_dir or '').strip(),
        target_dir=(settings.target_dir or '').strip(),
        min_duration_seconds=min(MAX_MIN_DURATION_SECONDS, max(0.0, float(settings.min_duration_seconds))),
        grace_seconds=int(min(MAX_GRACE_SECONDS, max(0, int(settings.grace_seconds)))),
    )
    with _settings_lock:
        previous = _settings
        _settings = normalized
    if normalized != previous and (normalized.enabled or previous.enabled):
        logger.info(
            f"文件整理配置: {'开启' if normalized.enabled else '关闭'}, "
            f"间隔 {normalized.interval_seconds // 60} 分钟, "
            f"源 {normalized.source_dir or '(未设置)'}, "
            f"归档 {normalized.target_dir or '(不归档)'}, "
            f"删除小于 {normalized.min_duration_seconds:g} 秒, "
            f"宽限 {normalized.grace_seconds} 秒"
        )
    if normalized.enabled:
        start_organizer_thread()
    return normalized != previous


def start_organizer_thread() -> None:
    """幂等启动整理线程(第一次启用时才创建)。"""
    global _loop_thread
    with _settings_lock:
        if _loop_thread is not None and _loop_thread.is_alive():
            return
        _loop_thread = threading.Thread(target=_organizer_loop, name='file-organizer', daemon=True)
        _loop_thread.start()


def is_organizer_running() -> bool:
    with _settings_lock:
        return _loop_thread is not None and _loop_thread.is_alive()


def _organizer_loop() -> None:
    """后台循环: 每秒醒一次, 到点执行一轮; 任何异常都不退出线程。"""
    last_run = 0.0
    while True:
        try:
            settings = get_settings()
            if settings.enabled:
                now = time.monotonic()
                if now - last_run >= max(1, settings.interval_seconds):
                    last_run = now
                    report = run_once(settings)
                    if report.total:
                        logger.info(
                            f"文件整理完成: 扁平化 {report.moved_to_root} 个, "
                            f"删除空目录 {report.removed_dirs} 个, "
                            f"删除短视频 {report.deleted_short} 个, "
                            f"归档 {report.archived} 个"
                        )
                    else:
                        logger.debug("文件整理完成: 本轮没有需要处理的文件")
        except Exception as err:  # 整理失败不能影响录制主流程
            logger.error(f"文件整理任务异常: {err}")
        time.sleep(DEFAULT_LOOP_TICK_SECONDS)


# --------------------------------------------------------------------------
# 正在写入/处理中的路径注册表
# --------------------------------------------------------------------------

_busy_lock = threading.Lock()
_busy_dirs: dict = {}
_busy_files: dict = {}


def _norm_path(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def register_busy_dir(path) -> None:
    key = _norm_path(path)
    with _busy_lock:
        _busy_dirs[key] = _busy_dirs.get(key, 0) + 1


def unregister_busy_dir(path) -> None:
    key = _norm_path(path)
    with _busy_lock:
        count = _busy_dirs.get(key, 0)
        if count <= 1:
            _busy_dirs.pop(key, None)
        else:
            _busy_dirs[key] = count - 1


def register_busy_file(path) -> None:
    key = _norm_path(path)
    with _busy_lock:
        _busy_files[key] = _busy_files.get(key, 0) + 1


def unregister_busy_file(path) -> None:
    key = _norm_path(path)
    with _busy_lock:
        count = _busy_files.get(key, 0)
        if count <= 1:
            _busy_files.pop(key, None)
        else:
            _busy_files[key] = count - 1


@contextmanager
def busy_dir(path):
    register_busy_dir(path)
    try:
        yield
    finally:
        unregister_busy_dir(path)


@contextmanager
def busy_file(path):
    register_busy_file(path)
    try:
        yield
    finally:
        unregister_busy_file(path)


def reset_busy_registry() -> None:
    """测试用: 清空注册表。"""
    with _busy_lock:
        _busy_dirs.clear()
        _busy_files.clear()


def _reset_for_tests() -> None:
    """测试用: 恢复默认配置并清空注册表/提示/缓存(不结束已启动的线程)。"""
    global _settings
    with _settings_lock:
        _settings = OrganizerSettings()
    reset_busy_registry()
    _reset_warned_once()
    _reset_ffprobe_cache()


def _file_is_busy(path) -> bool:
    key = _norm_path(path)
    with _busy_lock:
        return key in _busy_files


def _dir_is_busy(path) -> bool:
    """目录自身或任一祖先目录被注册为 busy 都算 busy。"""
    key = _norm_path(path)
    with _busy_lock:
        if key in _busy_dirs:
            return True
        busy = set(_busy_dirs)
    parent = os.path.dirname(key)
    while parent and parent != os.path.dirname(parent):
        if parent in busy:
            return True
        parent = os.path.dirname(parent)
    return False


def is_media_file(name: str) -> bool:
    suffix = os.path.splitext(name)[1].lstrip('.').lower()
    return bool(suffix) and suffix in MEDIA_EXTENSIONS


def _is_protected(path, settings: OrganizerSettings) -> bool:
    """符号链接、busy(自身/父目录/祖先目录)、mtime 在宽限期内的文件都跳过。"""
    if os.path.islink(path):
        return True
    if _file_is_busy(path) or _dir_is_busy(os.path.dirname(path)):
        return True
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return True
    return time.time() - mtime < max(0, settings.grace_seconds)


# --------------------------------------------------------------------------
# 时长探测
# --------------------------------------------------------------------------

_ffprobe_lock = threading.Lock()
_ffprobe_checked = False
_ffprobe_path: str | None = None


def _find_ffprobe() -> str | None:
    """查找 ffprobe: 先 PATH, 再找与 ffmpeg 同目录的同名程序(Windows 发行包)。"""
    global _ffprobe_checked, _ffprobe_path
    with _ffprobe_lock:
        if _ffprobe_checked:
            return _ffprobe_path
        _ffprobe_checked = True
        candidates = []
        found = shutil.which('ffprobe')
        if found:
            candidates.append(found)
        ffmpeg = shutil.which('ffmpeg')
        if ffmpeg:
            sibling_name = 'ffprobe.exe' if os.name == 'nt' else 'ffprobe'
            sibling = os.path.join(os.path.dirname(ffmpeg), sibling_name)
            if os.path.isfile(sibling):
                candidates.append(sibling)
        for candidate in candidates:
            if os.path.isfile(candidate):
                _ffprobe_path = candidate
                break
        return _ffprobe_path


def _reset_ffprobe_cache() -> None:
    """测试用: 允许重新探测 ffprobe 路径。"""
    global _ffprobe_checked, _ffprobe_path
    with _ffprobe_lock:
        _ffprobe_checked = False
        _ffprobe_path = None


def _positive_float(raw) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.upper() == 'N/A':
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def _probe_with_ffprobe(exe: str, path, timeout: float) -> float | None:
    try:
        result = subprocess.run(
            [exe, '-v', 'error', '-show_entries', 'format=duration:stream=duration',
             '-of', 'json', str(path)],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        payload = json.loads(result.stdout or '{}')
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    candidates = [(payload.get('format') or {}).get('duration')]
    for stream in payload.get('streams') or []:
        if isinstance(stream, dict):
            candidates.append(stream.get('duration'))
    for raw in candidates:
        value = _positive_float(raw)
        if value is not None:
            return value
    return None


def _probe_with_ffmpeg(path, timeout: float) -> float | None:
    ffmpeg = shutil.which('ffmpeg') or 'ffmpeg'
    try:
        result = subprocess.run(
            [ffmpeg, '-hide_banner', '-nostdin', '-i', str(path)],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _DURATION_LINE_RE.search(result.stderr or '')
    if not match:
        return None
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def probe_duration_seconds(path, timeout: float = PROBE_TIMEOUT_SECONDS) -> float | None:
    """返回媒体时长(秒); 0 字节文件返回 0.0; 无法判断返回 None(调用方保留文件)。"""
    try:
        if os.path.getsize(path) == 0:
            return 0.0
    except OSError:
        return None
    exe = _find_ffprobe()
    if exe:
        value = _probe_with_ffprobe(exe, path, timeout)
        if value is not None:
            return value
    else:
        _warn_once(('ffprobe-missing', ''),
                   '文件整理: 未找到 ffprobe, 时长判断改为解析 ffmpeg 输出')
    return _probe_with_ffmpeg(path, timeout)


# --------------------------------------------------------------------------
# 文件移动
# --------------------------------------------------------------------------

def _same_path(path_a, path_b) -> bool:
    return _norm_path(path_a) == _norm_path(path_b)


def _is_within(path, root) -> bool:
    """path 是否等于 root 或位于 root 之下。"""
    try:
        normalized_path = _norm_path(path)
        normalized_root = _norm_path(root)
        return os.path.commonpath([normalized_path, normalized_root]) == normalized_root
    except ValueError:  # Windows 不同盘符
        return False


def _unique_destination(directory: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    index = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f'{base}_{index}{ext}')
        index += 1
    return candidate


def move_to_directory(src_path, dst_dir) -> str | None:
    """把文件移动到 dst_dir(同名自动加 _1/_2 后缀); 已是目标目录则返回 None。"""
    src_abs = os.path.abspath(str(src_path))
    dst_abs = os.path.abspath(str(dst_dir))
    if _norm_path(os.path.dirname(src_abs)) == _norm_path(dst_abs):
        return None
    try:
        os.makedirs(dst_abs, exist_ok=True)
        dst_path = _unique_destination(dst_abs, os.path.basename(src_abs))
        shutil.move(src_abs, dst_path)
        return dst_path
    except (OSError, shutil.Error) as err:
        logger.warning(f'文件整理: 移动 {src_abs} 到 {dst_abs} 失败: {err}')
        return None


def _resolved_source(settings: OrganizerSettings) -> str:
    raw = (settings.source_dir or '').strip()
    return os.path.abspath(raw) if raw else ''


def _resolved_target(settings: OrganizerSettings) -> str:
    raw = (settings.target_dir or '').strip()
    return os.path.abspath(raw) if raw else ''


def _excluded_dir(path, source: str, target: str) -> bool:
    """源目录内部的归档目标子树不参与扫描/删除。"""
    if not target:
        return False
    return _is_within(target, source) and _is_within(path, target)


# --------------------------------------------------------------------------
# 单次整理的四个步骤
# --------------------------------------------------------------------------

def flatten_media(source_dir, settings: OrganizerSettings, report: OrganizeReport,
                  touched_dirs: set | None = None) -> None:
    """递归把子目录里的媒体文件移动到源根目录(跳过 busy/宽限期/归档目标)。

    touched_dirs 收集"本轮被搬空"的目录, 供 remove_empty_dirs 区分"刚建出来还没
    写入的录制目录"(要保留)和"刚被搬空的目录"(要删除)——两者 mtime 都很新。
    """
    source = os.path.abspath(str(source_dir))
    target = _resolved_target(settings)
    for dirpath, dirnames, filenames in os.walk(source, topdown=True):
        dirnames[:] = sorted(
            name for name in dirnames
            if not os.path.islink(os.path.join(dirpath, name))
            and not _excluded_dir(os.path.join(dirpath, name), source, target)
            and not _dir_is_busy(os.path.join(dirpath, name))
        )
        if _same_path(dirpath, source):
            continue
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if not is_media_file(name) or _is_protected(path, settings):
                continue
            moved = move_to_directory(path, source)
            if moved:
                report.moved_to_root += 1
                if touched_dirs is not None:
                    touched_dirs.add(_norm_path(dirpath))
                logger.debug(f'文件整理: 扁平化 {path} -> {moved}')


def remove_empty_dirs(source_dir, settings: OrganizerSettings, report: OrganizeReport,
                      touched_dirs: set | None = None) -> None:
    """自底向上删除空目录; 保留源根目录本身、busy 目录、新建目录和归档目标子树。"""
    source = os.path.abspath(str(source_dir))
    target = _resolved_target(settings)
    grace = max(0, settings.grace_seconds)
    touched = touched_dirs if touched_dirs is not None else set()
    for dirpath, _dirnames, _filenames in os.walk(source, topdown=False):
        if _same_path(dirpath, source) or os.path.islink(dirpath):
            continue
        if _excluded_dir(dirpath, source, target) or _dir_is_busy(dirpath):
            continue
        key = _norm_path(dirpath)
        if key not in touched:
            try:
                if time.time() - os.path.getmtime(dirpath) < grace:
                    continue  # 目录刚建出来, ffmpeg 可能还没打开输出文件
            except OSError:
                continue
        try:
            with os.scandir(dirpath) as entries:
                if any(True for _ in entries):
                    continue
            os.rmdir(dirpath)
            report.removed_dirs += 1
            touched.add(_norm_path(os.path.dirname(dirpath)))
            logger.debug(f'文件整理: 删除空目录 {dirpath}')
        except OSError:
            continue


def delete_short_media(source_dir, settings: OrganizerSettings, report: OrganizeReport) -> None:
    """删除源根目录直属的、时长小于阈值的媒体文件; 时长未知则保留。"""
    if settings.min_duration_seconds <= 0:
        return
    source = os.path.abspath(str(source_dir))
    try:
        with os.scandir(source) as entries:
            candidates = sorted(entries, key=lambda entry: entry.name)
    except OSError as err:
        logger.warning(f'文件整理: 无法列出 {source}: {err}')
        return
    for entry in candidates:
        try:
            if not entry.is_file(follow_symlinks=False) or not is_media_file(entry.name):
                continue
        except OSError:
            continue
        if _is_protected(entry.path, settings):
            continue
        duration = probe_duration_seconds(entry.path)
        if duration is None:
            logger.debug(f'文件整理: 无法获取时长, 保留 {entry.path}')
            continue
        if duration >= settings.min_duration_seconds:
            continue
        try:
            os.remove(entry.path)
            report.deleted_short += 1
            logger.info(f'文件整理: 删除过短视频 {entry.name} ({duration:g} 秒)')
        except OSError as err:
            logger.warning(f'文件整理: 删除 {entry.path} 失败: {err}')


def archive_media(source_dir, settings: OrganizerSettings, report: OrganizeReport) -> None:
    """把源根目录直属的媒体文件移动到归档目标目录。"""
    source = os.path.abspath(str(source_dir))
    target = _resolved_target(settings)
    if not target or _same_path(source, target):
        return
    try:
        with os.scandir(source) as entries:
            candidates = sorted(entries, key=lambda entry: entry.name)
    except OSError as err:
        logger.warning(f'文件整理: 无法列出 {source}: {err}')
        return
    for entry in candidates:
        try:
            if not entry.is_file(follow_symlinks=False) or not is_media_file(entry.name):
                continue
        except OSError:
            continue
        if _is_protected(entry.path, settings):
            continue
        moved = move_to_directory(entry.path, target)
        if moved:
            report.archived += 1
            logger.debug(f'文件整理: 归档 {entry.path} -> {moved}')


def run_once(settings: OrganizerSettings | None = None) -> OrganizeReport:
    """执行一轮整理: 扁平化 -> 删空目录 -> 删短视频 -> 归档。"""
    settings = settings or get_settings()
    report = OrganizeReport()
    if not settings.enabled:
        return report
    source = _resolved_source(settings)
    if not source:
        _warn_once(
            ('source-empty', ''),
            '文件整理: 已开启但源目录为空, 请设置 [文件整理] 整理源目录'
            '或 [录制设置] 直播保存路径',
        )
        return report
    if not os.path.isdir(source):
        _warn_once(('source-missing', source), f'文件整理: 源目录不存在 {source}, 跳过本轮')
        return report
    touched_dirs: set = set()
    flatten_media(source, settings, report, touched_dirs)
    remove_empty_dirs(source, settings, report, touched_dirs)
    delete_short_media(source, settings, report)
    archive_media(source, settings, report)
    return report
