"""URL_config.ini 行解析：检查间隔标记与字段拆分。

行格式（间隔标记可写在任意位置，推荐放在主播名之后）::

    https://live.douyin.com/277869507858,主播: MMA盼盼,A    # 字母: 时长在 config 里配
    https://live.douyin.com/277869507858,主播: MMA盼盼,45   # 直接写秒数

字母时长在 config.ini 的 `[检查时长等级]` 段配置(`<字母>等级检查时长(秒)`)，默认
A=5 / B=30 / C=90；字母可以自行扩展(不限于 A/B/C)，段里多一个字母键就多一个可用等级。
行内也可以直接写秒数(允许范围见 MIN/MAX_INTERVAL_SECONDS)。未标标记的链接按默认级 C 轮询。

旧的 `,优先: 是` 标记已废弃，解析时会被识别（has_legacy_priority）并由调用方
一次性迁移为 `,A`，避免老配置静默降级。
"""

import re

# 内置默认字母(配置模板会给这三行)；字母可自行扩展
BUILTIN_LEVELS = ('A', 'B', 'C')
LEVEL_DEFAULT = 'C'

# 旧版优先监控标记：仅用于兼容迁移成 A 等级，新配置不应再使用
LEGACY_PRIORITY_MARK = ',优先: 是'
LEGACY_PRIORITY_LEVEL = 'A'

# 抖动(秒): 字母按 LEVEL_JITTER(未配置的字母按默认级)；行内秒数按大小分档
LEVEL_JITTER = {'A': 1, 'B': 5, 'C': 5}
JITTER_SMALL_INTERVAL = 5   # 间隔不超过该值时用 JITTER_SMALL
JITTER_SMALL = 1
JITTER_DEFAULT = 5

# 行内直接写秒数的合法范围(秒): 更小容易被平台限制, 过大等于放弃检查
MIN_INTERVAL_SECONDS = 1
MAX_INTERVAL_SECONDS = 86400

_SPEC_LETTER_RE = re.compile(r'^[A-Za-z]$')
_SPEC_NUMBER_RE = re.compile(r'^\d+$')

QUALITY_NAMES = ("原画", "蓝光", "超清", "高清", "标清", "流畅")


def is_interval_spec(field) -> bool:
    """字段本身是否就是一个检查间隔标记：单个字母 或 纯数字秒数。"""
    text = field.strip() if isinstance(field, str) else ''
    return bool(_SPEC_LETTER_RE.match(text) or _SPEC_NUMBER_RE.match(text))


def normalize_spec(spec):
    """归一化间隔标记：字母转大写、数字去掉前导零；非法/缺失返回 None。"""
    text = spec.strip() if isinstance(spec, str) else ''
    if _SPEC_LETTER_RE.match(text):
        return text.upper()
    if _SPEC_NUMBER_RE.match(text):
        return str(int(text))   # 007 -> 7
    return None


def is_letter_spec(spec) -> bool:
    """归一化后是否为字母标记(需要去配置里查该字母的时长)。"""
    normalized = normalize_spec(spec)
    return bool(normalized and _SPEC_LETTER_RE.match(normalized))


def is_number_spec(spec) -> bool:
    """归一化后是否为行内秒数标记。"""
    normalized = normalize_spec(spec)
    return bool(normalized and _SPEC_NUMBER_RE.match(normalized))


def is_valid_number_spec(spec) -> bool:
    """行内秒数标记是否在允许范围内。"""
    if not is_number_spec(spec):
        return False
    return MIN_INTERVAL_SECONDS <= int(normalize_spec(spec)) <= MAX_INTERVAL_SECONDS


def extract_interval_spec(fields: list) -> tuple:
    """从字段列表里取出间隔标记，返回 (spec, 剩余字段)。

    标记写在哪个位置都可以；同一行出现多个标记时以最后一个为准（行尾/靠后的内容决定），
    因此 ``URL,主播: A,A`` / ``URL,30,B`` 都能把主播名与标记区分开。
    """
    spec = None
    remaining = []
    for field in fields:
        if is_interval_spec(field):
            spec = normalize_spec(field)    # 多个标记时最后一个生效
        else:
            remaining.append(field)
    return spec, remaining


def contains_url(string: str) -> bool:
    pattern = r"(https?://)?(www\.)?[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(:\d+)?(/.*)?"
    return re.search(pattern, string) is not None


