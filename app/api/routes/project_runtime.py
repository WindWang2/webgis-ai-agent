"""Project Runtime Snapshot 路由（ADR-0220 — Durable Project Cartographic
Runtime V1）。

薄入口（不做 UI redesign）：

- ``POST   /projects/{pid}/runtime-snapshots`` —— 显式冻结（freeze 点）；
- ``GET    /projects/{pid}/runtime-snapshots`` —— 有界清单；
- ``GET    /projects/{pid}/runtime-snapshots/{sid}`` —— validation preview
  （resume 之前 missing/stale/recompute 即可见）；
- ``POST   /projects/{pid}/runtime-snapshots/{sid}/resume`` —— verify-first
  恢复 → 新 session + ResumeReceipt；
- ``DELETE /projects/{pid}/runtime-snapshots/{sid}`` —— 定点删除（GC 回执）。

鉴权三重门（与 workspace snapshot 路由同款）：项目鉴权
（get_project_with_auth，跨 org 一律 404 不泄露存在性）→ 会话所有权
（_verify_session_access，仅 freeze 面）→ 服务侧 created_by 二道守卫。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import actor_ids, get_current_user, get_owner_token
from app.core.database import get_db
from app.schemas.project_runtime_schema import (
    RuntimeSnapshotDeleteResponse,
    RuntimeSnapshotInspectResponse,
    RuntimeSnapshotListResponse,
    RuntimeSnapshotResumeRequest,
    RuntimeSnapshotResumeResponse,
    RuntimeSnapshotSaveRequest,
    RuntimeSnapshotSaveResponse,
    RuntimeSnapshotSummary,
)
from app.services.project_service import ProjectService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Project Runtime"])


async def _verify_session_access(
    session_id: str,
    user: Dict[str, Any],
    owner_token: Optional[str],
) -> None:
    """SEC-08 session 所有权守卫（project.py 同款；404 不泄露存在性）。"""
    from app.core.async_runner import thread_async_session
    from app.services.auth_history_bridge import verify_session_owner

    user_id = user.get("user_id") if isinstance(user, dict) else None
    async with thread_async_session() as adb:
        await verify_session_owner(
            adb, session_id, user_id=user_id, owner_token=owner_token
        )


async def _auth_project(
    db: Session, project_id: str, user: Dict[str, Any]
):
    user_id, org_id = actor_ids(user)
    project = await asyncio.to_thread(
        ProjectService.get_project_with_auth,
        db=db, project_id=project_id, user_id=user_id, org_id=org_id,
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post(
    "/{project_id}/runtime-snapshots",
    response_model=RuntimeSnapshotSaveResponse,
)
async def save_runtime_snapshot(
    project_id: str,
    req: RuntimeSnapshotSaveRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> RuntimeSnapshotSaveResponse:
    """显式冻结当前 session 的制图运行时（manifest + durable 指针组合）。"""
    await _auth_project(db, project_id, user)
    await _verify_session_access(req.session_id, user, owner_token)
    from app.services.project_runtime.freeze import FreezeError, save_runtime_snapshot
    from app.services.project_runtime.promotion import DEFAULT_FREEZE_BUDGET_BYTES

    user_id, org_id = actor_ids(user)
    try:
        snapshot = await save_runtime_snapshot(
            db,
            session_id=req.session_id,
            project_id=project_id,
            user_id=user_id or "",
            org_id=org_id,
            label=req.label,
            freeze_reason=req.freeze_reason,
            map_product_version_no=req.map_product_version_no,
            budget_bytes=(
                int(req.materialize_budget_bytes)
                if req.materialize_budget_bytes is not None
                else DEFAULT_FREEZE_BUDGET_BYTES
            ),
        )
    except FreezeError as exc:
        reason = exc.reason
        if reason in ("no_mapspec", "invalid_project_id"):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if reason == "mapspec_doc_oversized":
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        logger.warning("[RuntimeSnapshot] freeze failed: %s", exc)
        raise HTTPException(status_code=500, detail="runtime snapshot persistence failed") from exc

    durable = sum(
        1 for b in snapshot.source_bindings if b.durable_pointer is not None
    )
    recompute = sum(
        1 for b in snapshot.source_bindings
        if b.promotion_class in ("non_promotable", "recomputable")
    )
    return RuntimeSnapshotSaveResponse(
        project_id=project_id,
        snapshot_id=snapshot.snapshot_id,
        session_id=snapshot.session_id,
        workspace_snapshot_id=snapshot.workspace_snapshot_id,
        label=snapshot.label,
        freeze_reason=snapshot.freeze_reason,
        mapspec_fingerprint=snapshot.mapspec_fingerprint,
        mapspec_mutation_revision=snapshot.mapspec_mutation_revision,
        sources_total=len(snapshot.source_bindings),
        sources_durable=durable,
        sources_needing_recompute=recompute,
        datasets=len(snapshot.datasets),
        promotion_summary=dict(
            snapshot.disclosure.get("promotion") or {}
        ),
        snapshot=snapshot.model_dump(mode="json"),
    )


@router.get(
    "/{project_id}/runtime-snapshots",
    response_model=RuntimeSnapshotListResponse,
)
async def list_runtime_snapshots(
    project_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
) -> RuntimeSnapshotListResponse:
    """有界快照清单（≤25；项目域）。"""
    await _auth_project(db, project_id, user)
    from app.services.project_runtime.store import get_project_runtime_store

    items = await get_project_runtime_store().list_snapshots(project_id)
    return RuntimeSnapshotListResponse(
        project_id=project_id,
        count=len(items),
        items=[RuntimeSnapshotSummary(**item) for item in items],
    )


@router.get(
    "/{project_id}/runtime-snapshots/{snapshot_id}",
    response_model=RuntimeSnapshotInspectResponse,
)
async def inspect_runtime_snapshot(
    project_id: str,
    snapshot_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
) -> RuntimeSnapshotInspectResponse:
    """validation preview（只读；stale/missing/recompute 在 resume 前可见）。"""
    await _auth_project(db, project_id, user)
    from app.services.project_runtime.store import (
        UnsupportedSnapshotSchema,
        get_project_runtime_store,
    )
    from app.services.project_runtime.validation import (
        build_recovery_plan,
        validate_runtime_snapshot,
    )

    user_id, _org_id = actor_ids(user)
    try:
        snapshot = await asyncio.to_thread(
            get_project_runtime_store().load, project_id, snapshot_id
        )
    except UnsupportedSnapshotSchema as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Runtime snapshot not found")
    report = await validate_runtime_snapshot(
        db,
        project_id=project_id,
        snapshot_id=snapshot_id,
        current_user_id=str(user_id or ""),
    )
    recovery_plan = build_recovery_plan(snapshot, report)
    return RuntimeSnapshotInspectResponse(
        **report.to_dict(),
        recovery_plan=recovery_plan.model_dump(),
    )


@router.post(
    "/{project_id}/runtime-snapshots/{snapshot_id}/resume",
    response_model=RuntimeSnapshotResumeResponse,
)
async def resume_runtime_snapshot(
    project_id: str,
    snapshot_id: str,
    req: RuntimeSnapshotResumeRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
) -> RuntimeSnapshotResumeResponse:
    """verify-first 恢复 → 新 session runtime + ResumeReceipt（诚实披露）。"""
    await _auth_project(db, project_id, user)
    from app.services.project_runtime.resume import (
        ResumeBlocked,
        ResumeConflict,
        resume_runtime_snapshot as do_resume,
    )

    user_id, _org_id = actor_ids(user)
    try:
        receipt = await do_resume(
            db,
            project_id=project_id,
            snapshot_id=snapshot_id,
            user_id=user_id,
            allow_stale=req.allow_stale,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResumeBlocked as exc:
        detail: Dict[str, Any] = {"status": exc.status}
        report = exc.report
        if hasattr(report, "to_dict"):
            detail["validation"] = report.to_dict()
        raise HTTPException(status_code=409, detail=detail) from exc
    except ResumeConflict as exc:
        raise HTTPException(status_code=409, detail={
            "error": "resume already in progress",
            "lease": exc.holder,
        }) from exc
    except Exception:  # noqa: BLE001
        logger.exception(
            "[RuntimeResume] failed project=%s snapshot=%s",
            project_id, snapshot_id,
        )
        raise HTTPException(status_code=500, detail="runtime resume failed")
    return RuntimeSnapshotResumeResponse(**receipt.model_dump())


@router.delete(
    "/{project_id}/runtime-snapshots/{snapshot_id}",
    response_model=RuntimeSnapshotDeleteResponse,
)
async def delete_runtime_snapshot(
    project_id: str,
    snapshot_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> RuntimeSnapshotDeleteResponse:
    """定点删除 manifest（blob 归 artifact lifecycle GC 引用计数，绝不私删）。"""
    await _auth_project(db, project_id, user)
    from app.services.project_runtime.store import get_project_runtime_store

    user_id, _org_id = actor_ids(user)
    result = await get_project_runtime_store().delete(
        project_id, snapshot_id, owner_id=str(user_id or "")
    )
    if result is None:
        raise HTTPException(
            status_code=404, detail="Runtime snapshot not found or not owned"
        )
    return RuntimeSnapshotDeleteResponse(
        project_id=project_id,
        snapshot_id=snapshot_id,
        deleted=bool(result.get("deleted")),
        durable_pointers=int(result.get("durable_pointers") or 0),
    )
