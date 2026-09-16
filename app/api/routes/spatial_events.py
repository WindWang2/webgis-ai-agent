"""Spatial Events REST / SSE / webhook / replay（additive, thin, org-scoped）。

纪律：
- 全端点 ``get_current_user``；org 由受信 tenancy 上下文盖章（不信 body）。
- 响应只含有界摘要与 ref，绝不含大数据 payload。
- runtime flag 关闭时：读路径/健康检查照常（诚实 diagnostics），写入路径
  503 —— feature-off 行为可预测。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import get_current_user, require_admin
from app.services.spatial_events import flags
from app.services.spatial_events.contracts import (
    EVENT_KINDS,
    SpatialEventEnvelope,
)
from app.services.spatial_events.service import get_spatial_event_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spatial-events", tags=["Spatial Events"])


def _org(user: Dict[str, Any]) -> str:
    from app.core import tenancy

    org = tenancy.effective_org_in_thread(user)
    if not org:
        raise HTTPException(status_code=403, detail="org_context_required")
    return org


def _svc():
    return get_spatial_event_service()


# ── health / stats ──────────────────────────────────────────────────


@router.get("/health")
def health() -> dict:
    return {
        "enabled": flags.runtime_enabled(),
        "schema": "spatial-event.v1",
        "bridges": {
            "mission": flags.mission_bridge_enabled(),
            "invalidation": flags.invalidation_enabled(),
            "governor_gate": flags.governor_gate_enabled(),
        },
    }


@router.get("/stats")
def stats(user: Dict[str, Any] = Depends(get_current_user)) -> dict:
    org = _org(user)
    ledger = _svc().ledger
    return {
        "org_id": org,
        "events_by_status": ledger.stats(org_id=org),
        "cursor": ledger.get_cursor("spatial-event-worker"),
    }


# ── events 读取 ─────────────────────────────────────────────────────


@router.get("/events")
def list_events(
    after_id: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    subject: Optional[str] = None,
    user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    org = _org(user)
    rows = _svc().ledger.list_events(
        org_id=org, after_id=after_id, limit=min(max(1, limit), 200),
        status=status, kind=kind, subject=subject,
    )
    return {
        "org_id": org,
        "events": rows,
        "count": len(rows),
        "next_after_id": rows[-1]["id"] if rows else after_id,
    }


@router.get("/events/{event_id}")
def get_event(
    event_id: str, user: Dict[str, Any] = Depends(get_current_user)
) -> dict:
    org = _org(user)
    row = _svc().ledger.get_event_by_event_id(event_id, org_id=org)
    if row is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    return row


# ── watches 管理 ────────────────────────────────────────────────────


class WatchBody(BaseModel):
    watch_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    kinds: List[str] = Field(min_length=1, max_length=16)
    subject_key_prefix: Optional[str] = Field(default=None, max_length=128)
    session_id: Optional[str] = Field(default=None, max_length=64)
    project_id: Optional[str] = Field(default=None, max_length=64)
    condition: dict = Field(default_factory=dict)
    actions: List[str] = Field(min_length=1, max_length=8)
    cooldown_s: float = Field(default=60.0, ge=0, le=86400)
    mission_goal_template: Optional[str] = Field(default=None, max_length=512)
    mission_project_id: Optional[str] = Field(default=None, max_length=64)


@router.post("/watches")
def upsert_watch(
    body: WatchBody, user: Dict[str, Any] = Depends(get_current_user)
) -> dict:
    from app.services.spatial_events.contracts import (
        SpatialWatch,
        WatchCondition,
    )

    org = _org(user)
    try:
        watch = SpatialWatch(
            watch_id=body.watch_id,
            org_id=org,  # org 域强制（body 不可指定）
            name=body.name,
            enabled=body.enabled,
            kinds=body.kinds,
            subject_key_prefix=body.subject_key_prefix,
            session_id=body.session_id,
            project_id=body.project_id,
            condition=WatchCondition(**body.condition),
            actions=body.actions,
            cooldown_s=body.cooldown_s,
            mission_goal_template=body.mission_goal_template,
            mission_project_id=body.mission_project_id,
        )
    except Exception as e:  # noqa: BLE001 — 契约校验错误 → 400
        raise HTTPException(status_code=400, detail=str(e)[:300]) from e
    _svc().ledger.upsert_watch(watch)
    return {"watch_id": watch.watch_id, "org_id": org, "stored": True}


@router.get("/watches")
def list_watches(user: Dict[str, Any] = Depends(get_current_user)) -> dict:
    org = _org(user)
    ids = _svc().ledger.list_watches(org_id=org)
    return {"org_id": org, "watch_ids": ids, "count": len(ids)}


@router.get("/watches/{watch_id}")
def get_watch(
    watch_id: str, user: Dict[str, Any] = Depends(get_current_user)
) -> dict:
    org = _org(user)
    watch = _svc().ledger.get_watch(watch_id, org_id=org)
    if watch is None:
        raise HTTPException(status_code=404, detail="watch_not_found")
    state = _svc().ledger.get_watch_state(watch_id, org_id=org)
    return {
        "watch": watch.model_dump(mode="json"),
        "state": state.model_dump(mode="json") if state else None,
    }


@router.delete("/watches/{watch_id}")
def delete_watch(
    watch_id: str, user: Dict[str, Any] = Depends(get_current_user)
) -> dict:
    org = _org(user)
    ok = _svc().ledger.delete_watch(watch_id, org_id=org)
    if not ok:
        raise HTTPException(status_code=404, detail="watch_not_found")
    return {"watch_id": watch_id, "deleted": True}


@router.get("/fires")
def list_fires(
    watch_id: Optional[str] = None,
    limit: int = 50,
    user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    org = _org(user)
    fires = _svc().ledger.list_fires(
        org_id=org, watch_id=watch_id, limit=min(max(1, limit), 200)
    )
    return {"org_id": org, "fires": fires, "count": len(fires)}


# ── 内部 ingest API（受信调用方；org 盖章）───────────────────────────


class IngestBody(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    subject_type: str = Field(min_length=1, max_length=32)
    subject_key: str = Field(min_length=1, max_length=128)
    payload: dict = Field(default_factory=dict)
    payload_ref: Optional[str] = Field(default=None, max_length=160)
    session_id: Optional[str] = Field(default=None, max_length=64)
    project_id: Optional[str] = Field(default=None, max_length=64)
    occurred_at: Optional[datetime] = None
    priority: str = Field(default="normal", max_length=16)
    dedupe_key: Optional[str] = Field(default=None, max_length=128)
    correlation_id: Optional[str] = Field(default=None, max_length=64)


@router.post("/events")
async def ingest_event(
    body: IngestBody, user: Dict[str, Any] = Depends(get_current_user)
) -> dict:
    if not flags.runtime_enabled():
        raise HTTPException(status_code=503, detail="spatial_event_runtime_disabled")
    org = _org(user)
    from app.services.spatial_events.adapters import make_envelope

    try:
        env = make_envelope(
            body.kind,
            org_id=org,
            subject_type=body.subject_type,
            subject_key=body.subject_key,
            payload=body.payload,
            payload_ref=body.payload_ref,
            session_id=body.session_id,
            project_id=body.project_id,
            occurred_at=body.occurred_at,
            priority=body.priority,
            dedupe_key=body.dedupe_key,
            correlation_id=body.correlation_id,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)[:300]) from e
    result = await _svc().ingest(env, org_id=org)
    if result is None:
        raise HTTPException(status_code=500, detail="ingest_failed")
    return {
        "event_id": result.event_id,
        "row_id": result.row_id,
        "status": result.status,
    }


# ── webhook（外部安全 seam；HMAC）────────────────────────────────────


class WebhookBody(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    subject_type: str = Field(min_length=1, max_length=32)
    subject_key: str = Field(min_length=1, max_length=128)
    org_id: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)
    payload_ref: Optional[str] = Field(default=None, max_length=160)
    event_id: Optional[str] = Field(default=None, max_length=64)
    occurred_at: Optional[datetime] = None
    project_id: Optional[str] = Field(default=None, max_length=64)


#: 每 org webhook 速率（有界内存；≤256 org）
_rl_lock = asyncio.Lock()
_rl_buckets: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=30))
_WEBHOOK_WINDOW_S = 60.0
_WEBHOOK_MAX_PER_WINDOW = 30


async def _rate_limit(org_id: str) -> bool:
    async with _rl_lock:
        if len(_rl_buckets) > 256:
            # 有界性：驱逐最早写入的桶（不清空全部——不重置他人窗口）
            for k in sorted(_rl_buckets.keys())[: len(_rl_buckets) - 255]:
                _rl_buckets.pop(k, None)
        now = time.monotonic()
        q = _rl_buckets[org_id]
        while q and now - q[0] > _WEBHOOK_WINDOW_S:
            q.popleft()
        if len(q) >= _WEBHOOK_MAX_PER_WINDOW:
            return False
        q.append(now)
        return True


@router.post("/webhook")
async def webhook(
    body: WebhookBody,
    request: Request,
    x_webgis_signature: str = Header(default=""),
) -> dict:
    """外部事件安全 seam（P1 加固：per-org 密钥绑定租户）。

    验签密钥 = ``GIS_SPATIAL_EVENT_WEBHOOK_SECRET__ORG_<ORG>``（per-org，
    唯一推荐方式）；部署级 ``GIS_SPATIAL_EVENT_WEBHOOK_SECRET`` 仅对
    ``GIS_SPATIAL_EVENT_WEBHOOK_DEFAULT_ORG`` 指定的单一 org 生效——
    持部署密钥的集成方**不能**向其他租户注入事件。HMAC 覆盖整个 body
    （含 org_id 声明），密钥即身份。
    """
    secret = flags.webhook_org_secret(body.org_id)
    if not secret:
        if (
            flags.webhook_secret()
            and body.org_id == flags.webhook_default_org()
        ):
            secret = flags.webhook_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="webhook_disabled_for_org")
    raw = await request.body()
    if len(raw) > 8192:
        raise HTTPException(status_code=413, detail="webhook_body_too_large")
    provided = x_webgis_signature.strip()
    if provided.startswith("sha256="):
        provided = provided[len("sha256="):]
    expected = hmac.new(
        secret.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()
    if not (provided and hmac.compare_digest(provided, expected)):
        raise HTTPException(status_code=401, detail="invalid_signature")
    if not await _rate_limit(body.org_id):
        raise HTTPException(status_code=429, detail="webhook_rate_limited")
    if not flags.runtime_enabled():
        raise HTTPException(status_code=503, detail="spatial_event_runtime_disabled")
    try:
        env = SpatialEventEnvelope.webhook(
            kind=body.kind,
            org_id=body.org_id,
            subject_type=body.subject_type,
            subject_key=body.subject_key,
            payload=body.payload,
            payload_ref=body.payload_ref,
            event_id=body.event_id or "",
            occurred_at=body.occurred_at
            or datetime.now(timezone.utc),
            project_id=body.project_id,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)[:300]) from e
    result = await _svc().ingest(env, org_id=body.org_id)
    if result is None:
        raise HTTPException(status_code=500, detail="ingest_failed")
    return {
        "event_id": result.event_id,
        "row_id": result.row_id,
        "status": result.status,
    }


# ── replay / drain（运维诊断）───────────────────────────────────────


class ReplayBody(BaseModel):
    from_id: int = Field(ge=0)
    to_id: int = Field(ge=0)
    dry_run: bool = False
    force: bool = False


@router.post("/replay")
async def replay(
    body: ReplayBody, user: Dict[str, Any] = Depends(get_current_user)
) -> dict:
    if not flags.runtime_enabled():
        raise HTTPException(status_code=503, detail="spatial_event_runtime_disabled")
    if body.to_id < body.from_id:
        raise HTTPException(status_code=400, detail="to_id_must_be_gte_from_id")
    return await _svc().replay(
        org_id=_org(user),
        from_id=body.from_id,
        to_id=body.to_id,
        dry_run=body.dry_run,
        force=body.force,
    )


@router.post("/drain")
async def drain(
    user: Dict[str, Any] = Depends(require_admin), batch: int = 32
) -> dict:
    """手动驱动一批（运维/测试入口；生产由 worker lifespan 驱动）。

    admin-only：drain 是**全局**处理动作（跨租户消费），不得暴露给普通
    租户用户（否则可加速他租户 mission 副作用的执行时序）。
    """
    if not flags.runtime_enabled():
        raise HTTPException(status_code=503, detail="spatial_event_runtime_disabled")
    return await _svc().drain_once("api-manual", batch=min(max(1, batch), 256))


# ── SSE timeline（bounded backlog + 心跳）───────────────────────────


@router.get("/stream")
async def stream(
    request: Request,
    after_id: int = 0,
    user: Dict[str, Any] = Depends(get_current_user),
):
    from fastapi.responses import StreamingResponse

    org = _org(user)
    ledger = _svc().ledger
    return StreamingResponse(
        _event_tail(ledger, org, after_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _event_tail(ledger, org_id: str, after_id: int):
    """有界 tail 生成器：backlog ≤128 + 心跳；总量 ≤1000 条 / 空闲 ≤600 tick。

    不依赖 ``request.is_disconnected``（TestClient 的 receive 通道在 body
    耗尽后挂起）；真服务器上客户端断开时 StreamingResponse 取消生成器。
    """
    cursor = max(0, after_id)
    sent = 0
    backlog = await asyncio.to_thread(
        ledger.list_events, org_id=org_id, after_id=cursor, limit=128
    )
    for row in backlog:
        cursor = max(cursor, int(row["id"]))
        sent += 1
        yield _sse_line(row)
    idle_ticks = 0
    while sent < 1000 and idle_ticks < 240:
        rows = await asyncio.to_thread(
            ledger.list_events, org_id=org_id, after_id=cursor, limit=64
        )
        if rows:
            idle_ticks = 0
            for row in rows:
                cursor = max(cursor, int(row["id"]))
                sent += 1
                yield _sse_line(row)
        else:
            idle_ticks += 1
            yield ":hb\n\n"
            await asyncio.sleep(15.0)


def _sse_line(row: Dict[str, Any]) -> str:
    payload = {
        "id": row["id"],
        "event_id": row["event_id"],
        "kind": row["kind"],
        "subject_key": row["subject_key"],
        "status": row["status"],
        "occurred_at": row["occurred_at"],
    }
    return f"event: spatial_event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


_ = EVENT_KINDS  # re-export guard（词表经 contracts 引用）
