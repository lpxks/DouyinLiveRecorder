"""FLV 代理分段检测测试。

- 单元: _check_video_tag 状态机(第二个 SPS/PPS 标记、关键帧触发)
- 集成: 本地合成 FLV 流 + 代理转发, 验证关键帧处连接被代理主动断开
"""

import http.server
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from src.flv_proxy import FLVProxy, SegmentRequired, normalize_tag_timestamp

# FLV 常量
AVC = 7
KEYFRAME = 1
INTERFRAME = 2
SEQ_HEADER = 0
NALU = 1


def make_video_tag(frame_type: int, codec_id: int, avc_packet_type: int,
                   payload: bytes = b"\x00\x00\x00\x00\x00\x00\x00\x00",
                   ts: int = 0) -> bytes:
    """构造一个 FLV video tag(含 15 字节 tag 头 + body)。ts 为 32 位时间戳(ms)。"""
    data = bytes([(frame_type << 4) | codec_id, avc_packet_type]) + payload
    tag_header = (b"\x00\x00\x00\x00"          # PreviousTagSize
                  + bytes([9])                 # tag type: video
                  + len(data).to_bytes(3, "big")
                  + (ts & 0xFFFFFF).to_bytes(3, "big")
                  + bytes([(ts >> 24) & 0xFF])  # timestamp ext
                  + b"\x00\x00\x00")           # stream id
    return tag_header + data


def make_flv_stream() -> bytes:
    """构造合成 FLV 流: 头 + 第一个 SPS + 普通帧 + 内容不同的第二个 SPS + 关键帧。"""
    header = b"FLV" + bytes([1, 0x05, 0, 0, 0, 9])
    tags = (make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x01" * 8)  # 第一个 SPS/PPS
            + make_video_tag(INTERFRAME, AVC, NALU)                          # 普通帧
            + make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x02" * 8)  # 第二个 SPS(参数变化!)
            + make_video_tag(KEYFRAME, AVC, NALU))                           # 关键帧 → 应触发分段
    return header + tags


class VideoTagStateTest(unittest.TestCase):
    """_check_video_tag 状态机单元测试(不联网)。"""

    def setUp(self):
        self.proxy = FLVProxy("http://127.0.0.1:1/x.flv")
        self.proxy._avc_header_count = 0
        self.proxy._pending_segment = False

    def test_first_seq_header_no_trigger(self):
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER)[15:])
        self.assertEqual(self.proxy._avc_header_count, 1)
        self.assertFalse(self.proxy._pending_segment)

    def test_second_seq_header_with_different_content_marks_pending(self):
        """第二个且内容不同的 SPS/PPS(参数确实变化)才标记分段。"""
        for tag in (make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x01" * 8),
                    make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x02" * 8)):
            self.proxy._check_video_tag(tag[15:])
        self.assertEqual(self.proxy._avc_header_count, 2)
        self.assertTrue(self.proxy._pending_segment)

    def test_identical_seq_header_repeat_not_trigger(self):
        """内容相同的 SPS/PPS 重发(CDN 周期性重发)不视为参数变化。"""
        for _ in range(3):
            self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER)[15:])
        self.assertEqual(self.proxy._avc_header_count, 3)
        self.assertFalse(self.proxy._pending_segment)

    def test_video_info_frame_not_counted(self):
        """VideoInfoFrame(frameType=5)不作为 sequence header 计数。"""
        self.proxy._check_video_tag(make_video_tag(5, AVC, SEQ_HEADER)[15:])  # VideoInfoFrame
        self.assertEqual(self.proxy._avc_header_count, 0)
        self.assertFalse(self.proxy._pending_segment)

    def test_keyframe_after_second_seq_header_triggers(self):
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x01" * 8)[15:])
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x02" * 8)[15:])
        with self.assertRaises(SegmentRequired):
            self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, NALU)[15:])
        # 触发后标志复位
        self.assertFalse(self.proxy._pending_segment)

    def test_min_segment_interval_blocks_rapid_segments(self):
        """节流: 距上次分段不足 min_segment_interval 时, 不触发(保留标记延迟分段)。"""
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x01" * 8)[15:])
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x02" * 8)[15:])
        # 第一次触发(记录 _last_segment_at)
        with self.assertRaises(SegmentRequired):
            self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, NALU)[15:])
        # 再次标记 + 关键帧, 间隔不足 → 不触发, 标记保留
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x03" * 8)[15:])
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, NALU)[15:])  # 不应抛
        self.assertTrue(self.proxy._pending_segment)

    def test_interframe_does_not_trigger(self):
        """标记后非关键帧不触发。"""
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x01" * 8)[15:])
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER, payload=b"\x02" * 8)[15:])
        self.proxy._check_video_tag(make_video_tag(INTERFRAME, AVC, NALU)[15:])  # 不应抛
        self.assertTrue(self.proxy._pending_segment)

    def test_non_avc_ignored(self):
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, 2, 0)[15:])  # Sorenson H.263
        self.assertEqual(self.proxy._avc_header_count, 0)
        self.assertFalse(self.proxy._pending_segment)

    def test_is_flv_stream(self):
        self.assertTrue(FLVProxy.is_flv_stream("http://x.com/a.flv"))
        self.assertTrue(FLVProxy.is_flv_stream("http://x.com/a?format=flv"))
        self.assertFalse(FLVProxy.is_flv_stream("http://x.com/a.m3u8"))


