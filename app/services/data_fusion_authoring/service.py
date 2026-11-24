"""Data Fusion Authoring 服务 —— API 与 Pi 工具共用的单一编排面。

职责（与 G38 service.py 同款纪律）：
- 计划 CRUD（revision CAS）、来源资格卡（WP2）、融合预览（WP2，
  只读不合并）、执行（durable job + 幂等收敛 + 冻结复核）、审阅队列
  分页、决定 upsert（CAS）、受权 commit（不可变融合数据集版本）、
  结果分页、取消、渲染投影、增量刷新（WP4）；
- 普通用户与 Pi 走同一 service/同一权限/同一收据/同一幂等键；
- 行内 JSON 只存有界投影（preview ≤8KB、receipt 有界），完整内容在
  终态包 + DataObject；
- 不新建任务状态机（analysis_tasks 事实源）、不绕过授权/确认门。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from app.services.data_fusion_authoring import (
    adjudication,
    contracts,
    executor,
    incremental,
    matching,
    outputs,
    review,
)
from app.services.data_fusion_authoring.errors import DataFusionError
from app.services.data_fusion_authoring.plan import FusionPlan, parse_plan

logger = logging.getLogger("data_fusion.service")

TASK_TYPE = "data_fusion_run"

_RUNNERS: Dict[str, asyncio.Task] = {}


def _actor(session_id: str) -> Dict[str, Any]:
    """REST 面 actor：JobOrigin 上下文（工具面由 registry 注入 trusted）。"""
    from app.services.jobs.context import current_origin

    origin = current_origin()
    return {
        "user_id": origin.owner_id if origin else None,
        "org_id": origin.org_id if origin else None,
        "owner_token": origin.owner_token if origin else None,
    }


# ───────────────────────── 计划 CRUD（CAS） ─────────────────────────

_MAX_TITLE = 200


def _plan_row_to_view(row: Any) -> Dict[str, Any]:
    return {
        "plan_id": row.id,
        "session_id": row.session_id,
        "project_id": row.project_id,
        "title": row.title,
        "status": row.status,
        "revision": row.revision,
        "plan": row.plan,
        "normalized_digest": row.normalized_digest,
        "fingerprint_a": row.fingerprint_a,
        "fingerprint_b": row.fingerprint_b,
        "preview": row.preview,
        "preview_digest": row.preview_digest,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _get_plan_row(db: Any, session_id: str, plan_id: str) -> Any:
    from app.models.data_fusion_authoring import DataFusionPlan

    row = db.get(DataFusionPlan, plan_id)
    if row is None or row.session_id != session_id:
        raise DataFusionError(
            "not_owned", f"计划不存在或不属于当前会话：{plan_id}",
            status=404)
    return row


async def create_plan(
    session_id: str, plan_data: Dict[str, Any], *,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionPlan

    actor = actor or {}
    plan = parse_plan(plan_data)
    blob = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    if len(blob.encode("utf-8")) > contracts.MAX_PLAN_BLOB_BYTES:
        raise DataFusionError(
            "invalid_plan",
            f"计划 JSON 超过 {contracts.MAX_PLAN_BLOB_BYTES} B 上限",
        )
    with SessionLocal() as db:
        row = DataFusionPlan(
            session_id=session_id,
            project_id=actor.get("project_id"),
            created_by=actor.get("user_id"),
            title=(plan.title or "")[:_MAX_TITLE],
            status="draft",
            revision=1,
            plan=plan.model_dump(mode="json"),
            normalized_digest=plan.normalized_digest(),
        )
        db.add(row)
        db.commit()
        return _plan_row_to_view(row)


async def get_plan(session_id: str, plan_id: str) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        return _plan_row_to_view(_get_plan_row(db, session_id, plan_id))


async def list_plans(
    session_id: str, *, limit: int = 50, cursor: int = 0,
    include_archived: bool = True,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionPlan

    lim = max(1, min(int(limit), 200))
    q = select(DataFusionPlan).where(
        DataFusionPlan.session_id == session_id)
    if not include_archived:
        q = q.where(DataFusionPlan.status == "draft")
    q = q.order_by(DataFusionPlan.created_at.desc(),
                   DataFusionPlan.id).offset(max(0, int(cursor))).limit(lim + 1)
    with SessionLocal() as db:
        rows = db.execute(q).scalars().all()
    has_more = len(rows) > lim
    rows = rows[:lim]
    return {
        "plans": [_plan_row_to_view(r) for r in rows],
        "cursor": (int(cursor) + lim) if has_more else None,
        "has_more": has_more,
    }


async def update_plan(
    session_id: str, plan_id: str, plan_data: Dict[str, Any], *,
    expected_revision: int, actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    actor = actor or {}
    plan = parse_plan(plan_data)
    blob = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    if len(blob.encode("utf-8")) > contracts.MAX_PLAN_BLOB_BYTES:
        raise DataFusionError(
            "invalid_plan",
            f"计划 JSON 超过 {contracts.MAX_PLAN_BLOB_BYTES} B 上限",
        )
    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise DataFusionError(
                "stale_plan_revision",
                f"计划修订冲突：expected {expected_revision}，"
                f"当前 {row.revision}",
                current_revision=row.revision,
            )
        if row.status != "draft":
            raise DataFusionError(
                "invalid_plan", "已归档计划不可修改（请先恢复或另建）")
        row.title = (plan.title or "")[:_MAX_TITLE]
        row.plan = plan.model_dump(mode="json")
        row.normalized_digest = plan.normalized_digest()
        row.revision += 1
        if actor.get("project_id") is not None:
            row.project_id = actor["project_id"]
        if actor.get("user_id") is not None:
            row.created_by = actor["user_id"]
        # 计划变化 → 预览失效（digest 锚）
        row.preview = None
        row.preview_digest = None
        db.commit()
        return _plan_row_to_view(row)


async def archive_plan(
    session_id: str, plan_id: str, *, expected_revision: int,
    archived: bool = True,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise DataFusionError(
                "stale_plan_revision",
                f"计划修订冲突：expected {expected_revision}，"
                f"当前 {row.revision}",
                current_revision=row.revision,
            )
        row.status = "archived" if archived else "draft"
        row.revision += 1
        db.commit()
        return _plan_row_to_view(row)


# ───────────────────────── 来源资格卡（WP2） ─────────────────────────

async def describe_sources(
    session_id: str, sources: List[Dict[str, Any]], actor: Dict[str, Any]
) -> Dict[str, Any]:
    """候选来源资格卡（只读；区分 advertised 与 verified）。

    每卡给：解析三态（ok/unresolvable）、行数、CRS 与分类、几何形态、
    声明字段可用性、时间字段可解析率（有界样本）、许可/署名声明。
    绝不自动联网抓取；不可验证项如实标 unknown，不伪造 verified。
    """
    cards: List[Dict[str, Any]] = []
    for src in sources[:contracts.SOURCES_PER_PLAN]:
        side = str(src.get("side") or "a")
        try:
            spec_probe = _probe_spec(side, src)
            view, evidence = await executor.resolve_view(
                session_id, spec_probe, actor, side)
            cards.append(_eligibility_card(side, spec_probe, view, evidence))
        except DataFusionError as exc:
            cards.append({
                "side": side, "state": "unresolvable",
                "error_code": exc.code, "message": exc.message,
                "correction_hint": exc.correction_hint,
            })
    return {"session_id": session_id, "sources": cards}


def _probe_spec(side: str, src: Dict[str, Any]) -> FusionPlan:
    """资格卡探测计划（只为复用 resolve_view 的授权/装载路径）。"""
    from app.services.data_fusion_authoring.plan import (
        FieldMapping,
        FusionSourceSpec,
    )

    spec = FusionSourceSpec.model_validate({
        "kind": src.get("kind") or "session_ref",
        "session_id": src.get("session_id"),
        "ref": src.get("ref"),
        "project_id": src.get("project_id"),
        "artifact_id": src.get("artifact_id"),
        "revision_no": src.get("revision_no"),
        "table_ref": src.get("table_ref"),
        "label": src.get("label") or side,
        "priority": 1 if side == "a" else 2,
        "priority_reason": "资格卡探测（非融合信任声明）",
        "external_id_field": src.get("external_id_field"),
        "id_space": src.get("id_space"),
        "name_field": src.get("name_field") or "name",
        "address_field": src.get("address_field"),
        "category_field": src.get("category_field"),
        "category_map": src.get("category_map") or {},
        "temporal_field": src.get("temporal_field"),
        "field_mappings": [
            FieldMapping(source_field=f["source_field"],
                         output_field=f.get("output_field") or f["source_field"],
                         rule="trust_priority")
            for f in (src.get("field_mappings") or [])
        ],
    })
    # 单源探测计划（不执行匹配；strategy 仅作装载参数）
    a_dump = spec.model_dump(mode="json") | {
        "priority": 1, "priority_reason": "资格卡探测（非融合信任声明）"}
    b_dump = spec.model_dump(mode="json") | {
        "priority": 2, "priority_reason": "资格卡探测（非融合信任声明）"}
    return FusionPlan.model_validate({
        "entity_kind": src.get("entity_kind") or "point_poi",
        "title": "eligibility-probe",
        "source_a": a_dump,
        "source_b": b_dump,
        "strategy": {"weights": {
            # 资格卡不评分：category/id 恒 0（计划校验要求正权重必须
            # 配齐可算字段 —— 探测面不假装能算）
            "external_id": 0.0, "name": 0.55, "address": 0.15,
            "category": 0.0, "distance": 0.30},
            "name_normalizers": ["trim", "casefold", "collapse_spaces"]},
    })


def _eligibility_card(
    side: str, spec_probe: FusionPlan, view: Any, evidence: Dict[str, Any]
) -> Dict[str, Any]:
    spec = spec_probe.source_a if side == "a" else spec_probe.source_b
    declared = set(view.declared_fields)
    field_report = {}
    for key in ("name_field", "external_id_field", "address_field",
                "category_field", "temporal_field"):
        f = getattr(spec, key)
        if f:
            field_report[key] = {
                "field": f, "present": f in declared,
            }
    # 时间可解析率（有界样本 ≤50；样本≠总体，如实标注）
    temporal_parse = None
    if spec.temporal_field and spec.temporal_field in declared:
        from app.services.data_fusion_authoring import textsim

        sample = [r.get(spec.temporal_field) for r in view.rows[:50]]
        parsed = sum(1 for v in sample
                     if v is not None and textsim.parse_temporal(v) is not None)
        temporal_parse = {
            "sample_n": len(sample), "parsed": parsed,
            "note": "前 50 行样本；非总体统计",
        }
    geom_state = "none"
    if view.geoms and any(g is not None for g in view.geoms):
        kinds = {getattr(g, "geom_type", "?") for g in view.geoms
                 if g is not None}
        geom_state = ",".join(sorted(kinds))
    return {
        "side": side,
        "state": "ok",
        "adapter_verified": True,
        "label": view.label,
        "row_count": view.row_count(),
        "crs": view.crs,
        "crs_class": evidence.get("crs_class"),
        "geometry": geom_state,
        "invalid_geometry_excluded": view.excluded_invalid_geometry,
        "fields": field_report,
        "declared_field_names": view.declared_fields[:60],
        "temporal_parse": temporal_parse,
        "content_fingerprint": view.content_fingerprint,
        "content_revision": view.content_revision,
        "license_declared": spec.license,
        "attribution_declared": spec.attribution,
        "note": "资格卡为装载时验证事实；来源自我声明（advertised）"
                "不等于本域可验证（verified）项以本卡为准",
    }


# ───────────────────────── 融合预览（WP2） ─────────────────────────

async def preview_plan(
    session_id: str, plan_id: str, *, expected_revision: int,
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    """融合预览（只读；不建成果、不改源、不偷偷合并）。

    给出：双源资格、覆盖对比、字段映射可用性、预算估算、冲突/重复
    候选比例估计（真实匹配运行的有界投影）与样本。结果缓存按
    digest 失效（计划或源变化 → 旧预览失效）。
    """

    from app.core.database import SessionLocal

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise DataFusionError(
                "stale_plan_revision",
                f"计划修订冲突：expected {expected_revision}，"
                f"当前 {row.revision}",
                current_revision=row.revision,
            )
        if row.preview and row.preview_digest == row.normalized_digest:
            return {"cached": True, **row.preview}
        plan = parse_plan(row.plan)
        plan_revision = row.revision

    src_a, card_a = await executor.resolve_view(
        session_id, plan, actor, "a")
    src_b, card_b = await executor.resolve_view(
        session_id, plan, actor, "b")
    executor.enforce_budgets(plan, src_a, src_b)
    ctx = executor.build_spatial_context(src_a, src_b, plan)

    result = matching.match_sources(
        src_a.rows, src_a.geoms, src_b.rows, src_b.geoms, plan, ctx)
    st = result.stats

    preview_payload = {
        "plan_digest": plan.normalized_digest(),
        "preview_digest": plan.normalized_digest(),
        "sources": [card_a, card_b],
        "match_stats": st,
        "budgets": plan.effective_budgets(),
        "samples": _preview_samples(plan, src_a, src_b, result, ctx),
        "warnings": _preview_warnings(plan, src_a, src_b),
    }
    blob = json.dumps(preview_payload, ensure_ascii=False, default=str)
    if len(blob.encode("utf-8")) > contracts.MAX_PREVIEW_BLOB_BYTES:
        # 有界投影：样本裁剪（绝不静默截断后宣称完整 —— 显式记录）
        preview_payload["samples"] = _preview_samples(
            plan, src_a, src_b, result, ctx, sample_limit=10)
        preview_payload["truncated_for_bound"] = True
        blob = json.dumps(preview_payload, ensure_ascii=False, default=str)

    from app.core.database import SessionLocal

    with SessionLocal() as db:
        prow = _get_plan_row(db, session_id, plan_id)
        if prow.revision != plan_revision:
            raise DataFusionError(
                "stale_plan_revision", "计划在预览期间被修改；请重试",
                current_revision=prow.revision)
        prow.preview = preview_payload
        prow.preview_digest = plan.normalized_digest()
        prow.fingerprint_a = src_a.content_fingerprint
        prow.fingerprint_b = src_b.content_fingerprint
        db.commit()
    return {"cached": False, **preview_payload}


def _preview_samples(
    plan: FusionPlan, src_a: Any, src_b: Any, result: matching.MatchResult,
    ctx: matching.SpatialContext, sample_limit: int = 20,
) -> Dict[str, Any]:
    """预览样本（有界；read-only）。"""
    recs_a, recs_b = matching.build_records(
        src_a.rows, src_a.geoms, plan.source_a, plan), \
        matching.build_records(src_b.rows, src_b.geoms, plan.source_b, plan)

    def _brief(rec: Any, spec: Any) -> Dict[str, Any]:
        return {
            "index": rec.index,
            "name": (rec.row.get(spec.name_field) or "")[:80],
            "external_id": rec.ext_id,
        }

    matched_sample = []
    for p in result.pairs:
        if p.status == "matched" and len(matched_sample) < sample_limit:
            matched_sample.append({
                "idx_a": p.idx_a, "idx_b": p.idx_b, "score": p.score,
                "name_a": _brief(recs_a[p.idx_a], plan.source_a)["name"],
                "name_b": _brief(recs_b[p.idx_b], plan.source_b)["name"],
            })
    review_sample = []
    for p in result.pairs:
        if p.status in ("needs_review", "ambiguous") and \
                len(review_sample) < sample_limit:
            review_sample.append({
                "idx_a": p.idx_a, "idx_b": p.idx_b, "status": p.status,
                "score": p.score,
                "name_a": _brief(recs_a[p.idx_a], plan.source_a)["name"],
                "name_b": _brief(recs_b[p.idx_b], plan.source_b)["name"],
                "field_conflicts": dict(p.field_conflicts),
            })
    return {
        "matched": matched_sample,
        "review": review_sample,
        "unmatched_a_first": result.unmatched_a[:sample_limit],
        "unmatched_b_first": result.unmatched_b[:sample_limit],
        "sample_limit": sample_limit,
    }


def _preview_warnings(
    plan: FusionPlan, src_a: Any, src_b: Any,
) -> List[Dict[str, str]]:
    warns: List[Dict[str, str]] = []
    if src_a.crs != src_b.crs:
        warns.append({
            "item": "crs_differs",
            "message": f"两侧 CRS 声明不同（a={src_a.crs}，b={src_b.crs}）"
                       "——已按可比帧校验通过；逐对距离在统一度量下计算",
        })
    if plan.source_a.license is None or plan.source_b.license is None:
        warns.append({
            "item": "license_undeclared",
            "message": "存在未声明许可/署名的来源：交付时须按既有交付链"
                       "补齐（G18），本域不代填",
        })
    for side, view in (("a", src_a), ("b", src_b)):
        if view.excluded_invalid_geometry:
            warns.append({
                "item": f"invalid_geometry_{side}",
                "message": f"source_{side} 有 "
                           f"{view.excluded_invalid_geometry} 个非法几何被"
                           "排除并披露（政策=exclude_count）",
            })
    return warns


# ───────────────────────── 执行（durable job） ─────────────────────────

def _build_idempotency_key(
    session_id: str, plan_id: str, plan_digest: str,
    fp_a: str, fp_b: str,
) -> str:
    return (f"{TASK_TYPE}:{session_id}:{plan_id}:{plan_digest}:"
            f"{fp_a[:48]}:{fp_b[:48]}")


async def execute_plan(
    session_id: str, plan_id: str, *, expected_revision: int,
    actor: Dict[str, Any], background: bool = True,
    base_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行：源复核 → 幂等建 job → run 行 → 后台/内联 runner。

    ``base_run_id``：增量刷新基底（触发影响面报告；首跑 None）。
    返回 run 视图（含 job_id/poll）；内联完成时直接带 receipt。
    """
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise DataFusionError(
                "stale_plan_revision",
                f"计划修订冲突：expected {expected_revision}，"
                f"当前 {row.revision}",
                current_revision=row.revision,
            )
        plan = parse_plan(row.plan)
        plan_revision = row.revision

    src_a, _ = await executor.resolve_view(session_id, plan, actor, "a")
    src_b, _ = await executor.resolve_view(session_id, plan, actor, "b")
    executor.enforce_budgets(plan, src_a, src_b)
    # 资格闸前置（CRS/几何形态）—— 提交前 typed 失败，不留孤儿 job
    executor.build_spatial_context(src_a, src_b, plan)

    plan_digest = plan.normalized_digest()
    fp_a, fp_b = src_a.content_fingerprint, src_b.content_fingerprint
    base_key = _build_idempotency_key(
        session_id, plan_id, plan_digest, fp_a, fp_b)

    existing = _find_completed_run_by_key(session_id, base_key)
    if existing is not None and not base_run_id:
        # 两段式复用：已 commit → 内容可核验才复用（J28 同款）；
        # 仅匹配完成 → 匹配包可读即复用（commit 是显式后续步骤）
        if existing.get("output_ref"):
            try:
                outputs.verify_published(existing["run_id"])
                return {**existing, "idempotent_reuse": True}
            except (FileNotFoundError, ValueError):
                _invalidate_run(session_id, existing["run_id"],
                                "receipt_missing")
        elif outputs.read_match_bundle(existing["run_id"]) is not None:
            return {**existing, "idempotent_reuse": True,
                    "state": "matched_pending_commit"}
        else:
            _invalidate_run(session_id, existing["run_id"], "receipt_missing")

    active = _find_active_run_by_key(session_id, base_key)
    if active is not None:
        return {**_run_view(session_id, active["run_id"]),
                "idempotent_reuse": True,
                "job_id": active["job_id"],
                "poll": f"/api/v1/tasks/jobs/{active['job_id']}"}

    generation = _count_runs_by_key_prefix(session_id, base_key)
    idem = base_key if generation == 0 else f"{base_key}:g{generation + 1}"

    run_id = outputs.new_run_id()
    with SessionLocal() as db:
        prow = _get_plan_row(db, session_id, plan_id)
        if prow.revision != plan_revision:
            raise DataFusionError(
                "stale_plan_revision", "计划在执行提交期间被修改；请重试",
                current_revision=prow.revision,
            )
        run = DataFusionRun(
            id=run_id,
            plan_id=plan_id,
            session_id=session_id,
            project_id=prow.project_id,
            created_by=actor.get("user_id"),
            plan_revision=plan_revision,
            plan_digest=plan_digest,
            status="pending",
            idempotency_key=idem,
            base_run_id=base_run_id,
        )
        db.add(run)
        db.commit()

    from app.services.data_operations.runner import submit_async_job

    try:
        job_id, job_status, reused = submit_async_job(
            task_type=TASK_TYPE,
            kind="data_fusion",
            display_name=f"实体融合 · {(plan.title or plan.output_name)[:24]}",
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
        from app.core.database import SessionLocal
        from app.models.data_fusion_authoring import DataFusionRun

        with SessionLocal() as db:
            dead = db.get(DataFusionRun, run_id)
            if dead is not None:
                db.delete(dead)
                db.commit()
        raise
    with SessionLocal() as db:
        from app.models.data_fusion_authoring import DataFusionRun

        run = db.get(DataFusionRun, run_id)
        run.job_id = job_id
        db.commit()
    if reused and job_status in ("pending", "queued", "running", "cancelling"):
        twin = _find_run_by_job(session_id, job_id)
        if twin is not None and twin != run_id:
            with SessionLocal() as db:
                from app.models.data_fusion_authoring import DataFusionRun

                dead = db.get(DataFusionRun, run_id)
                if dead is not None:
                    db.delete(dead)
                    db.commit()
            return {**_run_view(session_id, twin), "idempotent_reuse": True,
                    "job_id": job_id,
                    "poll": f"/api/v1/tasks/jobs/{job_id}"}

    if background:
        runner_task = asyncio.get_running_loop().create_task(
            _run_execution_job(run_id, job_id, session_id, plan, actor,
                               fp_a=fp_a, fp_b=fp_b),
            name=f"data-fusion:{run_id}",
        )
        _RUNNERS[run_id] = runner_task
        runner_task.add_done_callback(lambda t: _RUNNERS.pop(run_id, None))
        return _run_view(session_id, run_id) | {
            "job_id": job_id, "poll": f"/api/v1/tasks/jobs/{job_id}",
        }
    await _run_execution_job(run_id, job_id, session_id, plan, actor,
                             fp_a=fp_a, fp_b=fp_b)
    return _run_view(session_id, run_id)


def _entity_brief(rec: Any, spec: Any) -> Dict[str, Any]:
    """审阅面板侧名投影（有界；名称 ≤80 字符 + id）。"""
    name = rec.row.get(spec.name_field)
    return {
        "name": ("" if name is None else str(name))[:80],
        "external_id": rec.ext_id,
    }


async def _run_execution_job(
    run_id: str, job_id: int, session_id: str, plan: FusionPlan,
    actor: Dict[str, Any], *, fp_a: str, fp_b: str,
) -> None:
    from app.services.data_operations.runner import run_async_job

    async def body(handle: Any) -> Dict[str, Any]:
        return await _execute_body(handle, run_id, session_id, plan, actor,
                                   fp_a=fp_a, fp_b=fp_b)

    await run_async_job(job_id, body)


async def _execute_body(
    handle: Any, run_id: str, session_id: str, plan: FusionPlan,
    actor: Dict[str, Any], *, fp_a: str, fp_b: str,
) -> Dict[str, Any]:
    """执行体：冻结复核 → 装载 → 匹配 → 匹配包冻结 → run 终态。"""
    from datetime import datetime, timezone

    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    def _mark(status: str, *, error_code: Optional[str] = None,
              receipt: Optional[Dict[str, Any]] = None) -> None:
        with SessionLocal() as db:
            run = db.get(DataFusionRun, run_id)
            if run is not None:
                run.status = status
                run.error_code = error_code
                if receipt is not None:
                    run.receipt = receipt
                if status in ("completed", "failed", "cancelled"):
                    run.finished_at = datetime.now(timezone.utc)
                db.commit()

    _mark("running")
    started = time.monotonic()
    try:
        await handle.ensure_not_cancelled()
        src_a, card_a = await executor.resolve_view(session_id, plan, actor, "a")
        src_b, card_b = await executor.resolve_view(session_id, plan, actor, "b")
        if src_a.content_fingerprint != fp_a or \
                src_b.content_fingerprint != fp_b:
            raise DataFusionError(
                "stale_source",
                "源内容在提交与执行之间发生变化；请重新预览后再执行",
                fingerprint_a_now=src_a.content_fingerprint,
                fingerprint_b_now=src_b.content_fingerprint,
            )
        executor.enforce_budgets(plan, src_a, src_b)
        ctx = executor.build_spatial_context(src_a, src_b, plan)
        await handle.progress(phase="sources_loaded", message=(
            f"A {src_a.row_count()} 行 / B {src_b.row_count()} 行已冻结"))
        result = await asyncio.to_thread(
            matching.match_sources, src_a.rows, src_a.geoms,
            src_b.rows, src_b.geoms, plan, ctx)
        # deadline 强制（合同承诺「执行前估算、执行中强制」—— 此前
        # 只有承诺无闸，审查 P3）
        elapsed_now = time.monotonic() - started
        if elapsed_now > plan.effective_budgets()["deadline_s"]:
            raise DataFusionError(
                "budget_exceeded",
                f"执行耗时 {elapsed_now:.0f}s 超过计划 deadline "
                f"{plan.effective_budgets()['deadline_s']}s（已终止，"
                "不冒充完成）",
                elapsed_s=round(elapsed_now, 1),
                cap=plan.effective_budgets()["deadline_s"],
            )
        await handle.progress(phase="matched", message=(
            f"自动匹配 {result.stats['pairs_matched']} 对 / "
            f"审阅 {result.stats['pairs_needs_review'] + result.stats['pairs_ambiguous']} 对"))
        recs_a, recs_b = matching.build_records(
            src_a.rows, src_a.geoms, plan.source_a, plan), \
            matching.build_records(src_b.rows, src_b.geoms, plan.source_b, plan)
        pair_dicts: List[Dict[str, Any]] = []
        for p in result.pairs:
            d = p.to_dict()
            # pair 稳定键在包写入时冻结（内容指纹；跨刷新/审阅挂靠锚）
            d["pair_key"] = adjudication.pair_stable_key(
                recs_a[p.idx_a], recs_b[p.idx_b], plan,
                src_a.source_ref, src_b.source_ref)
            pair_dicts.append(d)
        # 双侧完整实体键集（增量刷新 diff 的诚实锚 —— 不只 matched 对）
        from app.services.data_fusion_authoring import incremental

        side_keys = {
            "a": sorted(incremental.fingerprint_records(
                recs_a, plan, src_a.source_ref, "a")),
            "b": sorted(incremental.fingerprint_records(
                recs_b, plan, src_b.source_ref, "b")),
        }
        # 审阅面板侧名投影（有界：名称 ≤80、id 原样短串）
        side_briefs = {
            "a": {str(r.index): _entity_brief(r, plan.source_a)
                  for r in recs_a},
            "b": {str(r.index): _entity_brief(r, plan.source_b)
                  for r in recs_b},
        }
        outputs.write_match_bundle(
            run_id,
            pairs=pair_dicts,
            match_stats=dict(result.stats),
            source_cards=[card_a, card_b],
            plan_snapshot=plan.model_dump(mode="json"),
            side_keys=side_keys,
            side_briefs=side_briefs,
        )
        elapsed = time.monotonic() - started
        receipt = {
            "run_id": run_id,
            "match_stats": dict(result.stats),
            "unmatched_a": len(result.unmatched_a),
            "unmatched_b": len(result.unmatched_b),
            "fingerprint_a": fp_a,
            "fingerprint_b": fp_b,
            "elapsed_s": round(elapsed, 3),
            "state": "matched_pending_commit",
        }
        _mark("completed", receipt=receipt)
        return receipt
    except Exception as exc:  # noqa: BLE001 — typed 失败落行，runner 落 job 终态
        from app.lib.cancellation import OperationCancelled

        if isinstance(exc, OperationCancelled):
            outputs.discard_staging(run_id)
            _mark("cancelled")
            raise
        if isinstance(exc, DataFusionError):
            _mark("failed", error_code=exc.code)
        else:
            logger.exception("[data-fusion] run %s internal error", run_id)
            _mark("failed", error_code="internal_error")
        raise


# ───────────────────────── run 视图/查询 ─────────────────────────

def _run_view(session_id: str, run_id: str) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    with SessionLocal() as db:
        run = db.get(DataFusionRun, run_id)
        if run is None or run.session_id != session_id:
            raise DataFusionError(
                "run_not_found", f"运行不存在：{run_id}", status=404)
        return {
            "run_id": run.id,
            "plan_id": run.plan_id,
            "session_id": run.session_id,
            "status": run.status,
            "job_id": run.job_id,
            "plan_revision": run.plan_revision,
            "plan_digest": run.plan_digest,
            "base_run_id": run.base_run_id,
            "receipt": run.receipt,
            "error_code": run.error_code,
            "output_ref": run.output_ref,
            "output_sha256": run.output_sha256,
            "output_features": run.output_features,
            "commit_receipt": run.commit_receipt,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "finished_at": (run.finished_at.isoformat()
                            if run.finished_at else None),
        }


async def inspect_run(session_id: str, run_id: str) -> Dict[str, Any]:
    return _run_view(session_id, run_id)


async def run_result_page(
    session_id: str, run_id: str, *, cursor: int = 0, limit: int = 50,
) -> Dict[str, Any]:
    view = _run_view(session_id, run_id)
    if not view["output_ref"]:
        raise DataFusionError(
            "result_unavailable",
            "融合成果尚未 commit 发布（先完成审阅并提交）", status=409)
    lim = max(1, min(int(limit), contracts.RESULT_PAGE_MAX_ROWS))
    page = outputs.result_page(run_id, int(cursor), lim)
    return {"run_id": run_id, **page}


async def cancel_run(session_id: str, run_id: str) -> Dict[str, Any]:
    """取消（两段式：job 取消请求 + runner 协作收敛；不留假完成）。"""
    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    view = _run_view(session_id, run_id)
    if view["status"] in ("completed", "failed", "cancelled"):
        return {"run_id": run_id, "status": view["status"],
                "note": "already_terminal"}
    job_id = view.get("job_id")
    if job_id is None:
        raise DataFusionError(
            "run_not_active", "运行无关联 durable job（无法取消）",
            run_id=run_id)
    with SessionLocal() as db:
        changed, _status = DurableJobStore.request_cancel_sync(db, int(job_id))
        db.commit()
    return {"run_id": run_id, "job_id": job_id,
            "cancel_requested": bool(changed)}


async def list_runs(
    session_id: str, plan_id: str, *, limit: int = 50,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    lim = max(1, min(int(limit), 200))
    with SessionLocal() as db:
        rows = db.execute(
            select(DataFusionRun)
            .where(DataFusionRun.session_id == session_id,
                   DataFusionRun.plan_id == plan_id)
            .order_by(DataFusionRun.created_at.desc(), DataFusionRun.id)
            .limit(lim)
        ).scalars().all()
    return {"runs": [_run_view(session_id, r.id) for r in rows]}


def _find_completed_run_by_key(
    session_id: str, base_key: str
) -> Optional[Dict[str, Any]]:
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    with SessionLocal() as db:
        rows = db.execute(
            select(DataFusionRun)
            .where(DataFusionRun.session_id == session_id,
                   DataFusionRun.status == "completed")
            .order_by(DataFusionRun.created_at.desc())
        ).scalars().all()
    for r in rows:
        if (r.idempotency_key or "").startswith(base_key) and r.receipt:
            return _run_view(session_id, r.id)
    return None


def _find_active_run_by_key(
    session_id: str, base_key: str
) -> Optional[Dict[str, Any]]:
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    with SessionLocal() as db:
        rows = db.execute(
            select(DataFusionRun)
            .where(DataFusionRun.session_id == session_id,
                   DataFusionRun.status.in_(("pending", "running")))
            .order_by(DataFusionRun.created_at.desc())
        ).scalars().all()
    for r in rows:
        if (r.idempotency_key or "").startswith(base_key):
            return _run_view(session_id, r.id)
    return None


def _count_runs_by_key_prefix(session_id: str, base_key: str) -> int:
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    with SessionLocal() as db:
        rows = db.execute(
            select(DataFusionRun).where(
                DataFusionRun.session_id == session_id)
        ).scalars().all()
    return sum(1 for r in rows
               if (r.idempotency_key or "").startswith(base_key))


def _find_run_by_job(session_id: str, job_id: int) -> Optional[str]:
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    with SessionLocal() as db:
        rows = db.execute(
            select(DataFusionRun).where(
                DataFusionRun.session_id == session_id,
                DataFusionRun.job_id == int(job_id),
            )
        ).scalars().all()
    return rows[0].id if rows else None


def _invalidate_run(session_id: str, run_id: str, code: str) -> None:
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    with SessionLocal() as db:
        run = db.get(DataFusionRun, run_id)
        if run is not None and run.status == "completed":
            run.status = "failed"
            run.error_code = code
            db.commit()


# ───────────────────────── 审阅队列与决定 ─────────────────────────

def review_queue(
    session_id: str, run_id: str, *, cursor: int = 0, limit: int = 50,
    status_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """审阅队列分页（needs_review/ambiguous + 未决字段冲突的对）。

    每项带两侧字段投影（有界）、证据摘要、既有决定（如有）。
    """
    bundle = outputs.read_match_bundle(run_id)
    if bundle is None:
        raise DataFusionError(
            "result_unavailable", "运行匹配包不可读（未完成或已失效）",
            status=409)
    view = _run_view(session_id, run_id)
    items: List[Dict[str, Any]] = []
    decisions = review.load_decisions_by_key(view["plan_id"])
    for p in bundle["pairs"]:
        if p["score"] is None:
            continue
        queue_worthy = p["status"] in ("needs_review", "ambiguous") or \
            any(v == "conflict" for v in (p.get("field_conflicts") or {}).values())
        if status_filter and p["status"] != status_filter:
            continue
        if not queue_worthy:
            continue
        pair_key = p.get("pair_key")
        if not pair_key:
            raise DataFusionError(
                "receipt_missing",
                "匹配包缺少 pair 稳定键（旧包格式）——请重新执行",
                status=410)
        briefs = bundle.get("side_briefs") or {}
        brief_a = (briefs.get("a") or {}).get(str(p["idx_a"])) or {}
        brief_b = (briefs.get("b") or {}).get(str(p["idx_b"])) or {}
        items.append({
            "pair_key": pair_key,
            "idx_a": p["idx_a"], "idx_b": p["idx_b"],
            "status": p["status"], "score": p["score"],
            "hard_block": p.get("hard_block"),
            "id_pinned": p.get("id_pinned", False),
            "name_a": brief_a.get("name", ""),
            "name_b": brief_b.get("name", ""),
            "external_id_a": brief_a.get("external_id"),
            "external_id_b": brief_b.get("external_id"),
            "evidence": p.get("evidence"),
            "renormalized": p.get("renormalized", False),
            "geometry_evidence": p.get("geometry_evidence"),
            "field_conflicts": p.get("field_conflicts"),
            "decision": decisions.get(pair_key),
        })
    lim = max(1, min(int(limit), contracts.REVIEW_PAGE_MAX_ITEMS))
    start = max(0, int(cursor))
    page = items[start:start + lim]
    return {
        "run_id": run_id,
        "total": len(items),
        "cursor": start + len(page),
        "has_more": start + len(page) < len(items),
        "items": page,
    }


def _load_plan_snapshot(plan_id: str) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionPlan

    with SessionLocal() as db:
        row = db.get(DataFusionPlan, plan_id)
        return (row.plan if row else {}) or {}


async def upsert_decision(
    session_id: str, plan_id: str, *, pair_key: str, action: str,
    decision_run_id: Optional[str] = None, idx_a: Optional[int] = None,
    idx_b: Optional[int] = None, field_values: Optional[Dict[str, Any]] = None,
    geometry_side: Optional[str] = None, reviewer: Optional[str] = None,
    note: Optional[str] = None, expected_revision: int = 0,
) -> Dict[str, Any]:
    """决定 upsert（CAS；审阅冲突 typed 拒绝，不覆盖他人）。"""
    return review.upsert_decision(
        session_id, plan_id, pair_key=pair_key, action=action,
        decision_run_id=decision_run_id, idx_a=idx_a, idx_b=idx_b,
        field_values=field_values, geometry_side=geometry_side,
        reviewer=reviewer, note=note, expected_revision=expected_revision)


async def delete_decision(
    session_id: str, plan_id: str, pair_key: str, *,
    expected_revision: int,
) -> Dict[str, Any]:
    return review.delete_decision(
        session_id, plan_id, pair_key, expected_revision=expected_revision)


async def list_decisions(
    session_id: str, plan_id: str, *, limit: int = 200, cursor: int = 0,
) -> Dict[str, Any]:
    out = review.list_decisions(session_id, plan_id, limit=limit,
                                cursor=cursor)
    out["plan_id"] = plan_id
    return out


# ───────────────────────── commit（受权发布） ─────────────────────────

async def commit_run(
    session_id: str, run_id: str, *, actor: Dict[str, Any],
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """``project_id``：调用方声明的成果归属项目（入 DataObject
    owner_scope 与 plan.project_id —— 之前被接受但未使用，审查 P3）。"""
    """受权提交：匹配包 + 决定 → 不可变融合数据集版本。

    门（顺序）：
    1. run 终态 completed 且未 commit（幂等：已 commit 且内容可核验 →
       直接复用；内容损坏 → typed receipt_missing）；
    2. 源复核：当前快照指纹 == run 冻结指纹（漂移 → stale_source，
       需重新执行；决定按 pair_key 自动继承到新 run）；
    3. 提交政策：require_resolved 且存在未决审阅项 → commit_blocked
       （typed 披露计数）；
    4. 组装 → staging → 原子发布 → FC 哈希 → DataObject → session
       台账（ref:data-fusion/<run>）→ run.output_ref。
    """
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    view = _run_view(session_id, run_id)
    if view["status"] != "completed":
        raise DataFusionError(
            "result_unavailable",
            f"运行未完成（{view['status']}），不可提交", status=409)
    if view["output_ref"]:
        try:
            outputs.verify_published(run_id)
            return {**view, "idempotent_reuse": True}
        except (FileNotFoundError, ValueError):
            _invalidate_run(session_id, run_id, "receipt_missing")
            raise DataFusionError(
                "receipt_missing", "已发布内容不可核验（缺失或哈希不符）",
                status=410)
    bundle = outputs.read_match_bundle(run_id)
    if bundle is None:
        raise DataFusionError(
            "receipt_missing", "匹配包缺失（终态但内容不可读）", status=410)
    plan = parse_plan(view_snapshot_plan(view))
    # 计划漂移闸：run 冻结的 digest 与当前计划必须一致 —— 否则按新阈值
    # 重跑匹配会绕过既有审阅门（未审的新 auto-match 直接 merged）且
    # lineage 失真。改计划 → 必须重新 execute（决定按内容键继承）。
    if plan.normalized_digest() != view["plan_digest"]:
        raise DataFusionError(
            "stale_plan_revision",
            "计划在 run 完成后被修改（digest 不一致）：commit 将按当前计划"
            "重跑匹配、绕过既有审阅门 —— 请重新 execute 后再 commit"
            "（人工决定按内容键自动继承）",
            run_plan_digest=view["plan_digest"],
            current_plan_digest=plan.normalized_digest(),
        )
    fp = (view.get("receipt") or {})
    fp_a = fp.get("fingerprint_a")
    fp_b = fp.get("fingerprint_b")
    if not fp_a or not fp_b:
        raise DataFusionError(
            "receipt_missing", "收据缺少冻结源指纹", status=410)

    src_a, _ = await executor.resolve_view(session_id, plan, actor, "a")
    src_b, _ = await executor.resolve_view(session_id, plan, actor, "b")
    if src_a.content_fingerprint != fp_a or \
            src_b.content_fingerprint != fp_b:
        raise DataFusionError(
            "stale_source",
            "源内容在 run 与 commit 之间发生变化：请重新执行"
            "（人工决定将按内容键自动继承）",
            fingerprint_a_now=src_a.content_fingerprint,
            fingerprint_b_now=src_b.content_fingerprint,
        )

    decisions = review.load_decisions_by_key(view["plan_id"])
    ctx = executor.build_spatial_context(src_a, src_b, plan)
    result = await asyncio.to_thread(
        matching.match_sources, src_a.rows, src_a.geoms,
        src_b.rows, src_b.geoms, plan, ctx)

    # 决定按内容键挂靠到当前匹配（跨 index 稳定）
    recs_a, recs_b = matching.build_records(
        src_a.rows, src_a.geoms, plan.source_a, plan), \
        matching.build_records(src_b.rows, src_b.geoms, plan.source_b, plan)
    key_by_pair: Dict[Tuple[int, int], str] = {}
    for pair in result.pairs:
        rec_a, rec_b = recs_a[pair.idx_a], recs_b[pair.idx_b]
        key = adjudication.pair_stable_key(
            rec_a, rec_b, plan, src_a.source_ref, src_b.source_ref)
        key_by_pair[(pair.idx_a, pair.idx_b)] = key
    # decisions_by_key 只保留能挂靠到当前匹配的决定（orphan 计数披露）

    decisions_by_key = {
        k: v for k, v in decisions.items() if k in set(key_by_pair.values())}
    orphan = sorted(set(decisions) - set(key_by_pair.values()))
    # 未决统计（require_resolved 门）
    outstanding = 0
    for pair in result.pairs:
        if pair.status in ("needs_review", "ambiguous"):
            key = key_by_pair[(pair.idx_a, pair.idx_b)]
            if key not in decisions_by_key:
                outstanding += 1

    if plan.commit_policy == "require_resolved" and outstanding > 0:
        raise DataFusionError(
            "commit_blocked",
            f"存在 {outstanding} 个未决审阅项（政策=require_resolved）："
            "完成审阅后提交，或改为 allow_unresolved",
            outstanding=outstanding,
        )

    fc, quality, receipt = await asyncio.to_thread(
        executor.assemble_commit, plan, src_a, src_b, result,
        decisions_by_key, run_id)
    receipt["orphan_decisions"] = len(orphan)
    lineage = {
        "run_id": run_id,
        "plan_id": view["plan_id"],
        "plan_digest": view["plan_digest"],
        "plan_revision": view["plan_revision"],
        "source_a": {
            "ref": plan.source_a.ref, "kind": plan.source_a.kind,
            "fingerprint": fp_a, "license": plan.source_a.license,
            "attribution": plan.source_a.attribution,
        },
        "source_b": {
            "ref": plan.source_b.ref, "kind": plan.source_b.kind,
            "fingerprint": fp_b, "license": plan.source_b.license,
            "attribution": plan.source_b.attribution,
        },
        "match_stats": dict(result.stats),
        "decisions_applied": len(decisions_by_key),
        "decisions_orphan": len(orphan),
        "method_version": contracts.METHOD_VERSION,
    }
    # 先写内容文件再哈希「文件字节」—— 收据哈希与 verify_published 同源
    #（内存序列化与落盘序列化不同，绝不用两套口径）
    fc_sha = outputs.write_commit_staging(
        run_id, feature_collection=fc, quality_report=quality,
        lineage=lineage)
    receipt["fc_sha256"] = fc_sha
    outputs.write_commit_receipt(run_id, receipt)
    publish_state = outputs.atomic_publish(run_id)  # published | claimed

    owner_scope = {"session_id": session_id}
    if project_id:
        owner_scope = {"project_id": project_id}
    elif plan.source_a.project_id:
        owner_scope["project_id"] = plan.source_a.project_id
    do_publish = outputs.publish_data_object_for_run(
        run_id, owner_scope=owner_scope,
        source_refs=[plan.source_a.ref, plan.source_b.ref],
        input_fingerprint=fp_a[:32] + ":" + fp_b[:32])
    if publish_state == "claimed":
        # 崩溃窗口恢复：认领已发布内容（重试不再 500）—— 收据如实记录
        receipt["publish_claimed"] = True
    descriptor = {
        "alias": plan.output_name or "融合成果",
        "title": plan.title or "融合成果",
        "kind": "data_fusion",
        "crs": fc.get("crs"),
        "feature_count": receipt["total_features"],
        "entity_kind": plan.entity_kind,
        "quality_summary": {
            "merged": receipt["counts"]["merged"],
            "withheld_review": receipt["counts"]["withheld_review"],
            "unresolved_field_count": receipt["unresolved_field_count"],
        },
    }
    ref = await outputs.register_session_artifact(
        session_id, run_id, descriptor, lineage)
    with SessionLocal() as db:
        run = db.get(DataFusionRun, run_id)
        run.output_ref = ref or f"ref:data-fusion/{run_id}"
        run.output_sha256 = fc_sha
        run.output_features = receipt["total_features"]
        run.commit_receipt = {
            "counts": receipt["counts"],
            "total_features": receipt["total_features"],
            "unresolved_field_count": receipt["unresolved_field_count"],
            "decided_pairs": receipt["decided_pairs"],
            "orphan_decisions": receipt["orphan_decisions"],
            "data_object": do_publish,
            "quality_summary_bounded": {
                k: quality["matching"][k]
                for k in ("matched_auto", "needs_review", "ambiguous",
                          "hard_blocked")
            },
        }
        db.commit()
    return _run_view(session_id, run_id)


def view_snapshot_plan(view: Dict[str, Any]) -> Dict[str, Any]:
    return _load_plan_snapshot(view["plan_id"])


# ───────────────────────── 增量刷新（WP4） ─────────────────────────

async def refresh_plan(
    session_id: str, plan_id: str, *, expected_revision: int,
    actor: Dict[str, Any], dry_run: bool = False,
) -> Dict[str, Any]:
    """来源刷新：识别变动实体 → 影响面报告 →（安全时）受控重跑。

    - 与最近一次 completed run 比较实体指纹（added/removed/changed）；
    - 决定三态预测：inherited / stale（保留待复审）/ orphan（悬空不
      应用）—— 锁定决定绝不无声推翻；
    - 受影响占比超安全阈 → typed ``refresh_not_safe``（全量重算路径）；
    - ``dry_run=True`` 只给影响面报告，不建 run；
    - 重跑走 ``execute_plan`` 同一 durable job 路径（base_run_id 链）
      —— 新 run 的 commit 用继承的决定，产生新融合版本；旧版本可追溯。
    """
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise DataFusionError(
                "stale_plan_revision",
                f"计划修订冲突：expected {expected_revision}，"
                f"当前 {row.revision}",
                current_revision=row.revision,
            )
        plan = parse_plan(row.plan)
        base_row = db.execute(
            select(DataFusionRun)
            .where(DataFusionRun.plan_id == plan_id,
                   DataFusionRun.session_id == session_id,
                   DataFusionRun.status == "completed")
            .order_by(DataFusionRun.created_at.desc())
        ).scalars().first()
    if base_row is None:
        raise DataFusionError(
            "run_not_found", "无可刷新基底：先执行一次匹配", status=404)
    base_view = _run_view(session_id, base_row.id)
    bundle = outputs.read_match_bundle(base_row.id)
    if bundle is None:
        raise DataFusionError(
            "receipt_missing", "基底 run 匹配包不可读", status=410)
    base_receipt = base_view.get("receipt") or {}
    fp_a_old = base_receipt.get("fingerprint_a")
    fp_b_old = base_receipt.get("fingerprint_b")

    src_a, _ = await executor.resolve_view(session_id, plan, actor, "a")
    src_b, _ = await executor.resolve_view(session_id, plan, actor, "b")
    fp_a_new, fp_b_new = src_a.content_fingerprint, src_b.content_fingerprint
    if fp_a_new == fp_a_old and fp_b_new == fp_b_old:
        return {
            "plan_id": plan_id, "base_run_id": base_row.id,
            "changed": False,
            "message": "两源内容指纹与基底一致：无刷新需求",
        }

    recs_a = matching.build_records(
        src_a.rows, src_a.geoms, plan.source_a, plan)
    recs_b = matching.build_records(
        src_b.rows, src_b.geoms, plan.source_b, plan)
    decisions = review.load_decisions_by_key(plan_id)
    impact = incremental.compute_impact_from_key_sets(
        plan, recs_a, recs_b,
        old_keys_a=_old_keys_from_bundle(bundle, "a"),
        old_keys_b=_old_keys_from_bundle(bundle, "b"),
        ref_a=src_a.source_ref, ref_b=src_b.source_ref,
        decisions_by_key=decisions)
    if dry_run:
        return {
            "plan_id": plan_id, "base_run_id": base_row.id,
            "changed": True, "dry_run": True, **impact.to_dict(),
        }
    incremental.assert_refresh_safe(impact)
    result = await execute_plan(
        session_id, plan_id, expected_revision=expected_revision,
        actor=actor, background=False, base_run_id=base_row.id)
    return {
        "plan_id": plan_id, "base_run_id": base_row.id,
        "changed": True, "impact": impact.to_dict(), "run": result,
    }


def _old_keys_from_bundle(bundle: Dict[str, Any], side: str) -> set:
    """基底包冻结信息 → 旧侧实体指纹键集合（完整键集，运行时冻结）。"""
    keys: set = set()
    frozen = bundle.get("side_keys") or {}
    stored = frozen.get(side)
    if isinstance(stored, list):
        keys.update(stored)
        return keys
    # 旧包回退：仅 pair_key 携带的键（matched 部分）—— 保守口径
    for p in bundle.get("pairs") or []:
        k = p.get("pair_key")
        if not k or not k.startswith("fp:"):
            continue
        parts = k[3:].split(":")
        if len(parts) == 2:
            keys.add(parts[0] if side == "a" else parts[1])
    return keys


# ───────────────────────── 渲染投影 ─────────────────────────

RENDER_REF_PREFIX = "fusion-render"


async def render_ref_for_run(
    session_id: str, run_id: str,
) -> Dict[str, Any]:
    """地图渲染投影（≤RENDER_REF_MAX_FEATURES 时铸 session FC ref）。"""
    view = _run_view(session_id, run_id)
    if not view["output_ref"]:
        raise DataFusionError(
            "result_unavailable", "融合成果尚未提交", status=409)
    fc = outputs.load_output_content(run_id)
    if fc is None:
        raise DataFusionError("receipt_missing", "成果不可读", status=410)
    feats = fc.get("features") or []
    if len(feats) > contracts.RENDER_REF_MAX_FEATURES:
        raise DataFusionError(
            "render_ref_over_budget",
            f"输出 {len(feats)} 要素超过渲染投影上限 "
            f"{contracts.RENDER_REF_MAX_FEATURES}：用交付（G18）获取完整数据",
            total=len(feats), cap=contracts.RENDER_REF_MAX_FEATURES,
        )
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store(
        session_id, fc, prefix=RENDER_REF_PREFIX)
    return {"render_ref": ref, "features": len(feats)}


# ───────────────────────── 恢复（进程重启收敛） ─────────────────────────

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
    except Exception:  # noqa: BLE001 — 保守：无法判定按 live
        return True


def recover_pending_runs(session_id: Optional[str] = None) -> int:
    """恢复：pending/running 且 job 已不在活跃集合 → typed 失效。

    ``session_id`` 缺省 = 全库（lifespan 启动恢复）；REST 会话路由
    必须传 session（否则任一会话可置他人孤儿 run 为 failed —— 审查
    P2 越界写）。
    """
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionRun

    n = 0
    with SessionLocal() as db:
        q = select(DataFusionRun).where(
            DataFusionRun.status.in_(("pending", "running")))
        if session_id:
            q = q.where(DataFusionRun.session_id == session_id)
        rows = db.execute(q).scalars().all()
        for r in rows:
            if r.job_id is None or not _job_is_live(int(r.job_id)):
                r.status = "failed"
                r.error_code = "internal_error"
                from datetime import datetime, timezone

                r.finished_at = datetime.now(timezone.utc)
                n += 1
        db.commit()
    return n
