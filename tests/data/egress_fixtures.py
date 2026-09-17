"""egress 守卫接线测试的回环 HTTP 服务 fixture。

为什么不用"死端口必然连接失败"验证放行路径：Windows + TUN/安全软件会把
回环死端口的 SYN 静默吞掉（timeout 而非 refused），失败形态因机器而异。
放行路径的正向证明 = 真实回环服务 200 往返；拒绝路径（守卫在连接前抛
typed 错误）不依赖网络栈。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer


class _HelloHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — stdlib 接口
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默
        pass


@contextmanager
def loopback_http_server():
    """在 127.0.0.1 临时端口起一个 200-GET 服务；yield base_url。"""
    server = HTTPServer(("127.0.0.1", 0), _HelloHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
