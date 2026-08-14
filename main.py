# -*- encoding: utf-8 -*-

"""
Author: Hmily
GitHub: https://github.com/ihmily
Date: 2023-07-17 23:52:05
Update: 2025-10-23 19:48:05
Copyright (c) 2023-2025 by Hmily, All Rights Reserved.
Function: Record live stream video.
"""
import asyncio
import os
import sys
import builtins
import subprocess
import signal
import threading
import time
import datetime
import re
import shutil
import random
import uuid
from pathlib import Path
import urllib.request
from urllib.error import URLError, HTTPError
from typing import Any
import configparser
import httpx
from src import spider
from src.flv_proxy import FLVProxy
from src.proxy import ProxyDetector
from src.utils import logger
from src import utils
from msg_push import (
    dingtalk, xizhi, tg_bot, send_email, bark, ntfy, pushplus
)
from ffmpeg_install import (
    check_ffmpeg, ffmpeg_path, current_env_path
)
from retry import retry_delay
from url_parser import dedup_priority_action, find_writeback_index, split_url_line

version = "v4.0.7"
platforms = ("\n国内站点：抖音|快手|虎牙|斗鱼|YY|B站|小红书|bigo|blued|网易CC|千度热播|猫耳FM|Look|TwitCasting|百度|微博|"
             "酷狗|花椒|流星|Acfun|畅聊|映客|音播|知乎|嗨秀|VV星球|17Live|浪Live|漂漂|六间房|乐嗨|花猫|淘宝|京东|咪咕|连接|来秀"
             "\n海外站点：TikTok|SOOP|PandaTV|WinkTV|FlexTV|PopkonTV|TwitchTV|LiveMe|ShowRoom|CHZZK|Shopee|"
             "Youtube|Faceit|Picarto")

recording = set()
error_count = 0
pre_max_request = 10
max_request_lock = threading.Lock()
error_window = []
error_window_size = 10
error_threshold = 5
monitoring = 0
running_list = []
url_tuples_list = []
priority_urls = set()
url_comments = []
text_no_repeat_url = []
create_var = locals()
first_start = True
exit_recording = False
need_update_line_list = []
FFMPEG_VERSION_MAJOR = 0
first_run = True
not_record_list = []
start_display_time = datetime.datetime.now()
global_proxy = False
recording_time_list = {}
script_path = os.path.split(os.path.realpath(sys.argv[0]))[0]
config_file = f'{script_path}/config/config.ini'
url_config_file = f'{script_path}/config/URL_config.ini'
backup_dir = f'{script_path}/backup_config'
text_encoding = 'utf-8-sig'
rstr = r"[\/\\\:\*\？?\"\<\>\|&#.。,， ~！· ]"
default_path = f'{script_path}/downloads'
os.makedirs(default_path, exist_ok=True)
file_update_lock = threading.Lock()
os_type = os.name
clear_command = "cls" if os_type == 'nt' else "clear"
color_obj = utils.Color()
os.environ['PATH'] = ffmpeg_path + os.pathsep + current_env_path


def signal_handler(_signal, _frame):
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)


def display_info() -> None:
    global start_display_time
    time.sleep(5)
    while True:
        try:
            sys.stdout.flush()
            time.sleep(5)
            if Path(sys.executable).name != 'pythonw.exe':
                os.system(clear_command)
            print(f"\r共监测{monitoring}个直播中", end=" | ")
            print(f"同一时间访问网络的线程数: {max_request}", end=" | ")
            print(f"是否开启代理录制: {'是' if use_proxy else '否'}", end=" | ")
            if split_video_by_time:
                print(f"录制分段开启: {split_time}秒", end=" | ")
            else:
                print("录制分段开启: 否", end=" | ")
            if create_time_file:
                print("是否生成时间文件: 是", end=" | ")
            print(f"录制视频质量为: {video_record_quality}", end=" | ")
            print(f"录制视频格式为: {video_save_type}", end=" | ")
            print(f"目前瞬时错误数为: {error_count}", end=" | ")
            now = time.strftime("%H:%M:%S", time.localtime())
            print(f"当前时间: {now}")

            if len(recording) == 0:
                time.sleep(5)
                if monitoring == 0:
                    print("\r没有正在监测和录制的直播")
                else:
                    print(f"\r没有正在录制的直播 循环监测间隔时间：{delay_default}秒")
            else:
                now_time = datetime.datetime.now()
                print("x" * 60)
                no_repeat_recording = list(set(recording))
                print(f"正在录制{len(no_repeat_recording)}个直播: ")
                for recording_live in no_repeat_recording:
                    rt, qa = recording_time_list[recording_live]
                    have_record_time = now_time - rt
                    print(f"{recording_live}[{qa}] 正在录制中 {str(have_record_time).split('.')[0]}")

                # print('\n本软件已运行：'+str(now_time - start_display_time).split('.')[0])
                print("x" * 60)
                start_display_time = now_time
        except Exception as e:
            logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")


def update_file(file_path: str, old_str: str, new_str: str, start_str: str = None) -> str | None:
    if old_str == new_str and start_str is None:
        return old_str
    with file_update_lock:
        file_data = []
        with open(file_path, "r", encoding=text_encoding) as f:
            try:
                for text_line in f:
                    if old_str in text_line:
                        text_line = text_line.replace(old_str, new_str)
                        if start_str:
                            text_line = f'{start_str}{text_line}'
                    if text_line not in file_data:
                        file_data.append(text_line)
            except RuntimeError as e:
                logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                if ini_URL_content:
                    with open(file_path, "w", encoding=text_encoding) as f2:
                        f2.write(ini_URL_content)
                    return old_str
        if file_data:
            with open(file_path, "w", encoding=text_encoding) as f:
                f.write(''.join(file_data))
        return new_str


def delete_line(file_path: str, del_line: str, delete_all: bool = False) -> None:
    with file_update_lock:
        with open(file_path, 'r+', encoding=text_encoding) as f:
            lines = f.readlines()
            f.seek(0)
            f.truncate()
            skip_line = False
            for txt_line in lines:
                if del_line in txt_line:
                    if delete_all or not skip_line:
                        skip_line = True
                        continue
                else:
                    skip_line = False
                f.write(txt_line)


def get_startup_info(system_type: str):
    if system_type == 'nt':
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    else:
        startup_info = None
    return startup_info


def segment_video(converts_file_path: str, segment_save_file_path: str, segment_format: str, segment_time: str,
                  is_original_delete: bool = True) -> None:
    try:
        if os.path.exists(converts_file_path) and os.path.getsize(converts_file_path) > 0:
            ffmpeg_command = [
                "ffmpeg",
                "-i", converts_file_path,
                "-c:v", "copy",
                "-c:a", "copy",
                "-map", "0",
                "-f", "segment",
                "-segment_time", segment_time,
                "-segment_format", segment_format,
                "-reset_timestamps", "1",
                "-movflags", "+frag_keyframe+empty_moov",
                segment_save_file_path,
            ]
            _output = subprocess.check_output(
                ffmpeg_command, stderr=subprocess.STDOUT, startupinfo=get_startup_info(os_type)
            )
            if is_original_delete:
                time.sleep(1)
                if os.path.exists(converts_file_path):
                    os.remove(converts_file_path)
    except subprocess.CalledProcessError as e:
        logger.error(f'Error occurred during conversion: {e}')
    except Exception as e:
        logger.error(f'An unknown error occurred: {e}')


def converts_mp4(converts_file_path: str, is_original_delete: bool = True) -> None:
    try:
        if os.path.exists(converts_file_path) and os.path.getsize(converts_file_path) > 0:
            if converts_to_h264:
                color_obj.print_colored("正在转码为MP4格式并重新编码为h264\n", color_obj.YELLOW)
                ffmpeg_command = [
                    "ffmpeg", "-i", converts_file_path,
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "23",
                    "-vf", "format=yuv420p",
                    "-c:a", "copy",
                    "-f", "mp4", converts_file_path.rsplit('.', maxsplit=1)[0] + ".mp4",
                ]
            else:
                color_obj.print_colored("正在转码为MP4格式\n", color_obj.YELLOW)
                ffmpeg_command = [
                    "ffmpeg", "-i", converts_file_path,
                    "-c:v", "copy",
                    "-c:a", "copy",
                    "-f", "mp4", converts_file_path.rsplit('.', maxsplit=1)[0] + ".mp4",
                ]
            _output = subprocess.check_output(
                ffmpeg_command, stderr=subprocess.STDOUT, startupinfo=get_startup_info(os_type)
            )
            if is_original_delete:
                time.sleep(1)
                if os.path.exists(converts_file_path):
                    os.remove(converts_file_path)
    except subprocess.CalledProcessError as e:
        logger.error(f'Error occurred during conversion: {e}')
    except Exception as e:
        logger.error(f'An unknown error occurred: {e}')


def analyze_resolution_changes(file_path: str) -> list:
    """录制后检测文件内出现过的分辨率(ffprobe 只解关键帧, 秒级)。

    返回出现过的分辨率列表: 空 = 无法检测/无视频流; 长度 > 1 = 发生过分辨率切换。
    参照 biliLive-tools analyzeResolutionChanges(packages/shared/src/task/video.ts):
    -skip_frame nokey 只解关键帧, 大文件也只要几秒; 与 flv_proxy 实时分段互补,
    兜底直录 TS/MKV、HLS 源等不走代理的路径。
    """
    if shutil.which("ffprobe") is None:
        return []
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-skip_frame", "nokey",
             "-select_streams", "v:0",
             "-show_entries", "frame=width,height",
             "-of", "csv=p=0", file_path],
            capture_output=True, text=True, timeout=120, startupinfo=get_startup_info(os_type)
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    resolutions = []
    for line in result.stdout.splitlines():
        parts = line.split(',')
        if len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            resolutions.append(f"{int(parts[0])}x{int(parts[1])}")
    return list(dict.fromkeys(resolutions))


def check_resolution_changes(save_file_path: str, record_name: str) -> None:
    """录制结束后(线程内)扫描文件分辨率变化: 展开分段模板(%03d)后逐个扫描。

    与 flv_proxy 的实时分段互补: 后者只覆盖 FLV 代理路径, 这里兜底所有录完的
    文件(直录 TS/MKV、HLS 源等), 确认每个文件内部参数是否统一。
    """
    directory, filename = os.path.split(save_file_path)
    if re.search(r'%\d+d', filename):
        # 分段录制模板(如 xxx_%03d.ts): 扫描匹配的所有分段。
        # 注意不能用 '%' in filename 判断——主播名/标题可能含 %(如 100%辣妹)
        prefix = filename.split('%')[0]
        for path in sorted(utils.get_file_paths(directory)):
            if os.path.basename(path).startswith(prefix):
                _report_resolution_changes(path, record_name)
    else:
        _report_resolution_changes(save_file_path, record_name)


