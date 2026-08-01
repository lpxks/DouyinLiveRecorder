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
