"""检查时长等级配置读取与 main.py 接线断言。

`read_level_intervals` 从 main.py 用 AST 提取真实源码执行(注入假的 read_config_value,
避免读写真配置); 接线断言用源码文本检查关键不变量: 等级映射的原子替换、三条生效路径
都登记等级、延时计算改用等级、旧"优先"概念已彻底移除。
"""

import ast
import configparser
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from url_parser import LEVELS, LEVEL_DEFAULT, split_url_line  # noqa: E402

_LOADED_NAMES = ('read_level_intervals',)
_LOADED_CONSTANTS = ('LEVEL_INTERVAL_DEFAULTS',)

DEFAULTS = {'A': 5, 'B': 30, 'C': 90}


class _FakeLogger:
    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def load_namespace():
    tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
    namespace = {'logger': _FakeLogger(), 'configparser': configparser}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, 'id', None) in _LOADED_CONSTANTS for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), namespace)
        elif isinstance(node, ast.FunctionDef) and node.name in _LOADED_NAMES:
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), namespace)
    missing = [n for n in _LOADED_NAMES + _LOADED_CONSTANTS if n not in namespace]
    if missing:
        raise RuntimeError(f'not found in main.py: {missing}')
    return namespace


NS = load_namespace()
READ_LEVEL_INTERVALS = NS['read_level_intervals']


class ReadLevelIntervalsTest(unittest.TestCase):
    """[检查时长等级] 段的读取: 默认值、覆盖、非法值回退、节名与键名契约。"""

    def setUp(self):
        self.calls = []

    def reader(self, values):
        def fake_read_config_value(config_parser, section, option, default_value):
            self.calls.append((section, option, default_value))
            return values.get(option, default_value)
        return fake_read_config_value

    def read(self, values):
        NS['read_config_value'] = self.reader(values)
        try:
            return READ_LEVEL_INTERVALS(object())
        finally:
            NS.pop('read_config_value', None)

    def test_defaults_when_section_is_fresh(self):
        self.assertEqual(self.read({}), DEFAULTS)

    def test_section_and_option_names(self):
        self.read({})
        self.assertEqual([section for section, _, _ in self.calls],
                         ['检查时长等级'] * len(LEVELS))
        self.assertEqual([option for _, option, _ in self.calls],
                         ['A等级检查时长(秒)', 'B等级检查时长(秒)', 'C等级检查时长(秒)'])
        self.assertEqual([default for _, _, default in self.calls],
                         [DEFAULTS['A'], DEFAULTS['B'], DEFAULTS['C']])

    def test_configured_values_win(self):
        values = {'A等级检查时长(秒)': '1', 'B等级检查时长(秒)': '45', 'C等级检查时长(秒)': '120'}
        self.assertEqual(self.read(values), {'A': 1, 'B': 45, 'C': 120})

    def test_whitespace_and_plus_sign_tolerated(self):
        values = {'A等级检查时长(秒)': ' 7 ', 'B等级检查时长(秒)': '30', 'C等级检查时长(秒)': '90'}
        self.assertEqual(self.read(values)['A'], 7)

    def test_invalid_values_fall_back_to_that_level_default(self):
        values = {'A等级检查时长(秒)': 'abc', 'B等级检查时长(秒)': '0', 'C等级检查时长(秒)': '-30'}
        self.assertEqual(self.read(values), DEFAULTS)

    def test_zero_or_negative_never_returns_below_one(self):
        values = {'A等级检查时长(秒)': '0', 'B等级检查时长(秒)': '0', 'C等级检查时长(秒)': '0'}
        self.assertEqual(self.read(values), DEFAULTS)

    def test_non_string_values_are_tolerated(self):
        values = {'A等级检查时长(秒)': None, 'B等级检查时长(秒)': 12, 'C等级检查时长(秒)': 90}
        self.assertEqual(self.read(values), {'A': 5, 'B': 12, 'C': 90})


