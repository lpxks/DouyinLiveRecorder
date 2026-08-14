"""录制后分辨率变化检测测试。

analyze_resolution_changes 从 main.py 用 AST 提取真实源码执行(避免导入整个程序);
用本机 ffmpeg/ffprobe 生成测试视频: 单分辨率文件应返回 1 项, 双分辨率(切换)应返回 2 项。
无 ffmpeg/ffprobe 的环境自动跳过。
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HAS_TOOLS = shutil.which('ffmpeg') and shutil.which('ffprobe')


def load_analyze_resolution_changes():
    """从 main.py 提取 analyze_resolution_changes + get_startup_info 的真实源码执行。"""
    main_py = Path(__file__).resolve().parents[1] / 'main.py'
    tree = ast.parse(main_py.read_text(encoding='utf-8'))
    namespace = {'shutil': shutil, 'subprocess': subprocess, 'os_type': os.name}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ('analyze_resolution_changes',
                                                               'get_startup_info'):
            module = ast.Module(body=[node], type_ignores=[])
            exec(compile(module, 'main.py', 'exec'), namespace)
    if 'analyze_resolution_changes' not in namespace:
        raise RuntimeError('analyze_resolution_changes not found in main.py')
    return namespace['analyze_resolution_changes']


class _FakeLogger:
    """AST 提取命名空间里的 logger 替身(修复函数会打日志)。"""

    def warning(self, *args, **kwargs):
        pass


def load_pts_namespace():
    """从 main.py 提取 PTS 检测/修复函数链的真实源码执行, 返回命名空间。"""
    main_py = Path(__file__).resolve().parents[1] / 'main.py'
    tree = ast.parse(main_py.read_text(encoding='utf-8'))
    namespace = {'shutil': shutil, 'subprocess': subprocess, 'os_type': os.name,
                 'os': os, 'threading': threading,
                 'logger': _FakeLogger(), 'FFMPEG_VERSION_MAJOR': 8}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ('detect_pts_jumps',
                                                               '_scan_pts_jumps',
                                                               '_format_jump',
                                                               'repair_pts_jumps',
                                                               'get_startup_info'):
            module = ast.Module(body=[node], type_ignores=[])
            exec(compile(module, 'main.py', 'exec'), namespace)
    if 'detect_pts_jumps' not in namespace or 'repair_pts_jumps' not in namespace:
        raise RuntimeError('detect_pts_jumps/repair_pts_jumps not found in main.py')
    return namespace


# 模块级保存函数(存类属性会被 descriptor 绑定成方法, 多传 self 导致 TypeError)
DETECT = load_analyze_resolution_changes()


@unittest.skipUnless(HAS_TOOLS, "需要本机 ffmpeg/ffprobe")
class ResolutionDetectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    def run_ffmpeg(self, args):
        return subprocess.run(['ffmpeg', '-y', '-loglevel', 'error'] + args,
                              capture_output=True, text=True)

    def make_single_resolution(self):
        """生成 320x240 单分辨率视频。"""
        path = Path(self.tmp, 'single.mp4')
        r = self.run_ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=10',
                             '-t', '1', '-c:v', 'libx264', '-preset', 'ultrafast', str(path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        return str(path)

    def make_changed_resolution(self):
        """生成 320x240 → 640x480 切换视频(concat demuxer -c copy 硬拼,
        参数不匹配的真实花屏文件形态, ffprobe 逐关键帧可检出两个尺寸)。"""
        a, b = Path(self.tmp, 'a.mp4'), Path(self.tmp, 'b.mp4')
        for size, out in (('320x240', a), ('640x480', b)):
            r = self.run_ffmpeg(['-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=10',
                                 '-t', '1', '-c:v', 'libx264', '-preset', 'ultrafast', str(out)])
            self.assertEqual(r.returncode, 0, r.stderr)
        list_file = Path(self.tmp, 'list.txt')
        list_file.write_text(f"file '{a}'\nfile '{b}'\n", encoding='utf-8')
        path = Path(self.tmp, 'changed.mp4')
        r = self.run_ffmpeg(['-f', 'concat', '-safe', '0', '-i', str(list_file),
                             '-c', 'copy', str(path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        return str(path)

    def test_single_resolution_returns_one(self):
        resolutions = DETECT(self.make_single_resolution())
        self.assertEqual(len(resolutions), 1, resolutions)

    def test_changed_resolution_returns_two(self):
        resolutions = DETECT(self.make_changed_resolution())
        self.assertEqual(len(resolutions), 2, resolutions)
        self.assertIn('320x240', resolutions)
        self.assertIn('640x480', resolutions)


def shift_flv_timestamps(data: bytes, from_ms: int, delta_ms: int) -> bytes:
    """把 FLV 中时间戳 >= from_ms 的 tag 整体平移 delta_ms(制造结尾跳变)。"""
    buf = bytearray(data)
    pos = 9
    while pos + 15 <= len(buf):
        dlen = int.from_bytes(buf[pos + 5:pos + 8], 'big')
        if pos + 15 + dlen > len(buf):
            break
        ts = (buf[pos + 11] << 24) | (buf[pos + 8] << 16) | (buf[pos + 9] << 8) | buf[pos + 10]
        if ts >= from_ms:
            new_ts = ts + delta_ms
            buf[pos + 8:pos + 11] = (new_ts & 0xFFFFFF).to_bytes(3, 'big')
            buf[pos + 11] = (new_ts >> 24) & 0xFF
        pos = pos + 15 + dlen
    return bytes(buf)


def shift_range_flv_timestamps(data: bytes, from_ms: int, to_ms: int, delta_ms: int) -> bytes:
    """把 FLV 中时间戳落在 [from_ms, to_ms) 的 tag 平移 delta_ms(制造中段跳变+回退)。"""
    buf = bytearray(data)
    pos = 9
    while pos + 15 <= len(buf):
        dlen = int.from_bytes(buf[pos + 5:pos + 8], 'big')
        if pos + 15 + dlen > len(buf):
            break
        ts = (buf[pos + 11] << 24) | (buf[pos + 8] << 16) | (buf[pos + 9] << 8) | buf[pos + 10]
        if from_ms <= ts < to_ms:
            new_ts = ts + delta_ms
            buf[pos + 8:pos + 11] = (new_ts & 0xFFFFFF).to_bytes(3, 'big')
            buf[pos + 11] = (new_ts >> 24) & 0xFF
        pos = pos + 15 + dlen
    return bytes(buf)


# 模块级保存函数(存类属性会被 descriptor 绑定成方法, 多传 self 导致 TypeError)
PTS_NS = load_pts_namespace()
DETECT_PTS_JUMPS = PTS_NS['detect_pts_jumps']


@unittest.skipUnless(HAS_TOOLS, "需要本机 ffmpeg/ffprobe")
class PtsJumpDetectTest(unittest.TestCase):
    """detect_pts_jumps: 尾部时间戳跳变的文件应检出, 正常文件不检出。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        src = Path(cls.tmp, 'src.flv')
        r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error',
                            '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=10',
                            '-t', '30', '-c:v', 'libx264', '-preset', 'ultrafast',
                            '-f', 'flv', str(src)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        cls.src_data = src.read_bytes()

    def record_mkv(self, flv_data, name):
        flv = Path(self.tmp, f'{name}.flv')
        flv.write_bytes(flv_data)
        mkv = Path(self.tmp, f'{name}.mkv')
        r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(flv),
                            '-c', 'copy', '-f', 'matroska', str(mkv)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return str(mkv)

    def test_normal_file_no_jump(self):
        self.assertIsNone(DETECT_PTS_JUMPS(self.record_mkv(self.src_data, 'normal')))

    def test_tail_jump_detected(self):
        """尾部 +2h 平移(ffmpeg 透传, 时长虚高)应被检出。"""
        jumped = shift_flv_timestamps(self.src_data, 25000, 2 * 3600 * 1000)
        mkv = self.record_mkv(jumped, 'jump')
        result = DETECT_PTS_JUMPS(mkv)
        self.assertIsNotNone(result, '尾部 2 小时跳变应被检出')
        self.assertIn('2.0 小时', result)

    def test_minute_level_jump_detected(self):
        """+10 分钟跳变(播放卡 10 分钟)也应检出, 提示用分钟格式。"""
        jumped = shift_flv_timestamps(self.src_data, 25000, 10 * 60 * 1000)
        mkv = self.record_mkv(jumped, 'jump10m')
        result = DETECT_PTS_JUMPS(mkv)
        self.assertIsNotNone(result, '尾部 10 分钟跳变应被检出')
        self.assertIn('10 分钟', result)


