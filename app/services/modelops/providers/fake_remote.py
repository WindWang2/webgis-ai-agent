"""Fake remote inference server（**TEST-ONLY fixture**，Epic §9）。

诚实边界：本模块只服务测试（本地回环 + 随机端口），**不是**生产组件；
生产 remote provider 的端点经 operator allowlist 配置。行为可编程：
成功/错误码/延迟/超大响应/重定向/垃圾体 —— 驱动 remote 安全与取消测试。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional


class FakeRemoteInferenceServer:
    """本地回环 JSON 推理端点（stdlib ThreadingHTTPServer；随机端口）。

    ``infer_hook(payload) -> (status, body_dict)`` 可注入行为；缺省回显
    确定性概率（与 tiny_reference 的 softmax 不同源，但足够协议测试）。
    """

    def __init__(
        self,
        *,
        infer_hook: Optional[Callable[[Dict[str, Any]], tuple]] = None,
        health_status: int = 200,
    ) -> None:
        self._infer_hook = infer_hook
        self._health_status = health_status
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.requests_seen: list = []

    # ── lifecycle ───────────────────────────────────────────────────
    def start(self) -> str:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # 静默
                return

            def _send(self, status: int, body: Dict[str, Any]) -> None:
                raw = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802 — stdlib 约定
                if self.path == "/health":
                    outer.requests_seen.append({"path": self.path})
                    self._send(outer._health_status, {"ok": outer._health_status == 200})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802 — stdlib 约定
                if self.path != "/infer":
                    self._send(404, {"error": "not found"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except ValueError:
                    self._send(400, {"error": "bad json"})
                    return
                outer.requests_seen.append({"path": self.path, "batch": payload.get("batch")})
                if outer._infer_hook is not None:
                    status, body = outer._infer_hook(payload)
                    self._send(status, body)
                    return
                import math

                h = int(payload.get("height", 0))
                w = int(payload.get("width", 0))
                n = int(payload.get("batch", 1))
                cls = 3
                probs = [
                    [
                        [[0.2, 0.5, 0.3] for _ in range(w)] for _ in range(h)
                    ]
                    for _ in range(n)
                ]
                # 行波纹（确定性，可断言行序），数值合法（和=1）。
                for i in range(n):
                    for y in range(h):
                        a = 0.5 + 0.1 * math.sin(y)
                        probs[i][y] = [[1 - a, a, 0.0] for _ in range(w)]
                self._send(200, {"class_probabilities": probs})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "FakeRemoteInferenceServer":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ── 行为注入 helper ─────────────────────────────────────────────
    @staticmethod
    def redirect_hook(location: str) -> Callable[[Dict[str, Any]], tuple]:
        def hook(_payload: Dict[str, Any]) -> tuple:
            return 302, {"redirect": location}

        return hook

    @staticmethod
    def delayed_hook(delay_s: float, delegate: Callable[[Dict[str, Any]], tuple]) -> Callable[[Dict[str, Any]], tuple]:
        def hook(payload: Dict[str, Any]) -> tuple:
            time.sleep(delay_s)
            return delegate(payload)

        return hook

    @staticmethod
    def error_hook(status: int, message: str) -> Callable[[Dict[str, Any]], tuple]:
        def hook(_payload: Dict[str, Any]) -> tuple:
            return status, {"error": message}

        return hook
