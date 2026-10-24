"""TikTok 采集策略(src/tiktok_fetch.py)的单元测试。

用假的 live_stream 驱动, 不依赖网络与 streamget:
覆盖 API 优先、退避重试、网页回退、未开播不重试、语义错误不重试等分支。
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import tiktok_fetch  # noqa: E402


class FakeStreamData:
    def __init__(self, anchor_name='主播', is_live=False):
        self.anchor_name = anchor_name
        self.is_live = is_live


class FakeLiveStream:
    """按脚本返回/抛异常, 并记录调用次数。"""

    def __init__(self, app=(), web=(), stream_url_error=None):
        self._app = list(app)
        self._web = list(web)
        self.app_calls = 0
        self.web_calls = 0
        self.stream_url_calls = 0
        self.stream_url_error = stream_url_error

    async def fetch_app_stream_data(self, url):
        self.app_calls += 1
        item = self._app.pop(0) if self._app else RuntimeError('no more app results')
        if isinstance(item, Exception):
            raise item
        return item

    async def fetch_web_stream_data(self, url):
        self.web_calls += 1
        item = self._web.pop(0) if self._web else RuntimeError('no more web results')
        if isinstance(item, Exception):
            raise item
        return item

    async def fetch_stream_url(self, json_data, video_quality=None):
        self.stream_url_calls += 1
        if self.stream_url_error:
            raise self.stream_url_error
        return FakeStreamData(json_data.get('anchor', '主播'), json_data.get('is_live', False))


OK_APP = {'data': {'user': {}}, 'anchor': '主播-x'}


class FetchStreamDataTest(unittest.IsolatedAsyncioTestCase):
    URL = 'https://www.tiktok.com/@x/live'

    async def test_app_api_success_first_try(self):
        ls = FakeLiveStream(app=[OK_APP])
        out = await tiktok_fetch.get_stream_data(ls, self.URL, backoff=(0, 0))
        self.assertEqual((ls.app_calls, ls.web_calls, ls.stream_url_calls), (1, 0, 1))
        self.assertEqual(out.anchor_name, '主播-x')

    async def test_retries_then_succeeds(self):
        ls = FakeLiveStream(app=[ConnectionError('429'), ValueError('empty'), OK_APP])
        out = await tiktok_fetch.get_stream_data(ls, self.URL, backoff=(0, 0))
        self.assertEqual(ls.app_calls, 3)
        self.assertEqual(ls.web_calls, 0)
        self.assertIsNotNone(out)

    async def test_falls_back_to_web_after_three_failures(self):
        ls = FakeLiveStream(app=[ConnectionError('429')] * 3,
                            web=[{'LiveRoom': {}, 'anchor': '网页主播'}])
        out = await tiktok_fetch.get_stream_data(ls, self.URL, backoff=(0, 0))
        self.assertEqual((ls.app_calls, ls.web_calls), (3, 1))
        self.assertEqual(out.anchor_name, '网页主播')

    async def test_all_paths_fail_raises(self):
        ls = FakeLiveStream(app=[ConnectionError('429')] * 3,
                            web=[ConnectionError('challenge page')])
        with self.assertRaises(ConnectionError):
            await tiktok_fetch.get_stream_data(ls, self.URL, backoff=(0, 0))

    async def test_not_live_result_is_valid_and_not_retried(self):
        payload = {'data': {'user': {}}, 'anchor': 'n', 'is_live': False}
        ls = FakeLiveStream(app=[payload])
        out = await tiktok_fetch.get_stream_data(ls, self.URL, backoff=(0, 0))
        self.assertFalse(out.is_live)
        self.assertEqual(ls.app_calls, 1)

    async def test_empty_room_data_is_retried(self):
        ls = FakeLiveStream(app=[{'data': {}}, OK_APP])
        await tiktok_fetch.get_stream_data(ls, self.URL, backoff=(0, 0))
        self.assertEqual(ls.app_calls, 2)

    async def test_semantic_error_from_stream_url_propagates_without_retry(self):
        ls = FakeLiveStream(app=[OK_APP])
        ls.stream_url_error = ValueError('age restricted')
        with self.assertRaises(ValueError):
            await tiktok_fetch.get_stream_data(ls, self.URL, backoff=(0, 0))
        self.assertEqual(ls.app_calls, 1)
        self.assertEqual(ls.web_calls, 0)


class SpiderWiringTest(unittest.TestCase):
    def test_tiktok_uses_app_api_strategy(self):
        src = (ROOT / 'src' / 'spider.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        node = next(n for n in tree.body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_tiktok_stream_data')
        attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        self.assertIn('get_stream_data', attrs)
        self.assertNotIn('fetch_web_stream_data', attrs)
        self.assertIn('from . import tiktok_fetch', src)


if __name__ == '__main__':
    unittest.main()