@unittest.skipUnless(HAS_TOOLS, "需要本机 ffmpeg/ffprobe")
class PtsJumpRepairTest(unittest.TestCase):
    """repair_pts_jumps: 尾部跳变应被 -c copy 修复; 不安全跳变形态应跳过。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        src = Path(cls.tmp, 'src.flv')
        r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error',
                            '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=10',
                            '-t', '30', '-c:v', 'libx264', '-preset', 'ultrafast',
                            '-f', 'flv', str(src)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        cls.src_data = src.read_bytes()

    def record_mkv(self, flv_data, name):
        flv = Path(self.tmp, f'{name}.flv')
        flv.write_bytes(flv_data)
        mkv = Path(self.tmp, f'{name}.mkv')
        r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(flv),
                            '-c', 'copy', '-f', 'matroska', str(mkv)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return str(mkv)

    def duration(self, path):
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'default=nw=1:nk=1', path],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())

    def test_tail_jump_repaired(self):
        """尾部 +2h 跳变: 修复后时长应恢复 ~30s, 且原文件被原子替换。"""
        jumped = shift_flv_timestamps(self.src_data, 25000, 2 * 3600 * 1000)
        mkv = self.record_mkv(jumped, 'repair')
        jumps = PTS_NS['_scan_pts_jumps'](mkv)
        self.assertTrue(jumps, '应检出跳变')
        self.assertGreater(self.duration(mkv), 3600, '前置条件: 修复前时长虚高')
        self.assertTrue(PTS_NS['repair_pts_jumps'](mkv, jumps), '应修复成功')
        fixed_dur = self.duration(mkv)
        self.assertGreater(fixed_dur, 29.0, f'时长异常: {fixed_dur}')
        self.assertLess(fixed_dur, 31.0, f'修复无效, 时长仍虚高: {fixed_dur}')

    def test_mid_jump_clamped_then_repaired(self):
        """中段 +30 分钟后又回退的 FLV: ffmpeg 解复用时已把回退钳制为尾部跳变形态
        (尾部帧时间戳被钉在钳制点, 原始时间信息已丢), 修复应把时长大幅拉回内容
        时间量级(钳制造成的尾部压缩无法由 setts 恢复; 不同 ffmpeg 版本钳制方式
        略有差异, 故只断言时长显著回落)。"""
        mid = shift_range_flv_timestamps(self.src_data, 5000, 20000, 30 * 60 * 1000)
        mkv = self.record_mkv(mid, 'mid')
        jumps = PTS_NS['_scan_pts_jumps'](mkv)
        self.assertTrue(jumps, '应检出跳变')
        before = self.duration(mkv)
        self.assertGreater(before, 1500, '前置条件: 修复前时长虚高')
        self.assertTrue(PTS_NS['repair_pts_jumps'](mkv, jumps), '钳制后的跳变应可修复')
        fixed_dur = self.duration(mkv)
        self.assertGreater(fixed_dur, 0, f'时长异常: {fixed_dur}')
        self.assertLess(fixed_dur, before - 1000, f'修复无效, 时长仍虚高: {fixed_dur}')

    def test_unsafe_jump_skipped(self):
        """jumps 明细标记时间轴回退(tail_safe=False)时, 修复应被拒绝且文件不动。"""
        mkv = self.record_mkv(self.src_data, 'unsafe')
        before = self.duration(mkv)
        fake = {0: (25000.0, 7200000.0, False)}
        self.assertFalse(PTS_NS['repair_pts_jumps'](mkv, fake), 'tail_safe=False 应跳过修复')
        self.assertEqual(self.duration(mkv), before, '文件不应被改动')


if __name__ == '__main__':
    unittest.main()
