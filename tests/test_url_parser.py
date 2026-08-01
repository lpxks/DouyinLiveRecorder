import unittest

from url_parser import is_priority


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


if __name__ == '__main__':
    unittest.main()