def split_url_line(line: str, default_quality: str):
    """拆分行字段，返回 (quality, url, name, has_legacy_priority, spec)。

    间隔标记(字母或秒数)与旧的 `,优先: 是` 都在字段切分前被识别并剥离，避免标记里的
    逗号干扰字段对齐。字段顺序按"URL 在哪"定位：URL 之前的字段是画质，之后的字段是
    主播名——因此 ``URL,主播: X,45`` 这类没有画质前缀的行也能正确解析。

    - spec: 'A'/'B'/'C'/'D' 等字母, 或 '45' 这类行内秒数; 未标注为 None(按默认级处理)
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

    spec, fields = extract_interval_spec(fields)
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
    return quality, url.strip(), name, has_legacy_priority, spec


def dedup_marker_action(has_marker: bool, is_comment: bool,
                        kept_is_comment: bool, kept_has_marker: bool):
    """URL 去重时间隔标记的处理动作。

    注释行上的标记是「暂停标注」：永远保留（不因去重丢失），但绝不激活。
    生效行上的标记才决定该链接的检查间隔。

    返回：
      'merge'                   —— 把标记合并进保留行（保留注释标注，或生效行去重保护）
      'keep_comment'            —— 生效行 + 带标记注释行：保留注释标注，不合并、不激活
      'keep_comment_and_active' —— 带标记注释行 + 无标记生效行：两者都保留，不激活
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


def _configured_seconds(intervals: dict, level, default_level: str = LEVEL_DEFAULT) -> int:
    """取某个字母在配置里的间隔(秒)；缺失/非法时回退默认级，再缺则回退下限。"""
    for key in (level, default_level):
        if not key:
            continue
        try:
            seconds = int(intervals.get(key))
        except (TypeError, ValueError):
            continue
        if seconds >= MIN_INTERVAL_SECONDS:
            return seconds
    return MIN_INTERVAL_SECONDS


def resolve_check_interval(spec, intervals: dict, default_level: str = LEVEL_DEFAULT) -> int:
    """间隔标记 → 轮询检查间隔(秒)，结果至少 MIN_INTERVAL_SECONDS 秒。

    - 行内秒数在允许范围内时直接生效；
    - 字母取配置里该字母的时长；
    - 行内秒数超范围 / 字母未配置 / 标记非法或缺失 → 回退默认级(默认 C)。
    """
    if is_valid_number_spec(spec):
        return int(normalize_spec(spec))
    if is_letter_spec(spec):
        return _configured_seconds(intervals, normalize_spec(spec), default_level)
    return _configured_seconds(intervals, None, default_level)


def resolve_jitter(spec, default_level: str = LEVEL_DEFAULT) -> int:
    """间隔标记 → 轮询抖动幅度(秒)。

    字母按 LEVEL_JITTER(未配置的字母按默认级)；行内秒数按大小分档：不超过
    JITTER_SMALL_INTERVAL 秒的用 ±JITTER_SMALL，其余用 ±JITTER_DEFAULT；
    超范围/非法标记按默认级处理。
    """
    if is_valid_number_spec(spec):
        seconds = int(normalize_spec(spec))
        return JITTER_SMALL if seconds <= JITTER_SMALL_INTERVAL else JITTER_DEFAULT
    normalized = normalize_spec(spec)
    level = normalized if (normalized and normalized in LEVEL_JITTER) else default_level
    return LEVEL_JITTER.get(level, JITTER_DEFAULT)


def interval_spec_warning(spec, intervals: dict, default_level: str = LEVEL_DEFAULT):
    """间隔标记的配置警告（没有问题返回 None）。

    未在 config 中配置的字母、超出允许范围的行内秒数都会回退默认级，这里给出提示文本，
    由调用方负责打印（同一标记只提示一次，避免每轮解析刷屏）。
    """
    if is_number_spec(spec):
        seconds = int(normalize_spec(spec))
        if not (MIN_INTERVAL_SECONDS <= seconds <= MAX_INTERVAL_SECONDS):
            return (f'行内间隔 {seconds} 秒超出 {MIN_INTERVAL_SECONDS}~{MAX_INTERVAL_SECONDS} 秒范围, '
                    f'已按默认级 {default_level} 处理')
        return None
    if is_letter_spec(spec):
        level = normalize_spec(spec)
        if not intervals.get(level):
            return (f'字母 {level} 未在 [检查时长等级] 段配置检查时长, '
                    f'已按默认级 {default_level} 处理')
        return None
    return None


def spec_mark(spec) -> str:
    """间隔标记 → 行内标记文本(用于迁移与去重合并时的写回)。"""
    normalized = normalize_spec(spec)
    return f',{normalized or LEVEL_DEFAULT}'
