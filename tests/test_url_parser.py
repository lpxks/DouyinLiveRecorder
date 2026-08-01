import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from url_parser import (
    dedup_priority_action, find_writeback_index, is_priority, split_url_line, strip_priority,
)


class UrlParserTest(unittest.TestCase):
    def test_line_with_priority_marker(self):
        self.assertTrue(is_priority('https://x.com/1,主播: 张三,优先: 是'))
        self.assertTrue(is_priority('https://x.com/1,优先: 是'))

    def test_plain_line_not_priority(self):
        self.assertFalse(is_priority('https://x.com/1'))
        self.assertFalse(is_priority('https://x.com/1,主播: 张三'))
        self.assertFalse(is_priority('https://x.com/1,优先: 否'))

    def test_marker_position_does_not_matter(self):
        self.assertTrue(is_priority('https://x.com/1,优先: 是,主播: 张三'))


class StripPriorityTest(unittest.TestCase):
    def test_strip_removes_marker_at_end(self):
        self.assertEqual(strip_priority('https://x.com/1,主播: 张三,优先: 是'),
                         'https://x.com/1,主播: 张三')

    def test_strip_removes_marker_in_middle(self):
        self.assertEqual(strip_priority('https://x.com/1,优先: 是,主播: 张三'),
                         'https://x.com/1,主播: 张三')

    def test_strip_noop_without_marker(self):
        self.assertEqual(strip_priority('https://x.com/1,主播: 张三'),
                         'https://x.com/1,主播: 张三')


class SplitUrlLineTest(unittest.TestCase):
    def test_marker_with_name_field_parses_correctly(self):
        quality, url, name, has_priority = split_url_line(
            'https://x.com/1,主播: 张三,优先: 是', '原画')
        self.assertEqual((quality, url, name), ('原画', 'https://x.com/1', '主播: 张三'))
        self.assertTrue(has_priority)

    def test_marker_without_name_field(self):
        quality, url, name, has_priority = split_url_line(
            'https://x.com/1,优先: 是', '原画')
        self.assertEqual((quality, url, name), ('原画', 'https://x.com/1', ''))
        self.assertTrue(has_priority)

    def test_plain_url_with_quality(self):
        quality, url, name, has_priority = split_url_line('超清,https://x.com/1', '原画')
        self.assertEqual((quality, url, name), ('超清', 'https://x.com/1', ''))
        self.assertFalse(has_priority)

    def test_plain_url_only(self):
        quality, url, name, has_priority = split_url_line('https://x.com/1', '原画')
        self.assertEqual((quality, url, name), ('原画', 'https://x.com/1', ''))
        self.assertFalse(has_priority)


class DedupPriorityActionTest(unittest.TestCase):
    def test_merge_marker_into_kept_comment(self):
        # 注释+注释，当前行带标记 → 合并进保留注释行（仅保留标注，不激活）
        self.assertEqual(dedup_priority_action(True, True, True, False), 'merge')

    def test_merge_marker_into_kept_active(self):
        # 生效+生效，当前行带标记 → 合并并激活（去重保护）
        self.assertEqual(dedup_priority_action(True, False, False, False), 'merge')

    def test_keep_comment_annotation(self):
        # 生效行 + 带标记注释行 → 保留注释标注，不合并、不激活
        self.assertEqual(dedup_priority_action(True, True, False, False), 'keep_comment')

    def test_keep_comment_and_active(self):
        # 带标记注释行 + 无标记生效行 → 两者都保留，不激活（消除顺序差异）
        self.assertEqual(dedup_priority_action(False, False, True, True),
                         'keep_comment_and_active')

    def test_no_action_when_kept_already_marked(self):
        self.assertIsNone(dedup_priority_action(True, True, False, True))
        self.assertIsNone(dedup_priority_action(True, False, True, True))

    def test_no_action_without_marker_signal(self):
        self.assertIsNone(dedup_priority_action(False, False, False, False))
        self.assertIsNone(dedup_priority_action(False, True, True, False))
        self.assertIsNone(dedup_priority_action(False, True, True, True))


class FindWritebackIndexTest(unittest.TestCase):
    def test_prefers_active_line_over_annotation_comment(self):
        lines = ['#https://x.com/1,优先: 是', 'https://x.com/1']
        self.assertEqual(find_writeback_index(lines, 'https://x.com/1'), 1)

    def test_active_line_already_first(self):
        lines = ['https://x.com/1', '#https://x.com/1,优先: 是']
        self.assertEqual(find_writeback_index(lines, 'https://x.com/1'), 0)

    def test_falls_back_to_comment_when_only_comment_matches(self):
        lines = ['#https://x.com/1,主播: 张三']
        self.assertEqual(find_writeback_index(lines, 'https://x.com/1'), 0)

    def test_returns_none_when_no_match(self):
        self.assertIsNone(find_writeback_index(['https://other.com/2'], 'https://x.com/1'))


if __name__ == '__main__':
    unittest.main()
