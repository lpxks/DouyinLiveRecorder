# -*- encoding: utf-8 -*-

"""
Author: Hmily
GitHub: https://github.com/ihmily
Date: 2023-07-15 23:15:00
Update: 2025-10-23 18:28:00
Copyright (c) 2023-2025 by Hmily, All Rights Reserved.
Function: Get live stream data.
"""

import hashlib
import random
import subprocess
import time
import uuid
from operator import itemgetter
import urllib.parse
import urllib.error
from typing import List
import httpx
import ssl
import re
import json
import execjs
import urllib.request
from . import JS_SCRIPT_PATH, utils
from .utils import logger, trace_error_decorator, generate_random_string
from .logger import script_path
from .room import get_sec_user_id, get_unique_id, UnsupportedUrlError
from .http_clients.async_http import async_req
from .ab_sign import ab_sign
import streamget


ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE
OptionalStr = str | None
OptionalDict = dict | None


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


async def get_play_url_list(m3u8: str, proxy: OptionalStr = None, header: OptionalDict = None,
                            abroad: bool = False) -> List[str]:
    resp = await async_req(url=m3u8, proxy_addr=proxy, headers=header, abroad=abroad)
    play_url_list = []
    for i in resp.split('\n'):
        if i.startswith('https://'):
            play_url_list.append(i.strip())
    if not play_url_list:
        for i in resp.split('\n'):
            if i.strip().endswith('m3u8'):
                play_url_list.append(i.strip())
    bandwidth_pattern = re.compile(r'BANDWIDTH=(\d+)')
    bandwidth_list = bandwidth_pattern.findall(resp)
    url_to_bandwidth = {url: int(bandwidth) for bandwidth, url in zip(bandwidth_list, play_url_list)}
    play_url_list = sorted(play_url_list, key=lambda url: url_to_bandwidth[url], reverse=True)
    return play_url_list


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
async def get_douyin_stream_data(url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> dict:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Referer': 'https://live.douyin.com/',
        'Cookie': 'ttwid=1%7CB1qls3GdnZhUov9o2NxOMxxYS2ff6OSvEWbv0ytbES4%7C1680522049%7C280d802d6d478e3e78d0c807f7c487e7ffec0ae4e5fdd6a0fe74c3c6af149511; my_rd=1; passport_csrf_token=3ab34460fa656183fccfb904b16ff742; passport_csrf_token_default=3ab34460fa656183fccfb904b16ff742; d_ticket=9f562383ac0547d0b561904513229d76c9c21; n_mh=hvnJEQ4Q5eiH74-84kTFUyv4VK8xtSrpRZG1AhCeFNI; store-region=cn-fj; store-region-src=uid; LOGIN_STATUS=1; __security_server_data_status=1; FORCE_LOGIN=%7B%22videoConsumedRemainSeconds%22%3A180%7D; pwa2=%223%7C0%7C3%7C0%22; download_guide=%223%2F20230729%2F0%22; volume_info=%7B%22isUserMute%22%3Afalse%2C%22isMute%22%3Afalse%2C%22volume%22%3A0.6%7D; strategyABtestKey=%221690824679.923%22; stream_recommend_feed_params=%22%7B%5C%22cookie_enabled%5C%22%3Atrue%2C%5C%22screen_width%5C%22%3A1536%2C%5C%22screen_height%5C%22%3A864%2C%5C%22browser_online%5C%22%3Atrue%2C%5C%22cpu_core_num%5C%22%3A8%2C%5C%22device_memory%5C%22%3A8%2C%5C%22downlink%5C%22%3A10%2C%5C%22effective_type%5C%22%3A%5C%224g%5C%22%2C%5C%22round_trip_time%5C%22%3A150%7D%22; VIDEO_FILTER_MEMO_SELECT=%7B%22expireTime%22%3A1691443863751%2C%22type%22%3Anull%7D; home_can_add_dy_2_desktop=%221%22; __live_version__=%221.1.1.2169%22; device_web_cpu_core=8; device_web_memory_size=8; xgplayer_user_id=346045893336; csrf_session_id=2e00356b5cd8544d17a0e66484946f28; odin_tt=724eb4dd23bc6ffaed9a1571ac4c757ef597768a70c75fef695b95845b7ffcd8b1524278c2ac31c2587996d058e03414595f0a4e856c53bd0d5e5f56dc6d82e24004dc77773e6b83ced6f80f1bb70627; __ac_nonce=064caded4009deafd8b89; __ac_signature=_02B4Z6wo00f01HLUuwwAAIDBh6tRkVLvBQBy9L-AAHiHf7; ttcid=2e9619ebbb8449eaa3d5a42d8ce88ec835; webcast_leading_last_show_time=1691016922379; webcast_leading_total_show_times=1; webcast_local_quality=sd; live_can_add_dy_2_desktop=%221%22; msToken=1JDHnVPw_9yTvzIrwb7cQj8dCMNOoesXbA_IooV8cezcOdpe4pzusZE7NB7tZn9TBXPr0ylxmv-KMs5rqbNUBHP4P7VBFUu0ZAht_BEylqrLpzgt3y5ne_38hXDOX8o=; msToken=jV_yeN1IQKUd9PlNtpL7k5vthGKcHo0dEh_QPUQhr8G3cuYv-Jbb4NnIxGDmhVOkZOCSihNpA2kvYtHiTW25XNNX_yrsv5FN8O6zm3qmCIXcEe0LywLn7oBO2gITEeg=; tt_scid=mYfqpfbDjqXrIGJuQ7q-DlQJfUSG51qG.KUdzztuGP83OjuVLXnQHjsz-BRHRJu4e986'
    }
    if cookies:
        headers['Cookie'] = cookies

    try:
        origin_url_list = None
        html_str = await async_req(url=url, proxy_addr=proxy_addr, headers=headers)
        match_json_str = re.search(r'(\{\\"state\\":.*?)]\\n"]\)', html_str)
        if not match_json_str:
            match_json_str = re.search(r'(\{\\"common\\":.*?)]\\n"]\)</script><div hidden', html_str)
        json_str = match_json_str.group(1)
        cleaned_string = json_str.replace('\\', '').replace(r'u0026', r'&')
        room_store = re.search('"roomStore":(.*?),"linkmicStore"', cleaned_string, re.DOTALL).group(1)
        anchor_name = re.search('"nickname":"(.*?)","avatar_thumb', room_store, re.DOTALL).group(1)
        room_store = room_store.split(',"has_commerce_goods"')[0] + '}}}'
        json_data = json.loads(room_store)['roomInfo']['room']
        json_data['anchor_name'] = anchor_name
        if 'status' in json_data and json_data['status'] == 4:
            return json_data
        stream_orientation = json_data['stream_url']['stream_orientation']
        match_json_str2 = re.findall(r'"(\{\\"common\\":.*?)"]\)</script><script nonce=', html_str)
        if match_json_str2:
            json_str = match_json_str2[0] if stream_orientation == 1 else match_json_str2[1]
            json_data2 = json.loads(
                json_str.replace('\\', '').replace('"{', '{').replace('}"', '}').replace('u0026', '&'))
            if 'origin' in json_data2['data']:
                origin_url_list = json_data2['data']['origin']['main']

        else:
            html_str = html_str.replace('\\', '').replace('u0026', '&')
            match_json_str3 = re.search('"origin":\\{"main":(.*?),"dash"', html_str, re.DOTALL)
            if match_json_str3:
                origin_url_list = json.loads(match_json_str3.group(1) + '}')

        if origin_url_list:
            origin_hls_codec = origin_url_list['sdk_params'].get('VCodec') or ''
            origin_m3u8 = {'ORIGIN': origin_url_list["hls"] + '&codec=' + origin_hls_codec}
            origin_flv = {'ORIGIN': origin_url_list["flv"] + '&codec=' + origin_hls_codec}
            hls_pull_url_map = json_data['stream_url']['hls_pull_url_map']
            flv_pull_url = json_data['stream_url']['flv_pull_url']
            json_data['stream_url']['hls_pull_url_map'] = {**origin_m3u8, **hls_pull_url_map}
            json_data['stream_url']['flv_pull_url'] = {**origin_flv, **flv_pull_url}
        return json_data

    except Exception as e:
        print(f"First data retrieval failed: {url} Preparing to switch parsing methods due to {e}")
        return await get_douyin_app_stream_data(url=url, proxy_addr=proxy_addr, cookies=cookies)


