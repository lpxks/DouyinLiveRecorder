"""按链接覆盖分段时长(dur: 标记)在 main.py 里的接线断言。

`_parse_segment_seconds` / `resolve_record_segment` / `_format_segment_seconds` 从 main.py
用 AST 提取真实源码执行(注入假的全局 duration_spec_by_url, 避免跑起真录制)；接线断言用源码
文本检查关键不变量：映射原子替换、三条生效路径都登记 dur、录制命令只用链接自己的分段值、
6 个 check_subprocess 调用都传了真实分段开关。
"""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_LOADED_NAMES = ('_parse_segment_seconds', '_format_segment_seconds', 'resolve_record_segment')


def load_namespace():
    tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
    namespace = {'duration_spec_by_url': {}}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _LOADED_NAMES:
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), namespace)
    missing = [n for n in _LOADED_NAMES if n not in namespace]
    if missing:
        raise RuntimeError(f'not found in main.py: {missing}')
    return namespace


NS = load_namespace()
PARSE_SEGMENT_SECONDS = NS['_parse_segment_seconds']
FORMAT_SEGMENT_SECONDS = NS['_format_segment_seconds']
RESOLVE_RECORD_SEGMENT = NS['resolve_record_segment']


class ParseSegmentSecondsTest(unittest.TestCase):
    """配置里的 `视频分段时间(秒)`：非法值回退默认值, 避免写坏后整条 ffmpeg 失败。"""

    def test_numbers_are_parsed(self):
        self.assertEqual(PARSE_SEGMENT_SECONDS('1800'), 1800)
        self.assertEqual(PARSE_SEGMENT_SECONDS(' 600 '), 600)
        self.assertEqual(PARSE_SEGMENT_SECONDS(900), 900)
        self.assertEqual(PARSE_SEGMENT_SECONDS('12.7'), 12)

    def test_invalid_falls_back_to_default(self):
        for bad in ('', 'abc', None, 0, -5, '  '):
            self.assertEqual(PARSE_SEGMENT_SECONDS(bad), 1800, repr(bad))

    def test_custom_default(self):
        self.assertEqual(PARSE_SEGMENT_SECONDS('abc', default_seconds=600), 600)


class ResolveRecordSegmentTest(unittest.TestCase):
    """行内 dur: 与全局开关的组合：未标注随全局, 正数强制分段, 0 强制不分段。"""

    URL = 'https://live.douyin.com/1'

    def resolve(self, marks, default_enabled, default_seconds=1800):
        NS['duration_spec_by_url'] = dict(marks)
        try:
            return RESOLVE_RECORD_SEGMENT(self.URL, default_enabled, default_seconds)
        finally:
            NS['duration_spec_by_url'] = {}

    def test_unmarked_follows_global_setting(self):
        self.assertEqual(self.resolve({}, True), (True, 1800))
        self.assertEqual(self.resolve({}, False), (False, 1800))
        self.assertEqual(self.resolve({}, True, 600), (True, 600))

    def test_positive_mark_enables_and_overrides(self):
        self.assertEqual(self.resolve({self.URL: 1800}, False), (True, 1800))
        self.assertEqual(self.resolve({self.URL: 60}, True), (True, 60))

    def test_zero_mark_disables_segmentation(self):
        self.assertEqual(self.resolve({self.URL: 0}, True), (False, 1800))
        self.assertEqual(self.resolve({self.URL: 0}, False), (False, 1800))

    def test_other_urls_are_not_affected(self):
        self.assertEqual(self.resolve({'https://live.douyin.com/2': 60}, True), (True, 1800))


class FormatSegmentSecondsTest(unittest.TestCase):
    """提示文本: 整小时/整分钟用 h/m, 其余用秒。"""

    def test_units(self):
        self.assertEqual(FORMAT_SEGMENT_SECONDS(3600), '1h')
        self.assertEqual(FORMAT_SEGMENT_SECONDS(7200), '2h')
        self.assertEqual(FORMAT_SEGMENT_SECONDS(1800), '30m')
        self.assertEqual(FORMAT_SEGMENT_SECONDS(5400), '90m')
        self.assertEqual(FORMAT_SEGMENT_SECONDS(90), '90s')
        self.assertEqual(FORMAT_SEGMENT_SECONDS(45), '45s')


class SegmentWiringTest(unittest.TestCase):
    """main.py 的接线不变量(用源码文本断言, 防止以后改回全局值)。"""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'main.py').read_text(encoding='utf-8')

    def test_duration_mapping_is_swapped_atomically(self):
        self.assertEqual(
            len(re.findall(r'duration_spec_by_url = new_duration_spec_by_url', self.source)), 1)

    def test_all_active_paths_register_duration(self):
        """三条生效路径(首现/合并标记/解开注释)都必须登记 dur。"""
        self.assertEqual(len(re.findall(r'new_duration_spec_by_url\[url\] = ', self.source)), 3)

    def test_recording_commands_use_link_specific_value(self):
        self.assertIn('resolve_record_segment(', self.source)
        self.assertIn('"-segment_time", str(seg_seconds)', self.source)
        # 录制分支里不能再直接用全局值, 否则按链接覆盖不生效
        self.assertNotIn('"-segment_time", split_time', self.source)
        self.assertNotIn('"-segment_time", str(split_time)', self.source)

    def test_check_subprocess_gets_effective_segment_flag(self):
        self.assertIn('segment_enabled: bool = False', self.source)
        self.assertEqual(len(re.findall(r'custom_script,\n\s+seg_enabled', self.source)), 6)
        self.assertIn('if create_time_file and not segment_enabled', self.source)
        self.assertIn("f'--split_video_by_time {segment_enabled}'", self.source)

    def test_duration_mark_is_merged_on_dedup(self):
        self.assertIn('duration_mark(duration_raw)', self.source)
        self.assertIn('first_info.get(\'duration\') is None', self.source)

    def test_invalid_duration_is_warned_once(self):
        self.assertIn('duration_spec_warning(duration, duration_raw)', self.source)
        self.assertIn('warned_duration_specs.add(duration_raw)', self.source)

    def test_display_mentions_custom_segment_links(self):
        self.assertIn('自定义分段链接', self.source)


if __name__ == '__main__':
    unittest.main()
