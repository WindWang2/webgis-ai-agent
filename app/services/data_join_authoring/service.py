"""G38 单一编排面 —— REST 路由与 Pi 工具共用（不绕过本面直接触内核）。

生命周期：describe（源卡）→ create/update 计划（CAS）→ preview（全量诊断，
与执行同一 executor）→ execute（durable job + 冻结输入）→ inspect/result
（权威文件分页）→ cancel（两段式）→ recovery（孤儿收据收敛）。

授权：HTTP 由路由层 ``require_owned_session`` / 工具侧由 ToolRegistry 注入
session；每源解析经 G18 ``resolve_source`` 重新鉴权；跨 session 访问计划/
运行一律 ``not_owned``（404 语义，无存在性泄漏）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_join_authoring import contracts, outputs
from app.services.data_join_authoring.errors import DataJoinError
from app.services.data_join_authoring.executor import (
    JoinExecutor,
    JoinStats,
    LoadedSource,
)
from app.services.data_join_authoring.plan import JoinPlan, parse_plan

logger = logging.getLogger(__name__)

TASK_TYPE = "data_join_execute"

_PLAN_JSON_MAX = 32 * 1024
#: 单行内存估计（临时预算估算基数；与 executor 一致）
_EST_ROW_BYTES = 2048
_PREVIEW_JSON_MAX = 8 * 1024
#: 工具有界等待（inline 轮询上限，G23 import 工具同款语义）
DEFAULT_WAIT_MAX_S = 45.0


# ───────────────────────── 源解析（G18 复用） ─────────────────────────

async def _resolve_source_view(
    session_id: str, source: Dict[str, Any], actor: Dict[str, Any], side: str
) -> Tuple[LoadedSource, Dict[str, Any]]:
    """源锚 + 调用方 → 授权快照 + LoadedSource（行帽内装载）。

    返回 (LoadedSource, 源卡 evidence)。解析/授权失败 → typed
    source_unresolvable（包装 G18 原始码，不吞细节）。
    """
    from app.schemas.data_delivery_schema import DeliverySourceRef
    from app.services.data_delivery.source_resolver import (
        DeliveryActor,
        SourceResolutionError,
        resolve_source,
    )

    try:
        ref = DeliverySourceRef.model_validate(source)
    except Exception as exc:
        raise DataJoinError(
            "invalid_plan", f"{side} 源锚无效：{exc}", side=side) from exc
    dactor = DeliveryActor(
        user_id=actor.get("user_id"),
        org_id=actor.get("org_id"),
        owner_token=actor.get("owner_token"),
    )
    try:
        snapshot = await resolve_source(ref, dactor)
    except SourceResolutionError as exc:
        # 工具平面回退：Pi 工具的 session_id 由 ToolRegistry 从已授权上下文
        # 注入 —— 注入 session 本身即授权。匿名 actor（无 user/token）且源
        # 就在同一受信 session 内时，经同一快照构建器直读（selection_tools
        # 同款语义）；跨 session 源仍然拒绝。
        code = str(getattr(exc, "error_code", "") or getattr(exc, "code", ""))
        reason = str(getattr(exc, "reason", "") or exc)
        trusted = actor.get("trusted_session_id")
        if (code.upper().endswith("UNAUTHORIZED") and trusted
                and source.get("kind") in ("session_ref", "table_rows")
                and source.get("session_id") == session_id == trusted):
            ref = source.get("table_ref") or source.get("ref")
            snapshot = await _trusted_session_snapshot(
                session_id, ref,
                expect_rows=source.get("kind") == "table_rows")
        else:
            raise DataJoinError(
                "source_unresolvable",
                f"{side} 源解析失败：{reason}",
                side=side, upstream_code=code,
                status=410,
            ) from exc
    except Exception as exc:  # noqa: BLE001 — 非预期解析失败如实传播为域错误
        raise DataJoinError(
            "source_unresolvable",
            f"{side} 源解析异常：{type(exc).__name__}: {exc}", side=side,
        ) from exc
    view = _load_source(side, snapshot)
    evidence = {
        "kind": snapshot.kind,
        "label": snapshot.label,
        "is_table": snapshot.is_table,
        "row_count": snapshot.row_count,
        "content_fingerprint": snapshot.content_fingerprint,
        "content_revision": snapshot.content_revision,
        "artifact_revision_no": snapshot.artifact_revision_no,
        # 装载后的 CRS（含 FC 顶层声明回退）—— 空间资格判据
        "crs": view.crs,
        "completeness": snapshot.completeness,
        "fields": snapshot.fields,
        "geometry": snapshot.geometry,
    }
    return view, evidence


async def _trusted_session_snapshot(
    session_id: str, ref: str, *, expect_rows: bool = False
) -> Any:
    """受信 session 内的 session_ref/table_rows 直读（复用 G18 快照构建器）。

    ``expect_rows``（table_rows 锚）：按 G18 table_rows 路径的 rows 形态
    强校验（评审 F13 —— FC 载荷不得经 table_rows 锚静默当几何源）。
    """
    from app.services.data_delivery.source_resolver import (
        SourceSnapshot,
        _as_rows_payload,
        _canonical_json_sha256,
        _profile_rows,
        _snapshot_from_payload,
    )
    from app.services.session_data import session_data_manager

    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref)
    payload = await session_data_manager.get(session_id, ref)
    if payload is None:
        raise DataJoinError(
            "source_unresolvable",
            f"会话数据引用已过期：{ref}（请先物化或晋升为项目产物）",
            upstream_code="source_expired", status=410,
        )
    label = ref
    if descriptor:
        label = str(descriptor.get("alias") or descriptor.get("title") or ref)
    if expect_rows:
        rows = _as_rows_payload(payload)
        if rows is None:
            raise DataJoinError(
                "source_unresolvable",
                f"引用不是 rows 表格形态：{ref}",
                upstream_code="source_shape_unsupported", status=410,
            )
        snap = SourceSnapshot(
            kind="table_rows", label=label, is_table=True,
            row_count=len(rows["rows"]),
            content_fingerprint=_canonical_json_sha256(rows["rows"]),
            completeness={k: rows[k] for k in (
                "is_complete", "truncated", "total_matching", "group_count")
                if k in rows},
            fields=_profile_rows(rows["rows"]), payload=rows,
        )
        snap._src_ref = ref
        return snap
    snap = _snapshot_from_payload(label, payload, ref)
    snap.kind = "session_ref"
    if descriptor and isinstance(descriptor.get("content_revision"), int):
        snap.content_revision = descriptor["content_revision"]
    return snap


def _load_source(side: str, snapshot: Any) -> LoadedSource:
    """快照 → LoadedSource（行帽检查在调用方预算下执行）。

    CRS：快照 crs（G18 materialize 的 schema_info 证据）优先；缺省时认 FC
    顶层 ``crs`` 声明成员（RFC 7946 之外的显式声明帧 —— 空间关联的资格
    判据，缺声明即 typed 阻断，绝不默认 WGS84）。
    """
    from app.services.data_delivery.source_resolver import _normalize_crs

    rows: List[Dict[str, Any]] = []
    geoms: Optional[List[Optional[Dict[str, Any]]]] = None
    crs = snapshot.crs
    if snapshot.is_table:
        payload = snapshot.payload or {}
        rows = list(payload.get("rows") or [])
    else:
        fc = snapshot.payload or {}
        feats = fc.get("features") or []
        geoms = []
        for f in feats:
            rows.append((f.get("properties") or {}).copy())
            geoms.append(f.get("geometry"))
        if not crs:
            crs = _normalize_crs(fc.get("crs"))
    declared = [f.get("name") for f in (snapshot.fields or []) if f.get("name")]
    return LoadedSource(
        side=side,
        label=snapshot.label,
        is_table=snapshot.is_table,
        crs=crs,
        content_fingerprint=snapshot.content_fingerprint[:64],
        rows=rows,
        geometries=geoms,
        declared_fields=declared,
    )


def _enforce_source_budget(plan: JoinPlan, left: LoadedSource, right: LoadedSource) -> None:
    cap = plan.effective_budgets()["max_source_rows"]
    for side, src in (("left", left), ("right", right)):
        n = src.row_count()
        if n > cap:
            raise DataJoinError(
                "budget_exceeded",
                f"{side} 源 {n} 行超过单源预算 {cap}（执行前拦截）",
                side=side, rows=n, cap=cap,
            )
    est = (left.row_count() + right.row_count()) * _EST_ROW_BYTES
    if est > plan.effective_budgets()["temp_bytes_budget"]:
        raise DataJoinError(
            "budget_exceeded",
            f"临时内存估算 {est} B 超过预算 "
            f"{plan.effective_budgets()['temp_bytes_budget']} B",
            estimated_bytes=est,
        )


# ───────────────────────── 源卡（describe） ─────────────────────────

async def describe_source(
    session_id: str, source: Dict[str, Any], actor: Dict[str, Any]
) -> Dict[str, Any]:
    view, evidence = await _resolve_source_view(session_id, source, actor, "left")
    return {
        "session_id": session_id,
        "source": evidence,
        "row_count": view.row_count(),
        "has_geometry": view.has_geometry(),
        "sample_fields": evidence["fields"][:40],
    }


# ───────────────────────── 计划 CRUD（CAS） ─────────────────────────

def _plan_row_to_view(row: Any) -> Dict[str, Any]:
    return {
        "plan_id": row.id,
        "session_id": row.session_id,
        "project_id": row.project_id,
        "title": row.title,
        "status": row.status,
        "revision": row.revision,
        "normalized_digest": row.normalized_digest,
        "left_fingerprint": row.left_fingerprint,
        "right_fingerprint": row.right_fingerprint,
        "preview": row.preview,
        "preview_digest": row.preview_digest,
        "plan": row.plan,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def create_plan(
    session_id: str,
    plan_data: Dict[str, Any],
    *,
    actor: Dict[str, Any],
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    plan = parse_plan(plan_data)
    blob = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    if len(blob) > _PLAN_JSON_MAX:
        raise DataJoinError(
            "invalid_plan", f"计划过大（{len(blob)} B > {_PLAN_JSON_MAX}）")

    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinPlan

    with SessionLocal() as db:
        row = DataJoinPlan(
            session_id=session_id,
            project_id=project_id,
            created_by=actor.get("user_id"),
            title=plan.title or plan.output_name,
            status="draft",
            revision=1,
            plan=plan.model_dump(mode="json"),
            normalized_digest=plan.normalized_digest(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _plan_row_to_view(row)


def _get_plan_row(db: Any, session_id: str, plan_id: str) -> Any:
    from app.models.data_join_authoring import DataJoinPlan

    row = db.get(DataJoinPlan, plan_id)
    if row is None or row.session_id != session_id:
        raise DataJoinError("not_owned", "计划不存在或不属于当前会话", plan_id=plan_id)
    return row


async def get_plan(session_id: str, plan_id: str) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        return _plan_row_to_view(_get_plan_row(db, session_id, plan_id))


async def list_plans(
    session_id: str, *, limit: int = 50, include_archived: bool = False
) -> Dict[str, Any]:
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinPlan

    limit = max(1, min(int(limit), 100))
    with SessionLocal() as db:
        stmt = (
            select(DataJoinPlan)
            .where(DataJoinPlan.session_id == session_id)
            .order_by(DataJoinPlan.created_at.desc())
            .limit(limit)
        )
        rows = list(db.scalars(stmt))
        out = [_plan_row_to_view(r) for r in rows
               if include_archived or r.status != "archived"]
        return {"plans": out, "count": len(out)}


async def update_plan(
    session_id: str,
    plan_id: str,
    plan_data: Dict[str, Any],
    *,
    expected_revision: int,
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    plan = parse_plan(plan_data)
    blob = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    if len(blob) > _PLAN_JSON_MAX:
        raise DataJoinError(
            "invalid_plan", f"计划过大（{len(blob)} B > {_PLAN_JSON_MAX}）")
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise DataJoinError(
                "stale_plan_revision",
                f"计划修订冲突：expected {expected_revision}，当前 {row.revision}",
                current_revision=row.revision,
            )
        if row.status == "archived":
            raise DataJoinError(
                "invalid_plan", "已归档计划不可修改（新建计划继续）", plan_id=plan_id)
        row.plan = plan.model_dump(mode="json")
        row.normalized_digest = plan.normalized_digest()
        row.title = plan.title or plan.output_name
        row.revision = row.revision + 1
        # 计划变化 → 旧预览证据失效（digest 不再匹配）
        row.preview = None
        row.preview_digest = None
        db.commit()
        db.refresh(row)
        return _plan_row_to_view(row)


async def archive_plan(session_id: str, plan_id: str, *, expected_revision: int) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise DataJoinError(
                "stale_plan_revision",
                f"计划修订冲突：expected {expected_revision}，当前 {row.revision}",
                current_revision=row.revision,
            )
        row.status = "archived"
        row.revision = row.revision + 1
        db.commit()
        db.refresh(row)
        return _plan_row_to_view(row)


# ───────────────────────── 预览（全量诊断；与执行同一 executor） ─────────────────────────

def _stats_to_diagnostics(stats: JoinStats, plan: JoinPlan) -> Dict[str, Any]:
    return {
        "basis": stats.basis,
        "mode": plan.mode,
        "left_rows": stats.left_rows,
        "right_rows": stats.right_rows,
        "left_null_keys": stats.left_null_keys,
        "right_null_keys": stats.right_null_keys,
        "left_distinct_keys": stats.left_distinct_keys,
        "right_distinct_keys": stats.right_distinct_keys,
        "left_duplicate_keys": stats.left_duplicate_keys,
        "right_duplicate_keys": stats.right_duplicate_keys,
        "candidate_pairs": stats.candidate_pairs,
        "matched_pairs": stats.matched_pairs,
        "matched_left_rows": stats.matched_left_rows,
        "unmatched_left_rows": stats.unmatched_left_rows,
        "unmatched_right_rows": stats.unmatched_right_rows,
        "left_null_geometry": stats.left_null_geometry,
        "right_invalid_geometry": stats.right_invalid_geometry,
        "output_rows": stats.output_rows,
        "working_crs": stats.working_crs,
        "transform_note": stats.transform_note,
        "stage_counts": stats.stage_counts,
        "example_counts": {k: len(v) for k, v in stats.examples.items()},
    }


async def preview_plan(
    session_id: str,
    plan_id: str,
    *,
    expected_revision: int,
    actor: Dict[str, Any],
    sample_rows: int = contracts.PREVIEW_ROW_LIMIT,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise DataJoinError(
                "stale_plan_revision",
                f"计划修订冲突：expected {expected_revision}，当前 {row.revision}",
                current_revision=row.revision,
            )
        plan = parse_plan(row.plan)
        plan_view = _plan_row_to_view(row)

    left, left_ev = await _resolve_source_view(
        session_id, plan.left.model_dump(exclude_none=True), actor, "left")
    right, right_ev = await _resolve_source_view(
        session_id, plan.right.model_dump(exclude_none=True), actor, "right")
    _enforce_source_budget(plan, left, right)

    started = time.monotonic()
    executor = JoinExecutor(plan)
    try:
        if plan.mode == "attribute":
            out_rows = await asyncio.to_thread(executor.run_attribute, left, right)
        else:
            out_rows = await asyncio.to_thread(executor.run_spatial, left, right)
    except DataJoinError as exc:
        # 诊断语义：基数违规/超预算在预览面即披露（带全量计数），不落库
        diagnostics = _stats_to_diagnostics(executor.stats, plan)
        return {
            "plan": plan_view,
            "status": "blocked",
            "blocked_code": exc.code,
            "blocked": exc.payload(),
            "diagnostics": diagnostics,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    diagnostics = _stats_to_diagnostics(executor.stats, plan)
    sample = []
    for r in out_rows[:max(1, min(int(sample_rows), contracts.PREVIEW_ROW_LIMIT))]:
        r = dict(r)
        r.pop("__geometry__", None)
        sample.append(r)
    result = {
        "plan": plan_view,
        "status": "ready",
        "diagnostics": diagnostics,
        "sample_rows": sample,
        "sample_row_count": len(sample),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    # 预览证据落计划行（有界；digest 绑定 → 计划/源变化即失效）
    preview_ev = {
        "status": "ready",
        "diagnostics": diagnostics,
        "fingerprints": {
            "left": left.content_fingerprint,
            "right": right.content_fingerprint,
        },
        "at": time.time(),
    }
    digest_key = (f"{plan.normalized_digest()}:{left.content_fingerprint}:"
                  f"{right.content_fingerprint}")
    if len(json.dumps(preview_ev, ensure_ascii=False)) > _PREVIEW_JSON_MAX:
        preview_ev = {"status": "ready", "diagnostics": diagnostics,
                      "fingerprints": preview_ev["fingerprints"],
                      "truncated": True}
    with SessionLocal() as db:
        prow = _get_plan_row(db, session_id, plan_id)
        if prow.revision == int(expected_revision):
            prow.preview = preview_ev
            prow.preview_digest = digest_key[:128]
            prow.left_fingerprint = left.content_fingerprint[:128]
            prow.right_fingerprint = right.content_fingerprint[:128]
            db.commit()
    return result


# ───────────────────────── 执行（durable job） ─────────────────────────

def _build_idempotency_key(
    session_id: str, plan_id: str, plan_digest: str,
    left_fp: str, right_fp: str,
) -> str:
    return (f"{TASK_TYPE}:{session_id}:{plan_id}:{plan_digest}:"
            f"{left_fp[:48]}:{right_fp[:48]}")


async def execute_plan(
    session_id: str,
    plan_id: str,
    *,
    expected_revision: int,
    actor: Dict[str, Any],
    background: bool = True,
) -> Dict[str, Any]:
    """执行：源复核 → 幂等建 job → run 行 → 后台/内联 runner。

    返回 run 视图（含 job_id/poll）；内联完成时直接带 receipt。
    """
    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise DataJoinError(
                "stale_plan_revision",
                f"计划修订冲突：expected {expected_revision}，当前 {row.revision}",
                current_revision=row.revision,
            )
        plan = parse_plan(row.plan)
        plan_revision = row.revision

    # 源解析 + 授权 + 冻结指纹（执行与预览同一解析路径）
    left, _ = await _resolve_source_view(
        session_id, plan.left.model_dump(exclude_none=True), actor, "left")
    right, _ = await _resolve_source_view(
        session_id, plan.right.model_dump(exclude_none=True), actor, "right")
    _enforce_source_budget(plan, left, right)

    plan_digest = plan.normalized_digest()
    base_key = _build_idempotency_key(
        session_id, plan_id, plan_digest,
        left.content_fingerprint, right.content_fingerprint)

    # 收据短路：最新已完成 run —— 内容可核验才复用（J28：内容被删 →
    # typed 失效，绝不凭历史收据返回成功）
    existing = _find_completed_run_by_key(session_id, base_key)
    if existing is not None:
        try:
            outputs.verify_published(existing["run_id"])
            return {**existing, "idempotent_reuse": True}
        except DataJoinError:
            _invalidate_run(session_id, existing["run_id"], "receipt_missing")

    # 活跃收敛（J19）：同 base 键已有 pending/running run（另一副本在跑）
    # → 复用该 run，不建双执行/双成果
    active = _find_active_run_by_key(session_id, base_key)
    if active is not None:
        return {**_run_view(session_id, active["run_id"]),
                "idempotent_reuse": True,
                "job_id": active["job_id"],
                "poll": f"/api/v1/tasks/jobs/{active['job_id']}"}

    # 世代键：历史 run（failed/cancelled/已失效）不阻塞新执行 —— job store
    # 幂等键对终态 job 不释放，新世代换键得到新 job 行。
    generation = _count_runs_by_key_prefix(session_id, base_key)
    idem = base_key if generation == 0 else f"{base_key}:g{generation + 1}"

    # run 行先落（评审 F14）：CAS 终检在 job 创建之前 —— 若计划在此窗口被
    # 修改，typed 拒绝且不留下孤儿 job。
    run_id = outputs.new_run_id()
    with SessionLocal() as db:
        prow = _get_plan_row(db, session_id, plan_id)
        if prow.revision != plan_revision:
            raise DataJoinError(
                "stale_plan_revision", "计划在执行提交期间被修改；请重试",
                current_revision=prow.revision,
            )
        run = DataJoinRun(
            id=run_id,
            plan_id=plan_id,
            session_id=session_id,
            created_by=actor.get("user_id"),
            plan_revision=plan_revision,
            plan_digest=plan_digest,
            status="pending",
            idempotency_key=idem,
        )
        db.add(run)
        db.commit()

    from app.services.data_operations.runner import submit_async_job

    try:
        job_id, job_status, reused = submit_async_job(
            task_type=TASK_TYPE,
            kind="data_join",
            display_name=f"数据关联 · {(plan.title or plan.output_name)[:24]}",
            params={
                "session_id": session_id,
                "plan_id": plan_id,
                "run_id": run_id,
                "run_revision": plan_revision,
                "plan_digest": plan_digest,
            },
            idempotency_key=idem,
            owner_id=actor.get("user_id"),
            owner_token=actor.get("owner_token"),
            org_id=actor.get("org_id"),
            session_id=session_id,
            project_id=None,
        )
    except Exception:
        # 提交失败（评审 R3）：删除已落的 run 行 —— 不留无 job 的 pending 残留
        with SessionLocal() as db:
            dead = db.get(DataJoinRun, run_id)
            if dead is not None:
                db.delete(dead)
                db.commit()
        raise
    with SessionLocal() as db:
        run = db.get(DataJoinRun, run_id)
        run.job_id = job_id
        db.commit()
    if reused and job_status in ("pending", "queued", "running", "cancelling"):
        # 极端竞态：并发提交同世代键 —— 收敛到既有 job 的 run（丢弃本 run 行）
        twin = _find_run_by_job(session_id, job_id)
        if twin is not None and twin != run_id:
            with SessionLocal() as db:
                dead = db.get(DataJoinRun, run_id)
                if dead is not None:
                    db.delete(dead)
                    db.commit()
            return {**_run_view(session_id, twin), "idempotent_reuse": True,
                    "job_id": job_id,
                    "poll": f"/api/v1/tasks/jobs/{job_id}"}

    if background:
        runner_task = asyncio.get_running_loop().create_task(
            _run_execution_job(run_id, job_id, session_id, plan, plan_revision,
                               actor, left_fingerprint=left.content_fingerprint,
                               right_fingerprint=right.content_fingerprint),
            name=f"data-join:{run_id}",
        )
        _RUNNERS[run_id] = runner_task
        runner_task.add_done_callback(lambda t: _RUNNERS.pop(run_id, None))
        return _run_view(session_id, run_id) | {
            "job_id": job_id, "poll": f"/api/v1/tasks/jobs/{job_id}",
        }

    await _run_execution_job(
        run_id, job_id, session_id, plan, plan_revision, actor,
        left_fingerprint=left.content_fingerprint,
        right_fingerprint=right.content_fingerprint)
    view = _run_view(session_id, run_id)
    view["job_id"] = job_id
    return view


_RUNNERS: Dict[str, asyncio.Task] = {}


async def _run_execution_job(
    run_id: str,
    job_id: int,
    session_id: str,
    plan: JoinPlan,
    plan_revision: int,
    actor: Dict[str, Any],
    *,
    left_fingerprint: str,
    right_fingerprint: str,
) -> None:
    """runner 包装：body 内做源复核 + 执行 + 发布（全部 fenced 终态内）。"""
    from app.services.data_operations.runner import run_async_job

    async def body(handle: Any) -> Dict[str, Any]:
        return await _execute_body(
            handle, run_id, session_id, plan, actor,
            left_fingerprint=left_fingerprint, right_fingerprint=right_fingerprint)

    await run_async_job(job_id, body)


async def _execute_body(
    handle: Any,
    run_id: str,
    session_id: str,
    plan: JoinPlan,
    actor: Dict[str, Any],
    *,
    left_fingerprint: str,
    right_fingerprint: str,
) -> Dict[str, Any]:
    """执行体：冻结复核 → 装载 → 内核 → 装配 → 发布 → 收据。

    任何 typed 失败 → run 行 failed（错误码入行），job 终态由 runner 落；
    取消 → staging 清理，绝不留公开半成品。
    """
    from datetime import datetime, timezone

    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    def _mark(status: str, *, error_code: Optional[str] = None,
              receipt: Optional[Dict[str, Any]] = None) -> None:
        with SessionLocal() as db:
            run = db.get(DataJoinRun, run_id)
            if run is not None:
                run.status = status
                run.error_code = error_code
                if receipt is not None:
                    run.receipt = receipt
                    run.output_ref = receipt.get("output_ref")
                    run.output_sha256 = receipt.get("output_sha256")
                    run.output_rows = receipt.get("output_rows")
                if status in ("completed", "failed", "cancelled"):
                    run.finished_at = datetime.now(timezone.utc)
                db.commit()

    from app.lib.cancellation import OperationCancelled

    _mark("running")
    started = time.monotonic()
    try:
        # 1) 冻结复核：执行读的是与幂等键同代的快照（J17/J18）
        await handle.ensure_not_cancelled()
        left, left_ev = await _resolve_source_view(
            session_id, plan.left.model_dump(exclude_none=True), actor, "left")
        right, right_ev = await _resolve_source_view(
            session_id, plan.right.model_dump(exclude_none=True), actor, "right")
        if left.content_fingerprint != left_fingerprint or \
                right.content_fingerprint != right_fingerprint:
            raise DataJoinError(
                "stale_source",
                "源内容在提交与执行之间发生变化；请重新预览后再执行",
                left_fingerprint_now=left.content_fingerprint,
                right_fingerprint_now=right.content_fingerprint,
            )
        _enforce_source_budget(plan, left, right)
        await handle.progress(phase="sources_loaded", message=(
            f"左 {left.row_count()} 行 / 右 {right.row_count()} 行已冻结"))

        # 2) 内核执行（线程池执行 —— 长关联不阻塞事件循环/看门狗；
        #    取消协作 = handle.checkpoint 在批粒度抛 OperationCancelled）
        executor = JoinExecutor(plan, should_cancel=handle.checkpoint)
        if plan.mode == "attribute":
            out_rows = await asyncio.to_thread(executor.run_attribute, left, right)
        else:
            out_rows = await asyncio.to_thread(executor.run_spatial, left, right)
        stats = executor.stats
        await handle.progress(phase="joined", message=(
            f"匹配 {stats.matched_pairs} 对 / 输出 {stats.output_rows} 行"))

        # 3) 装配 + 发布（staging → oracle → 原子 → DataObject → 台账）
        output_kind = "enriched_layer" if left.has_geometry() else "table"
        if output_kind == "enriched_layer":
            fc = outputs.assemble_feature_collection(
                out_rows, left.crs, plan.output_name)
            rows_doc = None
            n_rows = len(fc["features"])
        else:
            fc = None
            rows_doc = outputs.assemble_rows(out_rows)
            n_rows = len(rows_doc["rows"])
        manifest = {
            "run_id": run_id,
            "session_id": session_id,
            "plan_digest": plan.normalized_digest(),
            "method_version": contracts.METHOD_VERSION,
            "counts": _stats_to_diagnostics(stats, plan),
            "left_fingerprint": left.content_fingerprint,
            "right_fingerprint": right.content_fingerprint,
            "sources": {"left": left_ev and _bounded(left_ev), "right": _bounded(right_ev)},
        }
        await handle.ensure_not_cancelled()
        outputs.write_staging(
            run_id, output_kind=output_kind, fc=fc, rows=rows_doc,
            manifest=manifest)
        outputs.atomic_publish(run_id)
        verified = outputs.verify_published(run_id)

        # 4) durable DataObject + session 台账（增值；失败不阻断本体）
        dob = outputs.publish_data_object_for_run(
            run_id,
            owner_scope={"session_id": session_id},
            source_refs=[left_ev.get("label"), right_ev.get("label")],
            input_fingerprint=plan.normalized_digest(),
        )
        artifact_ref = await outputs.register_session_artifact(
            session_id, run_id,
            descriptor={"title": plan.output_name, "kind": output_kind,
                        "rows": n_rows},
            metadata={"plan_digest": plan.normalized_digest()},
        )
        member_sha = next(iter(verified["members"].values()))
        receipt = {
            "run_id": run_id,
            "status": "completed",
            "output_kind": output_kind,
            # 台账登记失败时不伪造可解析 ref（评审 F12）；文件本体仍在
            # DATA_DIR/data-join/<run_id> 且经 manifest/receipt 可核验
            "output_ref": artifact_ref,
            "output_sha256": member_sha,
            "output_rows": n_rows,
            "counts": _stats_to_diagnostics(stats, plan),
            "working_crs": stats.working_crs,
            "transform_note": stats.transform_note,
            "left_version": {
                "fingerprint": left.content_fingerprint,
                "content_revision": left_ev.get("content_revision"),
                "artifact_revision_no": left_ev.get("artifact_revision_no"),
            },
            "right_version": {
                "fingerprint": right.content_fingerprint,
                "content_revision": right_ev.get("content_revision"),
                "artifact_revision_no": right_ev.get("artifact_revision_no"),
            },
            "plan_digest": plan.normalized_digest(),
            "method_version": contracts.METHOD_VERSION,
            "data_object": dob if dob.get("state") == "published" else {"state": dob.get("state")},
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        outputs.write_receipt(run_id, receipt)
        # 收据可回读后才 completed（发布因果：内容→收据→run 行）
        if outputs.read_receipt(run_id) is None:
            _mark("failed", error_code="receipt_missing")
            raise DataJoinError(
                "receipt_missing", "收据写后不可回读（run 置 failed）")
        _mark("completed", receipt=receipt)
        return {
            "run_id": run_id,
            "status": "completed",
            "output_rows": n_rows,
            "output_ref": receipt["output_ref"],
        }
    except OperationCancelled:
        # 协作取消：清 staging、run 行落 cancelled；job 终态由 runner 的
        # 两段取消迁移落定（绝不留公开半成品）
        outputs.discard_staging(run_id)
        outputs.discard_final(run_id)  # 发布后取消：终态包无收据 → 回收
        _mark("cancelled")
        raise
    except DataJoinError as exc:
        outputs.discard_staging(run_id)
        outputs.discard_final(run_id)  # 发布后失败：run 行 failed，回收终态包
        _mark("failed", error_code=exc.code)
        # 面向 job 的错误：runner 会 mark_failed；此处再抛让 job 记录原因
        raise
    except Exception:  # noqa: BLE001 — 未预期异常
        outputs.discard_staging(run_id)
        outputs.discard_final(run_id)  # 发布后失败：run 行 failed，回收终态包
        _mark("failed", error_code="internal_error")
        raise


def _bounded(ev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not ev:
        return {}
    return {
        "kind": ev.get("kind"), "label": ev.get("label"),
        "content_fingerprint": ev.get("content_fingerprint"),
        "row_count": ev.get("row_count"), "crs": ev.get("crs"),
    }


# ───────────────────────── 运行视图 / 结果 / 取消 ─────────────────────────

def _run_view(session_id: str, run_id: str) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    with SessionLocal() as db:
        run = db.get(DataJoinRun, run_id)
        if run is None or run.session_id != session_id:
            raise DataJoinError(
                "run_not_found", "运行不存在或不属于当前会话", run_id=run_id)
        return {
            "run_id": run.id,
            "plan_id": run.plan_id,
            "session_id": run.session_id,
            "plan_revision": run.plan_revision,
            "plan_digest": run.plan_digest,
            "status": run.status,
            "job_id": run.job_id,
            "error_code": run.error_code,
            "receipt": run.receipt,
            "output_ref": run.output_ref,
            "output_sha256": run.output_sha256,
            "output_rows": run.output_rows,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }


async def inspect_run(session_id: str, run_id: str) -> Dict[str, Any]:
    view = _run_view(session_id, run_id)
    if view["status"] == "completed":
        # 复用前内容核验（J28）：内容损坏 → typed 失效，不虚报完成
        try:
            outputs.verify_published(run_id)
        except DataJoinError:
            _invalidate_run(session_id, run_id, "receipt_missing")
            view = _run_view(session_id, run_id)
            view["content_invalidated"] = True
    return view


async def run_result_page(
    session_id: str, run_id: str, cursor: int, limit: int
) -> Dict[str, Any]:
    view = _run_view(session_id, run_id)
    if view["status"] != "completed":
        raise DataJoinError(
            "result_unavailable",
            f"运行状态 {view['status']} 尚无可分页结果", run_id=run_id,
            status=view["status"])
    page = outputs.result_page(run_id, cursor, limit)
    page["output_ref"] = view["output_ref"]
    return page


async def cancel_run(session_id: str, run_id: str) -> Dict[str, Any]:
    view = _run_view(session_id, run_id)
    if view["status"] in ("completed", "failed", "cancelled"):
        return {"run_id": run_id, "status": view["status"],
                "note": "already_terminal"}
    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    job_id = view.get("job_id")
    if job_id is None:
        raise DataJoinError(
            "run_not_active", "运行无关联 durable job（无法取消）", run_id=run_id)
    with SessionLocal() as db:
        changed, _status = DurableJobStore.request_cancel_sync(db, int(job_id))
        db.commit()
    return {"run_id": run_id, "job_id": job_id,
            "cancel_requested": bool(changed)}


async def list_runs(
    session_id: str, *, plan_id: Optional[str] = None, limit: int = 50
) -> Dict[str, Any]:
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    limit = max(1, min(int(limit), 100))
    with SessionLocal() as db:
        stmt = select(DataJoinRun).where(DataJoinRun.session_id == session_id)
        if plan_id:
            stmt = stmt.where(DataJoinRun.plan_id == plan_id)
        stmt = stmt.order_by(DataJoinRun.created_at.desc()).limit(limit)
        rows = list(db.scalars(stmt))
        out = []
        for r in rows:
            out.append({
                "run_id": r.id, "plan_id": r.plan_id, "status": r.status,
                "output_rows": r.output_rows, "error_code": r.error_code,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return {"runs": out, "count": len(out)}


# ───────────────────────── 幂等复用 / 失效 ─────────────────────────

def _like_prefix(base_key: str) -> str:
    """幂等键 LIKE 前缀模式：转义通配符（session/plan id 可含 _ 或 %）。"""
    escaped = (base_key.replace("\\", "\\\\")
               .replace("%", "\\%").replace("_", "\\_"))
    return f"{escaped}%"


def _find_completed_run_by_key(
    session_id: str, base_key: str
) -> Optional[Dict[str, Any]]:
    """base 键前缀下最新 **completed** run（跨世代收敛锚点）。"""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    with SessionLocal() as db:
        row = db.scalars(
            select(DataJoinRun).where(
                DataJoinRun.session_id == session_id,
                DataJoinRun.idempotency_key.like(_like_prefix(base_key), escape="\\"),
                DataJoinRun.status == "completed",
            ).order_by(DataJoinRun.created_at.desc())
        ).first()
        if row is None:
            return None
        return {
            "run_id": row.id, "status": row.status,
            "receipt": row.receipt, "output_ref": row.output_ref,
            "output_rows": row.output_rows,
        }


def _find_active_run_by_key(session_id: str, base_key: str) -> Optional[Dict[str, Any]]:
    """同 base 键（含世代后缀）的 pending/running run（活跃收敛锚点）。"""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    with SessionLocal() as db:
        row = db.scalars(
            select(DataJoinRun).where(
                DataJoinRun.session_id == session_id,
                DataJoinRun.idempotency_key.like(_like_prefix(base_key), escape="\\"),
                DataJoinRun.status.in_(["pending", "running"]),
            ).order_by(DataJoinRun.created_at.desc())
        ).first()
        if row is None:
            return None
        # 活跃判据复核（评审 F10/R3）：run 行 pending/running 但关联 job 已
        # 终态（崩溃残留）或从未回填 job（提交中途死亡）不算活跃 —— 交由
        # 恢复/新世代处理，避免永久楔死
        if row.job_id is None or not _job_is_live(row.job_id):
            return None
        return {"run_id": row.id, "job_id": row.job_id, "status": row.status}


def _job_is_live(job_id: int) -> bool:
    """job 存在且非终态（短事务；DB 瞬时不可用按 live 保守处理）。"""
    try:
        from app.core.database import SessionLocal
        from app.models.db_model import AnalysisTask

        with SessionLocal() as db:
            row = db.get(AnalysisTask, int(job_id))
            status = str(row.status) if row is not None else None
            db.commit()
        return status in ("pending", "queued", "running", "cancelling")
    except Exception:  # noqa: BLE001 — 探测失败不阻塞提交（保守视为活跃）
        return True


def _count_runs_by_key_prefix(session_id: str, base_key: str) -> int:
    from sqlalchemy import func, select

    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    with SessionLocal() as db:
        n = db.scalar(
            select(func.count(DataJoinRun.id)).where(
                DataJoinRun.session_id == session_id,
                DataJoinRun.idempotency_key.like(_like_prefix(base_key), escape="\\"),
            ))
        return int(n or 0)


def _find_run_by_job(session_id: str, job_id: int) -> Optional[str]:
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    with SessionLocal() as db:
        row = db.scalars(
            select(DataJoinRun).where(
                DataJoinRun.session_id == session_id,
                DataJoinRun.job_id == int(job_id),
            ).order_by(DataJoinRun.created_at.desc())
        ).first()
        return row.id if row is not None else None


def _invalidate_run(session_id: str, run_id: str, code: str) -> None:
    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    with SessionLocal() as db:
        run = db.get(DataJoinRun, run_id)
        if run is not None and run.session_id == session_id:
            run.status = "failed"
            run.error_code = code
            db.commit()


async def render_ref_for_run(session_id: str, run_id: str) -> Dict[str, Any]:
    """上图渲染投影（≤10k；超过 → typed render_ref_over_budget）。"""
    view = _run_view(session_id, run_id)
    if view["status"] != "completed":
        raise DataJoinError(
            "result_unavailable", "运行未完成，无渲染投影", run_id=run_id)
    return await outputs.mint_render_ref(session_id, run_id)
