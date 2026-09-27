"""检查间隔标记配置读取与 main.py 接线断言。

`read_level_intervals` / `_parse_interval_seconds` 从 main.py 用 AST 提取真实源码执行
(注入假的 read_config_value 与配置对象, 避免读写真配置); 接线断言用源码文本检查关键
不变量: 标记映射的原子替换、三条生效路径都登记标记、延时计算改用标记解析、旧"优先"
概念已彻底移除。
"""

import ast
import configparser
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from url_parser import (  # noqa: E402  (需先补 ROOT 到 sys.path)
    BUILTIN_LEVELS, LEVEL_DEFAULT, MIN_INTERVAL_SECONDS, resolve_check_interval,
    split_url_line,
)

_LOADED_NAMES = ('read_level_intervals', '_parse_interval_seconds')
_LOADED_CONSTANTS = ('LEVEL_INTERVAL_DEFAULTS', 'LEVEL_INTERVAL_OPTION_RE')

DEFAULTS = {'A': 5, 'B': 30, 'C': 90}


class _FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args, **kwargs):
        self.warnings.append(str(message))

    def error(self, *args, **kwargs):
        pass


class _FakeConfigParser:
    """只实现 read_level_intervals 用到的 has_section/items(configparser 会把键名小写)。"""

    def __init__(self, section_items=None, has_section=True):
        self.section_items = dict(section_items or {})
        self._has_section = has_section

    def has_section(self, section):
        return self._has_section

    def items(self, section):
        return list(self.section_items.items())


def load_namespace():
    tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
    namespace = {'logger': _FakeLogger(), 'configparser': configparser, 're': re,
                 'MIN_INTERVAL_SECONDS': MIN_INTERVAL_SECONDS, 'LEVEL_DEFAULT': LEVEL_DEFAULT}
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
    """[检查时长等级] 段的读取: 内置字母默认值、任意字母动态发现、非法值回退。"""

    def setUp(self):
        self.calls = []
        self.logger = _FakeLogger()
        NS['logger'] = self.logger

    def reader(self, values):
        def fake_read_config_value(config_parser, section, option, default_value):
            self.calls.append((section, option, default_value))
            return values.get(option, default_value)
        return fake_read_config_value

    def read(self, values, section_items=None, has_section=False):
        NS['read_config_value'] = self.reader(values)
        parser = _FakeConfigParser(section_items=section_items, has_section=has_section)
        try:
            return READ_LEVEL_INTERVALS(parser)
        finally:
            NS.pop('read_config_value', None)

    def test_defaults_when_section_is_fresh(self):
        self.assertEqual(self.read({}), DEFAULTS)

    def test_section_and_option_names(self):
        self.read({})
        self.assertEqual([section for section, _, _ in self.calls],
                         ['检查时长等级'] * len(BUILTIN_LEVELS))
        self.assertEqual([option for _, option, _ in self.calls],
                         ['A等级检查时长(秒)', 'B等级检查时长(秒)', 'C等级检查时长(秒)'])
        self.assertEqual([default for _, _, default in self.calls],
                         [DEFAULTS['A'], DEFAULTS['B'], DEFAULTS['C']])

    def test_configured_values_win(self):
        values = {'A等级检查时长(秒)': '1', 'B等级检查时长(秒)': '45', 'C等级检查时长(秒)': '120'}
        self.assertEqual(self.read(values), {'A': 1, 'B': 45, 'C': 120})

    def test_whitespace_tolerated(self):
        values = {'A等级检查时长(秒)': ' 7 ', 'B等级检查时长(秒)': '30', 'C等级检查时长(秒)': '90'}
        self.assertEqual(self.read(values)['A'], 7)

    def test_invalid_values_fall_back_to_that_level_default(self):
        values = {'A等级检查时长(秒)': 'abc', 'B等级检查时长(秒)': '0', 'C等级检查时长(秒)': '-30'}
        self.assertEqual(self.read(values), DEFAULTS)

    def test_non_string_values_are_tolerated(self):
        values = {'A等级检查时长(秒)': None, 'B等级检查时长(秒)': 12, 'C等级检查时长(秒)': 90}
        self.assertEqual(self.read(values), {'A': 5, 'B': 12, 'C': 90})

    def test_custom_letters_are_discovered(self):
        """段里自行增加的字母键(如 D/E)会成为可用等级。"""
        got = self.read({}, section_items={'d等级检查时长(秒)': '15', 'e等级检查时长(秒)': '600'},
                        has_section=True)
        self.assertEqual(got, {'A': 5, 'B': 30, 'C': 90, 'D': 15, 'E': 600})

    def test_custom_letter_invalid_value_falls_back_to_default_level(self):
        got = self.read({}, section_items={'d等级检查时长(秒)': 'abc'}, has_section=True)
        self.assertEqual(got['D'], DEFAULTS[LEVEL_DEFAULT])
        self.assertTrue(any('D' in warning for warning in self.logger.warnings))

    def test_custom_letter_zero_falls_back_to_default_level(self):
        got = self.read({}, section_items={'d等级检查时长(秒)': '0'}, has_section=True)
        self.assertEqual(got['D'], DEFAULTS[LEVEL_DEFAULT])

    def test_unrelated_options_are_ignored(self):
        got = self.read({}, section_items={'抖动(秒)': '3', '说明': 'abc'}, has_section=True)
        self.assertEqual(got, DEFAULTS)

    def test_multi_letter_option_is_ignored(self):
        got = self.read({}, section_items={'ab等级检查时长(秒)': '9'}, has_section=True)
        self.assertEqual(got, DEFAULTS)

    def test_builtin_letters_not_overridden_by_custom_scan(self):
        got = self.read({'A等级检查时长(秒)': '3'}, section_items={'a等级检查时长(秒)': '99'},
                        has_section=True)
        self.assertEqual(got['A'], 3)


