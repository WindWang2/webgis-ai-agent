"""#1648 全局请求体大小守卫（BodySizeLimitMiddleware）回归锁。

手驱动 ASGI（不依赖 httpx/TestClient 的传输语义）精确覆盖三条路径：
Content-Length 声明超限早拒（body 不被读入）、分块流式超限计数 413、
限额内放行；外加真实 app 的注册位置棘轮（Idempotency 外侧 / CORS 内侧）。
"""
from __future__ import annotations

import asyncio
import json

from app.core.body_limit import MAX_BODY_BYTES, BodySizeLimitMiddleware


def run(coro):
    return asyncio.run(coro)


class _InnerApp:
    """记录被调用次数与读到的 body 字节；http 请求回 200。"""

    def __init__(self) -> None:
        self.calls = 0
        self.body = b""

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        if scope.get("type") != "http":
            return
        while True:
            message = await receive()
            if message["type"] == "http.request":
                self.body += message.get("body") or b""
                if not message.get("more_body"):
                    break
            elif message["type"] == "http.disconnect":
                return
        await send({
            "type": "http.response.start", "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": b'{"ok":true}'})


def _scope(*, headers, method: str = "POST", path: str = "/api/v1/x") -> dict:
    return {
        "type": "http", "http_version": "1.1", "method": method,
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": headers, "scheme": "http",
        "server": ("testserver", 80), "client": ("testclient", 50000),
    }


async def _drive(app, scope, messages):
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    pending = list(messages)

    async def receive():
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    await app(scope, receive, send)
    return sent


def _response(sent: list[dict]) -> tuple[int, bytes]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body") or b"" for m in sent if m["type"] == "http.response.body"
    )
    return start["status"], body


def _mw(inner: _InnerApp, max_bytes: int = 1024) -> BodySizeLimitMiddleware:
    return BodySizeLimitMiddleware(inner, max_bytes=max_bytes)


# ── 限额与 nginx 对齐 ────────────────────────────────────────────────


def test_max_body_bytes_aligned_with_nginx_100m():
    assert MAX_BODY_BYTES == 100 * 1024 * 1024


# ── Content-Length 声明路径 ─────────────────────────────────────────


def test_declared_over_limit_413_without_reading_body():
    inner = _InnerApp()
    mw = _mw(inner)
    scope = _scope(headers=[
        (b"content-length", b"2048"), (b"content-type", b"application/json"),
    ])
    # 声明 2048 > 1024 限额：即使 body 消息就绪也绝不读入。
    sent = run(_drive(mw, scope, [
        {"type": "http.request", "body": b"x" * 2048, "more_body": False},
    ]))
    status, body = _response(sent)
    assert status == 413
    assert inner.calls == 0          # 下游未被调用
    assert inner.body == b""         # body 未被读入（零解析内存放大）


def test_declared_at_limit_passes_through():
    inner = _InnerApp()
    mw = _mw(inner)
    scope = _scope(headers=[(b"content-length", b"1024")])
    sent = run(_drive(mw, scope, [
        {"type": "http.request", "body": b"x" * 1024, "more_body": False},
    ]))
    status, _ = _response(sent)
    assert status == 200
    assert inner.calls == 1 and len(inner.body) == 1024


def test_missing_content_length_passes_through():
    inner = _InnerApp()
    mw = _mw(inner)
    scope = _scope(headers=[])
    sent = run(_drive(mw, scope, [
        {"type": "http.request", "body": b"small", "more_body": False},
    ]))
    status, _ = _response(sent)
    assert status == 200
    assert inner.body == b"small"


# ── 分块流式路径 ─────────────────────────────────────────────────────


def test_chunked_over_limit_413():
    inner = _InnerApp()
    mw = _mw(inner)
    scope = _scope(headers=[(b"transfer-encoding", b"chunked")])
    sent = run(_drive(mw, scope, [
        {"type": "http.request", "body": b"x" * 600, "more_body": True},
        {"type": "http.request", "body": b"y" * 600, "more_body": False},
    ]))
    status, _ = _response(sent)
    assert status == 413
    # 下游已进入但只读到第一块（超限即中断，拒绝式不静默放完整流）。
    assert inner.calls == 1
    assert len(inner.body) == 600


def test_chunked_under_limit_passes_through():
    inner = _InnerApp()
    mw = _mw(inner)
    scope = _scope(headers=[(b"transfer-encoding", b"chunked")])
    sent = run(_drive(mw, scope, [
        {"type": "http.request", "body": b"x" * 400, "more_body": True},
        {"type": "http.request", "body": b"y" * 400, "more_body": False},
    ]))
    status, _ = _response(sent)
    assert status == 200
    assert len(inner.body) == 800


# ── 非 http scope 透传 ──────────────────────────────────────────────


def test_non_http_scope_passthrough():
    inner = _InnerApp()
    mw = _mw(inner)
    sent = run(_drive(mw, {"type": "lifespan"}, [{"type": "lifespan.startup"}]))
    assert sent == []          # 中间件不碰 lifespan 收发
    assert inner.calls == 1    # 原样透传给下游


# ── 413 响应形状（统一错误信封 / legacy 回退）────────────────────────


def test_413_uses_unified_error_envelope():
    inner = _InnerApp()
    mw = _mw(inner)
    scope = _scope(headers=[(b"content-length", b"4096")])
    sent = run(_drive(mw, scope, []))
    status, body = _response(sent)
    assert status == 413
    envelope = json.loads(body)
    assert envelope["code"] == "PAYLOAD_TOO_LARGE"
    assert envelope["success"] is False
    assert envelope["message"] == "请求体过大"
    assert envelope["data"] is None
    # 分类学附加字段（classify_exception(HTTPException(413)) → validation）
    assert envelope["category"] == "validation"
    content_type = next(
        v for k, v in next(
            m for m in sent if m["type"] == "http.response.start"
        )["headers"] if k == b"content-type"
    )
    assert content_type == b"application/json"


def test_413_legacy_detail_envelope_fallback():
    inner = _InnerApp()
    mw = _mw(inner)
    scope = _scope(headers=[
        (b"content-length", b"4096"), (b"x-error-envelope", b"detail"),
    ])
    sent = run(_drive(mw, scope, []))
    status, body = _response(sent)
    assert status == 413
    assert json.loads(body) == {"detail": "请求体过大"}


# ── 真实 app 注册位置棘轮 ───────────────────────────────────────────


def test_registered_in_real_app_with_ordering():
    from app.main import app

    entries = {
        m.cls.__name__: m for m in app.user_middleware
        if isinstance(getattr(m, "cls", None), type)
    }
    assert "BodySizeLimitMiddleware" in entries
    # 默认构造（无参）→ max_bytes 走 MAX_BODY_BYTES（nginx 100M 对齐）。
    assert entries["BodySizeLimitMiddleware"].args == ()
    assert entries["BodySizeLimitMiddleware"].kwargs == {}
    # user_middleware 列表顺序 = 外层优先（add_middleware 反序插 0 位）：
    # 必须位于 IdempotencyMiddleware 外侧（先于其 50MB 体缓冲生效），
    # 同时位于 CORS 内侧（413 响应带 CORS 头供浏览器读取）。
    names = [m.cls.__name__ for m in app.user_middleware]
    assert names.index("BodySizeLimitMiddleware") < names.index("IdempotencyMiddleware")
    assert names.index("BodySizeLimitMiddleware") > names.index("CORSMiddleware")
