"""URL_config.ini 行解析：检查时长等级标记与字段拆分。

行格式（等级标记可写在任意位置，推荐放在主播名之后）::

    https://live.douyin.com/277869507858,主播: MMA盼盼,A
    原画，https://live.douyin.com/277869507858,主播: MMA盼盼,B

等级含义: A/B/C 三个检查时长等级，间隔秒数在 config.ini 的
`[检查时长等级]` 段配置；未标等级的链接按默认级 C 轮询。

旧的 `,优先: 是` 标记已废弃，解析时会被识别（has_legacy_priority）并由调用方
一次性迁移为 `,A`，避免老配置静默降级。
"""

import re

LEVELS = ('A', 'B', 'C')
LEVEL_DEFAULT = 'C'

# 旧版优先监控标记：仅用于兼容迁移成 A 等级，新配置不应再使用
LEGACY_PRIORITY_MARK = ',优先: 是'
LEGACY_PRIORITY_LEVEL = 'A'

# 各等级的轮询抖动(秒): A 等级沿用旧"优先监控"的 ±1, B/C 沿用普通链接的 ±5
LEVEL_JITTER = {'A': 1, 'B': 5, 'C': 5}

QUALITY_NAMES = ("原画", "蓝光", "超清", "高清", "标清", "流畅")


def is_level_token(field: str) -> bool:
    """字段本身是否就是一个等级标记(整字段 A/B/C, 大小写不敏感且允许空格)。"""
    return field.strip().upper() in LEVELS


def normalize_level(level, default_level: str = LEVEL_DEFAULT) -> str:
    """归一化等级：非法值/缺失(None/空)回退默认级。"""
    text = level.strip().upper() if isinstance(level, str) else ''
    return text if text in LEVELS else default_level


def extract_level(fields: list) -> tuple:
    """从字段列表里取出等级标记，返回 (level, 剩余字段)。

    等级写在哪个位置都可以；同一行出现多个等级字段时以最后一个为准
    （行尾/靠后的内容决定），因此 ``URL,主播: A,A`` 的主播名与等级都能正确区分。
    """
    level = None
    remaining = []
    for field in fields:
        if is_level_token(field):
            level = field.strip().upper()
        else:
            remaining.append(field)
    return level, remaining


def contains_url(string: str) -> bool:
    pattern = r"(https?://)?(www\.)?[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(:\d+)?(/.*)?"
    return re.search(pattern, string) is not None


def split_url_line(line: str, default_quality: str):
    """拆分行字段，返回 (quality, url, name, has_legacy_priority, level)。

    等级标记与旧的 `,优先: 是` 都在字段切分前被识别并剥离，避免标记里的逗号
    干扰字段对齐。字段顺序按"URL 在哪"定位：URL 之前的字段是画质，之后的字段
    是主播名——因此 ``URL,主播: X,A`` 这类没有画质前缀的行也能正确解析。

    - level: 'A'/'B'/'C'，未标注为 None（由调用方按默认级 C 处理）
    - has_legacy_priority: 是否出现过废弃的 `,优先: 是`（调用方负责迁移成 ,A）
    - 字段数多于"画质+URL+主播名"时（如主播名里带逗号），多余字段会并入主播名，
      不再像旧实现那样因解包失败抛 ValueError 打断整轮配置解析
    """
    has_legacy_priority = LEGACY_PRIORITY_MARK in line
    if has_legacy_priority:
        line = line.replace(LEGACY_PRIORITY_MARK, '')

    if re.search('[,，]', line):
        fields = re.split('[,，]', line)
    else:
        fields = [line]

    level, fields = extract_level(fields)
    url_index = next((i for i, field in enumerate(fields) if contains_url(field)), None)

    if url_index is None:
        # 没有任何字段像 URL: 退化为"第一段画质, 第二段 url"的旧解释方式
        quality, url = (list(fields) + ['', ''])[:2]
        name = ''
    else:
        quality = fields[url_index - 1] if url_index > 0 else default_quality
        url = fields[url_index]
        name = ','.join(f.strip() for f in fields[url_index + 1:] if f.strip())

    quality = quality.strip()
    if quality not in QUALITY_NAMES:
        quality = default_quality
    return quality, url.strip(), name, has_legacy_priority, level


def dedup_marker_action(has_marker: bool, is_comment: bool,
                        kept_is_comment: bool, kept_has_marker: bool):
    """URL 去重时等级标记的处理动作（沿用旧优先标记的语义）。

    注释行上的标记是「暂停标注」：永远保留（不因去重丢失），但绝不激活。
    生效行上的标记才决定该链接的检查间隔等级。

    返回：
      'merge'                   —— 把标记合并进保留行（保留注释标注，或生效行去重保护）
      'keep_comment'            —— 生效行 + 带标记注释行：保留注释标注，不合并、不激活
      'keep_comment_and_active'  —— 带标记注释行 + 无标记生效行：两者都保留，不激活
      None                      —— 无标记相关处理（走常规去重/解开逻辑）
    """
    if has_marker and not kept_has_marker:
        if kept_is_comment or not is_comment:
            return 'merge'
        return 'keep_comment'
    if kept_has_marker and kept_is_comment and not is_comment and not has_marker:
        return 'keep_comment_and_active'
    return None


def find_writeback_index(lines, url):
    """主播名写回时定位目标行：优先命中非注释行，无则回退注释行。

    同一 URL 可能存在「生效行 + 注释标注行」两行布局，写回主播名
    必须落在生效行上，否则会持续改写注释标注（文件churn）。
    """
    fallback = None
    for i, line in enumerate(lines):
        if url in line:
            if line.lstrip().startswith('#'):
                if fallback is None:
                    fallback = i
            else:
                return i
    return fallback


def resolve_check_interval(level, intervals: dict, default_level: str = LEVEL_DEFAULT) -> int:
    """等级 → 轮询检查间隔(秒)，结果至少 1 秒。

    等级非法/缺失回退默认级；默认级也没配到有效值时回退 1 秒，保证不会 0 秒空转。
    """
    chosen = normalize_level(level, default_level)
    seconds = intervals.get(chosen)
    if not seconds:
        seconds = intervals.get(default_level)
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 1
    return max(1, seconds)


def resolve_jitter(level, default_level: str = LEVEL_DEFAULT) -> int:
    """等级 → 轮询抖动幅度(秒)，非法/缺失等级按默认级处理。"""
    return LEVEL_JITTER.get(normalize_level(level, default_level), LEVEL_JITTER[default_level])


def level_mark(level) -> str:
    """等级 → 行内标记文本(用于迁移与去重合并时的写回)。"""
    return f',{normalize_level(level)}'

