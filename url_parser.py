"""URL_config.ini 行内优先监控标记的解析。"""

PRIORITY_MARK = ',优先: 是'


def is_priority(line: str) -> bool:
    """行是否标记为优先监控（调用方保证传入非注释行）。"""
    return PRIORITY_MARK in line
