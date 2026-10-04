"""WebSocket endpoint for real-time GIS data updates.

审计 SEC-03：之前 WS 端点完全无认证 + 无 session 所有权校验 —— 任意客户端
知道 session_id 就能连接并写入他人的 map state（layer toggle/remove/snapshot）。

现在：1) 要求合法 access token；2) 校验 session 属于该用户；3) rate limit
keyed on client IP 而非 session_id（攻击者无法通过轮换 session_id 绕过）。

WS 感知通道在前端是死代码（useWebSocket 从未挂载），所以收紧认证不会破坏
任何活跃功能。
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
import json
import logging
import time

from app.services.ws_service import manager, PERCEPTION_HANDLERS
from app.core.auth import authenticate_ws_token, WsAuthError
from app.core.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])

_WS_RATE_LIMIT_MAX = 5
_WS_RATE_LIMIT_WINDOW = 60
# security F-15：入站帧预算（与 ws_collab 同口径）+ 周期性撤销复核。
_MAX_INBOUND_BYTES = 16 * 1024
_MSG_BUDGET_CAPACITY = 20
_MSG_BUDGET_REFILL_PER_S = 20.0
_REAUTH_INTERVAL_S = 60.0


def _reject_constant(name: str):
    """json.loads 默认接受 NaN/Infinity —— 入站一律拒绝（F-15）。"""
    raise ValueError(f"non-finite JSON constant: {name}")


@router.websocket("/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
):
    """WebSocket endpoint — requires valid access token + session ownership.

    审计 SEC-03：
    - 必须提供合法 access token（不再允许匿名连接）
    - session_id 必须属于 token 中的 user（防 IDOR）
    - rate limit per client IP（不是 per session_id）

    审计 #757：token 优先取 Sec-WebSocket-Protocol 子协议（浏览器 WS 无法
    设置自定义 header，但可以传 subprotocol），避免 JWT 进入 URL query ——
    nginx `log_format` 含 $request，query token 会原样落盘。query 形式保留
    为兼容回退，未来移除。

    审计 #758：与 HTTP 路径一致地校验 token_version —— logout（bump ver）
    后旧 access token 不得继续开 WS（此前仅签名+exp 校验，30min TTL 内
    revoked token 仍可连）。
    """
    # #757: subprotocol 优先 —— 客户端以 new WebSocket(url, ["bearer", token])
    # 连接时，token 走握手 header（Sec-WebSocket-Protocol），不落入任何
    # 访问日志。query 形式保留为兼容回退。握手不提前 accept：校验失败仍以
    # close 拒绝（保持既有语义），校验通过后再带选中的 subprotocol accept。
    subproto_token = ""
    requested_subprotocols = [
        p.strip() for p in websocket.headers.get("sec-websocket-protocol", "").split(",") if p.strip()
    ]
    for part in requested_subprotocols:
        # 约定形如 ["bearer", "<jwt>"]；也容忍只传 token 的客户端库（JWT 形如 xxx.yyy.zzz）
        if part != "bearer" and part.count(".") >= 2:
            subproto_token = part
            break
    if subproto_token:
        token = subproto_token
    selected_subprotocol = "bearer" if subproto_token else None

    # Rate limit per client IP
    from app.core.client_ip import client_ip_from

    client_ip = client_ip_from(websocket)
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"ws_connect:{client_ip}", _WS_RATE_LIMIT_MAX, _WS_RATE_LIMIT_WINDOW
    ):
        await websocket.close(code=4029, reason="Rate limit exceeded")
        return

    # SEC-03 / #1412: 必须有合法 access token（含 legacy sunset + ver/is_active）
    if not token:
        await websocket.close(code=4001, reason="Access token required")
        return

    try:
        auth = await authenticate_ws_token(token)
    except WsAuthError as exc:
        await websocket.close(code=exc.code, reason=exc.reason)
        return
    user_id = auth["user_id"]

    # SEC-03: 校验 session 所有权
    from app.tools._utils import async_db_session
    from app.services.history_service_async import AsyncHistoryService

    try:
        async with async_db_session() as db:
            # #525: ownership-only variant — WS connect must not load the full
            # message collection for every reconnect.
            conv = await AsyncHistoryService(db).get_session_meta(session_id, user_id)
        if conv is None:
            await websocket.close(code=4003, reason="Session not found or not owned by user")
            return
    except Exception as e:
        logger.error(f"WS session ownership check failed for {session_id}: {e}")
        # DB 不可用时拒绝连接（fail-closed）
        await websocket.close(code=4500, reason="Internal error during session validation")
        return

    await manager.connect(websocket, session_id, subprotocol=selected_subprotocol)
    budget = float(_MSG_BUDGET_CAPACITY)
    last_refill = time.monotonic()
    last_reauth = last_refill
    try:
        while True:
            raw = await websocket.receive_text()
            now = time.monotonic()
            # F-15：撤销/停用复核（此前只在握手时校验，logout 后长连接仍可写）
            if now - last_reauth >= _REAUTH_INTERVAL_S:
                last_reauth = now
                try:
                    await authenticate_ws_token(token)
                except WsAuthError:
                    await websocket.close(code=4401, reason="Token revoked")
                    break
            # F-15：token bucket（超发断开）+ 帧大小上限（超限丢弃）
            budget = min(
                float(_MSG_BUDGET_CAPACITY),
                budget + (now - last_refill) * _MSG_BUDGET_REFILL_PER_S,
            )
            last_refill = now
            if budget < 1:
                await websocket.close(code=4408, reason="Message budget exceeded")
                break
            budget -= 1
            if len(raw) > _MAX_INBOUND_BYTES:
                continue
            try:
                data = json.loads(raw, parse_constant=_reject_constant)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            event_type = data.get("event")
            if event_type == "ping":
                await websocket.send_json({"event": "pong"})
            elif event_type in PERCEPTION_HANDLERS:
                payload = data.get("data", {})
                if not isinstance(payload, dict):
                    continue
                handler = PERCEPTION_HANDLERS[event_type]
                await handler(session_id, payload)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e}")
    finally:
        manager.disconnect(websocket, session_id)
