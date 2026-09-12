"""一次性直播链接自动注释辅助函数测试。

find_comment_target_line / in_interrupted_retry_window / should_comment_ephemeral /
_comment_ephemeral_link / comment_ephemeral_and_stop 从 main.py 用 AST 提取真实源码
执行(避免导入整个程序): 定位活跃行、判断"连续多轮未检测到在直播"是否应注释、给
配置行加 '#' 的实际写入, 以及注释后 running_list 的清理(决定链接被重新打开后能否
重新拉起线程)都在这里验证。

阈值语义: "未检测到在直播"把两种情况合并计数——轮询到不在直播(is_live False)与
完全取不到直播间信息(主播名为空, 走"获取失败"路径); 连续
EPHEMERAL_NOT_LIVE_COMMENT_ROUNDS 轮才注释, 中间只要有一轮看到在直播就清零。
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
                 'should_comment_ephemeral', '_comment_ephemeral_link',
                 'comment_ephemeral_and_stop', 'clear_record_info', 'update_file')

_LOADED_CONSTANTS = ('EPHEMERAL_LIVE_PLATFORMS', 'EPHEMERAL_NOT_LIVE_COMMENT_ROUNDS')

MAX_RETRY = 10
ROUNDS = 3


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
                getattr(t, 'id', None) in _LOADED_CONSTANTS for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), namespace)
        elif isinstance(node, ast.FunctionDef) and node.name in _LOADED_NAMES:
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), namespace)
    missing = [n for n in _LOADED_NAMES + _LOADED_CONSTANTS if n not in namespace]
    if missing:
        raise RuntimeError(f'not found in main.py: {missing}')
    return namespace


NS = load_namespace()
FIND = NS['find_comment_target_line']
IN_RETRY = NS['in_interrupted_retry_window']
SHOULD_COMMENT = NS['should_comment_ephemeral']
COMMENT_LINK = NS['_comment_ephemeral_link']
COMMENT_AND_STOP = NS['comment_ephemeral_and_stop']


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
    """快速重试窗口判定: 决定"未检测到在直播"算不算链接失效。"""

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


class ShouldCommentEphemeralTest(unittest.TestCase):
    """是否注释链接: 连续多轮未检测到在直播 + 平台白名单 + 不在快速重试窗口。"""

    def setUp(self):
        NS['EPHEMERAL_NOT_LIVE_COMMENT_ROUNDS'] = ROUNDS

    def tearDown(self):
        NS['EPHEMERAL_NOT_LIVE_COMMENT_ROUNDS'] = ROUNDS

    def test_requires_three_consecutive_rounds(self):
        """前两轮不注释(网络卡顿的偶发失败不算失效), 第三轮才注释。"""
        for platform in ('小红书直播', '淘宝直播'):
            self.assertFalse(SHOULD_COMMENT(platform, False, 0))
            self.assertFalse(SHOULD_COMMENT(platform, False, 1))
            self.assertFalse(SHOULD_COMMENT(platform, False, 2))
            self.assertTrue(SHOULD_COMMENT(platform, False, 3))
            self.assertTrue(SHOULD_COMMENT(platform, False, 4))

    def test_live_never_comments(self):
        """在直播中不注释(计数由调用方清零)。"""
        self.assertFalse(SHOULD_COMMENT('小红书直播', True, ROUNDS))
        self.assertFalse(SHOULD_COMMENT('淘宝直播', True, 99))

    def test_unknown_live_state_never_comments(self):
        """is_live 非 False(None/缺失)时不注释, 避免状态未知误伤。"""
        self.assertFalse(SHOULD_COMMENT('小红书直播', None, ROUNDS))
        self.assertFalse(SHOULD_COMMENT('淘宝直播', '', ROUNDS))

    def test_other_platform_never_comments(self):
        """非一次性链接平台(如抖音)保持原有行为, 不论失败多少轮都不注释。"""
        self.assertFalse(SHOULD_COMMENT('抖音直播', False, ROUNDS))
        self.assertFalse(SHOULD_COMMENT('抖音直播', None, 99))

    def test_retry_window_suppresses_comment(self):
        """断流快速重试窗口内不注释(仍在尝试续录同一场直播)。"""
        self.assertFalse(SHOULD_COMMENT('小红书直播', False, 99, True))
        self.assertFalse(SHOULD_COMMENT('淘宝直播', None, 99, True))

    def test_threshold_follows_constant(self):
        """阈值由 EPHEMERAL_NOT_LIVE_COMMENT_ROUNDS 单点控制。"""
        NS['EPHEMERAL_NOT_LIVE_COMMENT_ROUNDS'] = 5
        self.assertFalse(SHOULD_COMMENT('小红书直播', False, 4))
        self.assertTrue(SHOULD_COMMENT('小红书直播', False, 5))


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


class CommentAndStopTest(unittest.TestCase):
    """注释 + 录制列表清理(用户场景: 注释后重新打开链接仍能再次注释)。"""

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
        self.assertTrue(COMMENT_AND_STOP(self.record_name, self.url, '连续3轮未检测到直播, '))
        self.assertIn(f'#原画，{self.url}，主播A', path.read_text(encoding='utf-8'))
        self.assertNotIn(self.url, NS['running_list'])

    def test_reopen_then_comment_again(self):
        """模拟用户把注释行重新打开: 再次判定成立时应能再次注释并清理。"""
        path = self.write_config([f'原画，{self.url}，主播A'])
        self.assertTrue(COMMENT_AND_STOP(self.record_name, self.url, '连续3轮未检测到直播, '))
        # 用户手动打开注释(去掉行首 '#')并重新入列
        path.write_text(f'原画，{self.url}，主播A\n', encoding='utf-8')
        NS['running_list'] = [self.url]
        self.assertTrue(COMMENT_AND_STOP(self.record_name, self.url, '连续3轮未检测到直播, '))
        self.assertIn(f'#原画，{self.url}，主播A', path.read_text(encoding='utf-8'))
        self.assertNotIn(self.url, NS['running_list'])

    def test_comment_failure_keeps_running(self):
        """找不到活跃行 → 返回 False 且不动 running_list(继续轮询, 不静默丢弃)。"""
        path = self.write_config(['原画，https://other.com/x'])
        before = path.read_text(encoding='utf-8')
        self.assertFalse(COMMENT_AND_STOP(self.record_name, self.url, '连续3轮未检测到直播, '))
        self.assertEqual(path.read_text(encoding='utf-8'), before)
        self.assertIn(self.url, NS['running_list'])


class NotLiveRoundsLifecycleTest(unittest.TestCase):
    """把"轮询结果序列 + 计数 + 注释动作"串成 start_record 的轮询序列验证。

    run_polls 复刻 start_record 的计数规则: 看到在直播(is_live True)清零重算,
    否则 not_live_rounds += 1 后判定是否注释。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.url = 'https://xhslink.com/abc'
        self.record_name = '序号1 主播A'
        self.path = Path(self.tmp) / 'URL_config.ini'
        self.path.write_text(f'原画，{self.url}，主播A\n', encoding='utf-8')
        NS['url_config_file'] = str(self.path)
        NS['max_retry_interrupted'] = MAX_RETRY
        NS['EPHEMERAL_NOT_LIVE_COMMENT_ROUNDS'] = ROUNDS
        NS['running_list'] = [self.url]
        NS['url_comments'] = []
        NS['recording'] = {self.record_name}
        NS['monitoring'] = 1

    def tearDown(self):
        NS['EPHEMERAL_NOT_LIVE_COMMENT_ROUNDS'] = ROUNDS

    def run_polls(self, results, platform='小红书直播', in_retry_at=None):
        """按顺序模拟每轮轮询结果, 返回被注释的轮次(None = 始终未注释)。

        results 元素: False = 不在直播, 'fail' = 获取失败(主播名为空), True = 在直播。
        真实代码中"获取失败"的兜底结果是 {"anchor_name": "", "is_live": False}, 所以这里
        同样以 is_live=False 参与判定(仅 is_live 为 None/缺失等未知状态才会拒绝注释)。
        in_retry_at(index) 返回该轮是否处于断流快速重试窗口内, 默认都不在窗口内。
        """
        not_live_rounds = 0
        for index, result in enumerate(results, 1):
            if result is True:
                not_live_rounds = 0
                continue
            not_live_rounds += 1
            is_live = False if result == 'fail' else result
            in_retry = bool(in_retry_at(index)) if in_retry_at else False
            if not SHOULD_COMMENT(platform, is_live, not_live_rounds, in_retry):
                continue
            if COMMENT_AND_STOP(self.record_name, self.url,
                                f'连续{not_live_rounds}轮未检测到直播, '):
                return index
        return None

    def commented(self):
        return '#' in self.path.read_text(encoding='utf-8')

    def test_dead_link_comments_on_third_consecutive_poll(self):
        """从未开播过的死链: 连续 3 轮不在直播才注释。"""
        self.assertIsNone(self.run_polls([False, False]))
        self.assertFalse(self.commented())
        self.assertEqual(self.run_polls([False, False, False]), 3)
        self.assertIn(f'#原画，{self.url}，主播A', self.path.read_text(encoding='utf-8'))

    def test_network_hiccup_resets_counter(self):
        """网络卡顿: 两次失败后来一轮在直播 → 计数清零, 链接不被注释。"""
        self.assertIsNone(self.run_polls([False, 'fail', True]))
        self.assertFalse(self.commented())
        # 之后即使再连续失败, 也要重新累计 3 轮
        self.assertIsNone(self.run_polls([False, False, True, False, False]))
        self.assertFalse(self.commented())

    def test_mixed_failures_count_together(self):
        """获取失败与不在直播混合计数: 三轮都没看到在直播即注释。"""
        self.assertEqual(self.run_polls(['fail', False, 'fail']), 3)
        self.assertIn(f'#原画，{self.url}，主播A', self.path.read_text(encoding='utf-8'))

    def test_unresolved_only_reaches_threshold(self):
        """全程取不到直播间信息(如淘宝cookie失效): 第 3 轮注释。"""
        self.assertIsNone(self.run_polls(['fail', 'fail']))
        self.assertEqual(self.run_polls(['fail', 'fail', 'fail']), 3)

    def test_alternating_live_never_comments(self):
        """每两轮就能看到一次在直播(直播间时断时续) → 永远不注释。"""
        self.assertIsNone(self.run_polls([False, True, False, True, False, True, False]))
        self.assertFalse(self.commented())

    def test_retry_window_blocks_comment_until_exhausted(self):
        """断流快速重试: 窗口内连续失败不注释, 重试耗尽(离开窗口)后才注释。"""
        rounds = [False] * (MAX_RETRY + 1)
        commented_at = self.run_polls(rounds, in_retry_at=lambda index: index <= MAX_RETRY)
        self.assertEqual(commented_at, MAX_RETRY + 1)
        self.assertIn(f'#原画，{self.url}，主播A', self.path.read_text(encoding='utf-8'))

    def test_other_platform_never_comments(self):
        """非一次性平台(抖音)即使连续失败 10 轮也不注释。"""
        self.assertIsNone(self.run_polls([False] * 10, platform='抖音直播'))
        self.assertFalse(self.commented())


