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


if __name__ == '__main__':
    unittest.main()
