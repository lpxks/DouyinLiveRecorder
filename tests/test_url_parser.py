import unittest

from url_parser import is_priority, split_url_line, strip_priority


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


if __name__ == '__main__':
    unittest.main()
