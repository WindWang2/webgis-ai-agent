"""全局请求体大小守卫中间件（#1648）。

nginx ``client_max_body_size 100M``（deploy/nginx/nginx.conf）之外的应用层
兜底 choke point：直连暴露面（k8s 集群内服务间调用、开发直挂 uvicorn、
未来若 nginx 帽调整）此前对认证用户打任意大 JSON 零防御 —— 请求 schema
的字段帽只覆盖已枚举字段，本中间件对**所有** HTTP 请求体生效。

语义：
- **Content-Length 声明超限 → 413 早拒**：整个 body 不被读入（零解析
  内存放大；IdempotencyMiddleware 的 50MB 缓冲也不会被触发）；
- **缺 Content-Length 的分块流式 body → 流式计数**：超限同样 413（下游
  读到被截断的 body，其响应被 send 包装吞弃 —— 客户端只收到 413）；
- **限额与 nginx 100M 逐字对齐**：标准部署（nginx 前置）下不收紧任何
  合法流（上传面路由级帽 ≤100M 已在 nginx 侧先行截断）；
- 413 走统一错误信封（``X-Error-Envelope: detail`` / legacy 开关回退
  同既有纪律）；websocket scope 无 body 语义，直接透传。
"""
from __future__ import annotations

import json
from typing import Dict, Optional

from fastapi import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exception import unified_error_envelope, wants_legacy_envelope

#: 与 deploy/nginx/nginx.conf ``client_max_body_size 100M`` 对齐。
MAX_BODY_BYTES = 100 * 1024 * 1024


class _BodyTooLarge(Exception):
    """内部信号：无 Content-Length 的流式 body 计数超限（仅本模块流转）。"""


def _content_length(scope: Scope) -> Optional[int]:
    """声明式 Content-Length（缺失/畸形 → None，走流式计数路径）。"""
    for key, value in scope.get("headers") or []:
        if key.lower() == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


async def _reject(scope: Scope, send: Send) -> None:
    """413 响应：统一错误信封（legacy detail 回退同既有纪律）。"""
    request = Request(scope)
    message = "请求体过大"
    content: Dict[str, object]
    if wants_legacy_envelope(request):
        content = {"detail": message}
    else:
        content = unified_error_envelope(
            413,
            message,
            request=request,
            exc=HTTPException(status_code=413, detail=message),
        )
        # _STATUS_CODE_TO_CODE 暂无 413 条目（缺省 SERVER_ERROR 会把 4xx
        # 误标成 5xx 语义）—— 显式补业务码，客户端按 code 分流。
        content["code"] = "PAYLOAD_TOO_LARGE"
    body = json.dumps(content, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """纯 ASGI 中间件：http 请求体字节帽（默认与 nginx 100M 对齐）。

    注册位置纪律（app/main.py）：必须位于 IdempotencyMiddleware **外侧**
    （其 50MB 体缓冲先于本件执行即失去「不整 body 读入」意义），同时位于
    CORS **内侧**（413 响应才能带上 CORS 头供浏览器读取）。
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None:
            if declared > self.max_bytes:
                await _reject(scope, send)
                return
            await self.app(scope, receive, send)
            return
        # 无 Content-Length（分块传输）—— 流式计数。超限时从中断点抛
        # _BodyTooLarge：异常穿过下游栈回到本件（响应尚未开始即可回 413；
        # 若响应已开始则截断 stands —— 只有流式请求处理器会走到该分支）。
        total = 0

        async def counting_receive() -> Message:
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body") or b"")
                if total > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        response_started = False

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except _BodyTooLarge:
            if not response_started:
                await _reject(scope, send)


__all__ = ["MAX_BODY_BYTES", "BodySizeLimitMiddleware"]
