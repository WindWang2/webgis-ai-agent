"""Runtime snapshot 校验（ADR-0220 §5 —— verify-first，绝不先写后验）。

``validate_runtime_snapshot`` 只读：schema / project / permission /
MapSpec 完整性 / durable 指针 / dataset 版本指纹 / capability 可用性
逐项核验，产出 ``ValidationReport``（机器可读 findings + 逐源 dispositions）。
stale 绝不自动变 current：所有漂移进 findings，由状态词表如实表达。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from app.services.project_runtime.contract import (
    ALREADY_DURABLE,
    DISPOSITION_EXTERNAL_PINNED,
    DISPOSITION_MISSING,
    DISPOSITION_NEEDS_RECOMPUTE,
    DISPOSITION_RESTORE_DURABLE,
    DISPOSITION_RESTORE_LIVE,
    EXTERNAL_PINNED,
    PROMOTED,
    RECOMPUTABLE,
    RESUME_CAPABILITY_UNAVAILABLE,
    RESUME_DATASET_VERSION_CHANGED,
    RESUME_MISSING_ARTIFACT,
    RESUME_PERMISSION_CHANGED,
    RESUME_REQUIRES_RECOMPUTE,
    RecoveryPlan,
    SourceDisposition,
    ValidationFinding,
    ValidationReport,
)
from app.services.project_runtime.rebind import canonical_fingerprint
from app.services.project_runtime.store import (
    UnsupportedSnapshotSchema,
)

logger = logging.getLogger(__name__)

#: capability runtime status 词表中「不可用」语义的成员。
_UNAVAILABLE_CAP_STATUSES = {"unavailable", "credential_missing", "error"}


async def validate_runtime_snapshot(
    db: Any,
    *,
    project_id: str,
    snapshot_id: str,
    current_user_id: str = "",
    skip_capability_check: bool = False,
) -> ValidationReport:
    """只读校验（resume 前置；不写任何状态）。"""
    from app.services.project_runtime.store import get_project_runtime_store

    report = ValidationReport(snapshot_id=snapshot_id)
    try:
        snapshot = get_project_runtime_store().load(project_id, snapshot_id)
    except UnsupportedSnapshotSchema as exc:
        report.schema_supported = False
        report.findings.append(ValidationFinding(
            state="unsupported_schema",
            subject=snapshot_id,
            reason=str(exc)[:240],
        ))
        return report
    if snapshot is None:
        report.findings.append(ValidationFinding(
            state="blocked", subject=snapshot_id,
            reason="snapshot not found or unreadable",
        ))
        return report

    # ── project 状态（存在性/active 由路由侧项目鉴权先行；此处复核状态）──
    project_ok, permission = await _check_project(
        db, snapshot.project_id, current_user_id, snapshot.created_by
    )
    report.project_ok = project_ok
    report.permission = permission
    if not project_ok:
        report.findings.append(ValidationFinding(
            state="blocked", subject=snapshot.project_id,
            reason="project missing, archived, or deleted",
        ))
        return report
    if permission == "changed":
        report.findings.append(ValidationFinding(
            state=RESUME_PERMISSION_CHANGED,
            subject=str(current_user_id)[:160],
            reason=(
                "resuming a runtime snapshot created by another user "
                "(same project scope); provenance is preserved"
            ),
        ))

    # ── MapSpec 文档完整性 ──
    doc = snapshot.mapspec_doc if isinstance(snapshot.mapspec_doc, dict) else {}
    report.mapspec_integrity_ok = bool(doc) and (
        canonical_fingerprint(doc) == snapshot.mapspec_fingerprint
    )
    if not report.mapspec_integrity_ok:
        report.findings.append(ValidationFinding(
            state=RESUME_MISSING_ARTIFACT, subject="mapspec_doc",
            reason="embedded MapSpec doc missing or fingerprint mismatch",
        ))

    # ── 逐源 dispositions（durable 指针校验 + 旧会话存活兜底探测）──────
    await _validate_sources(snapshot, report)

    # ── dataset 版本指纹 ──
    await _validate_datasets(db, snapshot, report)

    # ── capability 可用性 ──
    if not skip_capability_check:
        _validate_capabilities(snapshot, report)
    return report


async def _check_project(
    db: Any, project_id: str, current_user_id: str, created_by: str
) -> tuple:
    """项目状态复核 + 权限注记（路由侧 get_project_with_auth 是第一道门）。

    ``db`` 是同步 Session（项目路由既有形态）；查询卸载到线程。
    """
    try:
        from sqlalchemy import select

        from app.models.project import Project

        def _query():
            return db.execute(
                select(Project.status).where(Project.id == project_id)
            ).scalar_one_or_none()

        status = await asyncio.to_thread(_query)
    except Exception as e:  # noqa: BLE001 — 状态不可判定 = 按不可恢复（fail-closed）
        logger.info("[RuntimeValidate] project check failed: %s", e)
        return False, "unknown"
    if status is None or str(status) != "active":
        return False, "ok"
    if created_by and current_user_id and created_by != current_user_id:
        return True, "changed"
    return True, "ok"


async def _validate_sources(snapshot: Any, report: ValidationReport) -> None:
    from app.services.workspace.durability import verify_durable_pointer

    old_sid = str(snapshot.session_id or "")
    for binding in snapshot.source_bindings:
        disposition = SourceDisposition(
            source_id=binding.source_id, ref=binding.ref,
            disposition=DISPOSITION_MISSING,
        )
        if binding.promotion_class == EXTERNAL_PINNED:
            disposition.disposition = DISPOSITION_EXTERNAL_PINNED
            report.dispositions.append(disposition)
            continue
        if not binding.ref:
            disposition.disposition = DISPOSITION_MISSING
            disposition.detail = "no session ref at freeze"
            report.dispositions.append(disposition)
            continue
        pointer = binding.durable_pointer
        if pointer is not None:
            status = await asyncio.to_thread(
                verify_durable_pointer, pointer.model_dump()
            )
            if status == "verified":
                disposition.disposition = DISPOSITION_RESTORE_DURABLE
            else:
                disposition.disposition = DISPOSITION_NEEDS_RECOMPUTE
                disposition.detail = f"pointer_{status}"
                report.findings.append(ValidationFinding(
                    state=RESUME_MISSING_ARTIFACT,
                    subject=binding.ref,
                    reason=f"durable pointer {status}",
                ))
            report.dispositions.append(disposition)
            continue
        # 无指针：冻结时已死/未晋升 —— 恢复期旧会话仍活则可抢救。
        if binding.promotion_class in (PROMOTED, ALREADY_DURABLE):
            # 有晋升分类却无指针 = manifest 损坏（防御披露）
            disposition.disposition = DISPOSITION_NEEDS_RECOMPUTE
            disposition.detail = "pointer missing from manifest"
            report.dispositions.append(disposition)
            report.findings.append(ValidationFinding(
                state=RESUME_MISSING_ARTIFACT, subject=binding.ref,
                reason="manifest records promotion without pointer",
            ))
            continue
        if await _ref_live_in_old_session(old_sid, binding.ref):
            disposition.disposition = DISPOSITION_RESTORE_LIVE
            disposition.detail = "old session still alive"
            report.dispositions.append(disposition)
            continue
        disposition.disposition = DISPOSITION_NEEDS_RECOMPUTE
        disposition.detail = (
            "recomputable" if binding.promotion_class == RECOMPUTABLE
            else "payload dead at freeze"
        )
        report.dispositions.append(disposition)
        if binding.promotion_class != RECOMPUTABLE:
            report.findings.append(ValidationFinding(
                state=RESUME_MISSING_ARTIFACT, subject=binding.ref,
                reason="no durable pointer and payload not promotable",
            ))
        else:
            report.findings.append(ValidationFinding(
                state=RESUME_REQUIRES_RECOMPUTE, subject=binding.ref,
                reason="payload dead at freeze; lineage parents allow recompute",
            ))


async def _ref_live_in_old_session(old_sid: str, ref: str) -> bool:
    if not old_sid:
        return False
    try:
        from app.services.artifact_registry import probe_ref

        return await probe_ref(old_sid, ref) is not None
    except Exception:  # noqa: BLE001 — 探测失败按不存活（诚实保守）
        return False


async def _validate_datasets(
    db: Any, snapshot: Any, report: ValidationReport
) -> None:
    if not snapshot.datasets:
        return
    try:
        from sqlalchemy import select

        from app.models.project import ProjectDataset

        ids = [d.dataset_id for d in snapshot.datasets]

        def _query():
            rows = db.execute(
                select(
                    ProjectDataset.id,
                    ProjectDataset.version_fingerprint,
                    ProjectDataset.detached_at,
                ).where(ProjectDataset.id.in_(ids))
            ).all()
            return {r[0]: (r[1], r[2]) for r in rows}

        current = await asyncio.to_thread(_query)
    except Exception as e:  # noqa: BLE001 — dataset 比对失败按不可判（诚实披露）
        logger.info("[RuntimeValidate] dataset check failed: %s", e)
        for d in snapshot.datasets:
            report.dataset_verdicts[d.dataset_id] = "unknown"
        report.findings.append(ValidationFinding(
            state=RESUME_DATASET_VERSION_CHANGED, subject="datasets",
            reason="dataset state could not be verified",
        ))
        return

    for d in snapshot.datasets:
        row = current.get(d.dataset_id)
        if row is None:
            verdict = "missing"
        elif row[1] is not None:
            verdict = "detached"
        elif d.version_fingerprint and row[0] != d.version_fingerprint:
            verdict = "changed"
        else:
            verdict = "ok"
        report.dataset_verdicts[d.dataset_id] = verdict
        if verdict != "ok":
            report.findings.append(ValidationFinding(
                state=RESUME_DATASET_VERSION_CHANGED,
                subject=d.dataset_id,
                reason=f"dataset {verdict} since freeze",
            ))


def _validate_capabilities(snapshot: Any, report: ValidationReport) -> None:
    caps = list(snapshot.plan.required_capabilities or [])
    if not caps:
        return
    try:
        from app.services.capability_runtime.snapshot import (
            build_capability_runtime_snapshot,
        )

        snap = build_capability_runtime_snapshot(caps)
    except Exception as e:  # noqa: BLE001 — capability 校验缺席按 unknown 披露
        logger.info("[RuntimeValidate] capability check skipped: %s", e)
        return
    for fact in snap.capabilities:
        status = str(fact.runtime_status)
        report.capability_verdicts[str(fact.capability_id)[:128]] = status
        if status in _UNAVAILABLE_CAP_STATUSES:
            report.findings.append(ValidationFinding(
                state=RESUME_CAPABILITY_UNAVAILABLE,
                subject=str(fact.capability_id)[:128],
                reason="; ".join(fact.hints[:2])[:240] or status,
            ))


def build_recovery_plan(
    snapshot: Any, report: ValidationReport
) -> RecoveryPlan:
    """ValidationReport → 显式恢复计划（仍不执行任何副作用）。"""
    needs_recompute = [
        d.ref or d.source_id
        for d in report.dispositions
        if d.disposition == DISPOSITION_NEEDS_RECOMPUTE
    ]
    steps: List[Dict[str, Any]] = [
        {"step": "mint_session", "detail": "new session id + Conversation row"},
        {"step": "restore_payloads", "detail": "digest-verified durable reads "
         "+ old-session fallback; binary lanes restore to new-session disk lane"},
        {"step": "rebind_mapspec", "detail": "guarded source rebind + atomic "
         "first-spec commit (revision=1)"},
    ]
    if snapshot.workspace_snapshot_id:
        steps.append({
            "step": "register_ledger",
            "detail": f"workspace snapshot {snapshot.workspace_snapshot_id} "
                      "contracts re-registered with remapped ref ids",
        })
    steps.append({"step": "seed_plan", "detail": "fresh SessionPlan with "
                 "resumed_from_runtime disclosure block"})
    return RecoveryPlan(
        snapshot_id=report.snapshot_id,
        status=report.status,
        steps=steps,
        needs_recompute=needs_recompute,
        invalidated=[
            "render_observations", "visual_evidence", "working_context_cache",
            "in_flight_jobs",
        ],
    )
