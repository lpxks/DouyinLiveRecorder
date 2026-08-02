"""spider.py 契约测试:AST 解析验证平台函数签名与 demo 映射完整性(不 import spider)。

遵循 tests/test_read_config_value.py 的模式:用 AST 提取源码结构断言,
避免 import main.py / spider.py 触发模块级副作用(网络、Node 检测等)。
"""

import ast
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def load_spider_functions():
    tree = ast.parse((REPO_ROOT / "src/spider.py").read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name.startswith("get_")}


# 非平台辅助函数(无 url 参数),重构时不得误删
NON_PLATFORM_HELPERS = {
    "get_params",
}


class SpiderContractTest(unittest.TestCase):
    def setUp(self):
        self.funcs = load_spider_functions()

    def test_platform_functions_have_url_param(self):
        """平台函数首个参数必须是 url(登录平台除外,签名从 url 起)。"""
        for name, node in self.funcs.items():
            if name in NON_PLATFORM_HELPERS:
                continue
            args = [a.arg for a in node.args.args]
            self.assertIn("url", args, f"{name} 缺少 url 参数")

    def test_demo_config_functions_exist_in_spider(self):
        """demo.py LIVE_STREAM_CONFIG 引用的每个 spider 函数必须存在。"""
        demo_src = (REPO_ROOT / "demo.py").read_text(encoding="utf-8")
        refs = set(re.findall(r"spider\.(get_[a-zA-Z0-9_]+)", demo_src))
        self.assertGreater(len(refs), 40, f"demo 平台条目异常少: {len(refs)}")
        for ref in refs:
            self.assertIn(ref, self.funcs, f"demo 引用了不存在的 spider 函数 {ref}")

    def test_streamget_wrapped_functions_have_video_quality_tail_param(self):
        """已迁移 streamget 的平台函数应有 video_quality 尾参(默认 None)。

        保留原实现的 get_liuxing_stream_url 与纯辅助函数不受此约束。
        """
        for name, node in self.funcs.items():
            if name in NON_PLATFORM_HELPERS or name == "get_liuxing_stream_url":
                continue
            args = [a.arg for a in node.args.args]
            self.assertEqual(args[-1], "video_quality", f"{name} 缺少 video_quality 尾参")

    def test_stream_module_deleted(self):
        """重构完成后 src/stream.py 应已删除(逻辑已并入 streamget)。"""
        self.assertFalse((REPO_ROOT / "src/stream.py").exists(),
                         "src/stream.py 应已删除,逻辑已在 streamget 各平台 fetch_stream_url 中")


if __name__ == "__main__":
    unittest.main()
