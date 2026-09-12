"""小红书主播名兜底解析测试。

背景: streamget 只在小红书直播中(liveStatus == success)或用户主页可解析时才给出
主播名; 一次性链接的常见状态是"已下播"(liveStatus == end), 此时返回
{"anchor_name": "", "is_live": False}, 会被 main.py 误判成"网址内容获取失败"而
无限重试, 永远进不了"等待直播"分支, 也就永远不会被自动注释。

_xhs_anchor_from_html / _xhs_page_anchor_name 从 src/spider.py 用 AST 提取真实源码
执行(避免 import spider 触发模块级副作用与网络请求)。
"""

import ast
import asyncio
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_LOADED_NAMES = ('_xhs_anchor_from_html', '_xhs_page_anchor_name')

ENDED_STATE = ('{"liveStream":{"pageStatus":"success","liveStatus":"end",'
               '"roomData":{"hostInfo":{"nickName":"舒塔ssstt"},'
               '"roomInfo":{"roomTitle":"回放"}}}}')
LIVE_STATE = ('{"liveStream":{"liveStatus":"success",'
              '"roomData":{"hostInfo":{"nickName":"主播A"},'
              '"roomInfo":{"roomTitle":"标题"}}}}')


def page(state_json: str) -> str:
    return (f'<html><head><title>小红书</title></head><body>'
            f'<script>window.__INITIAL_STATE__={state_json}</script></body></html>')


class _FakeLogger:
    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def load_namespace():
    tree = ast.parse((ROOT / 'src/spider.py').read_text(encoding='utf-8'))
    namespace = {'re': __import__('re'), 'json': __import__('json'), 'logger': _FakeLogger()}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _LOADED_NAMES:
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'spider.py', 'exec'), namespace)
    missing = [n for n in _LOADED_NAMES if n not in namespace]
    if missing:
        raise RuntimeError(f'not found in src/spider.py: {missing}')
    return namespace


NS = load_namespace()
ANCHOR_FROM_HTML = NS['_xhs_anchor_from_html']
PAGE_ANCHOR_NAME = NS['_xhs_page_anchor_name']


class XhsAnchorFromHtmlTest(unittest.TestCase):
    def test_ended_live_still_yields_anchor_name(self):
        """已下播(liveStatus == end)的直播间同样能取到主播名——一次性链接的常见状态。"""
        self.assertEqual(ANCHOR_FROM_HTML(page(ENDED_STATE)), '舒塔ssstt')

    def test_live_room_yields_anchor_name(self):
        self.assertEqual(ANCHOR_FROM_HTML(page(LIVE_STATE)), '主播A')

    def test_js_undefined_is_tolerated(self):
        """页面里的 JS undefined 需按 null 解析(streamget 同样处理)。"""
        html = page('{"liveStream":undefined}')
        self.assertEqual(ANCHOR_FROM_HTML(html), '')

    def test_missing_initial_state_returns_empty(self):
        self.assertEqual(ANCHOR_FROM_HTML('<html><title>小红书</title></html>'), '')
        self.assertEqual(ANCHOR_FROM_HTML(''), '')

    def test_invalid_json_returns_empty(self):
        self.assertEqual(ANCHOR_FROM_HTML(page('{not json')), '')

    def test_missing_host_info_returns_empty(self):
        self.assertEqual(ANCHOR_FROM_HTML(page('{"liveStream":{"roomData":{}}}')), '')
        self.assertEqual(ANCHOR_FROM_HTML(page('{"liveStream":{"roomData":{"hostInfo":{}}}}')), '')


class XhsPageAnchorNameTest(unittest.TestCase):
    """_xhs_page_anchor_name: 兜底请求直播间页面, 失败时返回 '' 保持"获取失败"语义。"""

    def setUp(self):
        # 注意: 提取出的函数 globals 就是 NS 本体, 桩函数必须注入 NS(不能是副本)
        self.ns = NS
        self.live_stream = types.SimpleNamespace(proxy_addr=None, mobile_headers={'user-agent': 'x'})
        self.requests = []

    def tearDown(self):
        self.ns.pop('async_req', None)

    def install_fake_req(self, html=None, raises=None):
        async def fake_req(url, proxy_addr=None, headers=None):
            self.requests.append(url)
            if raises:
                raise raises
            return html
        self.ns['async_req'] = fake_req

    def test_parses_anchor_name_from_page(self):
        self.install_fake_req(html=page(ENDED_STATE))
        url = 'https://www.xiaohongshu.com/livestream/abc/123?xsec_token=t'
        got = asyncio.run(self.ns['_xhs_page_anchor_name'](self.live_stream, {'live_url': url}))
        self.assertEqual(got, '舒塔ssstt')
        self.assertEqual(self.requests, [url])

    def test_no_page_url_skips_request(self):
        self.install_fake_req(html=page(ENDED_STATE))
        self.assertEqual(asyncio.run(self.ns['_xhs_page_anchor_name'](self.live_stream, {})), '')
        self.assertEqual(asyncio.run(self.ns['_xhs_page_anchor_name'](self.live_stream, None)), '')
        self.assertEqual(self.requests, [])

    def test_request_error_returns_empty(self):
        self.install_fake_req(raises=OSError('boom'))
        got = asyncio.run(self.ns['_xhs_page_anchor_name'](self.live_stream, {'live_url': 'https://x/y'}))
        self.assertEqual(got, '')

    def test_unparsable_page_returns_empty(self):
        self.install_fake_req(html='<html>验证</html>')
        got = asyncio.run(self.ns['_xhs_page_anchor_name'](self.live_stream, {'live_url': 'https://x/y'}))
        self.assertEqual(got, '')


if __name__ == '__main__':
    unittest.main()
