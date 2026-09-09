"""WebSocket collaboration endpoint —— /ws/collab/{session_id}（Workbench V6）。

与既有 /ws/{session_id}（感知通道，ws.py）的关系：独立端点、独立 rate 桶
（``ws_collab:{ip}``，评审 m5 —— 共桶会被重连风暴连带打挂两个端点）、
独立入站词表。信封沿用 ``{event, data}`` 形态（与 realtime contract
快照一致性最大化，评审 m4）。

认证（双前端，R1-M2/m2；所有权语义 = authorize_session_write）：
- subprotocol ``["bearer", <jwt>]`` —— 认证会话（user_id 匹配
  conv.user_id + token_version 校验）；
- subprotocol ``["session", <owner_token>]`` —— 匿名会话（SEC-08
  hmac 对比 Conversation.owner_token；#1109 legacy NULL/NULL fail-closed）。
- 交叉使用（JWT 连匿名会话 / owner_token 连用户绑定会话）一律 4003。

时序纪律（R1-M2）：**先订阅总线扇出 → 再读权威 revision → 再发 hello**
—— 订阅前窗口内的提交不会漏给该连接（最多 hello 里已带新 revision）。

心跳：客户端 10s ``ping`` → 服务端 ``pong`` 携带**新鲜** revision（失效
L1 后定向读）—— 只读参与者的陈旧探测路径（R1-M1）。

入站白名单 + 预算：ping | sync | presence | lease_acquire | lease_renew |
lease_release；token bucket（容量 20，速率 20/s）超发 → 4408 close；
单条消息 ≤ 16KB。载荷字段服务端白名单裁剪，凭据绝不入事件。
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.services.collab.bus import bus
from app.services.collab.leases import lease_registry
from app.services.collab.presence import presence_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws/collab", tags=["WebSocket 协作"])

_WS_RATE_LIMIT_MAX = 5
_WS_RATE_LIMIT_WINDOW = 60
#: 入站消息 token bucket（容量 = 突发；速率 = 稳态）。presence 类由客户端
#: 250ms coalesce + 服务端 200ms 合并节流兜底，预算只防滥用不防正常使用。
_MSG_BUDGET_CAPACITY = 20
_MSG_BUDGET_REFILL_PER_S = 20.0
_MAX_INBOUND_BYTES = 16 * 1024
#: 参与者记录中每字段的服务端上限（与 presence 白名单同向）。
_MAX_EVENT_DATA_BYTES = 4 * 1024


async def _fresh_revision(session_id: str) -> int:
    """新鲜 revision 读：失效本进程 L1 后定向读单字段（不经 2s 陈旧缓存）。"""
    from app.services.session_data import session_data_manager

    invalidate = getattr(session_data_manager, "invalidate_local_cache", None)
    if callable(invalidate):
        invalidate(session_id)
    get_field = getattr(session_data_manager, "get_state_field", None)
    raw = (
        await get_field(session_id, "_cartographic_mutation_revision")
        if callable(get_field)
        else None
    )
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def _read_authoritative_doc(session_id: str) -> tuple[int, dict | None]:
    """权威 workbench doc 读（引擎 store，含磁盘复活路径）+ 新鲜 revision。"""
    from app.api.routes.mapspec_mutations import _engine
    from app.services.session_data import session_data_manager

    invalidate = getattr(session_data_manager, "invalidate_local_cache", None)
    if callable(invalidate):
        invalidate(session_id)
    pre_state = await session_data_manager.get_map_state(session_id)
    try:
        revision = int(pre_state.get("_cartographic_mutation_revision", 0))
    except (TypeError, ValueError):
        revision = 0
    try:
        loaded = await _engine.store.get_mapspec(session_id, state_hint=pre_state)
    except Exception:  # noqa: BLE001 — spec 不可读时仍给 revision
        loaded = None
    doc = loaded.get("workbench") if isinstance(loaded, dict) else None
    return revision, (doc if isinstance(doc, dict) else None)


def _extract_subprotocol_token(websocket: WebSocket) -> tuple[str, str]:
    """解析 Sec-WebSocket-Protocol：["bearer", <jwt>] 或 ["session", <token>]。

    返回 (mode, token)；mode ∈ bearer|session|""（未识别）。
    """
    raw = websocket.headers.get("sec-websocket-protocol", "")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    for i, part in enumerate(parts):
        if part in ("bearer", "session") and i + 1 < len(parts):
            return part, parts[i + 1]
    return "", ""


class _CollabConnection:
    """一条 WS 连接：有界外发队列 + 发送 task（慢消费者隔离）。"""

    __slots__ = ("ws", "session_id", "client_id", "label", "queue", "send_task", "closed")

    def __init__(self, ws: WebSocket, session_id: str, client_id: str, label: str) -> None:
        self.ws = ws
        self.session_id = session_id
        self.client_id = client_id
        self.label = label
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.send_task: asyncio.Task | None = None
        self.closed = False

    def enqueue(self, envelope: dict) -> None:
        """总线扇出入口（同步、绝不抛出）。队列满 = 慢消费者 → 断开。

        None 哨兵在 closed 后仍入队（finally 依赖它结束 send_loop）。
        """
        if self.closed and envelope is not None:
            return
        try:
            self.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            logger.info("[ws-collab] slow consumer %s dropped", self.client_id)
            self.closed = True

    async def send_loop(self) -> None:
        try:
            while True:
                envelope = await self.queue.get()
                if envelope is None:
                    break
                await self.ws.send_text(json.dumps(envelope, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001 — 发送失败由主循环 finally 收尾
            pass


def _make_listener(connection: _CollabConnection):
    """总线 → 连接队列的适配（bus listener 约定：同步、快速、绝不抛出）。"""

    def _on_event(envelope: dict) -> None:
        # 端到端去重不必要（单扇出路径）；here 只裁剪 presence 快照类
        # 回声：自己产生的 presence/lock 事件仍回发（多 tab 同浏览器各为
        # 独立 clientId，需要看到彼此）。
        connection.enqueue(envelope)

    return _on_event


@router.websocket("/{session_id}")
async def collab_websocket(websocket: WebSocket, session_id: str) -> None:
    from app.core.client_ip import client_ip_from
    from app.core.rate_limiter import get_rate_limiter
    from app.models.db_model import Conversation, User

    mode, token = _extract_subprotocol_token(websocket)

    client_ip = client_ip_from(websocket)
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"ws_collab:{client_ip}", _WS_RATE_LIMIT_MAX, _WS_RATE_LIMIT_WINDOW
    ):
        await websocket.close(code=4029, reason="Rate limit exceeded")
        return

    if not token:
        await websocket.close(code=4001, reason="Access token required")
        return

    user_id: str | None = None
    owner_token: str | None = None

    if mode == "bearer":
        from app.core.auth import verify_token

        payload = verify_token(token)
        if payload is None:
            await websocket.close(code=4001, reason="Invalid token")
            return
        if payload.get("type") not in (None, "access"):
            await websocket.close(code=4001, reason="Wrong token type")
            return
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=4001, reason="Invalid token payload")
            return
        # #758 同语义：logout（token_version bump）后旧 token 不得开通道。
        from app.tools._utils import async_db_session

        try:
            async with async_db_session() as db:
                row = (
                    await db.execute(
                        select(User.token_version).where(User.id == user_id)
                    )
                ).scalar_one_or_none()
        except Exception:  # noqa: BLE001 — fail-closed
            await websocket.close(code=1011, reason="Auth unavailable")
            return
        if row is not None and int(payload.get("ver", 0)) != row:
            await websocket.close(code=4001, reason="Token revoked, please re-login")
            return
    elif mode == "session":
        owner_token = token
    else:
        await websocket.close(code=4001, reason="Unknown auth subprotocol")
        return

    # 所有权校验（SEC-03 / authorize_session_write 语义）。
    from app.tools._utils import async_db_session

    try:
        async with async_db_session() as db:
            conv = (
                await db.execute(
                    select(Conversation).where(Conversation.id == session_id)
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.error(f"[ws-collab] ownership check failed for {session_id}: {exc}")
        await websocket.close(code=4500, reason="Internal error during session validation")
        return
    if conv is None:
        await websocket.close(code=4003, reason="Session not found")
        return
    conv_uid = conv.user_id
    conv_owner_token = conv.owner_token
    authorized = False
    if conv_uid is not None:
        # 用户绑定会话：仅匹配的认证用户；owner_token 前端不适用。
        if user_id is not None and str(conv_uid) == str(user_id):
            authorized = True
    else:
        # 匿名会话：仅 owner_token 匹配；#1109 legacy NULL fail-closed。
        # JWT 不得连他人匿名会话（评审 m2 双拒）。
        if user_id is None and conv_owner_token is not None and owner_token is not None:
            authorized = hmac.compare_digest(str(owner_token), str(conv_owner_token))
    if not authorized:
        await websocket.close(code=4003, reason="Session not owned by caller")
        return

    label = ""
    if mode == "session":
        # 匿名：自报 clientLabel（客户端提供则裁剪；服务端不透传更多信息）。
        label = "访客"
    else:
        label = "用户"

    client_id = uuid.uuid4().hex[:12]
    connection = _CollabConnection(websocket, session_id, client_id, label)

    # accept 先于任何 send_text（presence_full/hello 均经队列由 send_loop 发）。
    # RFC 6455：必须从客户端提供的列表中回显选中的 subprotocol，否则浏览器
    # 直接握手失败（ws.py 同款语义）。
    await websocket.accept(subprotocol=mode)

    # 时序纪律（R1-M2）：先订阅本地扇出，再读权威状态，再发 hello。
    remove_listener = await bus.add_local_listener(session_id, _make_listener(connection))
    connection.send_task = asyncio.get_running_loop().create_task(connection.send_loop())

    degraded = bus.degraded()
    revision, doc = await _read_authoritative_doc(session_id)
    participants = await presence_registry.join(
        session_id, client_id, {"label": label, "kind": "user"}
    )
    if participants is None:
        # 参与者满员：仍允许收事件（只读观战），但不进 presence。
        connection.enqueue({"event": "presence_full", "data": {"max": 32}})
        participants = []
    leases = await lease_registry.snapshot(session_id)
    connection.enqueue({
        "event": "hello",
        "data": {
            "clientId": client_id,
            "revision": revision,
            "degraded": degraded,
            "participants": participants,
            "leases": leases,
            # hello 顺带 authoritative doc：晚到/重连客户端免一次 sync 往返
            # （小于 64KB 时；大 doc 由客户端按需 refetch）。
            "doc": doc,
        },
    })
    await bus.publish(session_id, "presence", {
        "action": "join", "client": {"clientId": client_id, "label": label},
    })

    budget = _MSG_BUDGET_CAPACITY
    last_budget_refill = time.monotonic()
    last_presence_relay = 0.0
    presence_pending: dict | None = None
    presence_flush_task: asyncio.Task | None = None

    async def _flush_presence() -> None:
        nonlocal presence_pending, last_presence_relay, presence_flush_task
        await asyncio.sleep(0.2)
        data_deferred = presence_pending
        presence_pending = None
        if data_deferred is None:
            return
        from app.services.collab.presence import _sanitize_patch as _sp

        await presence_registry.heartbeat(session_id, client_id, data_deferred)
        await bus.publish(session_id, "presence", {
            "action": "update",
            "client": {"clientId": client_id, **_sp(data_deferred)},
        })
        last_presence_relay = time.monotonic()
        presence_flush_task = None

    try:
        while True:
            raw = await websocket.receive_text()
            # 预算（token bucket）。
            now = time.monotonic()
            budget = min(
                _MSG_BUDGET_CAPACITY,
                budget + (now - last_budget_refill) * _MSG_BUDGET_REFILL_PER_S,
            )
            last_budget_refill = now
            if budget < 1:
                await websocket.close(code=4408, reason="Message budget exceeded")
                break
            budget -= 1
            if len(raw) > _MAX_INBOUND_BYTES:
                continue  # 超限丢弃（计数已扣 —— 持续超发会被预算断开）
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(message, dict):
                continue
            event = message.get("event")
            data = message.get("data") if isinstance(message.get("data"), dict) else {}

            if event == "ping":
                connection.enqueue({"event": "pong", "data": {"revision": await _fresh_revision(session_id)}})
            elif event == "sync":
                known = data.get("knownRevision")
                try:
                    known_int = int(known) if known is not None else None
                except (TypeError, ValueError):
                    known_int = None
                current, doc_now = await _read_authoritative_doc(session_id)
                if known_int is None or known_int != current:
                    connection.enqueue({
                        "event": "doc",
                        "data": {"revision": current, "doc": doc_now, "replay": True},
                    })
                else:
                    connection.enqueue({"event": "sync_ok", "data": {"revision": current}})
            elif event == "presence":
                # 服务端合并节流（200ms）：节流窗口内只保留最后态（评审 m-4
                # —— 直接丢弃会让最终状态永不下发）。
                pending_presence = dict(data)
                remaining = 0.2 - (now - last_presence_relay)
                if remaining > 0:
                    if presence_flush_task is None:
                        presence_flush_task = asyncio.get_running_loop().create_task(
                            _flush_presence()
                        )
                    continue  # flush task 稍后带走最后态（合并最后态，评审 m-4）
                last_presence_relay = now
                sanitized = dict(pending_presence)
                pending_presence = None
                await presence_registry.heartbeat(session_id, client_id, sanitized)
                # M-1：广播路径同样白名单裁剪 —— clientId 服务端权威，
                # 客户端字段不得覆盖/冒充他人身份。
                from app.services.collab.presence import _sanitize_patch

                await bus.publish(session_id, "presence", {
                    "action": "update",
                    "client": {"clientId": client_id, **_sanitize_patch(sanitized)},
                })
            elif event == "lease_acquire":
                lock_key = str(data.get("lockKey") or "")
                result = await lease_registry.acquire(session_id, lock_key, client_id, label)
                await bus.publish(session_id, "lock", {
                    "action": "acquire", "lockKey": lock_key,
                    "granted": bool(result.get("granted")),
                    "holder": result.get("holder"), "clientId": client_id,
                })
                connection.enqueue({"event": "lease_result", "data": {
                    "lockKey": lock_key, "requestId": data.get("requestId"), **result,
                }})
            elif event == "lease_renew":
                lock_key = str(data.get("lockKey") or "")
                granted = await lease_registry.renew(session_id, lock_key, client_id)
                connection.enqueue({"event": "lease_result", "data": {
                    "lockKey": lock_key, "requestId": data.get("requestId"),
                    "granted": granted, "action": "renew",
                }})
                if granted:
                    await bus.publish(session_id, "lock", {
                        "action": "renew", "lockKey": lock_key, "clientId": client_id,
                    })
            elif event == "lease_release":
                lock_key = str(data.get("lockKey") or "")
                released = await lease_registry.release(session_id, lock_key, client_id)
                if released:
                    await bus.publish(session_id, "lock", {
                        "action": "release", "lockKey": lock_key, "clientId": client_id,
                    })
                connection.enqueue({"event": "lease_result", "data": {
                    "lockKey": lock_key, "requestId": data.get("requestId"),
                    "granted": False, "released": released, "action": "release",
                }})
            # 其他事件：白名单外静默丢弃（预算已扣 —— 洪水会被断开）。
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[ws-collab] error for {session_id}: {exc}")
    finally:
        connection.closed = True
        connection.enqueue(None)  # 哨兵：结束 send_loop
        remove_listener()
        await presence_registry.leave(session_id, client_id)
        await lease_registry.release_all(session_id, client_id)
        await bus.publish(session_id, "presence", {
            "action": "leave", "client": {"clientId": client_id},
        })
        task = connection.send_task
        if task is not None:
            task.cancel()
