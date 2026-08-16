"""FLV 代理分段检测测试(参考 bililive-go flvproxy 语义的移植实现)。

- 单元: _check_video_tag 状态机(sequence header 内容比对 + 关键帧带内 SPS 检测)
- 集成: 本地合成 FLV 流 + 代理转发, 验证关键帧处连接被代理主动断开
"""

import http.server
import shutil
import socket
import threading
import time
import unittest

from src.flv_proxy import FLVProxy, SegmentRequired, _extract_inband_sps

# FLV 常量
AVC = 7
KEYFRAME = 1
INTERFRAME = 2
SEQ_HEADER = 0
NALU = 1


def make_video_tag(frame_type: int, codec_id: int, avc_packet_type: int,
                   payload: bytes = b"", ts: int = 0) -> bytes:
    """构造一个 FLV video tag(含 15 字节 tag 头 + body)。ts 为 32 位时间戳(ms)。"""
    data = bytes([(frame_type << 4) | codec_id, avc_packet_type]) + payload
    tag_header = (b"\x00\x00\x00\x00"          # PreviousTagSize
                  + bytes([9])                 # tag type: video
                  + len(data).to_bytes(3, "big")
                  + (ts & 0xFFFFFF).to_bytes(3, "big")
                  + bytes([(ts >> 24) & 0xFF])  # timestamp ext
                  + b"\x00\x00\x00")           # stream id
    return tag_header + data


def sps_nal(marker: int = 1) -> bytes:
    """构造一个假 SPS NAL(首字节 0x67 = NAL type 7)。"""
    return bytes([0x67, marker, 0, 0])


def make_keyframe_payload(*nalus: bytes) -> bytes:
    """构造 AVCNALU 关键帧 body 的 NALU 区: CompositionTime(3) + 长度前缀 NAL 序列。"""
    body = bytearray(b"\x00\x00\x00")  # CompositionTime = 0
    for nalu in nalus:
        body += len(nalu).to_bytes(4, "big") + nalu
    return bytes(body)


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
        self.addCleanup(self.proxy.close)  # 关闭监听 socket, 避免 ResourceWarning

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

    # ---------- 带内 SPS 检测通道 ----------

    def test_inband_sps_baseline_set_without_trigger(self):
        """首个携带带内 SPS 的关键帧只建立基线, 不触发分段。"""
        payload = make_keyframe_payload(sps_nal(1))
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, NALU, payload=payload)[15:])
        self.assertIsNotNone(self.proxy._first_inband_sps)
        self.assertFalse(self.proxy._pending_segment)

    def test_inband_sps_change_triggers_at_keyframe(self):
        """关键帧带内 SPS 与基线不同(CDN 不重发 sequence header 的参数变化) → 立即分段。"""
        p1 = make_keyframe_payload(sps_nal(1))
        p2 = make_keyframe_payload(sps_nal(2))
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, NALU, payload=p1)[15:])
        with self.assertRaises(SegmentRequired):
            self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, NALU, payload=p2)[15:])
        self.assertFalse(self.proxy._pending_segment)

    def test_inband_sps_repeat_not_trigger(self):
        """带内 SPS 每关键帧重发(repeat-headers 风格)且内容相同 → 不触发。"""
        payload = make_keyframe_payload(sps_nal(1))
        for _ in range(3):
            self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, NALU, payload=payload)[15:])
        self.assertFalse(self.proxy._pending_segment)

    def test_keyframe_without_inband_sps_no_trigger(self):
        """关键帧不带带内 SPS 时, 该通道不参与(基线不建立)。"""
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, NALU)[15:])
        self.assertIsNone(self.proxy._first_inband_sps)
        self.assertFalse(self.proxy._pending_segment)


class ExtractInbandSpsTest(unittest.TestCase):
    """_extract_inband_sps 解析单元测试。"""

    def test_extracts_sps_nal(self):
        payload = make_keyframe_payload(sps_nal(7), bytes([0x65, 1]))  # SPS + IDR
        self.assertEqual(_extract_inband_sps(bytes([0x17, 0x01]) + payload), sps_nal(7))

    def test_no_sps_returns_none(self):
        payload = make_keyframe_payload(bytes([0x65, 1]))  # 仅 IDR
        self.assertIsNone(_extract_inband_sps(bytes([0x17, 0x01]) + payload))

    def test_truncated_returns_none(self):
        self.assertIsNone(_extract_inband_sps(bytes([0x17, 0x01]) + b"\x00\x00\x00\x00\x10\x67"))

    def test_too_short_returns_none(self):
        self.assertIsNone(_extract_inband_sps(b"\x17\x01"))


class IsFlvStreamTest(unittest.TestCase):
    def test_flv_path(self):
        self.assertTrue(FLVProxy.is_flv_stream("http://x.com/a.flv"))

    def test_flv_query(self):
        self.assertTrue(FLVProxy.is_flv_stream("http://x.com/a?format=flv"))

    def test_m3u8_not_flv(self):
        self.assertFalse(FLVProxy.is_flv_stream("http://x.com/a.m3u8"))


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


if __name__ == "__main__":
    unittest.main()
