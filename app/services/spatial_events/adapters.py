"""内部事件生产适配器（E1–E4 fail-open hooks）+ 信封构造。

纪律：
- hook 全部 best-effort：任何异常只 debug 日志，**绝不阻断业务路径**
  （map mutation / artifact 晋升 / job 完成 / map product 版本的原语义不变）。
- org 红线：无受信 org 印章的事件**不入账**（宁可丢事件，不可猜租户）。
  hook 允许显式传 org_id，或由 ``set_org_resolver`` 装配的会话→org 解析器
  提供；解析失败 → 跳过（诚实缺省）。
- payload 只放标量摘要 + ref；大内容一律 payload_ref。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from app.services.spatial_events.contracts import (
    EventKind,
    EventPriority,
    SpatialEventEnvelope,
    SubjectType,
)

logger = logging.getLogger(__name__)

#: session_id → org_id 解析器（app 装配；测试可注入）。返回 "" = 不可解析。
OrgResolver = Callable[[str], Awaitable[str]]

_org_resolver: Optional[OrgResolver] = None


def set_org_resolver(resolver: Optional[OrgResolver]) -> None:
    global _org_resolver
    _org_resolver = resolver


async def resolve_org(session_id: Optional[str]) -> str:
    if not session_id or _org_resolver is None:
        return ""
    try:
        org = await _org_resolver(str(session_id))
        return str(org or "")
    except Exception as e:  # noqa: BLE001 — 解析失败 = 不入账（租户红线）
        logger.debug("[spatial_events] org resolve failed for %s: %s", session_id, e)
        return ""


def make_envelope(
    kind: str,
    *,
    org_id: str,
    subject_type: str,
    subject_key: str,
    payload: Optional[dict] = None,
    payload_ref: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    priority: str = "normal",
    source: str = "internal",
    dedupe_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> SpatialEventEnvelope:
    return SpatialEventEnvelope(
        kind=EventKind(kind),
        org_id=org_id,
        source=source,
        subject_type=SubjectType(subject_type),
        subject_key=subject_key,
        payload=payload or {},
        payload_ref=payload_ref,
        session_id=session_id,
        project_id=project_id,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        priority=EventPriority(priority),
        dedupe_key=dedupe_key,
        correlation_id=correlation_id,
    )


async def _dispatch(envelope: SpatialEventEnvelope) -> bool:
    """经全局 service 入账（fail-open）。返回是否入账成功。"""
    try:
        from app.services.spatial_events.service import (
            get_spatial_event_service,
        )

        result = get_spatial_event_service().ingest_sync(envelope)
        return bool(result and result.status == "appended")
    except Exception as e:  # noqa: BLE001
        logger.debug("[spatial_events] dispatch skipped: %s", e)
        return False


def _dispatch_sync(envelope: SpatialEventEnvelope) -> bool:
    """同步上下文的入账（线程池/Celery；fail-open）。"""
    return _dispatch(envelope)  # ingest_sync 本身同步；async 包装仅为便利


# ── E1: map mutation ────────────────────────────────────────────────

async def notify_map_mutation(
    session_id: str,
    *,
    mutation_kind: str,
    revision: int,
    actor: str = "",
    origin: str = "",
    org_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> bool:
    """成功 GIS mutation 之后调用（gis_world_state.mutation 发布点）。

    session 级事件：subject = layer/session mutation；org 缺失时经解析器，
    仍缺失 → 不入账。
    """
    org = org_id or await resolve_org(session_id)
    if not org:
        return False
    env = make_envelope(
        EventKind.MAP_MUTATION_APPLIED.value,
        org_id=org,
        subject_type=SubjectType.SESSION.value,
        subject_key=f"session:{session_id}"[:128],
        payload={
            "mutation_kind": str(mutation_kind)[:48],
            "revision": int(revision or 0),
            "actor": str(actor)[:48],
            "origin": str(origin)[:48],
        },
        session_id=session_id,
        priority=EventPriority.INTERACTIVE.value,
        correlation_id=correlation_id,
    )
    return await _dispatch(env)


# ── E2: artifact revision ───────────────────────────────────────────

async def notify_artifact_revision(
    artifact_id: str,
    *,
    org_id: str,
    project_id: Optional[str] = None,
    revision_no: int = 0,
    content_sha256: str = "",
    created: bool = True,
    workflow_run_id: Optional[str] = None,
) -> bool:
    """``record_revision`` 返回 ``created=True`` 后调用（幂等复用不产生事件）。"""
    if not created or not org_id or not artifact_id:
        return False
    env = make_envelope(
        EventKind.ARTIFACT_REVISION_COMMITTED.value,
        org_id=org_id,
        subject_type=SubjectType.ARTIFACT.value,
        subject_key=f"artifact:{artifact_id}"[:128],
        payload={
            "revision_no": int(revision_no or 0),
            "content_sha256": str(content_sha256)[:32],
            "workflow_run_id": str(workflow_run_id or "")[:64],
        },
        project_id=project_id,
        priority=EventPriority.NORMAL.value,
    )
    return await _dispatch(env)


# ── E3: job / simulation completion ─────────────────────────────────

_JOB_KIND_OF = {
    "completed": EventKind.JOB_COMPLETED.value,
    "failed": EventKind.JOB_FAILED.value,
    "cancelled": EventKind.JOB_CANCELLED.value,
}


async def notify_job_finished(
    job_id: str,
    *,
    org_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: str = "completed",
    job_type: str = "",
    result_ref: Optional[str] = None,
) -> bool:
    """``finish_job`` / 标记失败/取消终态后调用。kind 按终态映射。"""
    env = _job_envelope(
        job_id, org_id=org_id, session_id=session_id, project_id=project_id,
        status=status, job_type=job_type, result_ref=result_ref,
    )
    if env is None:
        return False
    return await _dispatch(env)


def _job_envelope(
    job_id: str,
    *,
    org_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: str = "completed",
    job_type: str = "",
    result_ref: Optional[str] = None,
) -> Optional[SpatialEventEnvelope]:
    kind = _JOB_KIND_OF.get(str(status or "").lower())
    if kind is None:
        return None  # 非终态（stale 等）不产生完成事件
    if kind == EventKind.JOB_COMPLETED.value and str(job_type or "").startswith(
        "simulation"
    ):
        kind = EventKind.SIMULATION_COMPLETED.value
    if not org_id:
        return None
    payload = {"job_type": str(job_type)[:48], "status": str(status)[:16]}
    return make_envelope(
        kind,
        org_id=org_id,
        subject_type=SubjectType.JOB.value,
        subject_key=f"job:{job_id}"[:128],
        payload=payload,
        payload_ref=result_ref,
        session_id=session_id,
        project_id=project_id,
        priority=EventPriority.NORMAL.value,
    )


def notify_job_finished_sync(
    job_id: str,
    *,
    org_id: str,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: str = "completed",
    job_type: str = "",
    result_ref: Optional[str] = None,
) -> bool:
    """同步变体（jobs worker 终态路径；org 必须显式提供——不解析不猜测）。"""
    env = _job_envelope(
        job_id, org_id=org_id, session_id=session_id, project_id=project_id,
        status=status, job_type=job_type, result_ref=result_ref,
    )
    if env is None:
        return False
    return _dispatch_sync(env)


# ── E4: map product version ─────────────────────────────────────────

async def notify_mapproduct_version(
    project_id: str,
    *,
    org_id: str,
    product_id: str = "",
    version_no: int = 0,
    data_changed: bool = False,
    diff_summary: Optional[dict] = None,
) -> bool:
    """``record_version`` 成功后调用；data_changed 驱动失效桥语义。"""
    env = _mapproduct_envelope(
        project_id, org_id=org_id, product_id=product_id,
        version_no=version_no, data_changed=data_changed,
        diff_summary=diff_summary,
    )
    if env is None:
        return False
    return await _dispatch(env)


def _mapproduct_envelope(
    project_id: str,
    *,
    org_id: str,
    product_id: str = "",
    version_no: int = 0,
    data_changed: bool = False,
    diff_summary: Optional[dict] = None,
) -> Optional[SpatialEventEnvelope]:
    if not org_id or not project_id:
        return None
    summary = {}
    for k, v in sorted((diff_summary or {}).items())[:6]:
        if isinstance(v, (int, float, bool)):
            summary[k] = v
        elif isinstance(v, str):
            summary[k] = v[:32]
    return make_envelope(
        EventKind.MAPPRODUCT_VERSION_RECORDED.value,
        org_id=org_id,
        subject_type=SubjectType.PRODUCT.value,
        subject_key=f"product:{product_id or project_id}"[:128],
        payload={
            "version_no": int(version_no or 0),
            "data_changed": bool(data_changed),
            **summary,
        },
        project_id=project_id,
        priority=EventPriority.NORMAL.value,
    )


def notify_mapproduct_version_sync(
    project_id: str,
    *,
    org_id: str,
    product_id: str = "",
    version_no: int = 0,
    data_changed: bool = False,
    diff_summary: Optional[dict] = None,
) -> bool:
    """同步变体（record_version 为 sync @staticmethod）。"""
    env = _mapproduct_envelope(
        project_id, org_id=org_id, product_id=product_id,
        version_no=version_no, data_changed=data_changed,
        diff_summary=diff_summary,
    )
    if env is None:
        return False
    return _dispatch_sync(env)
