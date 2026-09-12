"""一次性直播链接自动注释辅助函数测试。

find_comment_target_line / should_comment_offline_ephemeral 从 main.py 用 AST
提取真实源码执行(避免导入整个程序): 前者应在 URL 配置文件中找到包含目标 url 的
活跃(未注释)行并跳过已注释行; 后者应只对小红书/淘宝且不在直播时返回真。
"""

import ast
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_LOADED_NAMES = ('find_comment_target_line', 'should_comment_offline_ephemeral',
                 'EPHEMERAL_LIVE_PLATFORMS')


def load_namespace():
    """从 main.py 提取一次性链接相关函数/常量的真实源码执行, 返回命名空间。"""
    main_py = ROOT / 'main.py'
    tree = ast.parse(main_py.read_text(encoding='utf-8'))
    namespace = {'text_encoding': 'utf-8-sig'}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, 'id', None) == 'EPHEMERAL_LIVE_PLATFORMS' for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), namespace)
        elif isinstance(node, ast.FunctionDef) and node.name in _LOADED_NAMES:
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), namespace)
    missing = [n for n in _LOADED_NAMES if n not in namespace]
    if missing:
        raise RuntimeError(f'not found in main.py: {missing}')
    return namespace


NS = load_namespace()
FIND = NS['find_comment_target_line']
SHOULD_COMMENT = NS['should_comment_offline_ephemeral']


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


class ShouldCommentOfflineEphemeralTest(unittest.TestCase):
    """轮询到不在直播时是否应注释链接。"""

    def test_xhs_offline_should_comment(self):
        self.assertTrue(SHOULD_COMMENT('小红书直播', False))

    def test_taobao_offline_should_comment(self):
        self.assertTrue(SHOULD_COMMENT('淘宝直播', False))

    def test_ephemeral_live_should_not_comment(self):
        """在直播中不注释。"""
        self.assertFalse(SHOULD_COMMENT('小红书直播', True))
        self.assertFalse(SHOULD_COMMENT('淘宝直播', True))

    def test_other_platform_offline_should_not_comment(self):
        """非一次性链接平台(如抖音)离线时保持原有行为, 不注释。"""
        self.assertFalse(SHOULD_COMMENT('抖音直播', False))

    def test_unknown_live_state_should_not_comment(self):
        """is_live 非 False(None/缺失)时不注释, 避免状态未知误伤。"""
        self.assertFalse(SHOULD_COMMENT('小红书直播', None))
        self.assertFalse(SHOULD_COMMENT('淘宝直播', ''))


if __name__ == '__main__':
    unittest.main()
