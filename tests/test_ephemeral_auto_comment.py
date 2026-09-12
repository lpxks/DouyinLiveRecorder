"""一次性直播链接自动注释辅助函数测试。

find_comment_target_line / in_interrupted_retry_window /
should_comment_offline_ephemeral / _comment_ephemeral_link /
comment_offline_ephemeral_and_stop 从 main.py 用 AST 提取真实源码执行(避免导入
整个程序): 定位活跃行、判断"快速重试窗口之外轮询到不在直播"是否应注释、给配置行
加 '#' 的实际写入, 以及注释后 running_list 的清理(决定链接被重新打开后能否重新
拉起线程)都在这里验证。
"""

import ast
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from url_parser import split_url_line  # noqa: E402  (需先补 ROOT 到 sys.path)

_LOADED_NAMES = ('find_comment_target_line', 'in_interrupted_retry_window',
                 'should_comment_offline_ephemeral', '_comment_ephemeral_link',
                 'comment_offline_ephemeral_and_stop', 'clear_record_info',
                 'update_file', 'EPHEMERAL_LIVE_PLATFORMS')

MAX_RETRY = 10


class _FakeLogger:
    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _FakeColor:
    YELLOW = 'yellow'

    def print_colored(self, *args, **kwargs):
        pass


def load_namespace():
    """从 main.py 提取一次性链接相关函数/常量的真实源码执行, 返回命名空间。"""
    main_py = ROOT / 'main.py'
    tree = ast.parse(main_py.read_text(encoding='utf-8'))
    namespace = {'text_encoding': 'utf-8-sig', 'logger': _FakeLogger(),
                 'color_obj': _FakeColor(), 'file_update_lock': threading.Lock(),
                 'ini_URL_content': '', 'monitoring': 1, 'recording': set(),
                 'running_list': [], 'url_comments': [],
                 'max_retry_interrupted': MAX_RETRY,
                 'split_url_line': split_url_line}
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
IN_RETRY = NS['in_interrupted_retry_window']
SHOULD_COMMENT = NS['should_comment_offline_ephemeral']
COMMENT_LINK = NS['_comment_ephemeral_link']
COMMENT_AND_STOP = NS['comment_offline_ephemeral_and_stop']


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

    def test_prefix_collision_prefers_exact_match(self):
        """一个链接是另一个链接前缀时, 必须命中原链接的精确行(不能注释错行)。"""
        short = 'https://www.xiaohongshu.com/user/profile/abc'
        long = 'https://www.xiaohongshu.com/user/profile/abcdef'
        path = self.write_config([f'原画，{short}', f'高清，{long}'])
        # 目标为较长的链接: 子串匹配会错命中第一行, 精确匹配应命中第二行
        self.assertEqual(FIND(path, long), f'高清，{long}')
        self.assertEqual(FIND(path, short), f'原画，{short}')

    def test_line_with_query_params_falls_back_to_substring(self):
        """行内 URL 带额外查询参数(解析后不相等)时回退子串匹配, 仍能定位。"""
        url = 'https://xhslink.com/abc'
        path = self.write_config([f'原画，{url}?uid=888，主播A'])
        self.assertEqual(FIND(path, url), f'原画，{url}?uid=888，主播A')


class InInterruptedRetryWindowTest(unittest.TestCase):
    """快速重试窗口判定: 决定"不在直播"算不算链接失效。"""

    def setUp(self):
        NS['max_retry_interrupted'] = MAX_RETRY

    def tearDown(self):
        NS['max_retry_interrupted'] = MAX_RETRY

    def test_not_interrupted_is_never_in_window(self):
        """未断流(正常轮询阶段, 含从未录制成功的死链)不在窗口内。"""
        self.assertFalse(IN_RETRY(False, 0))
        self.assertFalse(IN_RETRY(False, MAX_RETRY))

    def test_interrupted_within_retries_is_in_window(self):
        self.assertTrue(IN_RETRY(True, 0))
        self.assertTrue(IN_RETRY(True, MAX_RETRY - 1))

    def test_interrupted_after_retries_exhausted_is_out_of_window(self):
        """重试次数用尽后离开窗口(由"断流重试耗尽"路径注释收尾)。"""
        self.assertFalse(IN_RETRY(True, MAX_RETRY))
        self.assertFalse(IN_RETRY(True, MAX_RETRY + 1))

    def test_window_follows_configured_retry_count(self):
        """窗口边界跟随配置的 直播断流重试次数。"""
        NS['max_retry_interrupted'] = 3
        self.assertTrue(IN_RETRY(True, 2))
        self.assertFalse(IN_RETRY(True, 3))


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

    def test_retry_window_suppresses_comment(self):
        """断流快速重试窗口内不在直播也不注释(可能只是同一场直播的短暂中断)。"""
        self.assertFalse(SHOULD_COMMENT('小红书直播', False, True))
        self.assertFalse(SHOULD_COMMENT('淘宝直播', False, True))