@trace_error_decorator
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
async def get_kuaishou_stream_data2(url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> dict | None:
    headers = {
        'User-Agent': 'ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Referer': "https://www.kuaishou.com/short-video/3x224rwabjmuc9y?fid=1712760877&cc=share_copylink&followRefer=151&shareMethod=TOKEN&docId=9&kpn=KUAISHOU&subBiz=BROWSE_SLIDE_PHOTO&photoId=3x224rwabjmuc9y&shareId=17144298796566&shareToken=X-6FTMeYTsY97qYL&shareResourceType=PHOTO_OTHER&userId=3xtnuitaz2982eg&shareType=1&et=1_i/2000048330179867715_h3052&shareMode=APP&originShareId=17144298796566&appType=21&shareObjectId=5230086626478274600&shareUrlOpened=0&timestamp=1663833792288&utm_source=app_share&utm_medium=app_share&utm_campaign=app_share&location=app_share",
        'content-type': 'application/json',
        'Cookie': 'did=web_e988652e11b545469633396abe85a89f; didv=1796004001000',
    }
    if cookies:
        headers['Cookie'] = cookies
    try:
        eid = url.split('/u/')[1].strip()
        data = {"source": 5, "eid": eid, "shareMethod": "card", "clientType": "WEB_OUTSIDE_SHARE_H5"}
        app_api = 'https://livev.m.chenzhongtech.com/rest/k/live/byUser?kpn=GAME_ZONE&captchaToken='
        json_str = await async_req(url=app_api, proxy_addr=proxy_addr, headers=headers, data=data)
        json_data = json.loads(json_str)
        live_stream = json_data['liveStream']
        anchor_name = live_stream['user']['user_name']
        result = {
            "type": 2,
            "anchor_name": anchor_name,
            "is_live": False,
        }
        live_status = live_stream['living']
        if live_status:
            result['is_live'] = True
            backup_m3u8_url = live_stream['hlsPlayUrl']
            backup_flv_url = live_stream['playUrls'][0]['url']
            if 'multiResolutionHlsPlayUrls' in live_stream:
                m3u8_url_list = live_stream['multiResolutionHlsPlayUrls'][0]['urls']
                result['m3u8_url_list'] = m3u8_url_list
            if 'multiResolutionPlayUrls' in live_stream:
                flv_url_list = live_stream['multiResolutionPlayUrls'][0]['urls']
                result['flv_url_list'] = flv_url_list
            result['backup'] = {'m3u8_url': backup_m3u8_url, 'flv_url': backup_flv_url}
        if result['anchor_name']:
            return result
    except Exception as e:
        print(f"{e}, Failed URL: {url}, preparing to switch to a backup plan for re-parsing.")
    return await get_kuaishou_stream_data(url, cookies=cookies, proxy_addr=proxy_addr)


@trace_error_decorator
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

def md5(data) -> str:
    return hashlib.md5(data.encode('utf-8')).hexdigest()


async def get_token_js(rid: str, did: str, proxy_addr: OptionalStr = None) -> List[str]:

    url = f'https://www.douyu.com/{rid}'
    html_str = await async_req(url=url, proxy_addr=proxy_addr)
    result = re.search(r'(vdwdae325w_64we[\s\S]*function ub98484234[\s\S]*?)function', html_str).group(1)
    func_ub9 = re.sub(r'eval.*?;}', 'strc;}', result)
    js = execjs.compile(func_ub9)
    res = js.call('ub98484234')

    t10 = str(int(time.time()))
    v = re.search(r'v=(\d+)', res).group(1)
    rb = md5(rid + did + t10 + v)

    func_sign = re.sub(r'return rt;}\);?', 'return rt;}', res)
    func_sign = func_sign.replace('(function (', 'function sign(')
    func_sign = func_sign.replace('CryptoJS.MD5(cb).toString()', '"' + rb + '"')

    js = execjs.compile(func_sign)
    params = js.call('sign', rid, did, t10)
    params_list = re.findall('=(.*?)(?=&|$)', params)
    return params_list


@trace_error_decorator
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
async def get_douyu_stream_data(rid: str, rate: str = '-1', proxy_addr: OptionalStr = None,
                          cookies: OptionalStr = None) -> dict:
    did = '10000000000000000000000000003306'
    params_list = await get_token_js(rid, did, proxy_addr=proxy_addr)
    headers = {
        'User-Agent': 'ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))',
        'Referer': 'https://m.douyu.com/3125893?rid=3125893&dyshid=0-96003918aa5365bc6dcb4933000316p1&dyshci=181',
        'Cookie': 'dy_did=413b835d2ae00270f0c69f6400031601; acf_did=413b835d2ae00270f0c69f6400031601; Hm_lvt_e99aee90ec1b2106afe7ec3b199020a7=1692068308,1694003758; m_did=96003918aa5365bc6dcb4933000316p1; dy_teen_mode=%7B%22uid%22%3A%22472647365%22%2C%22status%22%3A0%2C%22birthday%22%3A%22%22%2C%22password%22%3A%22%22%7D; PHPSESSID=td59qi2fu2gepngb8mlehbeme3; acf_auth=94fc9s%2FeNj%2BKlpU%2Br8tZC3Jo9sZ0wz9ClcHQ1akL2Nhb6ZyCmfjVWSlR3LFFPuePWHRAMo0dt9vPSCoezkFPOeNy4mYcdVOM1a8CbW0ZAee4ipyNB%2Bflr58; dy_auth=bec5yzM8bUFYe%2FnVAjmUAljyrsX%2FcwRW%2FyMHaoArYb5qi8FS9tWR%2B96iCzSnmAryLOjB3Qbeu%2BBD42clnI7CR9vNAo9mva5HyyL41HGsbksx1tEYFOEwxSI; wan_auth37wan=5fd69ed5b27fGM%2FGoswWwDo%2BL%2FRMtnEa4Ix9a%2FsH26qF0sR4iddKMqfnPIhgfHZUqkAk%2FA1d8TX%2B6F7SNp7l6buIxAVf3t9YxmSso8bvHY0%2Fa6RUiv8; acf_uid=472647365; acf_username=472647365; acf_nickname=%E7%94%A8%E6%88%B776576662; acf_own_room=0; acf_groupid=1; acf_phonestatus=1; acf_avatar=https%3A%2F%2Fapic.douyucdn.cn%2Fupload%2Favatar%2Fdefault%2F24_; acf_ct=0; acf_ltkid=25305099; acf_biz=1; acf_stk=90754f8ed18f0c24; Hm_lpvt_e99aee90ec1b2106afe7ec3b199020a7=1694003778'
    }
    if cookies:
        headers['Cookie'] = cookies

    data = {
        'v': params_list[0],
        'did': params_list[1],
        'tt': params_list[2],
        'sign': params_list[3],  # 10分钟有效期
        'ver': '22011191',
        'rid': rid,
        'rate': rate,  # 0蓝光、3超清、2高清、-1默认
    }

    # app_api = 'https://m.douyu.com/hgapi/livenc/room/getStreamUrl'
    app_api = f'https://www.douyu.com/lapi/live/getH5Play/{rid}'
    json_str = await async_req(url=app_api, proxy_addr=proxy_addr, headers=headers, data=data)
    json_data = json.loads(json_str)
    return json_data


@trace_error_decorator
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
async def get_bilibili_room_info_h5(url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> str:
    headers = {
        'user-agent': 'Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 (KHTML, like Gecko) '
                      'SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile Safari/537.36',
        'accept-language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'cookie': '',
        'origin': 'https://live.bilibili.com',
        'referer': 'https://live.bilibili.com/26066074',
    }
    if cookies:
        headers['cookie'] = cookies

    room_id = url.split('?')[0].rsplit('/', maxsplit=1)[1]
    api = f'https://api.live.bilibili.com/xlive/web-room/v1/index/getH5InfoByRoom?room_id={room_id}'
    json_str = await async_req(api, proxy_addr=proxy_addr, headers=headers)
    room_info = json.loads(json_str)
    title = room_info['data']['room_info'].get('title') if room_info.get('data') else ''
    return title


@trace_error_decorator
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

@trace_error_decorator
async def get_xhs_stream_url(url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> dict:
    headers = {
        'User-Agent': 'ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))',
        'xy-common-params': 'platform=iOS&sid=session.1722166379345546829388',
        'referer': 'https://app.xhs.cn/',
    }
    if cookies:
        headers['Cookie'] = cookies

    if "xhslink.com" in url:
        url = await async_req(url, proxy_addr=proxy_addr, headers=headers, redirect_url=True)

    host_id = get_params(url, "host_id")
    user_id = re.search("/user/profile/(.*?)(?=/|\\?|$)", url)
    user_id = user_id.group(1) if user_id else host_id
    result = {"anchor_name": '', "is_live": False}
    html_str = await async_req(url, proxy_addr=proxy_addr, headers=headers)
    match_data = re.search("<script>window.__INITIAL_STATE__=(.*?)</script>", html_str)

    if match_data:
        json_str = match_data.group(1).replace("undefined", "null")
        json_data = json.loads(json_str)

        if json_data.get("liveStream"):
            stream_data = json_data["liveStream"]
            if stream_data.get("liveStatus") == "success":
                room_info = stream_data["roomData"]["roomInfo"]
                title = room_info.get("roomTitle")
                if title and "回放" not in title:
                    live_link = room_info["deeplink"]
                    anchor_name = get_params(live_link, "host_nickname")
                    flv_url = get_params(live_link, "flvUrl")
                    room_id = flv_url.split('live/')[1].split('.')[0]
                    flv_url = f"http://live-source-play.xhscdn.com/live/{room_id}.flv"
                    m3u8_url = flv_url.replace('.flv', '.m3u8')
                    result |= {
                        "anchor_name": anchor_name,
                        "is_live": True,
                        "title": title,
                        "flv_url": flv_url,
                        "m3u8_url": m3u8_url,
                        'record_url': flv_url
                    }
                    return result

    profile_url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
    html_str = await async_req(profile_url, proxy_addr=proxy_addr, headers=headers)
    anchor_name = re.search("<title>@(.*?) 的个人主页</title>", html_str)
    if anchor_name:
        result["anchor_name"] = anchor_name.group(1)

    return result


@trace_error_decorator
@trace_error_decorator
async def get_bigo_stream_url(url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> dict:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Referer': 'https://www.bigo.tv/',
    }
    if cookies:
        headers['Cookie'] = cookies

    if 'bigo.tv' not in url:
        html_str = await async_req(url, proxy_addr=proxy_addr, headers=headers)
        web_url = re.search(
            '<meta data-n-head="ssr" data-hid="al:web:url" property="al:web:url" content="(.*?)">',
            html_str).group(1)
        room_id = web_url.split('&amp;h=')[-1]
    else:
        if '&h=' in url:
            room_id = url.split('&h=')[-1]
        else:
            room_id = url.split("?")[0].rsplit("/", maxsplit=1)[-1]

    data = {'siteId': room_id}  # roomId
    url2 = 'https://ta.bigo.tv/official_website/studio/getInternalStudioInfo'
    json_str = await async_req(url=url2, proxy_addr=proxy_addr, headers=headers, data=data)
    json_data = json.loads(json_str)
    anchor_name = json_data['data']['nick_name']
    live_status = json_data['data']['alive']
    result = {"anchor_name": anchor_name, "is_live": False}

    if live_status == 1:
        live_title = json_data['data']['roomTopic']
        m3u8_url = json_data['data']['hls_src']
        result['m3u8_url'] = m3u8_url
        result['record_url'] = m3u8_url
        result |= {"title": live_title, "is_live": True, "m3u8_url": m3u8_url, 'record_url': m3u8_url}
    elif result['anchor_name'] == '':
        html_str = await async_req(url=f'https://www.bigo.tv/{url.split("/")[3]}/{room_id}',
                                   proxy_addr=proxy_addr, headers=headers)
        match_anchor_name = re.search('<title>欢迎来到(.*?)的直播间</title>', html_str, re.DOTALL)
        if match_anchor_name:
            anchor_name = match_anchor_name.group(1)
        else:
            match_anchor_name = re.search('<meta data-n-head="ssr" data-hid="og:title" property="og:title" '
                                          'content="(.*?) - BIGO LIVE">', html_str, re.DOTALL)
            anchor_name = match_anchor_name.group(1)
        result['anchor_name'] = anchor_name

    return result


@trace_error_decorator
@trace_error_decorator
async def get_blued_stream_url(url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> dict:
    headers = {
        'User-Agent': 'ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
    }
    if cookies:
        headers['Cookie'] = cookies

    html_str = await async_req(url=url, proxy_addr=proxy_addr, headers=headers)
    json_str = re.search('decodeURIComponent\\(\"(.*?)\"\\)\\),window\\.Promise', html_str, re.DOTALL).group(1)
    json_str = urllib.parse.unquote(json_str)
    json_data = json.loads(json_str)
    anchor_name = json_data['userInfo']['name']
    live_status = json_data['userInfo']['onLive']
    result = {"anchor_name": anchor_name, "is_live": False}

    if live_status:
        m3u8_url = json_data['liveInfo']['liveUrl']
        result |= {"is_live": True, "m3u8_url": m3u8_url, 'record_url': m3u8_url}
    return result


@trace_error_decorator
@trace_error_decorator
async def login_sooplive(username: str, password: str, proxy_addr: OptionalStr = None) -> OptionalStr:
    if len(username) < 6 or len(password) < 10:
        raise RuntimeError("sooplive login failed! Please enter the correct account and password for the sooplive "
                           "platform in the config.ini file.")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': 'https://play.sooplive.co.kr',
        'Referer': 'https://play.sooplive.co.kr/superbsw123/277837074',
    }

    data = {
        'szWork': 'login',
        'szType': 'json',
        'szUid': username,
        'szPassword': password,
        'isSaveId': 'true',
        'isSavePw': 'true',
        'isSaveJoin': 'true',
        'isLoginRetain': 'Y',
    }

    url = 'https://login.sooplive.co.kr/app/LoginAction.php'

    try:
        cookie_dict = await async_req(url, proxy_addr=proxy_addr, headers=headers,
                                      data=data, return_cookies=True, timeout=20)
        cookie_str = '; '.join([f"{k}={v}" for k, v in cookie_dict.items()])
        return cookie_str
    except Exception as e:
        print(f"An error occurred during login: {e}")
        raise Exception(
            "sooplive login failed, please check if the account password in the configuration file is correct."
        )


@trace_error_decorator
@trace_error_decorator
async def get_sooplive_cdn_url(broad_no: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> dict:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Origin': 'https://play.sooplive.co.kr',
        'Referer': 'https://play.sooplive.co.kr/oul282/249469582',
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    if cookies:
        headers['Cookie'] = cookies

    params = {
        'return_type': 'gcp_cdn',
        'use_cors': 'false',
        'cors_origin_url': 'play.sooplive.co.kr',
        'broad_key': f'{broad_no}-common-master-hls',
        'time': '8361.086329376785',
    }

    url2 = 'http://livestream-manager.sooplive.co.kr/broad_stream_assign.html?' + urllib.parse.urlencode(params)
    json_str = await async_req(url=url2, proxy_addr=proxy_addr, headers=headers, abroad=True)
    json_data = json.loads(json_str)

    return json_data


@trace_error_decorator
@trace_error_decorator
async def get_sooplive_tk(url: str, rtype: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> str | tuple:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
        'Origin': 'https://play.sooplive.co.kr',
        'Referer': 'https://play.sooplive.co.kr/secretx/250989857',
        'Content-Type': 'application/x-www-form-urlencoded',
    }

    if cookies:
        headers['Cookie'] = cookies

    split_url = url.split('/')
    bj_id = split_url[3] if len(split_url) < 6 else split_url[5]
    room_password = get_params(url, "pwd")
    if not room_password:
        room_password = ''
    data = {
        'bid': bj_id,
        'bno': '',
        'type': rtype,
        'pwd': room_password,
        'player_type': 'html5',
        'stream_type': 'common',
        'quality': 'master',
        'mode': 'landing',
        'from_api': '0',
        'is_revive': 'false',
    }

    url2 = f'https://live.sooplive.co.kr/afreeca/player_live_api.php?bjid={bj_id}'
    json_str = await async_req(url=url2, proxy_addr=proxy_addr, headers=headers, data=data, abroad=True)
    json_data = json.loads(json_str)

    if rtype == 'aid':
        token = json_data["CHANNEL"]["AID"]
        return token
    else:
        bj_name = json_data['CHANNEL']['BJNICK']
        bj_id = json_data['CHANNEL']['BJID']
        return f"{bj_name}-{bj_id}", json_data['CHANNEL']['BNO']


def get_soop_headers(cookies):
    headers = {
        'client-id': str(uuid.uuid4()),
        'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, '
                      'like Gecko) Version/18.5 Mobile/15E148 Safari/604.1 Edg/141.0.0.0',
    }
    if cookies:
        headers['cookie'] = cookies
    return headers


async def _get_soop_channel_info_global(bj_id, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> str:
    headers = get_soop_headers(cookies)
    api = 'https://api.sooplive.com/v2/channel/info/' + str(bj_id)
    json_str = await async_req(api, proxy_addr=proxy_addr, headers=headers)
    json_data = json.loads(json_str)
    nickname = json_data['data']['streamerChannelInfo']['nickname']
    channelId = json_data['data']['streamerChannelInfo']['channelId']
    anchor_name = f"{nickname}-{channelId}"
    return anchor_name


async def _get_soop_stream_info_global(bj_id, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> tuple:
    headers = get_soop_headers(cookies)
    api = 'https://api.sooplive.com/v2/stream/info/' + str(bj_id)
    json_str = await async_req(api, proxy_addr=proxy_addr, headers=headers)
    json_data = json.loads(json_str)
    status = json_data['data']['isStream']
    title = json_data['data']['title']
    return status, title


async def _fetch_web_stream_data_global(url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> dict:
    split_url = url.split('/')
    bj_id = split_url[3] if len(split_url) < 6 else split_url[5]
    anchor_name = await _get_soop_channel_info_global(bj_id)
    result = {"anchor_name": anchor_name or '', "is_live": False, "live_url": url}
    status, title = await _get_soop_stream_info_global(bj_id)
    if not status:
        return result
    else:
        async def _get_url_list(m3u8: str) -> list[str]:
            headers = {
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0',
            }
            if cookies:
                headers['cookie'] = cookies
            resp = await async_req(url=m3u8, proxy_addr=proxy_addr, headers=headers)
            play_url_list = []
            url_prefix = '/'.join(m3u8.split('/')[0:3])
            for i in resp.split('\n'):
                if not i.startswith('#') and i.strip():
                    play_url_list.append(url_prefix + i.strip())
            bandwidth_pattern = re.compile(r'BANDWIDTH=(\d+)')
            bandwidth_list = bandwidth_pattern.findall(resp)
            url_to_bandwidth = {purl: int(bandwidth) for bandwidth, purl in zip(bandwidth_list, play_url_list)}
            play_url_list = sorted(play_url_list, key=lambda purl: url_to_bandwidth[purl], reverse=True)
            return play_url_list

        m3u8_url = 'https://global-media.sooplive.com/live/' + str(bj_id) + '/master.m3u8'
        result |= {
            'is_live': True,
            'title': title,
            'm3u8_url': m3u8_url,
            'play_url_list': await _get_url_list(m3u8_url)
        }
    return result


@trace_error_decorator
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
async def get_winktv_bj_info(url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> tuple:
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'content-type': 'application/x-www-form-urlencoded',
        'referer': 'https://www.winktv.co.kr/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
    }
    if cookies:
        headers['Cookie'] = cookies
    user_id = url.split('?')[0].rsplit('/', maxsplit=1)[-1]
    data = {
        'userId': user_id,
        'info': 'media',
    }

    info_api = 'https://api.winktv.co.kr/v1/member/bj'
    json_str = await async_req(url=info_api, proxy_addr=proxy_addr, headers=headers, data=data, abroad=True)
    json_data = json.loads(json_str)
    live_status = 'media' in json_data
    anchor_id = json_data['bjInfo']['id']
    anchor_name = f"{json_data['bjInfo']['nick']}-{anchor_id}"
    return anchor_name, live_status


@trace_error_decorator
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
async def login_flextv(username: str, password: str, proxy_addr: OptionalStr = None) -> OptionalStr:
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'content-type': 'application/json;charset=UTF-8',
        'referer': 'https://www.ttinglive.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
    }

    data = {
        'loginId': username,
        'password': password,
        'loginKeep': True,
        'saveId': True,
        'device': 'PCWEB',
    }

    url = 'https://www.ttinglive.com/v2/api/auth/signin'

    try:
        print("Logging into FlexTV platform...")
        cookie_dict = await async_req(url, proxy_addr=proxy_addr, headers=headers, json_data=data,
                                      return_cookies=True, timeout=20)

        if cookie_dict and 'flx_oauth_access' in cookie_dict:
            cookie_str = '; '.join([f"{k}={v}" for k, v in cookie_dict.items()])
            return cookie_str
        else:
            print("Please check if the FlexTV account and password in the configuration file are correct.")
            return None

    except Exception as e:
        print(f"FlexTV login request exception: {e}")
        raise Exception(
            "FlexTV login failed, please check if the account and password in the configuration file are correct."
        )


async def get_flextv_stream_url(
        url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None
) -> str:
    async def fetch_data(cookie) -> dict:
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'referer': 'https://www.ttinglive.com/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
        }
        user_id = url.split('/live')[0].rsplit('/', maxsplit=1)[-1]
        if cookie:
            headers['Cookie'] = cookie
        play_api = f'https://www.ttinglive.com/api/channels/{user_id}/stream?option=all'
        json_str = await async_req(play_api, proxy_addr=proxy_addr, headers=headers, abroad=True)
        if 'HTTP Error 400: Bad Request' in json_str:
            raise ConnectionError(
                "Failed to retrieve FlexTV live streaming data, please switch to a different proxy and try again."
            )
        return json.loads(json_str)

    json_data = await fetch_data(cookies)
    if 'sources' in json_data and len(json_data['sources']) > 0:
        play_url = json_data['sources'][0]['url']
        return play_url


@trace_error_decorator
@trace_error_decorator
async def get_flextv_stream_data(url, proxy_addr=None, cookies=None, username=None, password=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.FlexTVLiveStream(proxy_addr=proxy_addr, cookies=cookies, username=username, password=password)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_flextv_stream_data failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

def get_looklive_secret_data(text) -> tuple:
    # 本算法参考项目：https://github.com/785415581/MusicBox/blob/b8f716d43d/doc/analysis/analyze_captured_data.md

    modulus = '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee' \
              '341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe487' \
              '5d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7'
    nonce = b'0CoJUm6Qyw8W8jud'
    public_key = '010001'
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    import base64
    import binascii
    import secrets

    def create_secret_key(size: int) -> bytes:
        charset = '1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%^&*()_+-=[]{}|;:,.<>?'
        return ''.join(secrets.choice(charset) for _ in range(size)).encode('utf-8')

    def aes_encrypt(_text: str | bytes, _sec_key: str | bytes) -> bytes:
        if isinstance(_text, str):
            _text = _text.encode('utf-8')
        if isinstance(_sec_key, str):
            _sec_key = _sec_key.encode('utf-8')
        _sec_key = _sec_key[:16]  # 16 (AES-128), 24 (AES-192), or 32 (AES-256) bytes
        iv = bytes('0102030405060708', 'utf-8')
        encryptor = AES.new(_sec_key, AES.MODE_CBC, iv)
        padded_text = pad(_text, AES.block_size)
        ciphertext = encryptor.encrypt(padded_text)
        encoded_ciphertext = base64.b64encode(ciphertext)
        return encoded_ciphertext

    def rsa_encrypt(_text: str | bytes, pub_key: str, mod: str) -> str:
        if isinstance(_text, str):
            _text = _text.encode('utf-8')
        text_reversed = _text[::-1]
        text_int = int(binascii.hexlify(text_reversed), 16)
        encrypted_int = pow(text_int, int(pub_key, 16), int(mod, 16))
        return format(encrypted_int, 'x').zfill(256)

    sec_key = create_secret_key(16)
    enc_text = aes_encrypt(aes_encrypt(json.dumps(text), nonce), sec_key)
    enc_sec_key = rsa_encrypt(sec_key, public_key, modulus)
    return enc_text.decode(), enc_sec_key


async def get_looklive_stream_url(url, proxy_addr=None, cookies=None, video_quality=None) -> dict:
    try:
        live_stream = streamget.LookLiveStream(proxy_addr=proxy_addr, cookies=cookies)
        json_data = await live_stream.fetch_web_stream_data(url)
        return _stream_data_to_dict(await live_stream.fetch_stream_url(json_data, video_quality))
    except Exception as e:
        logger.error(f"get_looklive_stream_url failed: {url}, {type(e).__name__}: {e}")
        return {"anchor_name": "", "is_live": False}

@trace_error_decorator
async def login_popkontv(
        username: str, password: str, proxy_addr: OptionalStr = None, code: OptionalStr = 'P-00001'
) -> tuple:
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'Authorization': 'Basic FpAhe6mh8Qtz116OENBmRddbYVirNKasktdXQiuHfm88zRaFydTsFy63tzkdZY0u',
        'Content-Type': 'application/json',
        'Origin': 'https://www.popkontv.com',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
    }

    data = {
        'partnerCode': code,
        'signId': username,
        'signPwd': password,
    }

    url = 'https://www.popkontv.com/api/proxy/member/v1/login'

    try:
        proxy_addr = utils.handle_proxy_addr(proxy_addr)
        async with httpx.AsyncClient(proxy=proxy_addr, timeout=20, verify=False) as client:
            response = await client.post(url, json=data, headers=headers)
            response.raise_for_status()

            json_data = response.json()
            login_status_code = json_data.get("statusCd")

            if login_status_code == 'E4010':
                raise Exception("popkontv login failed, please reconfigure the correct login account or password!")
            elif login_status_code == 'S2000':
                token = json_data['data'].get("token")
                partner_code = json_data['data'].get("partnerCode")
                return token, partner_code
            else:
                raise Exception(f"popkontv login failed, {json_data.get('statusMsg', 'unknown error')}")
    except httpx.HTTPStatusError as e:
        print(f"HTTP status error occurred during login: {e.response.status_code}")
        raise
    except Exception as e:
        print(f"An exception occurred during popkontv login: {e}")
        raise


@trace_error_decorator
@trace_error_decorator
async def get_popkontv_stream_data(
        url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None,
        username: OptionalStr = None, code: OptionalStr = 'P-00001'
) -> tuple:
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'Content-Type': 'application/json',
        'Origin': 'https://www.popkontv.com',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
    }
    if cookies:
        headers['Cookie'] = cookies
    if 'mcid' in url:
        anchor_id = re.search('mcid=(.*?)&', url).group(1)
    else:
        anchor_id = re.search('castId=(.*?)(?=&|$)', url).group(1)

    data = {
        'partnerCode': code,
        'searchKeyword': anchor_id,
        'signId': username,
    }

    api = 'https://www.popkontv.com/api/proxy/broadcast/v1/search/all'
    json_str = await async_req(api, proxy_addr=proxy_addr, headers=headers, json_data=data, abroad=True)
    json_data = json.loads(json_str)

    partner_code = ''
    anchor_name = 'Unknown'
    for item in json_data['data']['broadCastList']:
        if item['mcSignId'] == anchor_id:
            mc_name = item['nickName']
            anchor_name = f"{mc_name}-{anchor_id}"
            partner_code = item['mcPartnerCode']
            break

    if not partner_code:
        if 'mcPartnerCode' in url:
            regex_result = re.search('mcPartnerCode=(P-\\d+)', url)
        else:
            regex_result = re.search('partnerCode=(P-\\d+)', url)
        partner_code = regex_result.group(1) if regex_result else code
        notices_url = f'https://www.popkontv.com/channel/notices?mcid={anchor_id}&mcPartnerCode={partner_code}'
        notices_response = await async_req(notices_url, proxy_addr=proxy_addr, headers=headers, abroad=True)
        mc_name_match = re.search(r'"mcNickName":"([^"]+)"', notices_response)
        mc_name = mc_name_match.group(1) if mc_name_match else 'Unknown'
        anchor_name = f"{anchor_id}-{mc_name}"

    live_url = f"https://www.popkontv.com/live/view?castId={anchor_id}&partnerCode={partner_code}"
    html_str2 = await async_req(live_url, proxy_addr=proxy_addr, headers=headers, abroad=True)
    json_str2 = re.search('<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_str2).group(1)
    json_data2 = json.loads(json_str2)
    if 'mcData' in json_data2['props']['pageProps']:
        room_data = json_data2['props']['pageProps']['mcData']['data']
        is_private = room_data['mc_isPrivate']
        cast_start_date_code = room_data['mc_castStartDate']
        mc_sign_id = room_data['mc_signId']
        cast_type = room_data['castType']
        return anchor_name, [cast_start_date_code, partner_code, mc_sign_id, cast_type, is_private]
    else:
        return anchor_name, None


@trace_error_decorator
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
async def login_twitcasting(
        account_type: str, username: str, password: str, proxy_addr: OptionalStr = None,
        cookies: OptionalStr = None
) -> OptionalStr:
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Referer': 'https://twitcasting.tv/indexcaslogin.php?redir=%2Findexloginwindow.php%3Fnext%3D%252F&keep=1',
        'Cookie': 'hl=zh; did=04fb08f1b15d248644f1dfa82816d323; _ga=GA1.1.1021187740.1709706998; keep=1; mfadid=yrQiEB26ruRg7mlMavABMBZWdOddzojW; _ga_X8R46Y30YM=GS1.1.1709706998.1.1.1709712274.0.0.0',
        'User-Agent': 'ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))',
    }

    if cookies:
        headers['Cookie'] = cookies

    if account_type == "twitter":
        login_url = 'https://twitcasting.tv/indexpasswordlogin.php'
        login_api = 'https://twitcasting.tv/indexpasswordlogin.php?redir=/indexloginwindow.php?next=%2F&keep=1'
    else:
        login_url = 'https://twitcasting.tv/indexcaslogin.php?redir=%2F&keep=1'
        login_api = 'https://twitcasting.tv/indexcaslogin.php?redir=/indexloginwindow.php?next=%2F&keep=1'

    html_str = await async_req(login_url, proxy_addr=proxy_addr, headers=headers)
    cs_session_id = re.search('<input type="hidden" name="cs_session_id" value="(.*?)">', html_str).group(1)

    data = {
        'username': username,
        'password': password,
        'action': 'login',
        'cs_session_id': cs_session_id,
    }
    try:
        cookie_dict = await async_req(login_api, proxy_addr=proxy_addr, headers=headers,
                                      data=data, return_cookies=True, timeout=20)
        if 'tc_ss' in cookie_dict:
            cookie = utils.dict_to_cookie_str(cookie_dict)
            return cookie
    except Exception as e:
        print("TwitCasting login error,", e)


@trace_error_decorator
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

async def get_twitchtv_room_info(url: str, token: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> tuple:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
        'Accept-Language': 'zh-CN',
        'Referer': 'https://www.twitch.tv/',
        'Client-Id': 'kimne78kx3ncx6brgo4mv6wki5h1ko',
        'Client-Integrity': token,
        'Content-Type': 'text/plain;charset=UTF-8',
    }
    if cookies:
        headers['Cookie'] = cookies
    uid = url.split('?')[0].rsplit('/', maxsplit=1)[-1]

    data = [
        {
            "operationName": "ChannelShell",
            "variables": {
                "login": uid
            },
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "580ab410bcd0c1ad194224957ae2241e5d252b2c5173d8e0cce9d32d5bb14efe"
                }
            }
        },
    ]

    json_str = await async_req('https://gql.twitch.tv/gql', proxy_addr=proxy_addr, headers=headers,
                               json_data=data, abroad=True)
    json_data = json.loads(json_str)
    user_data = json_data[0]['data']['userOrError']
    login_name = user_data["login"]
    nickname = f"{user_data['displayName']}-{login_name}"
    status = True if user_data['stream'] else False
    return nickname, status


@trace_error_decorator
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

async def get_huajiao_sn(url: str, cookies: OptionalStr = None, proxy_addr: OptionalStr = None) -> tuple | None:
    headers = {
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'referer': 'https://www.huajiao.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
    }

    if cookies:
        headers['Cookie'] = cookies

    live_id = url.split('?')[0].rsplit('/', maxsplit=1)[1]
    api = f'https://www.huajiao.com/l/{live_id}'
    try:
        html_str = await async_req(url=api, proxy_addr=proxy_addr, headers=headers)
        json_str = re.search('var feed = (.*?});', html_str).group(1)
        json_data = json.loads(json_str)
        sn = json_data['feed']['sn']
        uid = json_data['author']['uid']
        nickname = json_data['author']['nickname']
        live_id = url.split('?')[0].rsplit('/', maxsplit=1)[1]
        return nickname, sn, uid, live_id
    except Exception:
        utils.replace_url(f'{script_path}/config/URL_config.ini', old=url, new='#' + url)
        raise RuntimeError("Failed to retrieve live room data, the Huajiao live room address is not fixed, please use "
                           "the anchor's homepage address for recording.")


async def get_huajiao_user_info(url: str, cookies: OptionalStr = None, proxy_addr: OptionalStr = None) -> OptionalDict:
    headers = {
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'referer': 'https://www.huajiao.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
    }

    if cookies:
        headers['Cookie'] = cookies

    if 'user' in url:
        uid = url.split('?')[0].split('user/')[1]
        params = {
            'uid': uid,
            'fmt': 'json',
            '_': str(int(time.time() * 1000)),
        }

        api = f'https://webh.huajiao.com/User/getUserFeeds?{urllib.parse.urlencode(params)}'
        json_str = await async_req(url=api, proxy_addr=proxy_addr, headers=headers)
        json_data = json.loads(json_str)

        html_str = await async_req(url=f'https://www.huajiao.com/user/{uid}', proxy_addr=proxy_addr, headers=headers)
        anchor_name = re.search('<title>(.*?)的主页.*</title>', html_str).group(1)
        if json_data['data'] and 'sn' in json_data['data']['feeds'][0]['feed']:
            feed = json_data['data']['feeds'][0]['feed']
            return {
                "anchor_name": anchor_name,
                "title": feed['title'],
                "is_live": True,
                "sn": feed['sn'],
                "liveid": feed['relateid'],
                "uid": uid
            }
        else:
            return {"anchor_name": anchor_name, "is_live": False}


async def get_huajiao_stream_url_app(url: str, proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> OptionalDict:
    headers = {
        'User-Agent': 'living/9.4.0 (com.huajiao.seeding; build:2410231746; iOS 17.0.0) Alamofire/9.4.0',
        'accept-language': 'zh-Hans-US;q=1.0',
        'sdk_version': '1',
    }
    if cookies:
        headers['Cookie'] = cookies
    room_id = url.rsplit('/', maxsplit=1)[1]
    api = f'https://live.huajiao.com/feed/getFeedInfo?relateid={room_id}'
    json_str = await async_req(api, proxy_addr=proxy_addr, headers=headers)
    json_data = json.loads(json_str)

    if json_data['errmsg'] or not json_data['data'].get('creatime'):
        print("Failed to retrieve live room data, the Huajiao live room address is not fixed, please manually change "
              "the address for recording.")
        return
    data = json_data['data']
    return {
        "anchor_name": data['author']['nickname'],
        "title": data['feed']['title'],
        "is_live": True,
        "sn": data['feed']['sn'],
        "liveid": data['feed']['relateid'],
        "uid": data['author']['uid']
    }


@trace_error_decorator
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
async def get_acfun_sign_params(proxy_addr: OptionalStr = None, cookies: OptionalStr = None) -> tuple:
    did = f'web_{utils.generate_random_string(16)}'
    headers = {
        'referer': 'https://live.acfun.cn/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
        'cookie': f'_did={did};',
    }
    if cookies:
        headers['Cookie'] = cookies
    data = {
        'sid': 'acfun.api.visitor',
    }
    api = 'https://id.app.acfun.cn/rest/app/visitor/login'
    json_str = await async_req(api, data=data, proxy_addr=proxy_addr, headers=headers)
    json_data = json.loads(json_str)
    user_id = json_data["userId"]
    visitor_st = json_data["acfun.api.visitor_st"]
    return user_id, did, visitor_st


@trace_error_decorator
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