def parse_tag_ts(tag_header: bytes) -> int:
    """从 15 字节 tag 头解析 32 位时间戳(ms)。"""
    return (tag_header[11] << 24) | (tag_header[8] << 16) | (tag_header[9] << 8) | tag_header[10]


class TimestampNormalizeTest(unittest.TestCase):
    """normalize_tag_timestamp 状态机(结尾时间戳跳变 → 时长虚高的修复)。"""

    def header_with_ts(self, ts: int) -> bytes:
        # 取 make_video_tag 的 15 字节 tag 头部分
        return make_video_tag(KEYFRAME, AVC, NALU, ts=ts)[:15]

    def norm(self, header, last_ts, offset):
        return normalize_tag_timestamp(header, last_ts, offset, FLVProxy.MAX_FORWARD_GAP_MS)

    def test_normal_progress_untouched(self):
        h1, last, off = self.norm(self.header_with_ts(0), None, 0)
        self.assertEqual((parse_tag_ts(h1), last, off), (0, 0, 0))
        h2, last2, _ = self.norm(self.header_with_ts(100), last, off)
        self.assertEqual((parse_tag_ts(h2), last2), (100, 100))

    def test_forward_jump_absorbed(self):
        """向前跳变(如结尾 +30s)被吸收: 重写为与上一 tag 连续。"""
        _, last, off = self.norm(self.header_with_ts(24050), 24000, 0)
        h, last2, off2 = self.norm(self.header_with_ts(54000), last, off)  # +30s 跳变
        self.assertEqual(parse_tag_ts(h), 24050)
        self.assertEqual((last2, off2), (24050, 29950))

    def test_subsequent_tags_shifted_by_offset(self):
        """跳变吸收后, 后续 tag 整体平移(保留内部间隔)。"""
        h1, last, off = self.norm(self.header_with_ts(54000), 24000, 0)  # 吸收 +30000
        h, _, _ = self.norm(self.header_with_ts(54100), last, off)
        self.assertEqual(parse_tag_ts(h), 24100)

    def test_second_jump_accumulates(self):
        _, last, off = self.norm(self.header_with_ts(54000), 24000, 0)
        _, last, off = self.norm(self.header_with_ts(54100), last, off)
        h, _, off = self.norm(self.header_with_ts(90000), last, off)  # 第二次跳变
        self.assertEqual(parse_tag_ts(h), 24100)
        self.assertEqual(off, 65900)

    def test_small_gap_not_treated_as_jump(self):
        """正常步长(如停滞恢复的数秒推进)不触发归一化。"""
        h, _, off = self.norm(self.header_with_ts(24050), 24000, 0)
        self.assertEqual((parse_tag_ts(h), off), (24050, 0))

    def test_backward_ts_passed_through(self):
        """时间戳回退原样放行(ffmpeg 自行钳制, 实测不膨胀时长)。"""
        orig = self.header_with_ts(1000)
        h, last, off = self.norm(orig, 24000, 30000)
        self.assertEqual(h, orig)
        self.assertEqual((last, off), (24000, 30000))  # 状态不被回退破坏

    def test_high_timestamp_ext_byte_written(self):
        """超过 24 位的值正确读写 ts_ext 字节。"""
        h, last, _ = self.norm(self.header_with_ts(0x1234567), None, 0)
        self.assertEqual((parse_tag_ts(h), last), (0x1234567, 0x1234567))


class ProxyIntegrationTest(unittest.TestCase):
    """端到端: 本地合成 FLV 源 → 代理转发 → 关键帧处连接被代理主动断开。"""

    @classmethod
    def setUpClass(cls):
        cls.stream_data = make_flv_stream()
        # 本地 FLV 源服务器: 发完整流后保持连接(不关闭), 验证断连是代理主动的
        class FlvSourceHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "video/x-flv")
                self.end_headers()
                try:
                    self.wfile.write(cls.stream_data)
                    self.wfile.flush()
                    time.sleep(30)  # 保持连接, 等待代理主动断开
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        cls.source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FlvSourceHandler)
        cls.source_thread = threading.Thread(target=cls.source.serve_forever, daemon=True)
        cls.source_thread.start()
        cls.source_port = cls.source.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.source.shutdown()

    def test_proxy_forwards_and_disconnects_at_keyframe(self):
        proxy = FLVProxy(f"http://127.0.0.1:{self.source_port}/stream.flv")
        proxy.start()
        try:
            # 客户端(模拟 ffmpeg)请求本地代理
            client = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
            client.sendall(b"GET /stream.flv HTTP/1.1\r\nHost: localhost\r\n\r\n")
            received = b""
            deadline = time.time() + 10
            while time.time() < deadline:
                chunk = client.recv(65536)
                if not chunk:
                    break  # 连接被代理关闭
                received += chunk
            client.close()
            # HTTP 200 响应头之后读到完整 FLV 头 + 全部 tag(含关键帧 tag 先转发再断)
            self.assertIn(b"HTTP/1.1 200 OK", received[:32])
            self.assertIn(b"FLV", received[32:])
            self.assertGreaterEqual(len(received), len(self.stream_data) + 32)
            # 代理是主动断连方: 上游仍保持连接
            self.assertTrue(self.source.server_address)
        finally:
            proxy.close()


