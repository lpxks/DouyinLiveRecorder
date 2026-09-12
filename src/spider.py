# -*- encoding: utf-8 -*-

"""
Author: Hmily
GitHub: https://github.com/ihmily
Date: 2023-07-15 23:15:00
Update: 2025-10-23 18:28:00
Copyright (c) 2023-2025 by Hmily, All Rights Reserved.
Function: Get live stream data.
"""

import urllib.parse
import urllib.error
import httpx
import re
import json
from .utils import logger, trace_error_decorator
from .http_clients.async_http import async_req
import streamget


OptionalStr = str | None


def get_params(url: str, params: str) -> OptionalStr:
    parsed_url = urllib.parse.urlparse(url)
    query_params = urllib.parse.parse_qs(parsed_url.query)

    if params in query_params:
        return query_params[params][0]


def _stream_data_to_dict(sd: streamget.StreamData) -> dict:
    """StreamData → 风格B归一化dict(main.py 消费键契约)。

    StreamData 字段与 streamget 各平台 fetch_stream_url 返回值一一对应,
    失败兜底返回 {"anchor_name": "", "is_live": False} 触发 main.py 重试路径。
    """
    return {
        "anchor_name": sd.anchor_name,
        "is_live": bool(sd.is_live),
        "title": sd.title,
        "quality": sd.quality,
        "m3u8_url": sd.m3u8_url,
        "flv_url": sd.flv_url,
        "record_url": sd.record_url,
        "new_cookies": sd.new_cookies,
        "new_token": sd.new_token,
        "live_url": sd.live_url,
        "extra": sd.extra,
    }
async def get_douyin_web_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.DouyinLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_douyin_web_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_douyin_app_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.DouyinLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_app_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception:
        # app 接口对纯数字房间 URL 无 sec_user_id 可解析, 降级走 web 接口(与原实现行为一致)
        try:
            live_stream = streamget.DouyinLiveStream(proxy_addr=proxy_addr, cookies=cookies)
            json_data = await live_stream.fetch_web_stream_data(url)
            return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
        except Exception as e:
            logger.error(f"get_douyin_app_stream_data failed: {url}, {type(e).__name__}: {e}")
            return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_tiktok_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.TikTokLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_tiktok_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_kuaishou_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.KwaiLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_kuaishou_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_huya_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.HuyaLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_huya_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_huya_app_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.HuyaLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_app_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_huya_app_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}
