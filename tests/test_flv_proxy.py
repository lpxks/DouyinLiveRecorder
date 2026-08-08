"""FLV 代理分段检测测试。

- 单元: _check_video_tag 状态机(第二个 SPS/PPS 标记、关键帧触发)
- 集成: 本地合成 FLV 流 + 代理转发, 验证关键帧处连接被代理主动断开
"""

import http.server
import socket
import threading
import time
import unittest

from src.flv_proxy import FLVProxy, SegmentRequired

# FLV 常量
AVC = 7
KEYFRAME = 1
INTERFRAME = 2
SEQ_HEADER = 0
NALU = 1


def make_video_tag(frame_type: int, codec_id: int, avc_packet_type: int,
                   payload: bytes = b"\x00\x00\x00\x00\x00\x00\x00\x00") -> bytes:
    """构造一个 FLV video tag(含 15 字节 tag 头 + body)。"""
    data = bytes([(frame_type << 4) | codec_id, avc_packet_type]) + payload
    tag_header = (b"\x00\x00\x00\x00"          # PreviousTagSize
                  + bytes([9])                 # tag type: video
                  + len(data).to_bytes(3, "big")
                  + b"\x00\x00\x00\x00"        # timestamp
                  + b"\x00\x00\x00")           # stream id
    return tag_header + data


def make_flv_stream() -> bytes:
    """构造合成 FLV 流: 头 + 第一个 SPS + 普通帧 + 第二个 SPS + 关键帧。"""
    header = b"FLV" + bytes([1, 0x05, 0, 0, 0, 9])
    tags = (make_video_tag(KEYFRAME, AVC, SEQ_HEADER)   # 第一个 SPS/PPS
            + make_video_tag(INTERFRAME, AVC, NALU)     # 普通帧
            + make_video_tag(KEYFRAME, AVC, SEQ_HEADER)  # 第二个 SPS/PPS(参数变化!)
            + make_video_tag(KEYFRAME, AVC, NALU))      # 关键帧 → 应触发分段
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

    def test_second_seq_header_marks_pending(self):
        for tag in (make_video_tag(KEYFRAME, AVC, SEQ_HEADER),
                    make_video_tag(KEYFRAME, AVC, SEQ_HEADER)):
            self.proxy._check_video_tag(tag[15:])
        self.assertEqual(self.proxy._avc_header_count, 2)
        self.assertTrue(self.proxy._pending_segment)

    def test_keyframe_after_second_seq_header_triggers(self):
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER)[15:])
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER)[15:])
        with self.assertRaises(SegmentRequired):
            self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, NALU)[15:])
        # 触发后标志复位
        self.assertFalse(self.proxy._pending_segment)

    def test_interframe_does_not_trigger(self):
        """标记后非关键帧不触发。"""
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER)[15:])
        self.proxy._check_video_tag(make_video_tag(KEYFRAME, AVC, SEQ_HEADER)[15:])
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


if __name__ == "__main__":
    unittest.main()