def detect_pts_jumps(file_path: str) -> str | None:
    """录制后检测时间戳跳变, 返回跳变描述或 None。

    上游 CDN 偶发发出 ts_ext 字节异常的 tag(如 0xFF → 解析为 2^32ms≈49.7 天),
    经 ffmpeg 写入文件后 duration 严重虚高(几 KB 文件显示上千小时)。
    按流分别追踪时间轴(音视频时间轴基差是常见现象, 跨流比较会误报),
    相邻包间隔超过 5 分钟即判定跳变——直播录制中同流相邻包不会出现这么
    大的正常间隔, 再小的跳变(如 230s)播放时也有明显卡帧。
    packet 级扫描只解复用不解码, 大文件也很快; 此前大文件走 -skip_frame nokey
    只解关键帧, 关键帧之间发生的跳变会漏检(坏 ts_ext tag 通常是普通帧)。
    超长文件全包 csv 可达数百 MB, stdout 由读线程逐行增量解析, 不整块缓冲。
    """
    if shutil.which("ffprobe") is None:
        return None
    args = ["ffprobe", "-v", "error",
            "-show_entries", "packet=pts_time,stream_index", "-of", "csv=p=0", file_path]
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, startupinfo=get_startup_info(os_type))
    except OSError:
        return None

    result = {}

    def parse_stream() -> None:
        # 逐行增量解析: 只保留每个流的上一个 pts + 最大跳变, 内存 O(流数)
        prev = {}  # stream_index -> 上一个 pts(按流分别追踪)
        max_jump = 0.0
        jump_at = None
        try:
            for line in process.stdout:
                parts = line.split(',')
                if len(parts) < 2:
                    continue
                try:
                    stream_index = int(parts[0])
                    pts = float(parts[1])
                except ValueError:
                    continue
                if stream_index in prev and pts - prev[stream_index] > 300:
                    if pts - prev[stream_index] > max_jump:
                        max_jump = pts - prev[stream_index]
                        jump_at = prev[stream_index]
                prev[stream_index] = pts
        except (OSError, ValueError):
            pass
        result['max_jump'] = max_jump
        result['jump_at'] = jump_at

    # 读线程与 wait 并发: 主线程限时 120s(ffprobe 卡死时 kill), 读线程消费
    # stdout 防止管道写满阻塞 ffprobe
    reader = threading.Thread(target=parse_stream, daemon=True,
                              name=f"pts-scan-{os.path.basename(file_path)}")
    reader.start()
    try:
        process.wait(timeout=120)
    except subprocess.TimeoutExpired:
        process.kill()
        return None
    reader.join(timeout=10)
    if process.returncode != 0:
        return None
    jump_at = result.get('jump_at')
    if jump_at is None:
        return None
    max_jump = result.get('max_jump', 0.0)
    if max_jump >= 3600:
        human = f"{max_jump / 3600:.1f} 小时"
    else:
        human = f"{max_jump / 60:.0f} 分钟"
    return f"{jump_at:.1f}s 处 PTS 跳变 +{human}, 时长虚高约 {human}"


_resolution_scanned = {}  # path -> (size, mtime): 已扫描文件记录, 分段重扫时跳过未变化的


def _report_resolution_changes(file_path: str, record_name: str) -> None:
    try:
        key = (os.path.getsize(file_path), os.path.getmtime(file_path))
    except OSError:
        return  # 文件已不存在(如转换后删除), 跳过
    if _resolution_scanned.get(file_path) == key:
        return  # 该文件已扫描且未变化, 避免每次分段结束重扫全部历史分段
    if len(_resolution_scanned) > 500:
        _resolution_scanned.clear()  # 防无限增长, 长期运行只保留近期记录
    _resolution_scanned[file_path] = key
    if detect_resolution_change:
        resolutions = analyze_resolution_changes(file_path)
        if len(resolutions) > 1:
            color_obj.print_colored(
                f"[{record_name}] 检测到分辨率变化({len(resolutions)}种): "
                f"{' → '.join(resolutions)} 文件: {os.path.basename(file_path)}",
                color_obj.YELLOW)
            logger.warning(f"分辨率变化检测: {file_path} 出现过 {resolutions}")
    if detect_pts_jumps_enabled:
        jump = detect_pts_jumps(file_path)
        if jump:
            color_obj.print_colored(
                f"[{record_name}] 检测到时间戳跳变: {jump} 文件: {os.path.basename(file_path)}",
                color_obj.YELLOW)
            logger.warning(f"PTS 跳变检测: {file_path} {jump}")


def converts_m4a(converts_file_path: str, is_original_delete: bool = True) -> None:
    try:
        if os.path.exists(converts_file_path) and os.path.getsize(converts_file_path) > 0:
            _output = subprocess.check_output([
                "ffmpeg", "-i", converts_file_path,
                "-n", "-vn",
                "-c:a", "aac", "-bsf:a", "aac_adtstoasc", "-ab", "320k",
                converts_file_path.rsplit('.', maxsplit=1)[0] + ".m4a",
            ], stderr=subprocess.STDOUT, startupinfo=get_startup_info(os_type))
            if is_original_delete:
                time.sleep(1)
                if os.path.exists(converts_file_path):
                    os.remove(converts_file_path)
    except subprocess.CalledProcessError as e:
        logger.error(f'Error occurred during conversion: {e}')
    except Exception as e:
        logger.error(f'An unknown error occurred: {e}')


def generate_subtitles(record_name: str, ass_filename: str, sub_format: str = 'srt') -> None:
    index_time = 0
    today = datetime.datetime.now()
    re_datatime = today.strftime('%Y-%m-%d %H:%M:%S')

    def transform_int_to_time(seconds: int) -> str:
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    while True:
        index_time += 1
        txt = str(index_time) + "\n" + transform_int_to_time(index_time) + ',000 --> ' + transform_int_to_time(
            index_time + 1) + ',000' + "\n" + str(re_datatime) + "\n\n"

        with open(f"{ass_filename}.{sub_format.lower()}", 'a', encoding=text_encoding) as f:
            f.write(txt)

        if record_name not in recording:
            return
        time.sleep(1)
        today = datetime.datetime.now()
        re_datatime = today.strftime('%Y-%m-%d %H:%M:%S')


def adjust_max_request() -> None:
    global max_request, error_count, pre_max_request, error_window
    preset = max_request

    while True:
        time.sleep(5)
        with max_request_lock:
            if error_window:
                error_rate = sum(error_window) / len(error_window)
            else:
                error_rate = 0

            if error_rate > error_threshold:
                max_request = max(1, max_request - 1)
            elif error_rate < error_threshold / 2 and max_request < preset:
                max_request += 1
            else:
                pass

            if pre_max_request != max_request:
                pre_max_request = max_request
                print(f"\r同一时间访问网络的线程数动态改为 {max_request}")

        error_window.append(error_count)
        if len(error_window) > error_window_size:
            error_window.pop(0)
        error_count = 0


def push_message(record_name: str, live_url: str, content: str) -> None:
    msg_title = push_message_title.strip() or "直播间状态更新通知"
    push_functions = {
        '微信': lambda: xizhi(xizhi_api_url, msg_title, content),
        '钉钉': lambda: dingtalk(dingtalk_api_url, content, dingtalk_phone_num, dingtalk_is_atall),
        '邮箱': lambda: send_email(
            email_host, login_email, email_password, sender_email, sender_name,
            to_email, msg_title, content, smtp_port, open_smtp_ssl
        ),
        'TG': lambda: tg_bot(tg_chat_id, tg_token, content),
        'BARK': lambda: bark(
            bark_msg_api, title=msg_title, content=content, level=bark_msg_level, sound=bark_msg_ring
        ),
        'NTFY': lambda: ntfy(
            ntfy_api, title=msg_title, content=content, tags=ntfy_tags, action_url=live_url, email=ntfy_email
        ),
        'PUSHPLUS': lambda: pushplus(pushplus_token, msg_title, content),
    }

    for platform, func in push_functions.items():
        if platform in live_status_push.upper():
            try:
                result = func()
                print(f'提示信息：已经将[{record_name}]直播状态消息推送至你的{platform},'
                      f' 成功{len(result["success"])}, 失败{len(result["error"])}')
            except Exception as e:
                color_obj.print_colored(f"直播消息推送到{platform}失败: {e}", color_obj.RED)


def run_script(command: str) -> None:
    try:
        process = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=get_startup_info(os_type)
        )
        stdout, stderr = process.communicate()
        stdout_decoded = stdout.decode('utf-8')
        stderr_decoded = stderr.decode('utf-8')
        if stdout_decoded.strip():
            print(stdout_decoded)
        if stderr_decoded.strip():
            print(stderr_decoded)
    except PermissionError as e:
        logger.error(e)
        logger.error('脚本无执行权限!, 若是Linux环境, 请先执行:chmod +x your_script.sh 授予脚本可执行权限')
    except OSError as e:
        logger.error(e)
        logger.error('Please add `#!/bin/bash` at the beginning of your bash script file.')


def clear_record_info(record_name: str, record_url: str) -> None:
    global monitoring
    recording.discard(record_name)
    if record_url in url_comments and record_url in running_list:
        running_list.remove(record_url)
        monitoring -= 1
        color_obj.print_colored(f"[{record_name}]已经从录制列表中移除\n", color_obj.YELLOW)