class IntervalResolutionTableTest(unittest.TestCase):
    """行形态 → 标记 → 检查间隔(秒), 复刻解析循环的取值链路。"""

    URL = 'https://live.douyin.com/277869507858'
    NAME = '主播: MMA盼盼'
    INTERVALS = {'A': 5, 'B': 30, 'C': 90, 'D': 15}

    def resolve(self, line):
        quality, url, name, legacy, spec = split_url_line(line, '原画')
        return {'quality': quality, 'url': url, 'name': name, 'legacy': legacy,
                'spec': spec or LEVEL_DEFAULT,
                'seconds': resolve_check_interval(spec, self.INTERVALS)}

    def test_url_only(self):
        got = self.resolve(self.URL)
        self.assertEqual((got['url'], got['name'], got['spec'], got['seconds']),
                         (self.URL, '', 'C', 90))

    def test_url_with_name(self):
        got = self.resolve(f'{self.URL},主播: MMA盼盼')
        self.assertEqual((got['name'], got['spec'], got['seconds']), (self.NAME, 'C', 90))

    def test_letter_levels(self):
        for letter, expected in (('A', 5), ('B', 30), ('C', 90), ('D', 15)):
            got = self.resolve(f'{self.URL},主播: MMA盼盼,{letter}')
            self.assertEqual((got['name'], got['spec'], got['seconds']),
                             (self.NAME, letter, expected), letter)

    def test_inline_seconds(self):
        for seconds in ('45', '1', '3600'):
            got = self.resolve(f'{self.URL},主播: MMA盼盼,{seconds}')
            self.assertEqual((got['name'], got['spec'], got['seconds']),
                             (self.NAME, seconds, int(seconds)), seconds)

    def test_quality_prefix_forms(self):
        got = self.resolve(f'超清，{self.URL}，45')
        self.assertEqual((got['quality'], got['spec'], got['seconds']), ('超清', '45', 45))
        got = self.resolve(f'超清，{self.URL}，主播: MMA盼盼，D')
        self.assertEqual((got['quality'], got['name'], got['spec'], got['seconds']),
                         ('超清', self.NAME, 'D', 15))

    def test_lowercase_letter(self):
        got = self.resolve(f'{self.URL},a')
        self.assertEqual((got['spec'], got['seconds']), ('A', 5))

    def test_unconfigured_letter_falls_back_to_default_interval(self):
        got = self.resolve(f'{self.URL},Z')
        self.assertEqual((got['spec'], got['seconds']), ('Z', 90))

    def test_inline_seconds_out_of_range_falls_back(self):
        for token in ('0', '100000'):
            got = self.resolve(f'{self.URL},{token}')
            self.assertEqual((got['spec'], got['seconds']), (token, 90), token)

    def test_non_spec_tokens_stay_in_name(self):
        for token in ('AB', '1.5', '45秒'):
            got = self.resolve(f'{self.URL},{token}')
            self.assertEqual((got['name'], got['seconds']), (token, 90), token)

    def test_name_looking_like_spec_with_prefix(self):
        got = self.resolve(f'{self.URL},主播: A,A')
        self.assertEqual((got['name'], got['spec'], got['seconds']), ('主播: A', 'A', 5))
        got = self.resolve(f'{self.URL},主播: 45')
        self.assertEqual((got['name'], got['spec'], got['seconds']), ('主播: 45', 'C', 90))

    def test_spec_in_middle(self):
        got = self.resolve(f'{self.URL},B,主播: MMA盼盼')
        self.assertEqual((got['name'], got['spec'], got['seconds']), (self.NAME, 'B', 30))