@trace_error_decorator
async def get_douyu_info_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.DouyuLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_douyu_info_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_yy_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.YYLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_yy_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_bilibili_room_info(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.BilibiliLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_bilibili_room_info failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_bilibili_stream_data(url, qn='10000', platform='web', proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.BilibiliLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_bilibili_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

def _xhs_anchor_from_html(html_str: str) -> str:
    """从小红书直播间页面 __INITIAL_STATE__ 中取主播名(roomData.hostInfo.nickName)。

    已下播/未开播的直播间同样带 hostInfo, 但 streamget 只在直播中
    (liveStatus == success)或用户主页可解析时才给出主播名; 取不到主播名会让
    main.py 把"已下播"误判成"网址内容获取失败"而无限重试, 因此这里兜底解析。
    解析失败(页面异常/风控拦截)返回 ''。
    """
    match_data = re.search("<script>window.__INITIAL_STATE__=(.*?)</script>", html_str or '')
    if not match_data:
        return ''
    try:
        state = json.loads(match_data.group(1).replace("undefined", "null"))
    except ValueError:
        return ''
    live_stream = (state or {}).get("liveStream") or {}
    host_info = ((live_stream.get("roomData") or {}).get("hostInfo")) or {}
    return str(host_info.get("nickName") or '').strip()


async def _xhs_page_anchor_name(live_stream, json_data: dict) -> str:
    """兜底获取小红书主播名: 直接解析直播间页面(已下播也能拿到)。

    取不到(链接彻底失效/页面异常)返回 '', 保持 main.py 的"获取失败"重试语义。
    """
    page_url = (json_data or {}).get('live_url') or ''
    if not page_url:
        return ''
    try:
        html_str = await async_req(page_url, proxy_addr=live_stream.proxy_addr,
                                   headers=live_stream.mobile_headers)
    except Exception as e:
        logger.error(f"小红书直播间页面获取失败: {page_url}, {type(e).__name__}: {e}")
        return ''
    return _xhs_anchor_from_html(html_str)


@trace_error_decorator
async def get_xhs_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.RedNoteLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_app_stream_data(url)
        result = _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
        if not result.get("anchor_name"):
            # 已下播/未开播的小红书直播间(一次性链接的常见状态)拿不到主播名时,
            # main.py 会走"网址内容获取失败"重试路径, 永远不会判定为"不在直播"
            result["anchor_name"] = await _xhs_page_anchor_name(live_stream, json_data)
        return result
    except Exception as e:
        logger.error(f"get_xhs_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_bigo_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.BigoLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_bigo_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_blued_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.BluedLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_blued_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_sooplive_stream_data(url, proxy_addr=None, cookies=None, username=None, password=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.SoopLiveStream(proxy_addr=proxy_addr, cookies=cookies, username=username, password=password)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_sooplive_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_netease_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.NeteaseLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_netease_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_qiandurebo_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.QiandureboLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_qiandurebo_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_pandatv_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.PandaLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_pandatv_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_maoerfm_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.MaoerLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_maoerfm_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_winktv_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.WinkTVLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_winktv_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_flextv_stream_data(url, proxy_addr=None, cookies=None, username=None, password=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.FlexTVLiveStream(proxy_addr=proxy_addr, cookies=cookies, username=username, password=password)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_flextv_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}
async def get_looklive_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.LookLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_looklive_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_popkontv_stream_url(url, proxy_addr=None, access_token=None, username=None, password=None, partner_code='P-00001', video_quality=None) -> dict:
    try:
        live_stream = streamget.PopkonTVLiveStream(proxy_addr=proxy_addr, username=username, password=password, access_token=access_token, partner_code=partner_code or 'P-00001')
        old_token = live_stream.access_token
        json_data = await live_stream.fetch_web_stream_data(url)
        stream_data = await live_stream.fetch_stream_url(json_data, video_quality)
        result = _stream_data_to_dict(stream_data)
        # new_token 补偿:streamget 登录后 token 存 self.access_token, 不进返回 dict
        if live_stream.access_token and live_stream.access_token != old_token:
            result['new_token'] = f'Bearer {live_stream.access_token}'
        return result
    except Exception as e:
        logger.error(f"get_popkontv_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_twitcasting_stream_url(url, proxy_addr=None, cookies=None, account_type=None, username=None, password=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.TwitCastingLiveStream(proxy_addr=proxy_addr, cookies=cookies, username=username, password=password, account_type=account_type)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_twitcasting_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_baidu_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.BaiduLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_baidu_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_weibo_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.WeiboLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_weibo_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_kugou_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.KugouLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_kugou_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}
@trace_error_decorator
async def get_twitchtv_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.TwitchLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_twitchtv_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_liveme_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.LiveMeLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_liveme_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}
@trace_error_decorator
async def get_huajiao_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.HuajiaoLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_huajiao_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_liuxing_stream_url(url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> dict:
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'Referer': 'https://wap.7u66.com/198189?promoters=0',
        'User-Agent': 'ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))',

    }
    if cookies:
        headers['Cookie'] = cookies

    room_id = url.split('?')[0].rsplit('/', maxsplit=1)[1]
    params = {
        "promoters": "0",
        "roomidx": room_id,
        "currentUrl": f"https://www.7u66.com/{room_id}?promoters=0"
    }
    api = f'https://wap.7u66.com/api/ui/room/v1.0.0/live.ashx?{urllib.parse.urlencode(params)}'
    json_str = await async_req(url=api, proxy_addr=proxy_addr, headers=headers)
    json_data = json.loads(json_str)
    room_info = json_data['data']['roomInfo']
    anchor_name = room_info['nickname']
    live_status = room_info["live_stat"]
    result = {"anchor_name": anchor_name, "is_live": False}
    if live_status == 1:
        idx = room_info['idx']
        live_id = room_info['liveId1']
        flv_url = f'https://txpull1.5see.com/live/{idx}/{live_id}.flv'
        result |= {'is_live': True, 'flv_url': flv_url, 'record_url': flv_url}
    return result


@trace_error_decorator
async def get_showroom_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.ShowRoomLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_showroom_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_acfun_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.AcfunLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_acfun_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_changliao_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.ChangliaoLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_changliao_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_yingke_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.InkeLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_yingke_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_yinbo_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.YinboLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_yinbo_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_zhihu_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.ZhihuLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_zhihu_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_chzzk_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.ChzzkLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_chzzk_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_haixiu_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.HaixiuLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_haixiu_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_vvxqiu_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.VVXQLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_vvxqiu_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_17live_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.YiqiLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_17live_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_langlive_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.LangLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_langlive_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_pplive_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.PiaopaioLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_pplive_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_6room_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.SixRoomLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_6room_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_shopee_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.ShopeeLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_app_stream_data(url)
        stream_data = await live_stream.fetch_stream_url(json_data, video_quality)
        result = _stream_data_to_dict(stream_data)
        # uid 补偿:main.py 用它改写直播间 URL(与旧实现格式一致), 取不到则降级不注入
        try:
            session = json_data['data']['play_param_list'][0]['session']
            uid = session.get('uid') or session.get('username')
            session_id = session.get('session_id') or get_params(url, 'session')
            if uid and session_id:
                result['uid'] = f'uid={uid}&session={session_id}'
        except Exception:
            pass
        return result
    except Exception as e:
        logger.error(f"get_shopee_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_youtube_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.YoutubeLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_youtube_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_taobao_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.TaobaoLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_taobao_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_jd_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.JDLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_jd_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_faceit_stream_data(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.FaceitLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_faceit_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_migu_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.MiguLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_migu_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_lianjie_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.LianJieLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_lianjie_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_laixiu_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.LaixiuLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_laixiu_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def get_picarto_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.PicartoLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_picarto_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

