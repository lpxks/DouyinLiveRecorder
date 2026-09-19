"""url_parser 行解析与检查时长等级测试。

覆盖 URL_config.ini 的字段拆分(画质/URL/主播名)、等级标记(A/B/C)识别与位置无关性、
废弃的 `,优先: 是` 迁移信号、去重时的标记动作，以及等级→间隔/抖动的解析。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from url_parser import (  # noqa: E402  (需先补 ROOT 到 sys.path)
    LEGACY_PRIORITY_MARK, LEVEL_DEFAULT, LEVELS, dedup_marker_action, extract_level,
    find_writeback_index, is_level_token, level_mark, normalize_level,
    resolve_check_interval, resolve_jitter, split_url_line,
)

URL = 'https://live.douyin.com/277869507858'
LINE_WITH_NAME = f'{URL},主播: MMA盼盼'


class LevelTokenTest(unittest.TestCase):
    def test_valid_tokens_are_case_insensitive(self):
        for token in ('A', 'B', 'C', 'a', 'b', 'c', ' A ', '\tc\n'):
            self.assertTrue(is_level_token(token), token)

    def test_invalid_tokens_are_not_levels(self):
        for token in ('D', 'AB', '', ' ', '优先: 是', '主播: A', '1'):
            self.assertFalse(is_level_token(token), token)


class ExtractLevelTest(unittest.TestCase):
    def test_position_does_not_matter(self):
        for fields in ([URL, 'A'], ['A', URL], [URL, '主播: 张三', 'A'],
                       ['A', URL, '主播: 张三']):
            level, remaining = extract_level(list(fields))
            self.assertEqual(level, 'A')
            self.assertIn(URL, remaining)
            self.assertNotIn('A', remaining)

    def test_last_level_wins(self):
        """同行多个等级字段时靠后的决定（用户约定：第二个逗号之后的内容决定）。"""
        level, remaining = extract_level([URL, 'B', '主播: 张三', 'A'])
        self.assertEqual(level, 'A')
        self.assertEqual(remaining, [URL, '主播: 张三'])

    def test_no_level_returns_none(self):
        level, remaining = extract_level([URL, '主播: 张三'])
        self.assertIsNone(level)
        self.assertEqual(remaining, [URL, '主播: 张三'])


class NormalizeLevelTest(unittest.TestCase):
    def test_valid_and_invalid(self):
        self.assertEqual(normalize_level('a'), 'A')
        self.assertEqual(normalize_level(' C '), 'C')
        self.assertEqual(normalize_level(None), LEVEL_DEFAULT)
        self.assertEqual(normalize_level(''), LEVEL_DEFAULT)
        self.assertEqual(normalize_level('D'), LEVEL_DEFAULT)
        self.assertEqual(normalize_level('D', 'B'), 'B')


class SplitUrlLineTest(unittest.TestCase):
    """等级标记在字段切分前被剥离，因此不会打乱画质/URL/主播名对齐。"""

    def test_level_after_name(self):
        quality, url, name, legacy, level = split_url_line(f'{LINE_WITH_NAME},A', '原画')
        self.assertEqual((quality, url, name, legacy, level),
                         ('原画', URL, '主播: MMA盼盼', False, 'A'))

    def test_level_without_name(self):
        quality, url, name, legacy, level = split_url_line(f'{URL},B', '原画')
        self.assertEqual((quality, url, name, legacy, level),
                         ('原画', URL, '', False, 'B'))

    def test_level_with_quality_prefix_and_fullwidth_commas(self):
        quality, url, name, legacy, level = split_url_line(f'超清，{URL}，主播: 张三，A', '原画')
        self.assertEqual((quality, url, name, legacy, level),
                         ('超清', URL, '主播: 张三', False, 'A'))

    def test_lowercase_level(self):
        _, _, _, _, level = split_url_line(f'{URL},c', '原画')
        self.assertEqual(level, 'C')

    def test_no_level_returns_none(self):
        quality, url, name, legacy, level = split_url_line(LINE_WITH_NAME, '原画')
        self.assertEqual((quality, url, name, legacy), ('原画', URL, '主播: MMA盼盼', False))
        self.assertIsNone(level)

    def test_plain_url_with_quality(self):
        quality, url, name, legacy, level = split_url_line('超清,https://x.com/1', '原画')
        self.assertEqual((quality, url, name, legacy, level),
                         ('超清', 'https://x.com/1', '', False, None))

    def test_plain_url_only(self):
        quality, url, name, legacy, level = split_url_line(URL, '原画')
        self.assertEqual((quality, url, name, legacy, level), ('原画', URL, '', False, None))

    def test_url_first_with_name(self):
        """没有画质前缀但带主播名(旧实现在这里会把 URL 当画质)。"""
        quality, url, name, legacy, level = split_url_line(LINE_WITH_NAME, '原画')
        self.assertEqual(quality, '原画')
        self.assertEqual(url, URL)
        self.assertEqual(name, '主播: MMA盼盼')

    def test_name_that_looks_like_level_keeps_prefix(self):
        """主播名恰好是 A: 必须写 `主播: A` 才不会被当成等级。"""
        quality, url, name, legacy, level = split_url_line(f'{URL},主播: A,A', '原画')
        self.assertEqual((url, name, level), (URL, '主播: A', 'A'))

    def test_invalid_level_letter_stays_in_name(self):
        """`,D` 不是合法等级, 按原有语义当作主播名, 不影响 URL 解析。"""
        quality, url, name, legacy, level = split_url_line(f'{URL},D', '原画')
        self.assertEqual((quality, url, name, level), ('原画', URL, 'D', None))

    def test_extra_fields_no_longer_raise(self):
        """字段数超出"画质+URL+主播名"时旧实现会抛 ValueError 中断整轮解析。"""
        quality, url, name, legacy, level = split_url_line(f'{URL},主播: 张,三,A', '原画')
        self.assertEqual(url, URL)
        self.assertEqual(level, 'A')
        self.assertEqual(name, '主播: 张,三')

    def test_invalid_quality_falls_back_to_default(self):
        quality, url, _, _, _ = split_url_line(f'杜比，{URL}', '高清')
        self.assertEqual(quality, '高清')
        self.assertEqual(url, URL)

    def test_legacy_priority_marker_is_flagged(self):
        quality, url, name, legacy, level = split_url_line(f'{URL},主播: 张三,优先: 是', '原画')
        self.assertEqual((quality, url, name, level), ('原画', URL, '主播: 张三', None))
        self.assertTrue(legacy)

    def test_legacy_priority_with_explicit_level(self):
        """同时存在旧标记与显式等级: 显式等级生效, 旧标记只需被丢弃。"""
        _, url, name, legacy, level = split_url_line(f'{URL},主播: 张三,优先: 是,B', '原画')
        self.assertEqual((url, name, level), (URL, '主播: 张三', 'B'))
        self.assertTrue(legacy)

    def test_no_legacy_marker(self):
        self.assertFalse(split_url_line(LINE_WITH_NAME, '原画')[3])

    def test_legacy_marker_constant(self):
        self.assertEqual(LEGACY_PRIORITY_MARK, ',优先: 是')


class DedupMarkerActionTest(unittest.TestCase):
    """等级标记在 URL 去重时的动作(语义与旧优先标记一致)。"""

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

    def test_level_mark_text(self):
        self.assertEqual(level_mark('a'), ',A')
        self.assertEqual(level_mark(None), f',{LEVEL_DEFAULT}')


class FindWritebackIndexTest(unittest.TestCase):
    def test_prefers_active_line_over_annotation_comment(self):
        lines = [f'#原画，{URL},主播: 张三\n', f'原画，{URL},A\n']
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
    """等级 → 检查间隔(秒): 默认 A=5/B=30/C=90。"""

    INTERVALS = {'A': 5, 'B': 30, 'C': 90}

    def test_levels_map_to_configured_seconds(self):
        self.assertEqual(resolve_check_interval('A', self.INTERVALS), 5)
        self.assertEqual(resolve_check_interval('B', self.INTERVALS), 30)
        self.assertEqual(resolve_check_interval('C', self.INTERVALS), 90)

    def test_missing_or_invalid_level_falls_back_to_default(self):
        self.assertEqual(resolve_check_interval(None, self.INTERVALS), 90)
        self.assertEqual(resolve_check_interval('', self.INTERVALS), 90)
        self.assertEqual(resolve_check_interval('D', self.INTERVALS), 90)
        self.assertEqual(resolve_check_interval('d', self.INTERVALS), 90)

    def test_lowercase_level_accepted(self):
        self.assertEqual(resolve_check_interval('a', self.INTERVALS), 5)

    def test_custom_default_level(self):
        self.assertEqual(resolve_check_interval(None, self.INTERVALS, 'B'), 30)

    def test_broken_values_fall_back_to_one_second(self):
        """配置被写坏(0/负数/非数字/非字符串)时不会 0 秒空转。"""
        for bad in (0, -5, 'abc', None, ''):
            intervals = {'A': bad, 'B': bad, 'C': bad}
            self.assertGreaterEqual(resolve_check_interval('A', intervals), 1)

    def test_missing_level_key_falls_back_to_default_level_value(self):
        self.assertEqual(resolve_check_interval('A', {'C': 42}), 42)

    def test_all_levels_covered(self):
        self.assertEqual(set(self.INTERVALS), set(LEVELS))


class ResolveJitterTest(unittest.TestCase):
    """抖动沿用原行为: A 等级 ±1 秒, B/C ±5 秒, 未标注按默认级 C 处理。"""

    def test_jitter_by_level(self):
        self.assertEqual(resolve_jitter('A'), 1)
        self.assertEqual(resolve_jitter('B'), 5)
        self.assertEqual(resolve_jitter('C'), 5)

    def test_missing_or_invalid_level_uses_default_level_jitter(self):
        self.assertEqual(resolve_jitter(None), 5)
        self.assertEqual(resolve_jitter('D'), 5)


if __name__ == '__main__':
    unittest.main()