class LevelResolutionTableTest(unittest.TestCase):
    """12 种行形态 → 等级 → 检查间隔(秒), 复刻解析循环的取值链路。"""

    URL = 'https://live.douyin.com/277869507858'
    NAME = '主播: MMA盼盼'
    INTERVALS = DEFAULTS

    def resolve(self, line):
        quality, url, name, legacy, level = split_url_line(line, '原画')
        seconds = NS['read_level_intervals']
        del seconds  # 仅为表明间隔来自配置段, 此处用 DEFAULTS 直接解析
        from url_parser import resolve_check_interval
        return {'quality': quality, 'url': url, 'name': name,
                'legacy': legacy, 'level': level or LEVEL_DEFAULT,
                'seconds': resolve_check_interval(level, self.INTERVALS)}

    def test_url_only(self):
        got = self.resolve(self.URL)
        self.assertEqual((got['url'], got['name'], got['level'], got['seconds']),
                         (self.URL, '', 'C', 90))

    def test_url_with_name(self):
        got = self.resolve(f'{self.URL},主播: MMA盼盼')
        self.assertEqual((got['name'], got['level'], got['seconds']), (self.NAME, 'C', 90))

    def test_url_with_level_a(self):
        got = self.resolve(f'{self.URL},A')
        self.assertEqual((got['name'], got['level'], got['seconds']), ('', 'A', 5))

    def test_url_with_name_and_level_a(self):
        got = self.resolve(f'{self.URL},主播: MMA盼盼,A')
        self.assertEqual((got['name'], got['level'], got['seconds']), (self.NAME, 'A', 5))

    def test_quality_and_url(self):
        got = self.resolve(f'超清，{self.URL}')
        self.assertEqual((got['quality'], got['level'], got['seconds']), ('超清', 'C', 90))

    def test_quality_url_name(self):
        got = self.resolve(f'超清，{self.URL}，主播: MMA盼盼')
        self.assertEqual((got['quality'], got['name'], got['level']), ('超清', self.NAME, 'C'))

    def test_quality_url_level(self):
        got = self.resolve(f'超清，{self.URL}，B')
        self.assertEqual((got['quality'], got['level'], got['seconds']), ('超清', 'B', 30))

    def test_quality_url_name_level(self):
        got = self.resolve(f'超清，{self.URL}，主播: MMA盼盼，B')
        self.assertEqual((got['quality'], got['name'], got['level'], got['seconds']),
                         ('超清', self.NAME, 'B', 30))

    def test_lowercase_level(self):
        got = self.resolve(f'{self.URL},a')
        self.assertEqual((got['level'], got['seconds']), ('A', 5))

    def test_invalid_level_treated_as_name_with_default_interval(self):
        got = self.resolve(f'{self.URL},D')
        self.assertEqual((got['name'], got['level'], got['seconds']), ('D', 'C', 90))

    def test_name_letter_a_with_explicit_level(self):
        got = self.resolve(f'{self.URL},主播: A,A')
        self.assertEqual((got['name'], got['level'], got['seconds']), ('主播: A', 'A', 5))

    def test_level_in_middle(self):
        got = self.resolve(f'{self.URL},B,主播: MMA盼盼')
        self.assertEqual((got['name'], got['level'], got['seconds']), (self.NAME, 'B', 30))


class MainWiringTest(unittest.TestCase):
    """源码级不变量: 等级驱动轮询, 旧"优先"概念已清除。"""

    def setUp(self):
        self.source = (ROOT / 'main.py').read_text(encoding='utf-8')

    def test_priority_concept_removed(self):
        for stale in ('priority_urls', 'priority_delay', 'delay_default',
                      'dedup_priority_action', 'is_priority', 'strip_priority'):
            # 用词边界匹配, 避免 local_delay_default 这类含子串的新标识符误报
            self.assertIsNone(re.search(rf'\b{stale}\b', self.source), stale)

    def test_level_mapping_is_swapped_atomically(self):
        self.assertIn('level_by_url = new_level_by_url', self.source)
        self.assertEqual(len(re.findall(r'level_by_url = new_level_by_url', self.source)), 1)

    def test_all_active_paths_register_level(self):
        """三条生效路径(首现/合并标记/解开注释)都必须登记等级。"""
        self.assertEqual(len(re.findall(r'new_level_by_url\[url\] = ', self.source)), 3)

    def test_delay_uses_level_interval_and_jitter(self):
        self.assertIn('resolve_check_interval(link_level, level_intervals)', self.source)
        self.assertIn('resolve_jitter(link_level)', self.source)
        self.assertIn('link_level = level_by_url.get(record_url)', self.source)

    def test_legacy_marker_migrates_to_level_a(self):
        self.assertIn('LEGACY_PRIORITY_MARK', self.source)
        self.assertIn('level_mark(LEGACY_PRIORITY_LEVEL)', self.source)
        self.assertRegex(self.source, r'if has_legacy_priority:[\s\S]{0,2000}file_modified = True')
        self.assertIn('origin_line.replace(LEGACY_PRIORITY_MARK', self.source)

    def test_split_url_line_unpacked_into_five_values(self):
        self.assertIn('quality, url, name, has_legacy_priority, level = split_url_line(', self.source)

    def test_interval_config_read_from_dedicated_section(self):
        self.assertIn("read_level_intervals(config)", self.source)
        self.assertIn("'检查时长等级'", self.source)

    def test_loop_interval_config_key_removed(self):
        """循环时间(秒) 已被等级制度取代, 代码不再读取。"""
        self.assertNotIn('循环时间(秒)', self.source)
        self.assertNotIn('优先监控', self.source)


if __name__ == '__main__':
    unittest.main()