def direct_download_stream(source_url: str, save_path: str, record_name: str, live_url: str, platform: str,
                           proxy_addr: str | None = None) -> bool:
    try:
        with open(save_path, 'wb') as f:
            client = httpx.Client(timeout=None, proxy=proxy_addr)

            headers = {}
            header_params = get_record_headers(platform, live_url)
            if header_params:
                key, value = header_params.split(":", 1)
                headers[key] = value

            with client.stream('GET', source_url, headers=headers, follow_redirects=True) as response:
                # 接受 2xx 状态码(200/201/206 等, 服务端可能返回分段响应)
                if not (200 <= response.status_code < 300):
                    logger.error(f"请求直播流失败，状态码: {response.status_code}")
                    return False

                downloaded = 0
                chunk_size = 1024 * 16

                for chunk in response.iter_bytes(chunk_size):
                    if live_url in url_comments or exit_recording:
                        color_obj.print_colored(f"[{record_name}]录制时已被注释或请求停止,下载中断", color_obj.YELLOW)
                        clear_record_info(record_name, live_url)
                        return False

                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                print()
                return True
    except Exception as e:
        logger.error(f"FLV下载错误: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
        return False


def check_subprocess(record_name: str, record_url: str, ffmpeg_command: list, save_type: str,
                     script_command: str | None = None) -> bool:
    save_file_path = ffmpeg_command[-1]
    process = subprocess.Popen(
        ffmpeg_command, stdin=subprocess.PIPE, stderr=subprocess.STDOUT, startupinfo=get_startup_info(os_type)
    )

    subs_file_path = save_file_path.rsplit('.', maxsplit=1)[0]
    subs_thread_name = f'subs_{Path(subs_file_path).name}'
    if create_time_file and not split_video_by_time and '音频' not in save_type:
        create_var[subs_thread_name] = threading.Thread(
            target=generate_subtitles, args=(record_name, subs_file_path)
        )
        create_var[subs_thread_name].daemon = True
        create_var[subs_thread_name].start()

    while process.poll() is None:
        if record_url in url_comments or exit_recording:
            color_obj.print_colored(f"[{record_name}]录制时已被注释,本条线程将会退出", color_obj.YELLOW)
            clear_record_info(record_name, record_url)
            # process.terminate()
            if os.name == 'nt':
                if process.stdin:
                    process.stdin.write(b'q')
                    process.stdin.close()
            else:
                process.send_signal(signal.SIGINT)
            process.wait()
            return True
        time.sleep(1)

    return_code = process.returncode
    stop_time = time.strftime('%Y-%m-%d %H:%M:%S')
    if return_code == 0:
        if converts_to_mp4 and save_type == 'TS':
            if split_video_by_time:
                file_paths = utils.get_file_paths(os.path.dirname(save_file_path))
                prefix = os.path.basename(save_file_path).rsplit('_', maxsplit=1)[0]
                for path in file_paths:
                    if prefix in path:
                        threading.Thread(target=converts_mp4, args=(path, delete_origin_file)).start()
            else:
                threading.Thread(target=converts_mp4, args=(save_file_path, delete_origin_file)).start()
        print(f"\n{record_name} {stop_time} 直播录制完成\n")

        if script_command:
            logger.debug("开始执行脚本命令!")
            if "python" in script_command:
                params = [
                    f'--record_name "{record_name}"',
                    f'--save_file_path "{save_file_path}"',
                    f'--save_type {save_type}',
                    f'--split_video_by_time {split_video_by_time}',
                    f'--converts_to_mp4 {converts_to_mp4}',
                ]
            else:
                params = [
                    f'"{record_name.split(" ", maxsplit=1)[-1]}"',
                    f'"{save_file_path}"',
                    save_type,
                    f'split_video_by_time:{split_video_by_time}',
                    f'converts_to_mp4:{converts_to_mp4}'
                ]
            script_command = script_command.strip() + ' ' + ' '.join(params)
            run_script(script_command)
            logger.debug("脚本命令执行结束!")

    else:
        color_obj.print_colored(f"\n{record_name} {stop_time} 直播录制出错,返回码: {return_code}\n", color_obj.RED)

    # 录制后兜底检测(线程内跑, 不阻塞重试/转码): 分辨率变化与 PTS 跳变是两套
    # 独立检测, 各自受配置开关控制; 正常结束与断流/分段文件都扫——断流文件在
    # 直录/HLS 等不走 FLV 代理的路径下可能混入多段参数, 这是 flv_proxy 实时
    # 分段覆盖不到的
    if (detect_resolution_change or detect_pts_jumps_enabled) \
            and save_file_path.endswith(('.ts', '.flv', '.mkv', '.mp4')):
        threading.Thread(target=check_resolution_changes,
                         args=(save_file_path, record_name), daemon=True).start()

    recording.discard(record_name)
    return False


def clean_name(input_text):
    cleaned_name = re.sub(rstr, "_", input_text.strip()).strip('_')
    cleaned_name = cleaned_name.replace("（", "(").replace("）", ")")
    if clean_emoji:
        cleaned_name = utils.remove_emojis(cleaned_name, '_').strip('_')
    return cleaned_name or '空白昵称'


def get_quality_code(qn):
    QUALITY_MAPPING = {
        "原画": "OD",
        "蓝光": "BD",
        "超清": "UHD",
        "高清": "HD",
        "标清": "SD",
        "流畅": "LD"
    }
    return QUALITY_MAPPING.get(qn)


def get_record_headers(platform, live_url):
    live_domain = '/'.join(live_url.split('/')[0:3])
    record_headers = {
        'PandaTV': 'origin:https://www.pandalive.co.kr',
        'WinkTV': 'origin:https://www.winktv.co.kr',
        'PopkonTV': 'origin:https://www.popkontv.com',
        'FlexTV': 'origin:https://www.flextv.co.kr',
        '千度热播': 'referer:https://qiandurebo.com',
        '17Live': 'referer:https://17.live/en/live/6302408',
        '浪Live': 'referer:https://www.lang.live',
        'shopee': f'origin:{live_domain}',
        'Blued直播': 'referer:https://app.blued.cn'
    }
    return record_headers.get(platform)


def is_flv_preferred_platform(link):
    return any(i in link for i in ["douyin", "tiktok"])


def select_source_url(link, stream_info):
    if is_flv_preferred_platform(link):
        codec = utils.get_query_params(stream_info.get('flv_url'), "codec")
        if codec and codec[0] == 'h265':
            logger.warning("FLV is not supported for h265 codec, use HLS source instead")
        else:
            return stream_info.get('flv_url')

    return stream_info.get('record_url')


def start_record(url_data: tuple, count_variable: int = -1) -> None:
    global error_count

    flv_proxy = None  # FLV 流代理(检测分辨率/编码参数变化自动分段), 跨轮复用

    while True:
        try:
            run_once = False
            start_pushed = False
            new_record_url = ''
            count_time = time.time()
            retry = 0
            stream_interrupted = False
            interrupted_retries = 0
            was_recording = False
            record_quality_zh, record_url, anchor_name = url_data
            record_name = f'序号{count_variable} {anchor_name}'
            record_quality = get_quality_code(record_quality_zh)
            proxy_address = proxy_addr or None  # 空串传给 httpx.Client(proxy="") 会抛 ValueError
            platform = '未知平台'
            live_domain = '/'.join(record_url.split('/')[0:3])

            if proxy_addr:
                proxy_address = None
                for platform in enable_proxy_platform_list:
                    if platform and platform.strip() in record_url:
                        proxy_address = proxy_addr
                        break

            if not proxy_address:
                if extra_enable_proxy_platform_list:
                    for pt in extra_enable_proxy_platform_list:
                        if pt and pt.strip() in record_url:
                            proxy_address = proxy_addr_bak or None

            # print(f'\r代理地址:{proxy_address}')
            # print(f'\r全局代理:{global_proxy}')
            while True:
                if record_url in url_comments:
                    print(f"[{record_name}]已被注释,本条线程将会退出")
                    if flv_proxy:
                        flv_proxy.close()
                        flv_proxy = None
                    clear_record_info(record_name, record_url)
                    return

                try:
                    port_info = []
                    if record_url.find("douyin.com/") > -1:
                        platform = '抖音直播'
                        with semaphore:
                            if 'v.douyin.com' not in record_url and '/user/' not in record_url:
                                port_info = asyncio.run(spider.get_douyin_web_stream_data(
                                    url=record_url,
                                    proxy_addr=proxy_address,
                                    cookies=dy_cookie,
                                    video_quality=record_quality))
                            else:
                                port_info = asyncio.run(spider.get_douyin_app_stream_data(
                                    url=record_url,
                                    proxy_addr=proxy_address,
                                    cookies=dy_cookie,
                                    video_quality=record_quality))

                    elif record_url.find("https://www.tiktok.com/") > -1:
                        platform = 'TikTok直播'
                        with semaphore:
                            if global_proxy or proxy_address:
                                port_info = asyncio.run(spider.get_tiktok_stream_data(
                                    url=record_url,
                                    proxy_addr=proxy_address,
                                    cookies=tiktok_cookie,
                                    video_quality=record_quality))
                            else:
                                logger.error("错误信息: 网络异常，请检查网络是否能正常访问TikTok平台")

                    elif record_url.find("https://live.kuaishou.com/") > -1:
                        platform = '快手直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_kuaishou_stream_data(
                                url=record_url,
                                proxy_addr=proxy_address,
                                cookies=ks_cookie,
                                video_quality=record_quality))

                    elif record_url.find("https://www.huya.com/") > -1:
                        platform = '虎牙直播'
                        with semaphore:
                            if record_quality not in ['OD', 'BD', 'UHD']:
                                port_info = asyncio.run(spider.get_huya_stream_data(
                                    url=record_url,
                                    proxy_addr=proxy_address,
                                    cookies=hy_cookie,
                                    video_quality=record_quality))
                            else:
                                port_info = asyncio.run(spider.get_huya_app_stream_url(
                                    url=record_url,
                                    proxy_addr=proxy_address,
                                    cookies=hy_cookie
                                ))

                    elif record_url.find("https://www.douyu.com/") > -1:
                        platform = '斗鱼直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_douyu_info_data(
                                url=record_url, proxy_addr=proxy_address, cookies=douyu_cookie,
                                video_quality=record_quality))

                    elif record_url.find("https://www.yy.com/") > -1:
                        platform = 'YY直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_yy_stream_data(
                                url=record_url, proxy_addr=proxy_address, cookies=yy_cookie,
                                video_quality=record_quality))

                    elif record_url.find("https://live.bilibili.com/") > -1:
                        platform = 'B站直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_bilibili_room_info(
                                url=record_url, proxy_addr=proxy_address, cookies=bili_cookie,
                                video_quality=record_quality))

                    elif record_url.find("https://xhslink.com/") > -1 or \
                            record_url.find("https://www.xiaohongshu.com/") > -1:
                        platform = '小红书直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_xhs_stream_url(
                                record_url, proxy_addr=proxy_address, cookies=xhs_cookie))
                            retry += 1

                    elif record_url.find("www.bigo.tv/") > -1 or record_url.find("slink.bigovideo.tv/") > -1:
                        platform = 'Bigo直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_bigo_stream_url(
                                record_url, proxy_addr=proxy_address, cookies=bigo_cookie))

                    elif record_url.find("https://app.blued.cn/") > -1:
                        platform = 'Blued直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_blued_stream_url(
                                record_url, proxy_addr=proxy_address, cookies=blued_cookie))

                    elif record_url.find("sooplive.co.kr/") > -1 or record_url.find("sooplive.com/") > -1:
                        platform = 'SOOP'
                        with semaphore:
                            if global_proxy or proxy_address:
                                port_info = asyncio.run(spider.get_sooplive_stream_data(
                                    url=record_url, proxy_addr=proxy_address,
                                    cookies=sooplive_cookie,
                                    username=sooplive_username,
                                    password=sooplive_password,
                                    video_quality=record_quality
                                ))
                                if port_info and port_info.get('new_cookies'):
                                    utils.update_config(
                                        config_file, 'Cookie', 'sooplive_cookie', port_info['new_cookies']
                                    )
                            else:
                                logger.error("错误信息: 网络异常，请检查本网络是否能正常访问SOOP平台")

                    elif record_url.find("cc.163.com/") > -1:
                        platform = '网易CC直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_netease_stream_data(
                                url=record_url, cookies=netease_cookie, video_quality=record_quality))

                    elif record_url.find("qiandurebo.com/") > -1:
                        platform = '千度热播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_qiandurebo_stream_data(
                                url=record_url, proxy_addr=proxy_address, cookies=qiandurebo_cookie))

                    elif record_url.find("www.pandalive.co.kr/") > -1:
                        platform = 'PandaTV'
                        with semaphore:
                            if global_proxy or proxy_address:
                                port_info = asyncio.run(spider.get_pandatv_stream_data(
                                    url=record_url,
                                    proxy_addr=proxy_address,
                                    cookies=pandatv_cookie,
                                    video_quality=record_quality
                                ))
                            else:
                                logger.error("错误信息: 网络异常，请检查本网络是否能正常访问PandaTV直播平台")

                    elif record_url.find("fm.missevan.com/") > -1:
                        platform = '猫耳FM直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_maoerfm_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=maoerfm_cookie))

                    elif record_url.find("www.winktv.co.kr/") > -1:
                        platform = 'WinkTV'
                        with semaphore:
                            if global_proxy or proxy_address:
                                port_info = asyncio.run(spider.get_winktv_stream_data(
                                    url=record_url,
                                    proxy_addr=proxy_address,
                                    cookies=winktv_cookie,
                                    video_quality=record_quality))
                            else:
                                logger.error("错误信息: 网络异常，请检查本网络是否能正常访问WinkTV直播平台")

                    elif record_url.find("www.flextv.co.kr/") > -1 or record_url.find("www.ttinglive.com/") > -1:
                        platform = 'FlexTV'
                        with semaphore:
                            if global_proxy or proxy_address:
                                port_info = asyncio.run(spider.get_flextv_stream_data(
                                    url=record_url,
                                    proxy_addr=proxy_address,
                                    cookies=flextv_cookie,
                                    username=flextv_username,
                                    password=flextv_password,
                                    video_quality=record_quality
                                ))
                                if port_info and port_info.get('new_cookies'):
                                    utils.update_config(
                                        config_file, 'Cookie', 'flextv_cookie', port_info['new_cookies']
                                    )
                            else:
                                logger.error("错误信息: 网络异常，请检查本网络是否能正常访问FlexTV直播平台")

                    elif record_url.find("look.163.com/") > -1:
                        platform = 'Look直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_looklive_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=look_cookie
                            ))

                    elif record_url.find("www.popkontv.com/") > -1:
                        platform = 'PopkonTV'
                        with semaphore:
                            if global_proxy or proxy_address:
                                port_info = asyncio.run(spider.get_popkontv_stream_url(
                                    url=record_url,
                                    proxy_addr=proxy_address,
                                    access_token=popkontv_access_token,
                                    username=popkontv_username,
                                    password=popkontv_password,
                                    partner_code=popkontv_partner_code
                                ))
                                if port_info and port_info.get('new_token'):
                                    utils.update_config(
                                        file_path=config_file, section='Authorization', key='popkontv_token',
                                        new_value=port_info['new_token']
                                    )

                            else:
                                logger.error("错误信息: 网络异常，请检查本网络是否能正常访问PopkonTV直播平台")

                    elif record_url.find("twitcasting.tv/") > -1:
                        platform = 'TwitCasting'
                        with semaphore:
                            port_info = asyncio.run(spider.get_twitcasting_stream_url(
                                url=record_url,
                                proxy_addr=proxy_address,
                                cookies=twitcasting_cookie,
                                account_type=twitcasting_account_type,
                                username=twitcasting_username,
                                password=twitcasting_password,
                                video_quality=record_quality
                            ))

                            if port_info and port_info.get('new_cookies'):
                                utils.update_config(
                                    file_path=config_file, section='Cookie', key='twitcasting_cookie',
                                    new_value=port_info['new_cookies']
                                )

                    elif record_url.find("live.baidu.com/") > -1:
                        platform = '百度直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_baidu_stream_data(
                                url=record_url,
                                proxy_addr=proxy_address,
                                cookies=baidu_cookie,
                                video_quality=record_quality))

                    elif record_url.find("weibo.com/") > -1:
                        platform = '微博直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_weibo_stream_data(
                                url=record_url, proxy_addr=proxy_address, cookies=weibo_cookie,
                                video_quality=record_quality))

                    elif record_url.find("kugou.com/") > -1:
                        platform = '酷狗直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_kugou_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=kugou_cookie))

                    elif record_url.find("www.twitch.tv/") > -1:
                        platform = 'TwitchTV'
                        with semaphore:
                            if global_proxy or proxy_address:
                                port_info = asyncio.run(spider.get_twitchtv_stream_data(
                                    url=record_url,
                                    proxy_addr=proxy_address,
                                    cookies=twitch_cookie,
                                    video_quality=record_quality
                                ))
                            else:
                                logger.error("错误信息: 网络异常，请检查本网络是否能正常访问TwitchTV直播平台")

                    elif record_url.find("www.liveme.com/") > -1:
                        if global_proxy or proxy_address:
                            platform = 'LiveMe'
                            with semaphore:
                                port_info = asyncio.run(spider.get_liveme_stream_url(
                                    url=record_url, proxy_addr=proxy_address, cookies=liveme_cookie))
                        else:
                            logger.error("错误信息: 网络异常，请检查本网络是否能正常访问LiveMe直播平台")

                    elif record_url.find("www.huajiao.com/") > -1:
                        platform = '花椒直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_huajiao_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=huajiao_cookie))

                    elif record_url.find("7u66.com/") > -1:
                        platform = '流星直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_liuxing_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=liuxing_cookie))

                    elif record_url.find("showroom-live.com/") > -1:
                        platform = 'ShowRoom'
                        with semaphore:
                            port_info = asyncio.run(spider.get_showroom_stream_data(
                                url=record_url, proxy_addr=proxy_address, cookies=showroom_cookie,
                                video_quality=record_quality))

                    elif record_url.find("live.acfun.cn/") > -1 or record_url.find("m.acfun.cn/") > -1:
                        platform = 'Acfun'
                        with semaphore:
                            port_info = asyncio.run(spider.get_acfun_stream_data(
                                url=record_url, proxy_addr=proxy_address, cookies=acfun_cookie,
                                video_quality=record_quality))

                    elif record_url.find("live.tlclw.com/") > -1:
                        platform = '畅聊直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_changliao_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=changliao_cookie))

                    elif record_url.find("ybw1666.com/") > -1:
                        platform = '音播直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_yinbo_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=yinbo_cookie))

                    elif record_url.find("www.inke.cn/") > -1:
                        platform = '映客直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_yingke_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=yingke_cookie))

                    elif record_url.find("www.zhihu.com/") > -1:
                        platform = '知乎直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_zhihu_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=zhihu_cookie))

                    elif record_url.find("chzzk.naver.com/") > -1:
                        platform = 'CHZZK'
                        with semaphore:
                            port_info = asyncio.run(spider.get_chzzk_stream_data(
                                url=record_url, proxy_addr=proxy_address, cookies=chzzk_cookie,
                                video_quality=record_quality))

                    elif record_url.find("www.haixiutv.com/") > -1:
                        platform = '嗨秀直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_haixiu_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=haixiu_cookie))

                    elif record_url.find("vvxqiu.com/") > -1:
                        platform = 'VV星球'
                        with semaphore:
                            port_info = asyncio.run(spider.get_vvxqiu_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=vvxqiu_cookie))

                    elif record_url.find("17.live/") > -1:
                        platform = '17Live'
                        with semaphore:
                            port_info = asyncio.run(spider.get_17live_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=yiqilive_cookie))

                    elif record_url.find("www.lang.live/") > -1:
                        platform = '浪Live'
                        with semaphore:
                            port_info = asyncio.run(spider.get_langlive_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=langlive_cookie))

                    elif record_url.find("m.pp.weimipopo.com/") > -1:
                        platform = '漂漂直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_pplive_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=pplive_cookie))

                    elif record_url.find(".6.cn/") > -1:
                        platform = '六间房直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_6room_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=six_room_cookie))

                    elif record_url.find("lehaitv.com/") > -1:
                        platform = '乐嗨直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_haixiu_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=lehaitv_cookie))

                    elif record_url.find("h.catshow168.com/") > -1:
                        platform = '花猫直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_pplive_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=huamao_cookie))

                    elif record_url.find("live.shopee") > -1 or record_url.find("shp.ee/") > -1:
                        platform = 'shopee'
                        with semaphore:
                            port_info = asyncio.run(spider.get_shopee_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=shopee_cookie))
                            if port_info.get('uid'):
                                new_record_url = record_url.split('?')[0] + '?' + str(port_info['uid'])

                    elif record_url.find("www.youtube.com/") > -1 or record_url.find("youtu.be/") > -1:
                        platform = 'Youtube'
                        with semaphore:
                            port_info = asyncio.run(spider.get_youtube_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=youtube_cookie,
                                video_quality=record_quality))

                    elif record_url.find("tb.cn") > -1:
                        platform = '淘宝直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_taobao_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=taobao_cookie,
                                video_quality=record_quality))

                    elif record_url.find("3.cn") > -1 or record_url.find("m.jd.com") > -1:
                        platform = '京东直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_jd_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=jd_cookie))

                    elif record_url.find("faceit.com/") > -1:
                        platform = 'faceit'
                        with semaphore:
                            if global_proxy or proxy_address:
                                with semaphore:
                                    port_info = asyncio.run(spider.get_faceit_stream_data(
                                        url=record_url, proxy_addr=proxy_address, cookies=faceit_cookie,
                                        video_quality=record_quality))
                            else:
                                logger.error("错误信息: 网络异常，请检查本网络是否能正常访问faceit直播平台")

                    elif record_url.find("www.miguvideo.com") > -1 or record_url.find("m.miguvideo.com") > -1:
                        platform = '咪咕直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_migu_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=migu_cookie))

                    elif record_url.find("show.lailianjie.com") > -1:
                        platform = '连接直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_lianjie_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=lianjie_cookie))

                    elif record_url.find("www.imkktv.com") > -1:
                        platform = '来秀直播'
                        with semaphore:
                            port_info = asyncio.run(spider.get_laixiu_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=laixiu_cookie))

                    elif record_url.find("www.picarto.tv") > -1:
                        platform = 'Picarto'
                        with semaphore:
                            port_info = asyncio.run(spider.get_picarto_stream_url(
                                url=record_url, proxy_addr=proxy_address, cookies=picarto_cookie))

                    elif record_url.find(".m3u8") > -1 or record_url.find(".flv") > -1:
                        platform = '自定义录制直播'
                        port_info = {
                            "anchor_name": platform + '_' + str(uuid.uuid4())[:8],
                            "is_live": True,
                            "record_url": record_url,
                        }
                        if '.flv' in record_url:
                            port_info['flv_url'] = record_url
                        else:
                            port_info['m3u8_url'] = record_url

                    else:
                        logger.error(f'{record_url} {platform}直播地址')
                        if flv_proxy:
                            flv_proxy.close()
                            flv_proxy = None
                        return

                    if anchor_name:
                        if '主播:' in anchor_name:
                            anchor_split: list = anchor_name.split('主播:')
                            if len(anchor_split) > 1 and anchor_split[1].strip():
                                anchor_name = anchor_split[1].strip()
                            else:
                                anchor_name = port_info.get("anchor_name", '')
                    else:
                        anchor_name = port_info.get("anchor_name", '')

                    if not port_info.get("anchor_name", ''):
                        print(f'序号{count_variable} 网址内容获取失败,进行重试中...获取失败的地址是:{url_data}')
                        with max_request_lock:
                            error_count += 1
                            error_window.append(1)
                    else:
                        anchor_name = clean_name(anchor_name)
                        record_name = f'序号{count_variable} {anchor_name}'

                        if not url_data[-1] and run_once is False:
                            if new_record_url:
                                need_update_line_list.append(
                                    f'{record_url}|{new_record_url},主播: {anchor_name.strip()}')
                                not_record_list.append(new_record_url)
                            else:
                                need_update_line_list.append(f'{record_url}|{record_url},主播: {anchor_name.strip()}')
                            run_once = True

                        push_at = datetime.datetime.today().strftime('%Y-%m-%d %H:%M:%S')
                        if port_info['is_live'] is False:
                            print(f"\r{record_name} 等待直播... ")

                            if start_pushed:
                                if over_show_push:
                                    push_content = "直播间状态更新：[直播间名称] 直播已结束！时间：[时间]"
                                    if over_push_message_text:
                                        push_content = over_push_message_text

                                    push_content = (push_content.replace('[直播间名称]', record_name).
                                                    replace('[时间]', push_at))
                                    threading.Thread(
                                        target=push_message,
                                        args=(record_name, record_url, push_content.replace(r'\n', '\n')),
                                        daemon=True
                                    ).start()
                                start_pushed = False

                        else:
                            if stream_interrupted:
                                color_obj.print_colored(f"\r{anchor_name} 直播已恢复, 自动续录中...",
                                                        color_obj.GREEN)
                                stream_interrupted = False
                                interrupted_retries = 0

                            content = f"\r{record_name} 正在直播中..."
                            print(content)

                            if live_status_push and not start_pushed:
                                if begin_show_push:
                                    push_content = "直播间状态更新：[直播间名称] 正在直播中，时间：[时间]"
                                    if begin_push_message_text:
                                        push_content = begin_push_message_text

                                    push_content = (push_content.replace('[直播间名称]', record_name).
                                                    replace('[时间]', push_at))
                                    threading.Thread(
                                        target=push_message,
                                        args=(record_name, record_url, push_content.replace(r'\n', '\n')),
                                        daemon=True
                                    ).start()
                                start_pushed = True

                            if disable_record:
                                time.sleep(push_check_seconds)
                                continue

                            real_url = select_source_url(record_url, port_info)
                            full_path = f'{default_path}/{platform}' if folder_by_platform else default_path
                            if real_url:
                                now = datetime.datetime.today().strftime("%Y-%m-%d_%H-%M-%S")
                                live_title = port_info.get('title')
                                title_in_name = ''
                                if live_title:
                                    live_title = clean_name(live_title)
                                    title_in_name = live_title + '_' if filename_by_title else ''

                                try:
                                    if len(video_save_path) > 0:
                                        if not video_save_path.endswith(('/', '\\')):
                                            full_path = f'{video_save_path}/{platform}' if folder_by_platform else video_save_path
                                        else:
                                            full_path = f'{video_save_path}{platform}' if folder_by_platform else video_save_path

                                    full_path = full_path.replace("\\", '/')
                                    if folder_by_author:
                                        full_path = f'{full_path}/{anchor_name}'
                                    if folder_by_time:
                                        full_path = f'{full_path}/{now[:10]}'
                                    if folder_by_title and port_info.get('title'):
                                        if folder_by_time:
                                            full_path = f'{full_path}/{live_title}_{anchor_name}'
                                        else:
                                            full_path = f'{full_path}/{now[:10]}_{live_title}'
                                    if not os.path.exists(full_path):
                                        os.makedirs(full_path)
                                except Exception as e:
                                    logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")

                                only_flv_record = False
                                only_flv_platform_list = ['shopee', '花椒直播']
                                if platform in only_flv_platform_list:
                                    logger.debug(f"提示: {platform} 将强制使用FLV格式录制")
                                    only_flv_record = True

                                if platform != '自定义录制直播':
                                    if enable_https_recording and real_url.startswith("http://"):
                                        real_url = real_url.replace("http://", "https://")

                                    http_record_list = ['shopee', "migu"]
                                    if platform in http_record_list:
                                        real_url = real_url.replace("https://", "http://")

                                # FLV 流代理: 检测 SPS/PPS 变化(分辨率/编码参数切换)自动分段,
                                # 在关键帧处断连让 ffmpeg 收尾当前文件, 由断流重试机制重开新文件
                                # (参考 bililive-go flvproxy, 对 ffmpeg 完全透明)
                                if flv_proxy is not None and flv_proxy.upstream_url != real_url:
                                    # 流地址已变化(如下播重开播/CDN 轮换): 重建代理指向新地址,
                                    # 否则代理会一直用过期 URL 请求, 新直播永远录不上
                                    flv_proxy.close()
                                    flv_proxy = None
                                if flv_proxy is None and FLVProxy.is_flv_stream(real_url) \
                                        and not only_flv_record:
                                    # 直录平台(shopee/花椒)不用 ffmpeg, 走 port_info 的 flv_url
                                    # 直接下载, 代理从无客户端上连 → 不创建, 避免僵尸 socket+线程
                                    proxy_header_str = get_record_headers(platform, record_url)
                                    proxy_headers = {}
                                    if proxy_header_str:
                                        k, v = proxy_header_str.split(":", 1)
                                        proxy_headers[k.strip()] = v.strip()
                                    flv_proxy = FLVProxy(real_url, headers=proxy_headers,
                                                         proxy_addr=proxy_address)
                                    flv_proxy.start()
                                if flv_proxy is not None:
                                    show_real_url = real_url  # 日志用原始流地址(下方 real_url 被替换为 loopback)
                                    real_url = flv_proxy.local_url

                                user_agent = ("Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 ("
                                              "KHTML, like Gecko) SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile "
                                              "Safari/537.36")

                                rw_timeout = "15000000"
                                analyzeduration = "20000000"
                                probesize = "10000000"
                                bufsize = "8000k"
                                max_muxing_queue_size = "1024"
                                for pt_host in overseas_platform_host:
                                    if pt_host in record_url:
                                        rw_timeout = "50000000"
                                        analyzeduration = "40000000"
                                        probesize = "20000000"
                                        bufsize = "15000k"
                                        max_muxing_queue_size = "2048"
                                        break

                                ffmpeg_command = [
                                    'ffmpeg', "-y",
                                    "-v", "verbose",
                                    "-rw_timeout", rw_timeout,
                                    "-loglevel", "error",
                                    "-hide_banner",
                                    "-user_agent", user_agent,
                                    "-protocol_whitelist", "rtmp,crypto,file,http,https,tcp,tls,udp,rtp,httpproxy",
                                    "-thread_queue_size", "1024",
                                    "-analyzeduration", analyzeduration,
                                    "-probesize", probesize,
                                    "-fflags", "+discardcorrupt+igndts",
                                    "-re", "-i", real_url,
                                    "-bufsize", bufsize,
                                    "-sn", "-dn",
                                    # 走 FLV 代理时禁用 ffmpeg 内置重连: 代理在关键帧处断连期望
                                    # ffmpeg 读到 EOF 收尾当前文件(分段); 若开启 -reconnect_at_eof,
                                    # ffmpeg 会把 EOF 当错误重连本地代理继续写同一文件, 分段永不生效
                                    # (直连路径保留内置重连, 断流由 ffmpeg 自身恢复)
                                    *([] if flv_proxy else ["-reconnect_delay_max", "60",
                                                            "-reconnect_streamed", "-reconnect_at_eof"]),
                                    "-max_muxing_queue_size", max_muxing_queue_size,
                                    "-correct_ts_overflow", "1",
                                    "-avoid_negative_ts", "1",
                                    "-flush_packets", "1"
                                ]

                                # FFmpeg 8 严格模式会拒绝容器与扩展名不匹配的 HLS 段(chzzk 平台特有),
                                # 老版本不认识该参数, 按版本条件启用
                                if platform == 'CHZZK' and FFMPEG_VERSION_MAJOR >= 8:
                                    idx = ffmpeg_command.index("-re")
                                    ffmpeg_command[idx:idx] = ["-extension_picky", "0"]

                                headers = get_record_headers(platform, record_url)
                                if headers:
                                    ffmpeg_command.insert(11, "-headers")
                                    ffmpeg_command.insert(12, headers)

                                if proxy_address and flv_proxy is None:
                                    # 走 FLV 代理时不传 -http_proxy: -i 已是本地 127.0.0.1,
                                    # 传给代理会把 loopback 也路由进用户代理导致录制失败
                                    # (上游连接由代理自身的 proxy_addr 负责)
                                    ffmpeg_command.insert(1, "-http_proxy")
                                    ffmpeg_command.insert(2, proxy_address)

                                recording.add(record_name)
                                was_recording = True
                                start_record_time = datetime.datetime.now()
                                recording_time_list[record_name] = [start_record_time, record_quality_zh]
                                rec_info = f"\r{anchor_name} 准备开始录制视频: {full_path}"
                                if show_url:
                                    re_plat = ('WinkTV', 'PandaTV', 'ShowRoom', 'CHZZK', 'Youtube')
                                    if platform in re_plat:
                                        logger.info(
                                            f"{platform} | {anchor_name} | 直播源地址: {port_info.get('m3u8_url')}")
                                    else:
                                        logger.info(
                                            f"{platform} | {anchor_name} | 直播源地址: "
                                            f"{show_real_url if flv_proxy else real_url}")

                                only_audio_record = False
                                only_audio_platform_list = ['猫耳FM直播', 'Look直播']
                                if platform in only_audio_platform_list:
                                    only_audio_record = True

                                record_save_type = video_save_type

                                if is_flv_preferred_platform(record_url) and port_info.get('flv_url'):
                                    codec = utils.get_query_params(port_info['flv_url'], "codec")
                                    if codec and codec[0] == 'h265':
                                        logger.warning("FLV is not supported for h265 codec, use TS format instead")
                                        record_save_type = "TS"

                                if only_audio_record or any(i in record_save_type for i in ['MP3', 'M4A']):
                                    try:
                                        now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                                        extension = "mp3" if "m4a" not in record_save_type.lower() else "m4a"
                                        name_format = "_%03d" if split_video_by_time else ""
                                        save_file_path = (f"{full_path}/{anchor_name}_{title_in_name}{now}"
                                                          f"{name_format}.{extension}")

                                        if split_video_by_time:
                                            print(f'\r{anchor_name} 准备开始录制音频: {save_file_path}')

                                            if "MP3" in record_save_type:
                                                command = [
                                                    "-map", "0:a",
                                                    "-c:a", "libmp3lame",
                                                    "-ab", "320k",
                                                    "-f", "segment",
                                                    "-segment_time", split_time,
                                                    "-reset_timestamps", "1",
                                                    save_file_path,
                                                ]
                                            else:
                                                command = [
                                                    "-map", "0:a",
                                                    "-c:a", "aac",
                                                    "-bsf:a", "aac_adtstoasc",
                                                    "-ab", "320k",
                                                    "-f", "segment",
                                                    "-segment_time", split_time,
                                                    "-segment_format", 'mpegts',
                                                    "-reset_timestamps", "1",
                                                    save_file_path,
                                                ]

                                        else:
                                            if "MP3" in record_save_type:
                                                command = [
                                                    "-map", "0:a",
                                                    "-c:a", "libmp3lame",
                                                    "-ab", "320k",
                                                    save_file_path,
                                                ]

                                            else:
                                                command = [
                                                    "-map", "0:a",
                                                    "-c:a", "aac",
                                                    "-bsf:a", "aac_adtstoasc",
                                                    "-ab", "320k",
                                                    "-movflags", "+faststart",
                                                    save_file_path,
                                                ]

                                        ffmpeg_command.extend(command)
                                        comment_end = check_subprocess(
                                            record_name,
                                            record_url,
                                            ffmpeg_command,
                                            record_save_type,
                                            custom_script
                                        )
                                        if comment_end:
                                            if flv_proxy:
                                                flv_proxy.close()
                                                flv_proxy = None
                                            return

                                    except subprocess.CalledProcessError as e:
                                        logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                        with max_request_lock:
                                            error_count += 1
                                            error_window.append(1)

                                if only_flv_record:
                                    logger.info(f"Use Direct Downloader to Download FLV Stream: {record_url}")
                                    filename = anchor_name + f'_{title_in_name}' + now + '.flv'
                                    save_file_path = f'{full_path}/{filename}'
                                    print(f'{rec_info}/{filename}')

                                    subs_file_path = save_file_path.rsplit('.', maxsplit=1)[0]
                                    subs_thread_name = f'subs_{Path(subs_file_path).name}'
                                    if create_time_file:
                                        create_var[subs_thread_name] = threading.Thread(
                                            target=generate_subtitles, args=(record_name, subs_file_path)
                                        )
                                        create_var[subs_thread_name].daemon = True
                                        create_var[subs_thread_name].start()

                                    try:
                                        flv_url = port_info.get('flv_url')
                                        if flv_url:
                                            recording.add(record_name)
                                            was_recording = True
                                            start_record_time = datetime.datetime.now()
                                            recording_time_list[record_name] = [start_record_time, record_quality_zh]

                                            download_success = direct_download_stream(
                                                flv_url, save_file_path, record_name, record_url, platform,
                                                proxy_address
                                            )

                                            if download_success:
                                                stream_interrupted = True
                                                print(
                                                    f"\n{anchor_name} {time.strftime('%Y-%m-%d %H:%M:%S')} 直播录制完成\n")

                                            recording.discard(record_name)
                                        else:
                                            logger.debug("未找到FLV直播流，跳过录制")
                                    except Exception as e:
                                        clear_record_info(record_name, record_url)
                                        color_obj.print_colored(
                                            f"\n{anchor_name} {time.strftime('%Y-%m-%d %H:%M:%S')} 直播录制出错,请检查网络\n",
                                            color_obj.RED)
                                        logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                        with max_request_lock:
                                            error_count += 1
                                            error_window.append(1)

                                elif record_save_type == "FLV":
                                    filename = anchor_name + f'_{title_in_name}' + now + ".flv"
                                    print(f'{rec_info}/{filename}')
                                    save_file_path = full_path + '/' + filename

                                    try:
                                        if split_video_by_time:
                                            now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                                            save_file_path = f"{full_path}/{anchor_name}_{title_in_name}{now}_%03d.flv"
                                            command = [
                                                "-map", "0",
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-bsf:a", "aac_adtstoasc",
                                                "-f", "segment",
                                                "-segment_time", split_time,
                                                "-segment_format", "flv",
                                                "-reset_timestamps", "1",
                                                save_file_path
                                            ]

                                        else:
                                            command = [
                                                "-map", "0",
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-bsf:a", "aac_adtstoasc",
                                                "-f", "flv",
                                                "{path}".format(path=save_file_path),
                                            ]
                                        ffmpeg_command.extend(command)

                                        comment_end = check_subprocess(
                                            record_name,
                                            record_url,
                                            ffmpeg_command,
                                            record_save_type,
                                            custom_script
                                        )
                                        if comment_end:
                                            if flv_proxy:
                                                flv_proxy.close()
                                                flv_proxy = None
                                            return

                                    except subprocess.CalledProcessError as e:
                                        logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                        with max_request_lock:
                                            error_count += 1
                                            error_window.append(1)

                                    try:
                                        if converts_to_mp4:
                                            seg_file_path = f"{full_path}/{anchor_name}_{title_in_name}{now}_%03d.mp4"
                                            if split_video_by_time:
                                                segment_video(
                                                    save_file_path, seg_file_path,
                                                    segment_format='mp4', segment_time=split_time,
                                                    is_original_delete=delete_origin_file
                                                )
                                            else:
                                                threading.Thread(
                                                    target=converts_mp4,
                                                    args=(save_file_path, delete_origin_file)
                                                ).start()

                                        else:
                                            seg_file_path = f"{full_path}/{anchor_name}_{title_in_name}{now}_%03d.flv"
                                            if split_video_by_time:
                                                segment_video(
                                                    save_file_path, seg_file_path,
                                                    segment_format='flv', segment_time=split_time,
                                                    is_original_delete=delete_origin_file
                                                )
                                    except Exception as e:
                                        logger.error(f"转码失败: {e} ")

                                elif record_save_type == "MKV":
                                    filename = anchor_name + f'_{title_in_name}' + now + ".mkv"
                                    print(f'{rec_info}/{filename}')
                                    save_file_path = full_path + '/' + filename

                                    try:
                                        if split_video_by_time:
                                            now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                                            save_file_path = f"{full_path}/{anchor_name}_{title_in_name}{now}_%03d.mkv"
                                            command = [
                                                "-flags", "global_header",
                                                "-c:v", "copy",
                                                "-c:a", "aac",
                                                "-map", "0",
                                                "-f", "segment",
                                                "-segment_time", split_time,
                                                "-segment_format", "matroska",
                                                "-reset_timestamps", "1",
                                                save_file_path,
                                            ]

                                        else:
                                            command = [
                                                "-flags", "global_header",
                                                "-map", "0",
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-f", "matroska",
                                                "{path}".format(path=save_file_path),
                                            ]
                                        ffmpeg_command.extend(command)

                                        comment_end = check_subprocess(
                                            record_name,
                                            record_url,
                                            ffmpeg_command,
                                            record_save_type,
                                            custom_script
                                        )
                                        if comment_end:
                                            if flv_proxy:
                                                flv_proxy.close()
                                                flv_proxy = None
                                            return

                                    except subprocess.CalledProcessError as e:
                                        logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                        with max_request_lock:
                                            error_count += 1
                                            error_window.append(1)

                                elif record_save_type == "MP4":
                                    filename = anchor_name + f'_{title_in_name}' + now + ".mp4"
                                    print(f'{rec_info}/{filename}')
                                    save_file_path = full_path + '/' + filename

                                    try:
                                        if split_video_by_time:
                                            now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                                            save_file_path = f"{full_path}/{anchor_name}_{title_in_name}{now}_%03d.mp4"
                                            command = [
                                                "-c:v", "copy",
                                                "-c:a", "aac",
                                                "-map", "0",
                                                "-f", "segment",
                                                "-segment_time", split_time,
                                                "-segment_format", "mp4",
                                                "-reset_timestamps", "1",
                                                "-movflags", "+frag_keyframe+empty_moov",
                                                save_file_path,
                                            ]

                                        else:
                                            command = [
                                                "-map", "0",
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-f", "mp4",
                                                save_file_path,
                                            ]

                                        ffmpeg_command.extend(command)
                                        comment_end = check_subprocess(
                                            record_name,
                                            record_url,
                                            ffmpeg_command,
                                            record_save_type,
                                            custom_script
                                        )
                                        if comment_end:
                                            if flv_proxy:
                                                flv_proxy.close()
                                                flv_proxy = None
                                            return

                                    except subprocess.CalledProcessError as e:
                                        logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                        with max_request_lock:
                                            error_count += 1
                                            error_window.append(1)

                                else:
                                    if split_video_by_time:
                                        now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                                        filename = anchor_name + f'_{title_in_name}' + now + ".ts"
                                        print(f'{rec_info}/{filename}')

                                        try:
                                            save_file_path = f"{full_path}/{anchor_name}_{title_in_name}{now}_%03d.ts"
                                            command = [
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-map", "0",
                                                "-f", "segment",
                                                "-segment_time", split_time,
                                                "-segment_format", 'mpegts',
                                                "-reset_timestamps", "1",
                                                save_file_path,
                                            ]

                                            ffmpeg_command.extend(command)
                                            comment_end = check_subprocess(
                                                record_name,
                                                record_url,
                                                ffmpeg_command,
                                                record_save_type,
                                                custom_script
                                            )
                                            if comment_end:
                                                if converts_to_mp4:
                                                    file_paths = utils.get_file_paths(os.path.dirname(save_file_path))
                                                    prefix = os.path.basename(save_file_path).rsplit('_', maxsplit=1)[0]
                                                    for path in file_paths:
                                                        if prefix in path:
                                                            try:
                                                                threading.Thread(
                                                                    target=converts_mp4,
                                                                    args=(path, delete_origin_file)
                                                                ).start()
                                                            except subprocess.CalledProcessError as e:
                                                                logger.error(f"转码失败: {e} ")
                                                if flv_proxy:
                                                    flv_proxy.close()
                                                    flv_proxy = None
                                                return

                                        except subprocess.CalledProcessError as e:
                                            logger.error(
                                                f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                            with max_request_lock:
                                                error_count += 1
                                                error_window.append(1)

                                    else:
                                        filename = anchor_name + f'_{title_in_name}' + now + ".ts"
                                        print(f'{rec_info}/{filename}')
                                        save_file_path = full_path + '/' + filename

                                        try:
                                            command = [
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-map", "0",
                                                "-f", "mpegts",
                                                save_file_path,
                                            ]

                                            ffmpeg_command.extend(command)
                                            comment_end = check_subprocess(
                                                record_name,
                                                record_url,
                                                ffmpeg_command,
                                                record_save_type,
                                                custom_script
                                            )
                                            if comment_end:
                                                threading.Thread(
                                                    target=converts_mp4, args=(save_file_path, delete_origin_file)
                                                ).start()
                                                if flv_proxy:
                                                    flv_proxy.close()
                                                    flv_proxy = None
                                                return

                                        except subprocess.CalledProcessError as e:
                                            logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                            with max_request_lock:
                                                error_count += 1
                                                error_window.append(1)

                                count_time = time.time()
                                stream_interrupted = True  # 直播录制中断, 标记为需要快速重试
                                was_recording = False

                except Exception as e:
                    logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                    if was_recording:
                        stream_interrupted = True  # 录制异常中断, 也进行快速重试
                    with max_request_lock:
                        error_count += 1
                        error_window.append(1)

                if record_url in priority_urls:
                    num = random.randint(-1, 1) + priority_delay
                else:
                    num = random.randint(-5, 5) + delay_default
                if num < 0:
                    num = 0

                if error_count > 20:
                    num = num + 60
                    color_obj.print_colored("\r瞬时错误太多,延迟加60秒", color_obj.YELLOW)

                # 直播录制结束(不论正常结束或断流中断), 统一走快速重试检测逻辑
                # 前5次2~5秒随机, 后5次5~8秒随机
                if stream_interrupted and interrupted_retries < max_retry_interrupted:
                    x = retry_delay(interrupted_retries)
                    interrupted_retries += 1
                    print(f"\r{anchor_name} 直播中断, 第{interrupted_retries}次重试检测中... "
                          f"(最多{max_retry_interrupted}次)", end="")
                else:
                    stream_interrupted = False
                    interrupted_retries = 0
                    x = num

                # 这里是正常循环
                while x:
                    x = x - 1
                    if loop_time:
                        print(f'\r{anchor_name}循环等待{x}秒 ', end="")
                    time.sleep(1)
                if loop_time:
                    print('\r检测直播间中...', end="")
        except Exception as e:
            logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
            with max_request_lock:
                error_count += 1
                error_window.append(1)
            time.sleep(2)


def backup_file(file_path: str, backup_dir_path: str, limit_counts: int = 6) -> None:
    try:
        if not os.path.exists(backup_dir_path):
            os.makedirs(backup_dir_path)

        timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        backup_file_name = os.path.basename(file_path) + '_' + timestamp
        backup_file_path = os.path.join(backup_dir_path, backup_file_name).replace("\\", "/")
        shutil.copy2(file_path, backup_file_path)

        files = os.listdir(backup_dir_path)
        _files = [f for f in files if f.startswith(os.path.basename(file_path))]
        _files.sort(key=lambda x: os.path.getmtime(os.path.join(backup_dir_path, x)))

        while len(_files) > limit_counts:
            oldest_file = _files[0]
            os.remove(os.path.join(backup_dir_path, oldest_file))
            _files = _files[1:]

    except Exception as e:
        logger.error(f'\r备份配置文件 {file_path} 失败：{str(e)}')


def backup_file_start() -> None:
    config_md5 = ''
    url_config_md5 = ''

    while True:
        try:
            if os.path.exists(config_file):
                new_config_md5 = utils.check_md5(config_file)
                if new_config_md5 != config_md5:
                    backup_file(config_file, backup_dir)
                    config_md5 = new_config_md5

            if os.path.exists(url_config_file):
                new_url_config_md5 = utils.check_md5(url_config_file)
                if new_url_config_md5 != url_config_md5:
                    backup_file(url_config_file, backup_dir)
                    url_config_md5 = new_url_config_md5
            time.sleep(600)
        except Exception as e:
            logger.error(f"备份配置文件失败, 错误信息: {e}")


def check_ffmpeg_existence() -> bool:
    global FFMPEG_VERSION_MAJOR
    try:
        result = subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            version_line = lines[0]
            built_line = lines[1]
            print(version_line)
            print(built_line)
            match = re.search(r'ffmpeg version (\d+)', version_line)
            if match:
                FFMPEG_VERSION_MAJOR = int(match.group(1))
    except subprocess.CalledProcessError as e:
        logger.error(e)
    except FileNotFoundError:
        pass
    finally:
        if check_ffmpeg():
            time.sleep(1)
            return True
    return False


# --------------------------初始化程序-------------------------------------
print("-----------------------------------------------------")
print("|                DouyinLiveRecorder                 |")
print("-----------------------------------------------------")

print(f"版本号: {version}")
print("GitHub: https://github.com/ihmily/DouyinLiveRecorder")
print(f'支持平台: {platforms}')
print('.....................................................')
if not check_ffmpeg_existence():
    logger.error("缺少ffmpeg无法进行录制，程序退出")
    sys.exit(1)
os.makedirs(os.path.dirname(config_file), exist_ok=True)
t3 = threading.Thread(target=backup_file_start, args=(), daemon=True)
t3.start()
utils.remove_duplicate_lines(url_config_file)


def read_config_value(config_parser: configparser.RawConfigParser, section: str, option: str, default_value: Any) \
        -> Any:
    try:

        config_parser.read(config_file, encoding=text_encoding)
        if '录制设置' not in config_parser.sections():
            config_parser.add_section('录制设置')
        if '推送配置' not in config_parser.sections():
            config_parser.add_section('推送配置')
        if 'Cookie' not in config_parser.sections():
            config_parser.add_section('Cookie')
        if 'Authorization' not in config_parser.sections():
            config_parser.add_section('Authorization')
        if '账号密码' not in config_parser.sections():
            config_parser.add_section('账号密码')
        return config_parser.get(section, option)
    except (configparser.NoSectionError, configparser.NoOptionError):
        if section not in config_parser.sections():
            config_parser.add_section(section)
        config_parser.set(section, option, str(default_value))
        with open(config_file, 'w', encoding=text_encoding) as f:
            config_parser.write(f)
        return default_value


options = {"是": True, "否": False}
config = configparser.RawConfigParser()
language = read_config_value(config, '录制设置', 'language(zh_cn/en)', "zh_cn")
skip_proxy_check = options.get(read_config_value(config, '录制设置', '是否跳过代理检测(是/否)', "否"), False)
if language and 'en' not in language.lower():
    from i18n import translated_print

    builtins.print = translated_print

try:
    if skip_proxy_check:
        global_proxy = True
    else:
        print('系统代理检测中，请耐心等待...')
        response_g = urllib.request.urlopen("https://www.google.com/", timeout=15)
        global_proxy = True
        print('\r全局/规则网络代理已开启√')
        pd = ProxyDetector()
        if pd.is_proxy_enabled():
            proxy_info = pd.get_proxy_info()
            print("System Proxy: http://{}:{}".format(proxy_info.ip, proxy_info.port))
except HTTPError as err:
    print(f"HTTP error occurred: {err.code} - {err.reason}")
except URLError:
    color_obj.print_colored("INFO：未检测到全局/规则网络代理，请检查代理配置（若无需录制海外直播请忽略此条提示）",
                            color_obj.YELLOW)
except Exception as err:
    print("An unexpected error occurred:", err)

while True:

    try:
        if not os.path.isfile(config_file):
            with open(config_file, 'w', encoding=text_encoding) as file:
                pass

        ini_URL_content = ''
        if os.path.isfile(url_config_file):
            with open(url_config_file, 'r', encoding=text_encoding) as file:
                ini_URL_content = file.read().strip()

        if not ini_URL_content.strip():
            color_obj.print_colored(
                f"检测到 {url_config_file} 为空: 请在该文件中添加直播间网址(每行一个), 保存后程序会自动检测生效",
                color_obj.YELLOW)
    except OSError as err:
        logger.error(f"发生 I/O 错误: {err}")

    video_save_path = read_config_value(config, '录制设置', '直播保存路径(不填则默认)', "")
    folder_by_platform = options.get(read_config_value(config, '录制设置', '保存文件夹是否以平台区分', "是"), True)
    folder_by_author = options.get(read_config_value(config, '录制设置', '保存文件夹是否以作者区分', "是"), False)
    folder_by_time = options.get(read_config_value(config, '录制设置', '保存文件夹是否以时间区分', "否"), False)
    folder_by_title = options.get(read_config_value(config, '录制设置', '保存文件夹是否以标题区分', "否"), False)
    filename_by_title = options.get(read_config_value(config, '录制设置', '保存文件名是否包含标题', "否"), False)
    clean_emoji = options.get(read_config_value(config, '录制设置', '是否去除名称中的表情符号', "是"), True)
    video_save_type = read_config_value(config, '录制设置', '视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频', "ts")
    video_record_quality = read_config_value(config, '录制设置', '原画|超清|高清|标清|流畅', "原画")
    use_proxy = options.get(read_config_value(config, '录制设置', '是否使用代理ip(是/否)', "是"), False)
    proxy_addr_bak = read_config_value(config, '录制设置', '代理地址', "")
    proxy_addr = None if not use_proxy else proxy_addr_bak
    max_request = int(read_config_value(config, '录制设置', '同一时间访问网络的线程数', 3))
    semaphore = threading.Semaphore(max_request)
    delay_default = int(read_config_value(config, '录制设置', '循环时间(秒)', 120))
    local_delay_default = int(read_config_value(config, '录制设置', '排队读取网址时间(秒)', 0))
    loop_time = options.get(read_config_value(config, '录制设置', '是否显示循环秒数', "否"), False)
    show_url = options.get(read_config_value(config, '录制设置', '是否显示直播源地址', "否"), False)
    split_video_by_time = options.get(read_config_value(config, '录制设置', '分段录制是否开启', "否"), False)
    enable_https_recording = options.get(read_config_value(config, '录制设置', '是否强制启用https录制', "否"), False)
    disk_space_limit = float(read_config_value(config, '录制设置', '录制空间剩余阈值(gb)', 1.0))
    split_time = str(read_config_value(config, '录制设置', '视频分段时间(秒)', 1800))
    max_retry_interrupted = int(read_config_value(config, '录制设置', '直播断流重试次数', 10))
    priority_delay = max(1, int(read_config_value(config, '优先监控', '优先监控轮询间隔(秒)', 3)))
    converts_to_mp4 = options.get(read_config_value(config, '录制设置', '录制完成后自动转为mp4格式', "否"), False)
    detect_resolution_change = options.get(
        read_config_value(config, '录制设置', '录制完成后检测分辨率变化(是/否)', "是"), False)
    detect_pts_jumps_enabled = options.get(
        read_config_value(config, '录制设置', '录制完成后检测时间戳跳变(是/否)', "是"), False)
    converts_to_h264 = options.get(read_config_value(config, '录制设置', 'mp4格式重新编码为h264', "否"), False)
    delete_origin_file = options.get(read_config_value(config, '录制设置', '追加格式后删除原文件', "否"), False)
    create_time_file = options.get(read_config_value(config, '录制设置', '生成时间字幕文件', "否"), False)
    is_run_script = options.get(read_config_value(config, '录制设置', '是否录制完成后执行自定义脚本', "否"), False)
    custom_script = read_config_value(config, '录制设置', '自定义脚本执行命令', "") if is_run_script else None
    enable_proxy_platform = read_config_value(
        config, '录制设置', '使用代理录制的平台(逗号分隔)',
        'tiktok, soop, pandalive, winktv, flextv, popkontv, twitch, liveme, showroom, chzzk, shopee, shp, youtu, faceit'
    )
    enable_proxy_platform_list = enable_proxy_platform.replace('，', ',').split(',') if enable_proxy_platform else None
    extra_enable_proxy = read_config_value(config, '录制设置', '额外使用代理录制的平台(逗号分隔)', '')
    extra_enable_proxy_platform_list = extra_enable_proxy.replace('，', ',').split(',') if extra_enable_proxy else None
    live_status_push = read_config_value(config, '推送配置', '直播状态推送渠道', "")
    dingtalk_api_url = read_config_value(config, '推送配置', '钉钉推送接口链接', "")
    xizhi_api_url = read_config_value(config, '推送配置', '微信推送接口链接', "")
    bark_msg_api = read_config_value(config, '推送配置', 'bark推送接口链接', "")
    bark_msg_level = read_config_value(config, '推送配置', 'bark推送中断级别', "active")
    bark_msg_ring = read_config_value(config, '推送配置', 'bark推送铃声', "bell")
    dingtalk_phone_num = read_config_value(config, '推送配置', '钉钉通知@对象(填手机号)', "")
    dingtalk_is_atall = options.get(read_config_value(config, '推送配置', '钉钉通知@全体(是/否)', "否"), False)
    tg_token = read_config_value(config, '推送配置', 'tgapi令牌', "")
    tg_chat_id = read_config_value(config, '推送配置', 'tg聊天id(个人或者群组id)', "")
    email_host = read_config_value(config, '推送配置', 'SMTP邮件服务器', "")
    open_smtp_ssl = options.get(read_config_value(config, '推送配置', '是否使用SMTP服务SSL加密(是/否)', "是"), True)
    smtp_port = read_config_value(config, '推送配置', 'SMTP邮件服务器端口', "")
    login_email = read_config_value(config, '推送配置', '邮箱登录账号', "")
    email_password = read_config_value(config, '推送配置', '发件人密码(授权码)', "")
    sender_email = read_config_value(config, '推送配置', '发件人邮箱', "")
    sender_name = read_config_value(config, '推送配置', '发件人显示昵称', "")
    to_email = read_config_value(config, '推送配置', '收件人邮箱', "")
    ntfy_api = read_config_value(config, '推送配置', 'ntfy推送地址', "")
    ntfy_tags = read_config_value(config, '推送配置', 'ntfy推送标签', "tada")
    ntfy_email = read_config_value(config, '推送配置', 'ntfy推送邮箱', "")
    pushplus_token = read_config_value(config, '推送配置', 'pushplus推送token', "")
    push_message_title = read_config_value(config, '推送配置', '自定义推送标题', "直播间状态更新通知")
    begin_push_message_text = read_config_value(config, '推送配置', '自定义开播推送内容', "")
    over_push_message_text = read_config_value(config, '推送配置', '自定义关播推送内容', "")
    disable_record = options.get(read_config_value(config, '推送配置', '只推送通知不录制(是/否)', "否"), False)
    push_check_seconds = int(read_config_value(config, '推送配置', '直播推送检测频率(秒)', 1800))
    begin_show_push = options.get(read_config_value(config, '推送配置', '开播推送开启(是/否)', "是"), True)
    over_show_push = options.get(read_config_value(config, '推送配置', '关播推送开启(是/否)', "否"), False)
    sooplive_username = read_config_value(config, '账号密码', 'sooplive账号', '')
    sooplive_password = read_config_value(config, '账号密码', 'sooplive密码', '')
    flextv_username = read_config_value(config, '账号密码', 'flextv账号', '')
    flextv_password = read_config_value(config, '账号密码', 'flextv密码', '')
    popkontv_username = read_config_value(config, '账号密码', 'popkontv账号', '')
    popkontv_partner_code = read_config_value(config, '账号密码', 'partner_code', 'P-00001')
    popkontv_password = read_config_value(config, '账号密码', 'popkontv密码', '')
    twitcasting_account_type = read_config_value(config, '账号密码', 'twitcasting账号类型', 'normal')
    twitcasting_username = read_config_value(config, '账号密码', 'twitcasting账号', '')
    twitcasting_password = read_config_value(config, '账号密码', 'twitcasting密码', '')
    popkontv_access_token = read_config_value(config, 'Authorization', 'popkontv_token', '')
    dy_cookie = read_config_value(config, 'Cookie', '抖音cookie', '')
    ks_cookie = read_config_value(config, 'Cookie', '快手cookie', '')
    tiktok_cookie = read_config_value(config, 'Cookie', 'tiktok_cookie', '')
    hy_cookie = read_config_value(config, 'Cookie', '虎牙cookie', '')
    douyu_cookie = read_config_value(config, 'Cookie', '斗鱼cookie', '')
    yy_cookie = read_config_value(config, 'Cookie', 'yy_cookie', '')
    bili_cookie = read_config_value(config, 'Cookie', 'B站cookie', '')
    xhs_cookie = read_config_value(config, 'Cookie', '小红书cookie', '')
    bigo_cookie = read_config_value(config, 'Cookie', 'bigo_cookie', '')
    blued_cookie = read_config_value(config, 'Cookie', 'blued_cookie', '')
    sooplive_cookie = read_config_value(config, 'Cookie', 'sooplive_cookie', '')
    netease_cookie = read_config_value(config, 'Cookie', 'netease_cookie', '')
    qiandurebo_cookie = read_config_value(config, 'Cookie', '千度热播_cookie', '')
    pandatv_cookie = read_config_value(config, 'Cookie', 'pandatv_cookie', '')
    maoerfm_cookie = read_config_value(config, 'Cookie', '猫耳fm_cookie', '')
    winktv_cookie = read_config_value(config, 'Cookie', 'winktv_cookie', '')
    flextv_cookie = read_config_value(config, 'Cookie', 'flextv_cookie', '')
    look_cookie = read_config_value(config, 'Cookie', 'look_cookie', '')
    twitcasting_cookie = read_config_value(config, 'Cookie', 'twitcasting_cookie', '')
    baidu_cookie = read_config_value(config, 'Cookie', 'baidu_cookie', '')
    weibo_cookie = read_config_value(config, 'Cookie', 'weibo_cookie', '')
    kugou_cookie = read_config_value(config, 'Cookie', 'kugou_cookie', '')
    twitch_cookie = read_config_value(config, 'Cookie', 'twitch_cookie', '')
    liveme_cookie = read_config_value(config, 'Cookie', 'liveme_cookie', '')
    huajiao_cookie = read_config_value(config, 'Cookie', 'huajiao_cookie', '')
    liuxing_cookie = read_config_value(config, 'Cookie', 'liuxing_cookie', '')
    showroom_cookie = read_config_value(config, 'Cookie', 'showroom_cookie', '')
    acfun_cookie = read_config_value(config, 'Cookie', 'acfun_cookie', '')
    changliao_cookie = read_config_value(config, 'Cookie', 'changliao_cookie', '')
    yinbo_cookie = read_config_value(config, 'Cookie', 'yinbo_cookie', '')
    yingke_cookie = read_config_value(config, 'Cookie', 'yingke_cookie', '')
    zhihu_cookie = read_config_value(config, 'Cookie', 'zhihu_cookie', '')
    chzzk_cookie = read_config_value(config, 'Cookie', 'chzzk_cookie', '')
    haixiu_cookie = read_config_value(config, 'Cookie', 'haixiu_cookie', '')
    vvxqiu_cookie = read_config_value(config, 'Cookie', 'vvxqiu_cookie', '')
    yiqilive_cookie = read_config_value(config, 'Cookie', '17live_cookie', '')
    langlive_cookie = read_config_value(config, 'Cookie', 'langlive_cookie', '')
    pplive_cookie = read_config_value(config, 'Cookie', 'pplive_cookie', '')
    six_room_cookie = read_config_value(config, 'Cookie', '6room_cookie', '')
    lehaitv_cookie = read_config_value(config, 'Cookie', 'lehaitv_cookie', '')
    huamao_cookie = read_config_value(config, 'Cookie', 'huamao_cookie', '')
    shopee_cookie = read_config_value(config, 'Cookie', 'shopee_cookie', '')
    youtube_cookie = read_config_value(config, 'Cookie', 'youtube_cookie', '')
    taobao_cookie = read_config_value(config, 'Cookie', 'taobao_cookie', '')
    jd_cookie = read_config_value(config, 'Cookie', 'jd_cookie', '')
    faceit_cookie = read_config_value(config, 'Cookie', 'faceit_cookie', '')
    migu_cookie = read_config_value(config, 'Cookie', 'migu_cookie', '')
    lianjie_cookie = read_config_value(config, 'Cookie', 'lianjie_cookie', '')
    laixiu_cookie = read_config_value(config, 'Cookie', 'laixiu_cookie', '')
    picarto_cookie = read_config_value(config, 'Cookie', 'picarto_cookie', '')

    video_save_type_list = ("FLV", "MKV", "TS", "MP4", "MP3音频", "M4A音频", "MP3", "M4A")
    if video_save_type and video_save_type.upper() in video_save_type_list:
        video_save_type = video_save_type.upper()
    else:
        video_save_type = "TS"

    check_path = video_save_path or default_path
    if utils.check_disk_capacity(check_path, show=first_run) < disk_space_limit:
        exit_recording = True
        if not recording:
            logger.warning(f"Disk space remaining is below {disk_space_limit} GB. "
                           f"Exiting program due to the disk space limit being reached.")
            sys.exit(-1)

    try:
        url_comments, url_line_list, url_tuples_list = [[] for _ in range(3)]
        seen_full_lines = set()      # 用于检测完全重复的行
        seen_url_info = {}           # url -> {'is_comment', 'output_idx'} 用于检测重复URL
        retained_annotations = {}    # url -> 保留的注释标注行 output_idx（暂停标注去重基准）
        output_lines = []            # 最终写回文件的行
        file_modified = False        # 标记文件是否被修改

        # 一次性读取所有行到内存
        new_priority_urls = set()
        with open(url_config_file, "r", encoding=text_encoding, errors='ignore') as file:
            raw_lines = file.readlines()

        for origin_line in raw_lines:
            stripped = origin_line.strip()
            
            # 保留空行
            if not stripped:
                output_lines.append(origin_line)
                continue
            
            # 检测完全重复的行（非注释且长度>=18）
            is_comment = stripped.startswith('#')
            if len(stripped) >= 18 and not is_comment:
                if stripped in seen_full_lines:
                    # 完全重复，整行丢弃
                    file_modified = True
                    continue
                seen_full_lines.add(stripped)
            
            # 处理有效内容
            line = stripped.lstrip('#') if is_comment else stripped
            
            if len(line) < 18:
                output_lines.append(origin_line)
                continue

            # 修正多个"主播: "的格式错误
            line_spilt = line.split('主播: ')
            if len(line_spilt) > 2:
                corrected = f'{line_spilt[0]}主播: {line_spilt[-1]}'
                if corrected != line:
                    line = corrected
                    file_modified = True
                    # 同步修正origin_line，保留#前缀和原始换行符
                    suffix = origin_line[len(origin_line.rstrip('\n\r')):]
                    prefix = '#' if is_comment else ''
                    origin_line = prefix + corrected + suffix

            if is_comment:
                line = line.lstrip('#')

            quality, url, name, has_priority = split_url_line(line, video_record_quality)

            url = 'https://' + url if '://' not in url else url
            url_host = url.split('/')[2]

            platform_host = [
                'live.douyin.com',
                'v.douyin.com',
                'www.douyin.com',
                'live.kuaishou.com',
                'www.huya.com',
                'www.douyu.com',
                'www.yy.com',
                'live.bilibili.com',
                'www.redelight.cn',
                'www.xiaohongshu.com',
                'xhslink.com',
                'www.bigo.tv',
                'slink.bigovideo.tv',
                'app.blued.cn',
                'cc.163.com',
                'qiandurebo.com',
                'fm.missevan.com',
                'look.163.com',
                'twitcasting.tv',
                'live.baidu.com',
                'weibo.com',
                'fanxing.kugou.com',
                'fanxing2.kugou.com',
                'mfanxing.kugou.com',
                'www.huajiao.com',
                'www.7u66.com',
                'wap.7u66.com',
                'live.acfun.cn',
                'm.acfun.cn',
                'live.tlclw.com',
                'wap.tlclw.com',
                'live.ybw1666.com',
                'wap.ybw1666.com',
                'www.inke.cn',
                'www.zhihu.com',
                'www.haixiutv.com',
                "h5webcdnp.vvxqiu.com",
                "17.live",
                'www.lang.live',
                "m.pp.weimipopo.com",
                "v.6.cn",
                "m.6.cn",
                'www.lehaitv.com',
                'h.catshow168.com',
                'm.tb.cn',
                'huodong.m.taobao.com',
                '3.cn',
                'eco.m.jd.com',
                'www.miguvideo.com',
                'm.miguvideo.com',
                'show.lailianjie.com',
                'www.imkktv.com',
                'www.picarto.tv'
            ]
            overseas_platform_host = [
                'www.tiktok.com',
                'play.sooplive.co.kr',
                'm.sooplive.co.kr',
                'www.sooplive.com',
                'm.sooplive.com',
                'www.pandalive.co.kr',
                'www.winktv.co.kr',
                'www.flextv.co.kr',
                'www.ttinglive.com',
                'www.popkontv.com',
                'www.twitch.tv',
                'www.liveme.com',
                'www.showroom-live.com',
                'chzzk.naver.com',
                'm.chzzk.naver.com',
                'live.shopee.',
                '.shp.ee',
                'www.youtube.com',
                'youtu.be',
                'www.faceit.com'
            ]

            platform_host.extend(overseas_platform_host)
            clean_url_host_list = (
                "live.douyin.com",
                "live.bilibili.com",
                "www.huajiao.com",
                "www.zhihu.com",
                "www.huya.com",
                "chzzk.naver.com",
                "www.liveme.com",
                "www.haixiutv.com",
                "v.6.cn",
                "m.6.cn",
                'www.lehaitv.com'
            )

            if 'live.shopee.' in url_host or '.shp.ee' in url_host:
                url_host = 'live.shopee.' if 'live.shopee.' in url_host else '.shp.ee'

            if url_host in platform_host or any(ext in url for ext in (".flv", ".m3u8")):
                # 清理URL参数
                if url_host in clean_url_host_list:
                    old_url = url
                    url = url.split('?')[0]
                    if url != old_url:
                        file_modified = True
                        origin_line = origin_line.replace(old_url, url)

                if 'xiaohongshu' in url:
                    host_id = re.search('&host_id=(.*?)(?=&|$)', url)
                    if host_id:
                        old_url = url
                        url = url.split('?')[0] + f'?host_id={host_id.group(1)}'
                        if url != old_url:
                            file_modified = True
                            origin_line = origin_line.replace(old_url, url)

                # 检查URL是否重复（无论注释/非注释）
                if url in seen_url_info:
                    first_info = seen_url_info[url]
                    # 去重时的优先标记处理：标注保留、不激活（见 dedup_priority_action）
                    action = dedup_priority_action(
                        has_priority, is_comment,
                        first_info['is_comment'], first_info.get('is_priority', False))
                    if action == 'merge':
                        kept_line = output_lines[first_info['output_idx']]
                        body = kept_line.rstrip('\n\r')
                        suffix = kept_line[len(body):]
                        output_lines[first_info['output_idx']] = body + ',优先: 是' + suffix
                        first_info['is_priority'] = True
                        file_modified = True
                    elif action == 'keep_comment':
                        # 生效行 + 带标记注释行：保留注释作为暂停标注（同 URL 仅保留一条），不激活
                        if url not in retained_annotations:
                            output_lines.append(origin_line)
                            retained_annotations[url] = len(output_lines) - 1
                        else:
                            file_modified = True  # 重复的注释标注丢弃
                        continue
                    elif action == 'keep_comment_and_active':
                        # 带标记注释行 + 无标记生效行：两者都保留，消除顺序差异
                        if url not in retained_annotations:
                            retained_annotations[url] = first_info['output_idx']
                            output_lines.append(origin_line)
                            url_comments = [i for i in url_comments if i != url]
                            url_tuples_list.append((quality, url, name))
                            # 去重基准切换到新增生效行，后续生效行重复按其去重
                            seen_url_info[url] = {
                                'is_comment': False,
                                'is_priority': False,
                                'output_idx': len(output_lines) - 1,
                            }
                        else:
                            file_modified = True  # 已有标注注释: 丢弃多余生效行
                        continue
                    # 第一条是注释，新的是非注释 → 解开第一条，删除新行
                    if first_info['is_comment'] and not is_comment:
                        old_line = output_lines[first_info['output_idx']]
                        # 去掉行首空白、# 及 # 后所有空白字符，保留原有缩进
                        lstripped = old_line.lstrip()
                        leading_ws = old_line[:len(old_line) - len(lstripped)]
                        if lstripped.startswith('#'):
                            output_lines[first_info['output_idx']] = leading_ws + lstripped[1:].lstrip()
                        url_comments = [i for i in url_comments if i != url]
                        url_tuples_list.append((quality, url, name))
                        first_info['is_comment'] = False
                    # 去重后按保留行决定优先级（含合并标记后立即生效）
                    if first_info.get('is_priority', False) and not first_info['is_comment']:
                        new_priority_urls.add(url)
                    # 所有重复情况：删除新行，保留第一条
                    file_modified = True
                    continue

                # 首次出现的URL，记录位置和注释状态
                seen_url_info[url] = {
                    'is_comment': is_comment,
                    'is_priority': has_priority,
                    'output_idx': len(output_lines)
                }

                if is_comment:
                    url_comments.append(url)
                    # 确保注释行以#开头
                    if not stripped.startswith('#'):
                        output_lines.append('#' + origin_line)
                    else:
                        output_lines.append(origin_line)
                else:
                    output_lines.append(origin_line)
                    new_line = (quality, url, name)
                    url_tuples_list.append(new_line)
                    if has_priority:
                        new_priority_urls.add(url)
            else:
                # 未知链接，注释掉
                if not is_comment:
                    color_obj.print_colored(f"\r{stripped} 本行包含未知链接.此条跳过", color_obj.YELLOW)
                    output_lines.append('#' + origin_line)
                    file_modified = True
                else:
                    output_lines.append(origin_line)

        # 解析完成后一次性替换优先集合，避免解析期间线程看到空集合
        priority_urls = new_priority_urls

        # 处理need_update_line_list
        while len(need_update_line_list):
            a = need_update_line_list.pop()
            replace_words = a.split('|')
            if replace_words[0] != replace_words[1]:
                if replace_words[1].startswith("#"):
                    start_with = '#'
                    new_word = replace_words[1][1:]
                else:
                    start_with = None
                    new_word = replace_words[1]
                # 在output_lines中查找并替换（优先命中非注释行，避免改写注释标注）
                i = find_writeback_index(output_lines, replace_words[0])
                if i is not None:
                    out_line = output_lines[i]
                    if '主播: ' in out_line:
                        # 目标行已含主播名(来自注释标注解开或已写回), 跳过避免重复写入
                        continue
                    if start_with:
                        output_lines[i] = start_with + out_line.replace(replace_words[0], new_word)
                    else:
                        output_lines[i] = out_line.replace(replace_words[0], new_word)
                    file_modified = True

        # 统一写回文件（仅当内容有变化时）
        if file_modified:
            with file_update_lock:
                with open(url_config_file, "w", encoding=text_encoding) as f:
                    f.writelines(output_lines)

        text_no_repeat_url = list(set(url_tuples_list))

        if len(text_no_repeat_url) > 0:
            for url_tuple in text_no_repeat_url:
                monitoring = len(running_list)

                if url_tuple[1] in not_record_list:
                    continue

                if url_tuple[1] not in running_list:
                    print(f"\r{'新增' if not first_start else '传入'}地址: {url_tuple[1]}")
                    monitoring += 1
                    args = [url_tuple, monitoring]
                    create_var[f'thread_{monitoring}'] = threading.Thread(target=start_record, args=args)
                    create_var[f'thread_{monitoring}'].daemon = True
                    create_var[f'thread_{monitoring}'].start()
                    running_list.append(url_tuple[1])
                    time.sleep(local_delay_default)
        url_tuples_list = []
        first_start = False

    except Exception as err:
        logger.error(f"错误信息: {err} 发生错误的行数: {err.__traceback__.tb_lineno}")

    if first_run:
        t = threading.Thread(target=display_info, args=(), daemon=True)
        t.start()
        t2 = threading.Thread(target=adjust_max_request, args=(), daemon=True)
        t2.start()
        first_run = False

    time.sleep(3)
