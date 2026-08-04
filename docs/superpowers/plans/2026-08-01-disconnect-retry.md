# 断流重试简化 + 优先监控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 两个改动：(1) 断流重试间隔改为「前 5 次 2–5 秒随机、后 5 次 5–8 秒随机」，保持 10 次总数不变，只做抖动防风控；(2) 新增优先监控——URL_config.ini 行内标记 `,优先: 是` 的房间，按 config.ini `[优先监控]` 段配置的间隔（默认 3 秒）轮询，减少重点房间的漏录时间。

**Architecture:** 纯逻辑抽到根级模块 `retry.py`（重试间隔决策）和 `url_parser.py`（优先标记解析），可单测；main.py 只做薄接线。断流重试不改 `check_subprocess`；优先监控复用现有 URL_config.ini 行格式新增一个可选字段，写回逻辑因采用 URL 子串替换而自动保留该字段，无需改动。

**Tech Stack:** Python 3.10+ / threading / unittest（纯标准库）

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `retry.py` | 新建 | `retry_delay(retry_index)`：前 5 次 2–5s、后 5 次 5–8s 随机 |
| `tests/test_retry_delay.py` | 新建 | `retry_delay` 单测 |
| `url_parser.py` | 新建 | `is_priority(line)`：行内优先标记解析 |
| `tests/test_url_parser.py` | 新建 | `is_priority` 单测 |
| `main.py` | 修改 | 重试块换用 `retry_delay`；解析优先标记、读优先间隔、轮询等待决策分档 |
| `config/config.ini` | 修改 | 新增 `[优先监控]` 段；删除不再使用的 `断流重试间隔(秒)` |

**明确不在范围**：`check_subprocess` 返回值改造/「录到数据才重试」门控（用户选择简化）、`error_window` 计数、ffmpeg 起播探测优化、并发线程数调整。

**设计要点**
- 断流重试：仍为 10 次（`直播断流重试次数`），第 1–5 次等待 2–5s 随机、第 6–10 次等待 5–8s 随机；`断流重试间隔(秒)` 键不再使用。
- 优先监控：URL_config.ini 行尾加 `,优先: 是`（示例：`https://live.douyin.com/xxx,主播: 张三,优先: 是`）；只对非注释行生效。
- 写回保留：main.py 写回 URL_config.ini 时是 `out_line.replace(旧URL, 新内容)` 的子串替换，只动 URL 部分，行尾 `,优先: 是` 自动保留，无需改写回逻辑。

---

## Task 1: retry.py（TDD）

**Files:**
- Create: `retry.py`
- Test: `tests/test_retry_delay.py`

- [ ] **Step 1: 写失败测试**

`tests/test_retry_delay.py`（完整内容）：

```python
import random
import unittest

from retry import retry_delay


class RetryDelayTest(unittest.TestCase):
    def test_first_five_retries_stay_in_2_to_5(self):
        rng = random.Random(7)
        for idx in range(5):
            delay = retry_delay(idx, rng=rng)
            self.assertGreaterEqual(delay, 2)
            self.assertLessEqual(delay, 5)

    def test_last_five_retries_stay_in_5_to_8(self):
        rng = random.Random(7)
        for idx in range(5, 10):
            delay = retry_delay(idx, rng=rng)
            self.assertGreaterEqual(delay, 5)
            self.assertLessEqual(delay, 8)

    def test_fast_band_uses_full_random_range(self):
        rng = random.Random(123)
        values = {retry_delay(i, rng=rng) for i in range(5) for _ in range(200)}
        self.assertEqual(values, {2, 3, 4, 5})

    def test_slow_band_uses_full_random_range(self):
        rng = random.Random(123)
        values = {retry_delay(i, rng=rng) for i in range(5, 10) for _ in range(200)}
        self.assertEqual(values, {5, 6, 7, 8})


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'retry'`（功能缺失）。

- [ ] **Step 3: 最小实现**

`retry.py`（完整内容）：

```python
"""断流重试间隔：前5次 2~5 秒随机，后5次 5~8 秒随机。"""

import random


def retry_delay(retry_index: int, rng=None) -> int:
    """返回第 retry_index 次（从 0 开始）重试前的等待秒数。

    前 5 次（0-4）在 2~5 秒间随机，后 5 次（5-9）在 5~8 秒间随机。
    """
    rng = rng or random
    if retry_index < 5:
        return rng.randint(2, 5)
    return rng.randint(5, 8)
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: 4 个测试全部 `ok`。

- [ ] **Step 5: 提交**

```bash
git add retry.py tests/test_retry_delay.py
git commit -m "feat: 断流重试间隔改为前5次2-5s/后5次5-8s随机"
```

---

## Task 2: url_parser.py（TDD）

**Files:**
- Create: `url_parser.py`
- Test: `tests/test_url_parser.py`

- [ ] **Step 1: 写失败测试**

`tests/test_url_parser.py`（完整内容）：

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'url_parser'`。