class CommentEphemeralLinkTest(unittest.TestCase):
    """_comment_ephemeral_link 实际写入行为(仅给活跃行加 '#', 失败返回 False)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def write_config(self, lines):
        path = Path(self.tmp) / 'URL_config.ini'
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        NS['url_config_file'] = str(path)
        NS['max_retry_interrupted'] = MAX_RETRY
        return path

    def test_comment_prefixes_active_line(self):
        url = 'https://www.xiaohongshu.com/user/profile/abc123'
        path = self.write_config([f'原画，{url}，主播A', '原画，https://other.com/x'])
        self.assertTrue(COMMENT_LINK('序号1 主播A', url))
        content = path.read_text(encoding='utf-8')
        self.assertIn(f'#原画，{url}，主播A', content)
        self.assertIn('原画，https://other.com/x', content)  # 其他行不受影响

    def test_comment_returns_false_when_line_missing(self):
        path = self.write_config(['原画，https://other.com/x'])
        before = path.read_text(encoding='utf-8')
        self.assertFalse(COMMENT_LINK('序号1 A', 'https://notexist.com/y'))
        self.assertEqual(path.read_text(encoding='utf-8'), before)

    def test_comment_returns_false_when_already_commented(self):
        url = 'https://huodong.m.taobao.com/abc'
        path = self.write_config([f'# 原画，{url}'])
        before = path.read_text(encoding='utf-8')
        self.assertFalse(COMMENT_LINK('序号1 A', url))
        self.assertEqual(path.read_text(encoding='utf-8'), before)

    def test_write_failure_returns_false_without_raising(self):
        """写入失败(磁盘只读/权限/占用) → 返回 False, 不抛异常打断录制线程。"""
        url = 'https://www.xiaohongshu.com/user/profile/abc123'
        path = self.write_config([f'原画，{url}，主播A'])

        def boom(*args, **kwargs):
            raise OSError('read-only file system')

        original = NS['update_file']
        NS['update_file'] = boom
        try:
            self.assertFalse(COMMENT_LINK('序号1 主播A', url))
        finally:
            NS['update_file'] = original
        self.assertIn(url, path.read_text(encoding='utf-8'))  # 文件未被改坏


class CommentOfflineAndStopTest(unittest.TestCase):
    """离线注释 + 录制列表清理(用户场景: 注释后重新打开链接仍能再次注释)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.url = 'https://www.xiaohongshu.com/user/profile/abc123'
        self.record_name = '序号1 主播A'

    def write_config(self, lines):
        path = Path(self.tmp) / 'URL_config.ini'
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        NS['url_config_file'] = str(path)
        NS['max_retry_interrupted'] = MAX_RETRY
        NS['running_list'] = [self.url]
        NS['url_comments'] = []          # 主循环尚未刷新(注释刚写入)
        NS['recording'] = {self.record_name}
        NS['monitoring'] = 1
        return path

    def test_comment_and_clean_running_list(self):
        """注释成功 → 返回 True 且 running_list 不再残留(重新打开后能重拉线程)。"""
        path = self.write_config([f'原画，{self.url}，主播A'])
        self.assertTrue(COMMENT_AND_STOP(self.record_name, self.url, '小红书直播', False))
        self.assertIn(f'#原画，{self.url}，主播A', path.read_text(encoding='utf-8'))
        self.assertNotIn(self.url, NS['running_list'])

    def test_reopen_then_comment_again(self):
        """模拟用户把注释行重新打开: 再次轮询到不在直播时应能再次注释并清理。"""
        path = self.write_config([f'原画，{self.url}，主播A'])
        self.assertTrue(COMMENT_AND_STOP(self.record_name, self.url, '小红书直播', False))
        # 用户手动打开注释(去掉行首 '#')并重新入列
        path.write_text(f'原画，{self.url}，主播A\n', encoding='utf-8')
        NS['running_list'] = [self.url]
        self.assertTrue(COMMENT_AND_STOP(self.record_name, self.url, '小红书直播', False))
        self.assertIn(f'#原画，{self.url}，主播A', path.read_text(encoding='utf-8'))
        self.assertNotIn(self.url, NS['running_list'])

    def test_comment_failure_keeps_running(self):
        """找不到活跃行 → 返回 False 且不动 running_list(继续轮询, 不静默丢弃)。"""
        path = self.write_config(['原画，https://other.com/x'])
        before = path.read_text(encoding='utf-8')
        self.assertFalse(COMMENT_AND_STOP(self.record_name, self.url, '小红书直播', False))
        self.assertEqual(path.read_text(encoding='utf-8'), before)
        self.assertIn(self.url, NS['running_list'])

    def test_other_platform_or_live_noop(self):
        """非一次性平台 / 在直播中 → 不注释且不动录制列表。"""
        path = self.write_config([f'原画，{self.url}，主播A'])
        before = path.read_text(encoding='utf-8')
        self.assertFalse(COMMENT_AND_STOP(self.record_name, self.url, '抖音直播', False))
        self.assertFalse(COMMENT_AND_STOP(self.record_name, self.url, '小红书直播', True))
        self.assertEqual(path.read_text(encoding='utf-8'), before)
        self.assertIn(self.url, NS['running_list'])

    def test_retry_window_noop(self):
        """快速重试窗口内(录制中断后的 10 次快速重试) → 不注释, 继续等待恢复。"""
        path = self.write_config([f'原画，{self.url}，主播A'])
        before = path.read_text(encoding='utf-8')
        self.assertFalse(COMMENT_AND_STOP(self.record_name, self.url, '小红书直播', False, True))
        self.assertEqual(path.read_text(encoding='utf-8'), before)
        self.assertIn(self.url, NS['running_list'])


