"""FLV 流透明代理:检测 AVC sequence header(SPS/PPS)二次出现——即分辨率/编码参数
切换——并在下一个关键帧处关闭到 ffmpeg 的连接, 让 ffmpeg 读到 EOF 自然收尾当前
文件; 由上层录制循环重开新文件(新容器头, 参数正确), 避免"参数变化后永久花屏"。

参考 bililive-go src/pkg/flvproxy/proxy.go 的实现:
- 检测与触发分离: 第二个 SPS/PPS 只是"标记待分段", 等到关键帧才真正断连
  (分段点在 GOP 边界, 两个文件各自可独立解码、无缝拼接)
- 对 ffmpeg 完全透明: 断连 = EOF, ffmpeg 正常收尾
"""

import socket
import threading
import time

import httpx


class SegmentRequired(Exception):
    """分段条件触发(新 SPS/PPS 之后遇到关键帧)。"""


class _BufferedStream:
    """按需读取包装:httpx iter_bytes 是分块产出, 解析需要精确按 N 字节读。"""

    def __init__(self, iterable):
        self._it = iter(iterable)
        self._buf = b""

    def read(self, n: int) -> bytes:
        while len(self._buf) < n:
            try:
                self._buf += next(self._it)
            except StopIteration:
                break
        out, self._buf = self._buf[:n], self._buf[n:]
        return out


class FLVProxy:
    """FLV 透明代理服务器。

    ffmpeg 通过 ``local_url`` 拉流, 代理转发上游 FLV 数据的同时逐 tag 解析,
    检测到第二个 AVC sequence header 后等待下一个关键帧, 然后关闭与 ffmpeg 的
    连接触发其收尾。用法::

        proxy = FLVProxy(upstream_url, headers=headers, proxy_addr=proxy)
        proxy.start()
        real_url = proxy.local_url          # ffmpeg -i 指向这里
        ... ffmpeg 录制 ...
        proxy.close()
    """

    FLV_HEADER_SIZE = 9
    TAG_HEADER_SIZE = 15  # PreviousTagSize(4) + TagHeader(11)
    VIDEO_TAG = 9
    AVC_CODEC_ID = 7
    AVC_SEQ_HEADER = 0
    AVC_NALU = 1
    KEYFRAME_FRAME_TYPE = 1

    def __init__(self, upstream_url: str, headers: dict | None = None,
                 proxy_addr: str | None = None, min_segment_interval: float = 10.0):
        self.upstream_url = upstream_url
        self.headers = headers or {}
        self.proxy_addr = proxy_addr
        self.min_segment_interval = min_segment_interval

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self.local_url = f"http://127.0.0.1:{self.port}/stream.flv"

        self._lock = threading.Lock()
        self._avc_header_count = 0
        self._pending_segment = False
        self._last_segment_at = 0.0
        self._client_conn = None  # 到 ffmpeg 的连接, 用于强制断连

        self._thread: threading.Thread | None = None
        self._closed = False

    # ---------- 生命周期 ----------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, daemon=True, name="flv-proxy")
        self._thread.start()

    def close(self) -> None:
        self._closed = True
        self._force_close_client()
        try:
            self._server.close()
        except OSError:
            pass

    @staticmethod
    def is_flv_stream(url: str) -> bool:
        """判断上游 URL 是否为 FLV 流(代理仅对 FLV 输入有效)。"""
        path = url.split("?", 1)[0].lower()
        return path.endswith(".flv") or "format=flv" in url.lower()

    # ---------- 服务端 ----------

    def _serve(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            try:
                self._handle_client(conn)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle_client(self, client_conn: socket.socket) -> None:
        # 读 ffmpeg 的 HTTP 请求头(直到空行)
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = client_conn.recv(4096)
            if not chunk:
                return
            request += chunk
            if len(request) > 65536:
                return

        try:
            with httpx.Client(proxy=self.proxy_addr, timeout=None, verify=False,
                              follow_redirects=True) as client:
                with client.stream("GET", self.upstream_url, headers=self.headers) as resp:
                    if resp.status_code >= 300:
                        return
                    # 回 200 响应头给 ffmpeg
                    client_conn.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: video/x-flv\r\n"
                        b"Cache-Control: no-cache\r\n\r\n")
                    with self._lock:
                        self._client_conn = client_conn
                    src = _BufferedStream(resp.iter_bytes())
                    try:
                        self._parse_and_forward(src, client_conn)
                    except SegmentRequired:
                        # 关键帧处触发分段: 关闭到 ffmpeg 的连接, 让其读到 EOF 收尾
                        self._force_close_client()
        except OSError:
            pass

    def _force_close_client(self) -> None:
        with self._lock:
            conn = self._client_conn
            self._client_conn = None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    # ---------- 解析与转发 ----------

    def _parse_and_forward(self, src, dst) -> None:
        # 每个新连接(新文件)重置计数
        with self._lock:
            self._avc_header_count = 0
            self._pending_segment = False

        header = src.read(self.FLV_HEADER_SIZE)
        if len(header) != self.FLV_HEADER_SIZE:
            return
        dst.sendall(header)

        while True:
            tag_header = src.read(self.TAG_HEADER_SIZE)
            if len(tag_header) != self.TAG_HEADER_SIZE:
                return
            data_size = (tag_header[5] << 16) | (tag_header[6] << 8) | tag_header[7]
            tag_data = src.read(data_size)
            if len(tag_data) != data_size:
                return

            tag_type = tag_header[4]
            if tag_type == self.VIDEO_TAG and len(tag_data) > 0:
                try:
                    self._check_video_tag(tag_data)
                except SegmentRequired:
                    # 先转发当前关键帧 tag, 再触发断连(关键帧数据本身不丢)
                    dst.sendall(tag_header)
                    dst.sendall(tag_data)
                    raise
            dst.sendall(tag_header)
            dst.sendall(tag_data)

    def _check_video_tag(self, data: bytes) -> None:
        """检测第二个 AVC sequence header(分辨率/参数变化)并在关键帧处触发分段。

        FLV video tag 布局: 第 1 字节高 4 位 frameType、低 4 位 codecID;
        AVC 时第 2 字节为 AVCPacketType(0=sequence header, 1=NALU)。
        """
        if len(data) < 2:
            return
        frame_type = (data[0] >> 4) & 0x0F
        codec_id = data[0] & 0x0F
        if codec_id != self.AVC_CODEC_ID:  # 只处理 AVC (H.264)
            return
        avc_packet_type = data[1]

        if avc_packet_type == self.AVC_SEQ_HEADER:  # SPS/PPS
            with self._lock:
                self._avc_header_count += 1
                count = self._avc_header_count
            if count > 1:
                # 第二个 SPS/PPS = 分辨率/编码参数变化: 标记待分段, 等关键帧
                self._pending_segment = True

        is_keyframe = frame_type == self.KEYFRAME_FRAME_TYPE and avc_packet_type == self.AVC_NALU
        if is_keyframe and self._pending_segment:
            self._pending_segment = False
            with self._lock:
                self._last_segment_at = time.time()
            raise SegmentRequired()
