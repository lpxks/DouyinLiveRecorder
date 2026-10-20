"""ffmpeg 输出接管/日志风暴保护/空输出保护(ffmpeg_guard.py)的单元测试。"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ffmpeg_guard import FfmpegOutputGuard, output_bytes  # noqa: E402


class FakeStream:
    """按块逐个返回的 stdout 替身, 用于制造"持续输出"。"""

    def __init__(self, chunks, delay=0.0):
        self._chunks = list(chunks)
        self._delay = delay

    def read(self, _n):
        if not self._chunks:
            return b""
        if self._delay:
            time.sleep(self._delay)
        return self._chunks.pop(0)


class FakeProcess:
    def __init__(self, chunks, delay=0.0):
        self.stdout = FakeStream(chunks, delay)
        self.killed = False

    def kill(self):
        self.killed = True


class OutputBytesTest(unittest.TestCase):
    """空输出保护依赖的字节统计: 普通文件/分段文件/不存在的情况。"""

    def test_plain_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.ts")
            Path(path).write_bytes(b"x" * 12)
            self.assertEqual(output_bytes(path), 12)

    def test_missing_file(self):
        self.assertEqual(output_bytes("/no/such/file.ts"), 0)

    def test_segment_prefix_sums_files(self):
        with tempfile.TemporaryDirectory() as d:
            for name, size in (
                ("主播_2026-01-01_00-00-00_000.ts", 10),
                ("主播_2026-01-01_00-00-00_001.ts", 20),
                ("other_000.ts", 99),
            ):
                Path(os.path.join(d, name)).write_bytes(b"x" * size)
            target = os.path.join(d, "主播_2026-01-01_00-00-00_%03d.ts")
            self.assertEqual(output_bytes(target), 30)

    def test_segment_missing_dir(self):
        self.assertEqual(output_bytes("/no/such/dir/a_%03d.ts"), 0)


class StormGuardTest(unittest.TestCase):
    def test_burst_kills_process_and_dumps_scene(self):
        with tempfile.TemporaryDirectory() as d:
            log_path = os.path.join(d, "ffmpeg.log")
            storm_path = os.path.join(d, "ffmpeg_storm.log")
            # 每 10ms 一行, 约 100 行/秒; 阈值 20 行/秒、连续 1 秒即触发
            chunks = [b"Stream ends prematurely at 1, should be 2\n"] * 200
            process = FakeProcess(chunks, delay=0.01)
            guard = FfmpegOutputGuard(process, log_path, storm_path, label="序号1 主播",
                                       lines_per_sec=20, storm_seconds=1, dump_lines=5)
            guard.start()
            guard.join(5)
            self.assertTrue(guard.storm)
            self.assertTrue(process.killed)
            self.assertGreater(guard.total_lines, 20)
            dumped = Path(storm_path).read_text(encoding="utf-8")
            self.assertIn("序号1 主播", dumped)
            self.assertIn("Stream ends prematurely", dumped)

    def test_quiet_output_is_not_storm(self):
        with tempfile.TemporaryDirectory() as d:
            log_path = os.path.join(d, "ffmpeg.log")
            storm_path = os.path.join(d, "ffmpeg_storm.log")
            process = FakeProcess([b"error one\n", b"error two\r\n"])
            guard = FfmpegOutputGuard(process, log_path, storm_path)
            guard.start()
            guard.join(5)
            self.assertFalse(guard.storm)
            self.assertFalse(process.killed)
            self.assertEqual(guard.total_lines, 2)
            self.assertIn("error one", Path(log_path).read_text(encoding="utf-8"))
            self.assertFalse(os.path.exists(storm_path))


class WiringTest(unittest.TestCase):
    def test_check_subprocess_uses_guard_and_pipe(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("FfmpegOutputGuard(", src)
        self.assertIn("stdout=subprocess.PIPE", src)
        self.assertIn("output_bytes(save_file_path)", src)
        self.assertIn("guard.storm", src)
        self.assertIn("guard.no_output", src)
        self.assertIn('"-nostats"', src)


if __name__ == "__main__":
    unittest.main()