class OfflineCommentLifecycleTest(unittest.TestCase):
    """把"窗口判定 + 注释动作"串成 start_record 的轮询序列验证。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.url = 'https://xhslink.com/abc'
        self.record_name = '序号1 主播A'
        self.path = Path(self.tmp) / 'URL_config.ini'
        self.path.write_text(f'原画，{self.url}，主播A\n', encoding='utf-8')
        NS['url_config_file'] = str(self.path)
        NS['max_retry_interrupted'] = MAX_RETRY
        NS['running_list'] = [self.url]
        NS['url_comments'] = []
        NS['recording'] = {self.record_name}
        NS['monitoring'] = 1

    def offline_poll(self, stream_interrupted, retries):
        """一次"轮询到不在直播"的判定, 返回是否注释并结束线程。"""
        return COMMENT_AND_STOP(self.record_name, self.url, '小红书直播', False,
                                IN_RETRY(stream_interrupted, retries))

    def test_never_recorded_dead_link_commented_on_first_offline_poll(self):
        """从未录制过的死链(如开播结束后才加入)首次轮询到不在直播即注释。"""
        self.assertTrue(self.offline_poll(False, 0))
        self.assertIn(f'#原画，{self.url}，主播A', self.path.read_text(encoding='utf-8'))

    def test_retry_window_polls_keep_link_then_comment_after_exhausted(self):
        """断流后 10 次快速重试都不在直播: 窗口内不注释, 窗口耗尽后注释一次。"""
        for retries in range(MAX_RETRY):
            self.assertFalse(self.offline_poll(True, retries))
        self.assertNotIn('#', self.path.read_text(encoding='utf-8'))
        self.assertTrue(self.offline_poll(True, MAX_RETRY))
        content = self.path.read_text(encoding='utf-8')
        self.assertEqual(content.count(f'#原画，{self.url}，主播A'), 1)
        # 已注释后异常路径再次触发: 不重复写入
        self.assertFalse(self.offline_poll(True, MAX_RETRY + 1))

    def test_live_recovered_inside_window_never_comments(self):
        """窗口内直播恢复(is_live 为真) → 不注释, 链接保留继续录制。"""
        self.assertFalse(COMMENT_AND_STOP(self.record_name, self.url, '小红书直播', True, True))
        self.assertNotIn('#', self.path.read_text(encoding='utf-8'))
        self.assertIn(self.url, NS['running_list'])


class StartRecordWiringTest(unittest.TestCase):
    """源码级串联检查: 离线注释必须挂在"等待直播"分支, 且传入快速重试窗口判定。"""

    def setUp(self):
        self.source = (ROOT / 'main.py').read_text(encoding='utf-8')

    def test_offline_branch_triggers_comment(self):
        start = self.source.index("if port_info['is_live'] is False:")
        # 离线分支的结束位置: 同级 else 下的"直播已恢复"续录分支
        end = self.source.index('else:\n                            if stream_interrupted:', start)
        block = self.source[start:end]
        self.assertIn('comment_offline_ephemeral_and_stop(', block)
        self.assertIn('in_interrupted_retry_window(', block)
        self.assertIn('return', block)

    def test_retry_window_check_is_single_source_of_truth(self):
        """窗口判定只定义一次(重试延迟与离线注释共用), 避免两处条件漂移。"""
        self.assertEqual(len(re.findall(r'def in_interrupted_retry_window\(', self.source)), 1)
        self.assertEqual(
            len(re.findall(r'interrupted_retries < max_retry_interrupted', self.source)), 1)


if __name__ == '__main__':
    unittest.main()