class StartRecordWiringTest(unittest.TestCase):
    """源码级串联检查: 两条"未检测到在直播"路径都必须接进 start_record。"""

    def setUp(self):
        self.source = (ROOT / 'main.py').read_text(encoding='utf-8')

    def offline_block(self):
        start = self.source.index("if port_info['is_live'] is False:")
        # 离线分支的结束位置: 同级 else 下的"直播已恢复"续录分支
        end = self.source.index('else:\n                            if stream_interrupted:', start)
        return start, self.source[start:end]

    def test_offline_branch_counts_and_comments(self):
        _, block = self.offline_block()
        self.assertIn('not_live_rounds += 1', block)
        self.assertIn('should_comment_ephemeral(', block)
        self.assertIn('comment_ephemeral_and_stop(', block)
        self.assertIn('in_interrupted_retry_window(', block)
        self.assertIn('return', block)

    def test_unresolved_branch_counts_and_comments(self):
        """"网址内容获取失败"分支(主播名为空)同样累计计数并可注释。"""
        start = self.source.index('if not port_info.get("anchor_name", \'\'):')
        end = self.source.index('else:\n                        anchor_name = clean_name', start)
        block = self.source[start:end]
        self.assertIn('not_live_rounds += 1', block)
        self.assertIn('should_comment_ephemeral(', block)
        self.assertIn('comment_ephemeral_and_stop(', block)
        self.assertIn('return', block)

    def test_live_branch_resets_counter(self):
        """看到在直播的分支必须清零计数, 否则"时断时续"的直播间会被误注释。"""
        start, _ = self.offline_block()
        end = self.source.index('content = f"\\r{record_name} 正在直播中..."', start)
        self.assertIn('not_live_rounds = 0', self.source[start:end])
        # 只应有"线程初始化"与"检测到在直播"两处赋值
        self.assertEqual(len(re.findall(r'not_live_rounds = 0', self.source)), 2)

    def test_retry_window_check_is_single_source_of_truth(self):
        """窗口判定只定义一次(重试延迟与两条计数路径共用), 避免多处条件漂移。"""
        self.assertEqual(len(re.findall(r'def in_interrupted_retry_window\(', self.source)), 1)
        self.assertEqual(
            len(re.findall(r'interrupted_retries < max_retry_interrupted', self.source)), 1)

    def test_comment_threshold_is_single_constant(self):
        self.assertEqual(len(re.findall(r'EPHEMERAL_NOT_LIVE_COMMENT_ROUNDS = ', self.source)), 1)


if __name__ == '__main__':
    unittest.main()
