"""断流重试间隔：前5次 4~5 秒随机，后5次 5~6 秒随机。"""

import random


def retry_delay(retry_index: int, rng=None) -> int:
    """返回第 retry_index 次（从 0 开始）重试前的等待秒数。

    前 5 次（0-4）在 4~5 秒间随机，后 5 次（5-9）在 5~6 秒间随机。
    """
    rng = rng or random
    if retry_index < 5:
        return rng.randint(4, 5)
    return rng.randint(5, 6)
