"""一次性直播链接自动注释辅助函数测试。

find_comment_target_line 从 main.py 用 AST 提取真实源码执行(避免导入整个程序):
应在 URL 配置文件中找到包含目标 url 的活跃(未注释)行, 跳过已注释行。
"""

import ast
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_find_comment_target_line():
    """从 main.py 提取 find_comment_target_line 的真实源码执行。"""
    main_py = ROOT / 'main.py'
    tree = ast.parse(main_py.read_text(encoding='utf-8'))
    namespace = {'text_encoding': 'utf-8-sig'}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'find_comment_target_line':
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), namespace)
    if 'find_comment_target_line' not in namespace:
        raise RuntimeError('find_comment_target_line not found in main.py')
    return namespace['find_comment_target_line']


FIND = load_find_comment_target_line()


class FindCommentTargetLineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def write_config(self, lines):
        path = Path(self.tmp) / 'URL_config.ini'
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return str(path)

    def test_finds_active_line_with_quality_and_name(self):
        """活跃行(带画质前缀/主播名)应被找到并原样返回。"""
        url = 'https://www.xiaohongshu.com/user/profile/abc123'
        path = self.write_config([
            f'原画，{url}，主播A',
            '# 原画，https://other.com/x',
        ])
        self.assertEqual(FIND(path, url), f'原画，{url}，主播A')

    def test_skips_commented_line(self):
        """目标 url 只存在于已注释行时返回 None。"""
        url = 'https://huodong.m.taobao.com/abc'
        path = self.write_config([f'# 原画，{url}'])
        self.assertIsNone(FIND(path, url))

    def test_missing_url_returns_none(self):
        """url 不在文件中返回 None。"""
        path = self.write_config(['原画，https://other.com/x'])
        self.assertIsNone(FIND(path, 'https://notexist.com/y'))

    def test_missing_file_returns_none(self):
        """文件不存在(读取失败)返回 None。"""
        self.assertIsNone(FIND(str(Path(self.tmp) / 'nope.ini'), 'https://x.com/y'))


if __name__ == '__main__':
    unittest.main()
