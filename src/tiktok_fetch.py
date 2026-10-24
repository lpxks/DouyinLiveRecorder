# -*- coding: utf-8 -*-
"""TikTok 采集策略: 移动端 API 优先 + 退避重试 + 网页回退。

背景
----
TikTok 对机房/共享 IP 的网页请求会返回 SlardarWAF 的 JS 挑战页(约 1.5 KB,
没有 SIGI_STATE)。streamget 的 fetch_web_stream_data 找不到 SIGI_STATE 时会抛
ConnectionError("Please check if your network can access the TikTok website
normally"), 把"被风控"误报成"网络不通"。

同一个代理下实测:
* 网页 /@user/live        -> 基本 100% 返回挑战页;
* 移动端 api-live/user/room -> 约 50% 返回 200 JSON(其余 429), 重试后即可稳定
  拿到房间数据, 返回的 anchor_name 与网页解析一致。

所以这里先走移动端 API, 失败按退避重试, 最后才回退到原来的网页解析。
本模块不依赖 streamget 类型, 只做流程编排, 便于单元测试。
"""

import asyncio

# 移动端 API 的尝试次数与退避秒数(第 1、2 次失败后的等待)
APP_API_ATTEMPTS = 3
APP_API_BACKOFF = (1.0, 2.0)


async def get_stream_data(live_stream, url, video_quality=None,
                            attempts: int = APP_API_ATTEMPTS,
                            backoff: tuple = APP_API_BACKOFF):
    """获取 TikTok 房间/流数据, 返回 streamget 的 StreamData。

    live_stream 需要提供 streamget TikTok 接口的三个方法:
    ``fetch_app_stream_data(url)`` / ``fetch_web_stream_data(url)`` /
    ``fetch_stream_url(json_data, video_quality)``。

    返回 ``fetch_stream_url`` 的结果(未开播时是 is_live=False 的 StreamData,
    属于有效结果, 不重试)。所有路径都失败时抛出最后一次网页回退的异常,
    由调用方统一兜底。
    """
    attempts = max(1, int(attempts))
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            json_data = await live_stream.fetch_app_stream_data(url)
        except Exception as e:  # 429 / 挑战页 HTML / JSON 解析失败等传输层问题
            last_error = e
        else:
            if json_data and json_data.get('data'):
                # 数据有效(含未开播): 直接解析; fetch_stream_url 的语义错误不重试
                return await live_stream.fetch_stream_url(json_data, video_quality)
            last_error = ValueError('TikTok app api returned no room data')

        if attempt + 1 < attempts and backoff:
            delay = backoff[min(attempt, len(backoff) - 1)]
            if delay:
                await asyncio.sleep(delay)

    # 移动端 API 全部失败: 回退到原来的网页解析(失败则抛出, 由调用方记录)
    json_data = await live_stream.fetch_web_stream_data(url)
    return await live_stream.fetch_stream_url(json_data, video_quality)