- [ ] **Step 3: 最小实现**

`url_parser.py`（完整内容）：

```python
"""URL_config.ini 行内优先监控标记的解析。"""

PRIORITY_MARK = ',优先: 是'


def is_priority(line: str) -> bool:
    """行是否标记为优先监控（调用方保证传入非注释行）。"""
    return PRIORITY_MARK in line
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: 3 个测试全部 `ok`。

- [ ] **Step 5: 提交**

```bash
git add url_parser.py tests/test_url_parser.py
git commit -m "feat: 新增URL行优先监控标记解析"
```

---

## Task 3: main.py 接线——断流重试

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 导入**

在 `main.py` 顶部 `from ffmpeg_install import (...)` 之后加：

```python
from retry import retry_delay
```

- [ ] **Step 2: 替换重试块**

将 1635–1646 行：

```python
                # 直播录制结束(不论正常结束或断流中断), 统一走快速重试检测逻辑
                # 50秒内轮询10次, 直播恢复则自动续录
                if stream_interrupted and interrupted_retries < max_retry_interrupted:
                    # 断流重试逻辑: 当直播中断(主播下播/网络问题)后立即重试校验直播在线状态
                    # 缩短断流漏录时长, 直播恢复则自动续录
                    x = retry_interrupted_interval
                    interrupted_retries += 1
                    print(f"\r{anchor_name} 直播中断, 第{interrupted_retries}次重试检测中... "
                          f"(最多{max_retry_interrupted}次)", end="")
                else:
                    stream_interrupted = False
                    interrupted_retries = 0
                    x = num
```

改为：

```python
                # 断流重试: 前5次2~5秒随机, 后5次5~8秒随机
                if stream_interrupted and interrupted_retries < max_retry_interrupted:
                    x = retry_delay(interrupted_retries)
                    interrupted_retries += 1
                    print(f"\r{anchor_name} 直播中断, 第{interrupted_retries}次重试检测中... "
                          f"(最多{max_retry_interrupted}次)", end="")
                else:
                    stream_interrupted = False
                    interrupted_retries = 0
                    x = num
```

- [ ] **Step 3: 移除不再使用的配置读取**

删除 1842 行：

```python
    retry_interrupted_interval = int(read_config_value(config, '录制设置', '断流重试间隔(秒)', 5))
```

`max_retry_interrupted`（1841 行）保留。

- [ ] **Step 4: 验证并提交**

```bash
python3 -m py_compile main.py retry.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: `py_compile` 无输出；全部测试 `ok`。

```bash
git add main.py
git commit -m "feat: 断流重试接入2-5s/5-8s随机间隔"
```

---

## Task 4: main.py 接线——优先监控

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 导入与全局集合**

在 `from retry import retry_delay` 之后加：

```python
from url_parser import is_priority
```

在 56 行 `url_tuples_list = []` 附近加模块级全局：

```python
priority_urls = set()
```

- [ ] **Step 2: 读取优先轮询间隔**

在 1841 行 `max_retry_interrupted` 读取处附近加：

```python
    priority_delay = int(read_config_value(config, '优先监控', '优先监控轮询间隔(秒)', 3))
```

- [ ] **Step 3: 解析时收集优先 URL**

在读取 URL_config.ini 的主循环内、`with open(url_config_file, ...)` 之前（约 1975 行前）加：

```python
        priority_urls = set()
```

（主循环与 `start_record` 同为模块级代码，直接读写全局即可，无需 `global` 声明。）

在 2167 行（重复-解开注释分支）与 2189 行（首次出现分支）的 `url_tuples_list.append(...)` 之后各加：

```python
                        if is_priority(line):
                            priority_urls.add(url)
```

注意两处缩进与所在分支一致；此处的 `url` 已是归一化后的 URL（含 `https://`），与 `start_record` 中的 `record_url` 一致。

- [ ] **Step 4: 轮询等待决策分档**

将 1627 行：

```python
                num = random.randint(-5, 5) + delay_default
```

改为：

```python
                if record_url in priority_urls:
                    num = random.randint(-1, 1) + priority_delay
                else:
                    num = random.randint(-5, 5) + delay_default
```