class MainWiringTest(unittest.TestCase):
    """源码级不变量: 间隔标记驱动轮询, 旧"优先"概念已清除。"""

    def setUp(self):
        self.source = (ROOT / 'main.py').read_text(encoding='utf-8')

    def test_priority_concept_removed(self):
        for stale in ('priority_urls', 'priority_delay', 'delay_default',
                      'dedup_priority_action', 'is_priority', 'strip_priority'):
            # 用词边界匹配, 避免 local_delay_default 这类含子串的新标识符误报
            self.assertIsNone(re.search(rf'\b{stale}\b', self.source), stale)

    def test_spec_mapping_is_swapped_atomically(self):
        self.assertEqual(
            len(re.findall(r'interval_spec_by_url = new_interval_spec_by_url', self.source)), 1)
        self.assertIsNone(re.search(r'\blevel_by_url\b', self.source))

    def test_all_active_paths_register_spec(self):
        """三条生效路径(首现/合并标记/解开注释)都必须登记标记。"""
        self.assertEqual(len(re.findall(r'new_interval_spec_by_url\[url\] = ', self.source)), 3)

    def test_delay_uses_spec_interval_and_jitter(self):
        self.assertIn('resolve_check_interval(link_spec, level_intervals)', self.source)
        self.assertIn('resolve_jitter(link_spec)', self.source)
        self.assertIn('link_spec = interval_spec_by_url.get(record_url)', self.source)

    def test_legacy_marker_migrates_to_level_a(self):
        self.assertIn('LEGACY_PRIORITY_MARK', self.source)
        self.assertIn('spec_mark(LEGACY_PRIORITY_LEVEL)', self.source)
        self.assertRegex(self.source, r'if has_legacy_priority:[\s\S]{0,2000}file_modified = True')
        self.assertIn('origin_line.replace(LEGACY_PRIORITY_MARK', self.source)

    def test_split_url_line_unpacked_into_five_values(self):
        self.assertIn('quality, url, name, has_legacy_priority, spec = split_url_line(', self.source)

    def test_interval_config_read_from_dedicated_section(self):
        self.assertIn('read_level_intervals(config)', self.source)
        self.assertIn("'检查时长等级'", self.source)

    def test_startup_print_marks_fallback_specs(self):
        """启动打印要能区分"行内秒数/等级"以及"回退到默认级"的情况。"""
        self.assertIn("按默认级 检查间隔", self.source)
        self.assertIn("行内间隔{link_seconds}秒", self.source)

    def test_invalid_spec_is_warned_once(self):
        """未配置字母/超范围秒数的提示只打印一次, 避免每轮解析刷屏。"""
        self.assertIn('warned_interval_specs.add(spec)', self.source)
        self.assertIn('interval_spec_warning(spec, level_intervals)', self.source)
        self.assertRegex(self.source,
                         r'if spec_warning and spec not in warned_interval_specs:[\s\S]{0,400}logger\.warning')

    def test_loop_interval_config_key_removed(self):
        """循环时间(秒) 已被标记制度取代, 代码不再读取。"""
        self.assertNotIn('循环时间(秒)', self.source)
        self.assertNotIn('优先监控', self.source)


if __name__ == '__main__':
    unittest.main()
