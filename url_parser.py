"""URL_config.ini 行解析：优先监控标记与字段拆分。"""

import re

PRIORITY_MARK = ',优先: 是'


def is_priority(line: str) -> bool:
    """行是否标记为优先监控（调用方保证传入非注释行）。"""
    return PRIORITY_MARK in line


def strip_priority(line: str) -> str:
    """移除行内的优先标记，返回干净的字段文本。"""
    return line.replace(PRIORITY_MARK, '')


def contains_url(string: str) -> bool:
    pattern = r"(https?://)?(www\.)?[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(:\d+)?(/.*)?"
    return re.search(pattern, string) is not None


def split_url_line(line: str, default_quality: str):
    """拆分行字段，返回 (quality, url, name, has_priority)。

    优先标记在字段拆分前被检测并剥离，避免 `,优先: 是` 中的逗号
    干扰 ``re.split('[,，]', line)`` 的字段对齐。
    """
    has_priority = is_priority(line)
    line = strip_priority(line)
    if re.search('[,，]', line):
        split_line = re.split('[,，]', line)
    else:
        split_line = [line, '']

    if len(split_line) == 1:
        url = split_line[0]
        quality, name = default_quality, ''
    elif len(split_line) == 2:
        if contains_url(split_line[0]):
            quality = default_quality
            url, name = split_line
        else:
            quality, url = split_line
            name = ''
    else:
        quality, url, name = split_line

    if quality not in ("原画", "蓝光", "超清", "高清", "标清", "流畅"):
        quality = '原画'
    return quality, url, name, has_priority


def dedup_priority_action(has_priority: bool, is_comment: bool,
                          kept_is_comment: bool, kept_is_priority: bool):
    """URL 去重时优先标记的处理动作。

    注释行上的标记是「暂停标注」：永远保留（不因去重丢失），但绝不激活。
    生效行上的标记才决定是否优先轮询。

    返回：
      'merge'                 —— 把标记合并进保留行（保留注释标注，或生效行去重保护）
      'keep_comment'          —— 生效行 + 带标记注释行：保留注释标注，不合并、不激活
      'keep_comment_and_active' —— 带标记注释行 + 无标记生效行：两者都保留，不激活
      None                    —— 无标记相关处理（走常规去重/解开逻辑）
    """
    if has_priority and not kept_is_priority:
        if kept_is_comment or not is_comment:
            return 'merge'
        return 'keep_comment'
    if kept_is_priority and kept_is_comment and not is_comment and not has_priority:
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