其后的 `if num < 0: num = 0` 保持不变（优先房间 3±1s，非优先房间维持原抖动）。

- [ ] **Step 5: 验证并提交**

```bash
python3 -m py_compile main.py url_parser.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: 无编译错误；全部测试 `ok`。

```bash
git add main.py
git commit -m "feat: 支持URL_config行内优先标记并按3s间隔轮询"
```

---

## Task 5: 配置文件

**Files:**
- Modify: `config/config.ini`

- [ ] **Step 1: 新增 [优先监控] 段、删除废弃键**

在 `config/config.ini` 末尾（或合适位置）新增：

```ini
[优先监控]
优先监控轮询间隔(秒) = 3
```

删除 25 行：

```ini
断流重试间隔(秒) = 5
```

（用户本地已存在的 `断流重试间隔(秒)` 键不再被读取，可保留或自行删除，均无影响。）

> 兼容性修复：旧版用户 config.ini 缺少 `[优先监控]` 分区时，`read_config_value` 的 except 分支会先 `add_section` 再 `set`，自动补建分区并写回默认值，避免 `NoSectionError` 崩溃（见 `tests/test_read_config_value.py`）。

> 评审修复（P1-P3）：优先标记在 `split_url_line` 中先检测后剥离再拆字段，避免逗号干扰字段对齐；URL 去重时保留行合并标记（任一重复行带标记即补到保留行），保证持久生效；`priority_urls` 改为临时集解析完成后原子替换，避免解析期间线程看到空集合回退慢轮询。

> 评审修复（去重语义）：注释行上的 `,优先: 是` 是「暂停标注」——由 `dedup_priority_action` 统一决策：标注永远保留（合并进保留行或保留注释行），激活只取决于生效行；生效行 + 带标记注释行、带标记注释行 + 无标记生效行两种顺序收敛为同一结果，不再因行序不同产生差异。

> 评审修复（去重基准）：`retained_annotations` 记录每条 URL 已保留的注释标注行；`keep_comment` 只保留一条标注（后续重复丢弃），`keep_comment_and_active` 把去重基准切换到新增生效行——3 条以上重复（含同 URL 不同 query 归一化）均正确收敛为「一条生效行 + 一条注释标注」，且不会误解开暂停标注。

- [ ] **Step 2: 验证读取**

```bash
python3 - <<'EOF'
import configparser
config = configparser.RawConfigParser()
config.read('config/config.ini', encoding='utf-8-sig')
print('优先间隔 =', config.get('优先监控', '优先监控轮询间隔(秒)'))
EOF
```

Expected: `优先间隔 = 3`。

- [ ] **Step 3: 提交**

```bash
git add config/config.ini
git commit -m "chore: 新增优先监控间隔配置, 移除废弃的断流重试间隔键"
```

---

## Task 6: 整体验证

- [ ] **Step 1: 全量测试与语法检查**

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m py_compile main.py retry.py url_parser.py
```

Expected: 7 个测试全部 `ok`，无编译错误。

- [ ] **Step 2: 手工冒烟检查清单（需要真实环境，可交给用户）**
  - URL_config.ini 中某行加 `,优先: 是` 后，该房间轮询间隔约为 3±1 秒，其他房间仍为 30 秒。
  - 打断正在录制的直播：前 5 次重试间隔在 2–5 秒、后 5 次在 5–8 秒（可观察打印的等待秒数）。
  - 程序运行一次后检查 URL_config.ini：`,优先: 是` 标记仍然保留（写回未丢失）。
  - 加标记的房间被 `#` 注释后不再按 3 秒轮询。

- [ ] **Step 3: 提交（如有验证期间的修复）**

```bash
git add -A
git commit -m "test: 断流重试与优先监控整体验证"
```

---

## 自检

- **需求覆盖**：前 5 次 2–5s、后 5 次 5–8s 随机（Task 1/3）✓；保持 10 次总数与 `直播断流重试次数` 配置兼容（Task 3 Step 2）✓；优先标记写在 URL_config.ini 行内（Task 2/4）✓；优先间隔参数在 config.ini `[优先监控]` 段（Task 4 Step 2 / Task 5）✓；写回保留标记（Task 6 冒烟清单第 3 项，依据子串替换机制）✓。
- **占位符扫描**：所有代码步骤均给出完整代码，无 TBD/「类似上文」。
- **类型一致性**：`retry_delay(retry_index, rng)` 与 `is_priority(line)` 的定义（Task 1/2）与使用（Task 3/4）签名一致；`priority_urls` 全局集合在 Task 4 中初始化、填充、消费三处一致。