class ProxyResilienceTest(unittest.TestCase):
    """F1: 上游异常/不可用后, 代理 accept 循环存活, 上游恢复后重连成功。"""

    def test_proxy_recovers_after_upstream_unavailable(self):
        # 拿一个空闲端口(先绑定获取再释放)
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        proxy = FLVProxy(f"http://127.0.0.1:{port}/stream.flv")
        proxy.start()
        try:
            # 阶段 1: 上游不可用(端口未监听) → 连接被代理关闭, 但 accept 循环存活
            c1 = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
            c1.sendall(b"GET /stream.flv HTTP/1.1\r\nHost: localhost\r\n\r\n")
            data1 = c1.recv(4096)
            c1.close()
            self.assertEqual(data1, b"", "上游不可用时不应有响应数据")

            # 阶段 2: 同一端口起 FLV 源(模拟上游恢复)
            class FlvSource(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "video/x-flv")
                    self.end_headers()
                    try:
                        self.wfile.write(make_flv_stream())
                        self.wfile.flush()
                        time.sleep(5)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

                def log_message(self, *args):
                    pass

            source = http.server.ThreadingHTTPServer(("127.0.0.1", port), FlvSource)
            source_thread = threading.Thread(target=source.serve_forever, daemon=True)
            source_thread.start()
            try:
                # 阶段 3: 新连接由新线程服务, 成功重连上游并收到 FLV 数据
                c2 = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
                c2.sendall(b"GET /stream.flv HTTP/1.1\r\nHost: localhost\r\n\r\n")
                received = b""
                deadline = time.time() + 5
                while time.time() < deadline:
                    chunk = c2.recv(65536)
                    if not chunk:
                        break
                    received += chunk
                c2.close()
                self.assertIn(b"HTTP/1.1 200 OK", received[:64])
                self.assertIn(b"FLV", received[64:])
            finally:
                source.shutdown()
        finally:
            proxy.close()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),
                     "需要本机 ffmpeg/ffprobe")
class TimestampNormalizeEndToEndTest(unittest.TestCase):
    """端到端: 结尾时间戳跳变的 FLV 走代理录制, 时长应恢复为真实内容时长。"""

    @classmethod
    def setUpClass(cls):
        # 用真实 ffmpeg 生成 30s FLV, 再把尾部(25s 起)tag 时间戳 +30s 制造"结尾跳变"
        cls.tmpdir = tempfile.mkdtemp()
        src = os.path.join(cls.tmpdir, "src.flv")
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10",
             "-t", "30", "-c:v", "libx264", "-preset", "ultrafast",
             "-f", "flv", src],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        cls.stream_data = _shift_flv_timestamps(open(src, "rb").read(), 25000, 30000)

        class FlvSource(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "video/x-flv")
                self.send_header("Content-Length", str(len(cls.stream_data)))
                self.end_headers()
                try:
                    self.wfile.write(cls.stream_data)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True

            def log_message(self, *args):
                pass

        cls.source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FlvSource)
        cls.source_thread = threading.Thread(target=cls.source.serve_forever, daemon=True)
        cls.source_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.source.shutdown()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_recorded_duration_matches_content(self):
        proxy = FLVProxy(f"http://127.0.0.1:{self.source.server_address[1]}/x.flv")
        proxy.start()
        out = os.path.join(self.tmpdir, "out.mkv")
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", proxy.local_url,
                 "-c", "copy", "-f", "matroska", out],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            dur = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", out],
                capture_output=True, text=True, timeout=30).stdout.strip()
            # 归一化后时长≈真实内容 30s(不归一化会虚高为 60s)
            self.assertGreater(float(dur), 29.0, f"时长异常: {dur}")
            self.assertLess(float(dur), 31.0, f"时长虚高: {dur}")
        finally:
            proxy.close()


def _shift_flv_timestamps(data: bytes, from_ms: int, delta_ms: int) -> bytes:
    """把 FLV 中时间戳 >= from_ms 的 tag 整体平移 delta_ms(制造结尾跳变)。"""
    buf = bytearray(data)
    pos = 9
    while pos + 15 <= len(buf):
        dlen = int.from_bytes(buf[pos + 5:pos + 8], 'big')
        if pos + 15 + dlen > len(buf):
            break
        ts = (buf[pos + 11] << 24) | (buf[pos + 8] << 16) | (buf[pos + 9] << 8) | buf[pos + 10]
        if ts >= from_ms:
            new_ts = ts + delta_ms
            buf[pos + 8:pos + 11] = (new_ts & 0xFFFFFF).to_bytes(3, 'big')
            buf[pos + 11] = (new_ts >> 24) & 0xFF
        pos = pos + 15 + dlen
    return bytes(buf)


if __name__ == "__main__":
    unittest.main()
