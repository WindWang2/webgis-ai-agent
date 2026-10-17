"""Interaction IR V1 摄入/投影端点（Goal 02 / ADR-0224）。

- ``POST /chat/sessions/{sid}/interaction-events`` —— 浏览器 outbox 批量
  上报（≤50/批，per-IP 限速，require_owned_session，session 锁内三键
  读-改-写）。幂等由 per-instance 水印 + event_id 查重保证：弱网整批重试
  收敛为 stale/duplicate，不双计。
- ``GET  /chat/sessions/{sid}/interaction-events/projection`` —— 有界交互
  投影只读面（诊断 / agent context 消费），不做任何写。

鉴权与 map-action-ack / mapspec mutation 同族（登录归属或匿名 owner_token）。
客户端端点只放行 ``actor=user`` 事件（ingest 内校验，防伪造 agent/system）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.client_ip import client_ip_from
from app.core.rate_limiter import get_rate_limiter
from app.models.db_model import Conversation
from app.schemas.interaction_event_schema import (
    InteractionEventBatchRequest,
    InteractionIngestResponse,
    InteractionProjectionResponse,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.distributed_lock import (
    LockDegradedError,
    LockLostError,
    session_lock_registry,
)
from app.services.gis_harness.interaction_ir.ingest import ingest_interaction_batch
from app.services.gis_harness.interaction_ir.projection import (
    interaction_snapshot_from_map_state,
)
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["AI对话"])

# 限速（per client IP，同 map-action-ack 模式）：outbox 500ms debounce + 批
# ≤50，正常交互节奏远低于此；风暴客户端在此被背压而非打爆存储。
_RATE_LIMIT_MAX = 300
_RATE_LIMIT_WINDOW = 60


def _session_busy_503(detail: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": detail, "message": "会话正被占用，请稍后重试。"},
    )


async def _raise_if_deleted(session_id: str) -> None:
    from app.agent_pi_bridge import is_cartographic_session_deleted

    if is_cartographic_session_deleted(session_id):
        raise HTTPException(status_code=410, detail="Session was deleted")
    # 进程内 tombstone 漏其它副本；Redis/map_state 标志才是跨副本契约
    # （与 map-action-ack 同款双探测）。
    if await session_data_manager.get_state_field(session_id, "_cartographic_deleted") is True:
        raise HTTPException(status_code=410, detail="Session was deleted")


@router.post(
    "/sessions/{session_id}/interaction-events",
    response_model=InteractionIngestResponse,
)
async def push_interaction_events(
    session_id: str,
    req: InteractionEventBatchRequest,
    request: Request,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """接收浏览器 outbox 批量交互事件（fire-and-forget 遥测语义）。

    与 map-action-ack 同纪律：锁降级/丢失 → 503（客户端幂等重试安全，
    状态未写）；锁竞争 → 503 背压。存储降级不 500 —— ack 信封内以
    ``persist_degraded`` 披露。
    """
    client_ip = client_ip_from(request)
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"interaction_events:{client_ip}", _RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW
    ):
        raise HTTPException(status_code=429, detail="Too many interaction events")

    try:
        # 写共享 map_state 键（ledger/clients/meta 三键 read-modify-write）——
        # 降级锁 fail-closed；写前复检所有权（与 map-action-ack F2/F3 同款）。
        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as _lock:
            session_data_manager.invalidate_local_cache(session_id)
            if getattr(_lock, "lost", False):
                raise LockLostError(
                    f"session lock ownership lost before interaction batch for {session_id}"
                )
            await _raise_if_deleted(session_id)
            return await ingest_interaction_batch(
                session_data_manager, session_id, req.events,
            )
    except (TimeoutError, asyncio.TimeoutError):
        raise _session_busy_503("session_busy")
    except (LockDegradedError, LockLostError):
        raise _session_busy_503("session_lock_unavailable")


@router.get(
    "/sessions/{session_id}/interaction-events/projection",
    response_model=InteractionProjectionResponse,
)
async def get_interaction_projection(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """有界交互投影只读面（agent context / 调试诊断）。"""
    from app.services.gis_harness.interaction_ir.contracts import SCHEMA_VERSION

    map_state = await session_data_manager.get_map_state(session_id)
    snapshot = interaction_snapshot_from_map_state(map_state)
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "projection": snapshot,
    }
