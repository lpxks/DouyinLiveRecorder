"""抖音 prompts(官方已下播)异常分类测试。

验证 _is_douyin_retryable_error 的判定:
- streamget 抖音实现的可重试类异常(风控/VR/拉取失败) → 应重试
- 抖音官方接口返回的 prompts 文案(如"直播已结束") → 不重试(标记 ended)

沿用 test_read_config_value 的 AST 提取模式: 提取 spider.py 中的
真实函数与常量源码执行, 避免 import spider 触发 src/__init__ 的
check_node() 副作用。
"""

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def load_error_classifier():
    src = (REPO_ROOT / "src/spider.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    namespace = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DOUYIN_RETRYABLE_EXCEPTION_MARKERS"
                for t in node.targets):
            module = ast.Module(body=[node], type_ignores=[])
            exec(compile(module, "spider.py", "exec"), namespace)
        elif isinstance(node, ast.FunctionDef) and node.name == "_is_douyin_retryable_error":
            module = ast.Module(body=[node], type_ignores=[])
            exec(compile(module, "spider.py", "exec"), namespace)
    if "_is_douyin_retryable_error" not in namespace:
        raise RuntimeError("_is_douyin_retryable_error not found in src/spider.py")
    return namespace["_is_douyin_retryable_error"]


class DouyinErrorClassifyTest(unittest.TestCase):
    def setUp(self):
        # 实例属性(而非类属性), 避免 self.classify 触发方法绑定把 self 传入
        self.classify = load_error_classifier()

    def test_retryable_markers(self):
        """风控/VR/拉取失败等固定文案 → 应重试。"""
        retryable_msgs = (
            "it triggered risk control",
            "VR live is not supported",
            "https://live.douyin.com/1 VR live is not supported",
            "Fetch stream data error",
            "Fetch failed: https://live.douyin.com/1, TimeoutError()",
        )
        for msg in retryable_msgs:
            self.assertTrue(self.classify(Exception(msg)), f"应判定为重试: {msg}")

    def test_ended_prompts_not_retryable(self):
        """抖音官方接口 prompts 文案(已下播等) → 不重试。"""
        ended_msgs = (
            "直播已结束",
            "该直播已结束",
            "直播间不存在",
            "主播已下线",
            "直播已取消",
        )
        for msg in ended_msgs:
            self.assertFalse(self.classify(Exception(msg)), f"不应判定为重试: {msg}")

    def test_empty_message_not_retryable(self):
        self.assertFalse(self.classify(Exception("")))


if __name__ == "__main__":
    unittest.main()
