"""url_parser 行解析与检查间隔标记测试。

覆盖 URL_config.ini 的字段拆分(画质/URL/主播名)、间隔标记识别(字母 A-Z 或行内秒数)、
标记的位置无关性与多标记取最后一个、废弃的 `,优先: 是` 迁移信号、去重时的标记动作，
以及标记→间隔/抖动的解析与警告文本。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from url_parser import (  # noqa: E402  (需先补 ROOT 到 sys.path)
    BUILTIN_LEVELS, JITTER_DEFAULT, JITTER_SMALL, LEGACY_PRIORITY_MARK, LEVEL_DEFAULT,
    MAX_INTERVAL_SECONDS, MIN_INTERVAL_SECONDS, dedup_marker_action, extract_interval_spec,
    find_writeback_index, interval_spec_warning, is_interval_spec, is_letter_spec,
    is_number_spec, is_valid_number_spec, normalize_spec, resolve_check_interval,
    resolve_jitter, spec_mark, split_url_line,
)

URL = 'https://live.douyin.com/277869507858'
LINE_WITH_NAME = f'{URL},主播: MMA盼盼'


class IntervalSpecTest(unittest.TestCase):
    """字段是否为间隔标记: 单个字母 或 纯数字秒数。"""

    def test_single_letters_are_specs(self):
        for token in ('A', 'B', 'C', 'D', 'z', ' a ', '\tD\n'):
            self.assertTrue(is_interval_spec(token), token)

    def test_numbers_are_specs(self):
        for token in ('5', '45', '3600', ' 30 ', '007'):
            self.assertTrue(is_interval_spec(token), token)

    def test_non_specs(self):
        for token in ('AB', '', ' ', '主播: A', '45秒', '1.5', '-5', '1e3', None, 45):
            self.assertFalse(is_interval_spec(token), repr(token))

    def test_letter_and_number_predicates(self):
        self.assertTrue(is_letter_spec('a'))
        self.assertTrue(is_number_spec(' 45 '))
        self.assertFalse(is_number_spec('a'))
        self.assertFalse(is_letter_spec('45'))
        self.assertFalse(is_letter_spec(None))

    def test_number_range_validation(self):
        self.assertTrue(is_valid_number_spec(str(MIN_INTERVAL_SECONDS)))
        self.assertTrue(is_valid_number_spec(str(MAX_INTERVAL_SECONDS)))
        self.assertFalse(is_valid_number_spec(str(MIN_INTERVAL_SECONDS - 1)))
        self.assertFalse(is_valid_number_spec(str(MAX_INTERVAL_SECONDS + 1)))
        self.assertFalse(is_valid_number_spec('abc'))


class NormalizeSpecTest(unittest.TestCase):
    def test_letters_are_uppercased(self):
        self.assertEqual(normalize_spec('a'), 'A')
        self.assertEqual(normalize_spec(' D '), 'D')

    def test_numbers_lose_leading_zeros(self):
        self.assertEqual(normalize_spec('007'), '7')
        self.assertEqual(normalize_spec(' 45 '), '45')

    def test_invalid_returns_none(self):
        for bad in (None, '', ' ', 'AB', '45秒', '-5', '1.5', 45):
            self.assertIsNone(normalize_spec(bad), repr(bad))


class ExtractIntervalSpecTest(unittest.TestCase):
    def test_letter_position_does_not_matter(self):
        for fields in ([URL, 'A'], ['A', URL], [URL, '主播: 张三', 'A'],
                       ['A', URL, '主播: 张三']):
            spec, remaining = extract_interval_spec(list(fields))
            self.assertEqual(spec, 'A')
            self.assertIn(URL, remaining)
            self.assertNotIn('A', remaining)

    def test_number_position_does_not_matter(self):
        spec, remaining = extract_interval_spec([URL, '主播: 张三', '45'])
        self.assertEqual(spec, '45')
        self.assertEqual(remaining, [URL, '主播: 张三'])

    def test_last_spec_wins(self):
        """同行多个标记时靠后的决定（用户约定：第二个逗号之后的内容决定）。"""
        spec, remaining = extract_interval_spec([URL, 'B', '主播: 张三', 'A'])
        self.assertEqual(spec, 'A')
        self.assertEqual(remaining, [URL, '主播: 张三'])

    def test_number_and_letter_precedence_by_position(self):
        spec, _ = extract_interval_spec([URL, '30', 'B'])
        self.assertEqual(spec, 'B')
        spec, _ = extract_interval_spec([URL, 'B', '30'])
        self.assertEqual(spec, '30')

    def test_no_spec_returns_none(self):
        spec, remaining = extract_interval_spec([URL, '主播: 张三'])
        self.assertIsNone(spec)
        self.assertEqual(remaining, [URL, '主播: 张三'])


class SplitUrlLineTest(unittest.TestCase):
    """间隔标记在字段切分前被剥离，因此不会打乱画质/URL/主播名对齐。"""

    def test_letter_after_name(self):
        quality, url, name, legacy, spec = split_url_line(f'{LINE_WITH_NAME},A', '原画')
        self.assertEqual((quality, url, name, legacy, spec),
                         ('原画', URL, '主播: MMA盼盼', False, 'A'))

    def test_number_after_name(self):
        quality, url, name, legacy, spec = split_url_line(f'{LINE_WITH_NAME},45', '原画')
        self.assertEqual((quality, url, name, legacy, spec),
                         ('原画', URL, '主播: MMA盼盼', False, '45'))

    def test_spec_without_name(self):
        for token, expected in (('B', 'B'), ('30', '30')):
            quality, url, name, legacy, spec = split_url_line(f'{URL},{token}', '原画')
            self.assertEqual((quality, url, name, legacy, spec),
                             ('原画', URL, '', False, expected))

    def test_spec_with_quality_prefix_and_fullwidth_commas(self):
        quality, url, name, legacy, spec = split_url_line(f'超清，{URL}，主播: 张三，45', '原画')
        self.assertEqual((quality, url, name, legacy, spec),
                         ('超清', URL, '主播: 张三', False, '45'))

    def test_custom_letter_and_lowercase(self):
        _, _, _, _, spec = split_url_line(f'{URL},d', '原画')
        self.assertEqual(spec, 'D')

    def test_number_leading_zeros_normalized(self):
        _, _, _, _, spec = split_url_line(f'{URL},007', '原画')
        self.assertEqual(spec, '7')

    def test_no_spec_returns_none(self):
        quality, url, name, legacy, spec = split_url_line(LINE_WITH_NAME, '原画')
        self.assertEqual((quality, url, name, legacy), ('原画', URL, '主播: MMA盼盼', False))
        self.assertIsNone(spec)

    def test_plain_url_with_quality(self):
        quality, url, name, legacy, spec = split_url_line('超清,https://x.com/1', '原画')
        self.assertEqual((quality, url, name, legacy, spec),
                         ('超清', 'https://x.com/1', '', False, None))

    def test_plain_url_only(self):
        quality, url, name, legacy, spec = split_url_line(URL, '原画')
        self.assertEqual((quality, url, name, legacy, spec), ('原画', URL, '', False, None))

    def test_url_first_with_name(self):
        """没有画质前缀但带主播名(旧实现在这里会把 URL 当画质)。"""
        quality, url, name, legacy, spec = split_url_line(LINE_WITH_NAME, '原画')
        self.assertEqual((quality, url, name), ('原画', URL, '主播: MMA盼盼'))

    def test_name_that_looks_like_spec_keeps_prefix(self):
        """主播名恰好是 A 或纯数字: 必须写 `主播: A` / `主播: 45` 才不会被当成标记。"""
        _, url, name, _, spec = split_url_line(f'{URL},主播: A,A', '原画')
        self.assertEqual((url, name, spec), (URL, '主播: A', 'A'))
        _, url, name, _, spec = split_url_line(f'{URL},主播: 45', '原画')
        self.assertEqual((url, name, spec), (URL, '主播: 45', None))

    def test_two_letter_field_stays_in_name(self):
        """`,AB` 不是合法标记, 保持原有语义当作主播名, 不影响 URL 解析。"""
        quality, url, name, legacy, spec = split_url_line(f'{URL},AB', '原画')
        self.assertEqual((quality, url, name, spec), ('原画', URL, 'AB', None))

    def test_non_integer_number_stays_in_name(self):
        """`,1.5` / `,-5` / `,45秒` 都不识别为标记。"""
        for token in ('1.5', '-5', '45秒'):
            _, url, name, _, spec = split_url_line(f'{URL},{token}', '原画')
            self.assertEqual((url, name, spec), (URL, token, None), token)

    def test_extra_fields_no_longer_raise(self):
        """字段数超出"画质+URL+主播名"时旧实现会抛 ValueError 中断整轮解析。"""
        quality, url, name, legacy, spec = split_url_line(f'{URL},主播: 张,三,A', '原画')
        self.assertEqual(url, URL)
        self.assertEqual(spec, 'A')
        self.assertEqual(name, '主播: 张,三')

    def test_invalid_quality_falls_back_to_default(self):
        quality, url, _, _, _ = split_url_line(f'杜比，{URL}', '高清')
        self.assertEqual(quality, '高清')
        self.assertEqual(url, URL)

    def test_number_and_legacy_marker_together(self):
        """旧标记与显式秒数同现: 秒数生效, 旧标记只需被丢弃。"""
        _, url, name, legacy, spec = split_url_line(f'{URL},主播: 张三,优先: 是,45', '原画')
        self.assertEqual((url, name, spec), (URL, '主播: 张三', '45'))
        self.assertTrue(legacy)

    def test_legacy_priority_marker_is_flagged(self):
        quality, url, name, legacy, spec = split_url_line(f'{URL},主播: 张三,优先: 是', '原画')
        self.assertEqual((quality, url, name, spec), ('原画', URL, '主播: 张三', None))
        self.assertTrue(legacy)

    def test_no_legacy_marker(self):
        self.assertFalse(split_url_line(LINE_WITH_NAME, '原画')[3])

    def test_legacy_marker_constant(self):
        self.assertEqual(LEGACY_PRIORITY_MARK, ',优先: 是')


class DedupMarkerActionTest(unittest.TestCase):
    """间隔标记在 URL 去重时的动作(语义与旧优先标记一致)。"""

    def test_merge_marker_into_kept_comment(self):
        self.assertEqual(dedup_marker_action(True, False, True, False), 'merge')

    def test_merge_marker_into_kept_active(self):
        self.assertEqual(dedup_marker_action(True, False, False, False), 'merge')

    def test_keep_comment_annotation(self):
        self.assertEqual(dedup_marker_action(True, True, False, False), 'keep_comment')

    def test_keep_comment_and_active(self):
        self.assertEqual(dedup_marker_action(False, False, True, True), 'keep_comment_and_active')

    def test_no_action_when_kept_already_marked(self):
        self.assertIsNone(dedup_marker_action(True, True, True, True))

    def test_no_action_without_marker_signal(self):
        self.assertIsNone(dedup_marker_action(False, False, False, False))
        self.assertIsNone(dedup_marker_action(False, True, False, False))

    def test_spec_mark_text(self):
        self.assertEqual(spec_mark('a'), ',A')
        self.assertEqual(spec_mark('45'), ',45')
        self.assertEqual(spec_mark(None), f',{LEVEL_DEFAULT}')


class FindWritebackIndexTest(unittest.TestCase):
    def test_prefers_active_line_over_annotation_comment(self):
        lines = [f'#原画，{URL},主播: 张三\n', f'原画，{URL},45\n']
        self.assertEqual(find_writeback_index(lines, URL), 1)

    def test_active_line_already_first(self):
        lines = [f'原画，{URL},A\n', f'#原画，{URL}\n']
        self.assertEqual(find_writeback_index(lines, URL), 0)

    def test_falls_back_to_comment_when_only_comment_matches(self):
        lines = ['原画，https://other.com/x\n', f'#原画，{URL},A\n']
        self.assertEqual(find_writeback_index(lines, URL), 1)

    def test_returns_none_when_no_match(self):
        self.assertIsNone(find_writeback_index(['原画，https://other.com/x\n'], URL))


class ResolveCheckIntervalTest(unittest.TestCase):
    """标记 → 检查间隔(秒): 字母查配置, 行内秒数直接用。"""

    INTERVALS = {'A': 5, 'B': 30, 'C': 90, 'D': 15}

    def test_levels_map_to_configured_seconds(self):
        self.assertEqual(resolve_check_interval('A', self.INTERVALS), 5)
        self.assertEqual(resolve_check_interval('B', self.INTERVALS), 30)
        self.assertEqual(resolve_check_interval('C', self.INTERVALS), 90)
        self.assertEqual(resolve_check_interval('D', self.INTERVALS), 15)

    def test_inline_seconds_win(self):
        self.assertEqual(resolve_check_interval('45', self.INTERVALS), 45)
        self.assertEqual(resolve_check_interval('1', self.INTERVALS), 1)
        self.assertEqual(resolve_check_interval(str(MAX_INTERVAL_SECONDS), self.INTERVALS),
                         MAX_INTERVAL_SECONDS)
        self.assertEqual(resolve_check_interval('007', self.INTERVALS), 7)

    def test_inline_seconds_out_of_range_fall_back(self):
        self.assertEqual(resolve_check_interval('0', self.INTERVALS), 90)
        self.assertEqual(resolve_check_interval(str(MAX_INTERVAL_SECONDS + 1), self.INTERVALS), 90)

    def test_missing_or_invalid_spec_falls_back_to_default(self):
        self.assertEqual(resolve_check_interval(None, self.INTERVALS), 90)
        self.assertEqual(resolve_check_interval('', self.INTERVALS), 90)
        self.assertEqual(resolve_check_interval('1.5', self.INTERVALS), 90)
        self.assertEqual(resolve_check_interval('AB', self.INTERVALS), 90)

    def test_lowercase_level_accepted(self):
        self.assertEqual(resolve_check_interval('a', self.INTERVALS), 5)

    def test_unconfigured_letter_falls_back_to_default(self):
        self.assertEqual(resolve_check_interval('E', self.INTERVALS), 90)

    def test_custom_default_level(self):
        self.assertEqual(resolve_check_interval(None, self.INTERVALS, 'B'), 30)
        self.assertEqual(resolve_check_interval('E', self.INTERVALS, 'B'), 30)

    def test_broken_values_fall_back_to_one_second(self):
        """配置被写坏(0/负数/非数字/非字符串)时不会 0 秒空转。"""
        for bad in (0, -5, 'abc', None, ''):
            intervals = {'A': bad, 'B': bad, 'C': bad}
            self.assertGreaterEqual(resolve_check_interval('A', intervals), MIN_INTERVAL_SECONDS)

    def test_missing_level_key_falls_back_to_default_level_value(self):
        self.assertEqual(resolve_check_interval('A', {'C': 42}), 42)

    def test_builtin_levels_are_covered_by_intervals(self):
        self.assertTrue(set(BUILTIN_LEVELS) <= set(self.INTERVALS))


class ResolveJitterTest(unittest.TestCase):
    """抖动: 字母按表(未配置按默认级), 行内秒数按大小分档。"""

    def test_jitter_by_level(self):
        self.assertEqual(resolve_jitter('A'), JITTER_SMALL)
        self.assertEqual(resolve_jitter('B'), JITTER_DEFAULT)
        self.assertEqual(resolve_jitter('C'), JITTER_DEFAULT)

    def test_custom_configured_letter_uses_default_jitter(self):
        self.assertEqual(resolve_jitter('D'), JITTER_DEFAULT)

    def test_inline_seconds_use_tiered_jitter(self):
        self.assertEqual(resolve_jitter('5'), JITTER_SMALL)
        self.assertEqual(resolve_jitter('3'), JITTER_SMALL)
        self.assertEqual(resolve_jitter('30'), JITTER_DEFAULT)
        self.assertEqual(resolve_jitter('3600'), JITTER_DEFAULT)

    def test_missing_or_invalid_spec_uses_default_level_jitter(self):
        self.assertEqual(resolve_jitter(None), JITTER_DEFAULT)
        self.assertEqual(resolve_jitter(''), JITTER_DEFAULT)
        self.assertEqual(resolve_jitter('AB'), JITTER_DEFAULT)

    def test_out_of_range_seconds_use_default_level_jitter(self):
        self.assertEqual(resolve_jitter('0'), JITTER_DEFAULT)
        self.assertEqual(resolve_jitter(str(MAX_INTERVAL_SECONDS + 1)), JITTER_DEFAULT)


class IntervalSpecWarningTest(unittest.TestCase):
    """回退到默认级时给出的提示文本(调用方负责打印一次)。"""

    INTERVALS = {'A': 5, 'B': 30, 'C': 90}

    def test_configured_letter_has_no_warning(self):
        self.assertIsNone(interval_spec_warning('A', self.INTERVALS))

    def test_unconfigured_letter_warns(self):
        warning = interval_spec_warning('E', self.INTERVALS)
        self.assertIn('E', warning)
        self.assertIn(LEVEL_DEFAULT, warning)

    def test_valid_inline_seconds_have_no_warning(self):
        self.assertIsNone(interval_spec_warning('45', self.INTERVALS))

    def test_out_of_range_seconds_warn(self):
        self.assertIn('超出', interval_spec_warning('0', self.INTERVALS))
        self.assertIn('超出', interval_spec_warning(str(MAX_INTERVAL_SECONDS + 1), self.INTERVALS))

    def test_invalid_spec_has_no_warning(self):
        self.assertIsNone(interval_spec_warning('1.5', self.INTERVALS))
        self.assertIsNone(interval_spec_warning(None, self.INTERVALS))


if __name__ == '__main__':
    unittest.main()
