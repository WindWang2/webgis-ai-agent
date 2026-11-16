"""G47 作者服务门面（WP08 / WP11）：UI 与 Pi **共用同一入口**。

* ``inspect``：层/源身份、强版本锚、有界字段画像、当前 cartogram 证据、支持矩阵；
* ``plan``：完整资格预检（数量语义、几何、CRS、邻接、资源上界）+ 面积比例预览，
  **零 mutation、零求解**；
* ``run``：提交进程内 durable job（幂等键 = 计划 + 源代次 + 算法版本），求解在
  独立线程执行，取消/重试/重启由既有 runner 保证；
* ``status`` / ``layout`` / ``list``：从权威 job 行与 session ref 读回真实状态，
  未完成/失败/未收敛都如实返回，不伪造成功；
* ``apply``：单笔 ``UpsertLayerIntent``（CAS/checkpoint/undo）写本域证据块，
  原几何/字段/用户锁不变；未达发布资格门的布局需显式 ``allow_diagnostic``；
* ``export``：从**冻结布局**生成 SVG/PNG/PDF 字节（同一份内容，DPI 只改像素）。

不新建第二套 agent loop / 存储 / 撤销栈 / 任务调度器。
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.cartogram_authoring.contract import (
    ALGORITHM_ID,
    ALGORITHM_VERSION,
    AuthoringPlan,
    clamp_plan_budget,
)
from app.services.cartogram_authoring.errors import AuthoringError
from app.services.cartogram_authoring.geography import prepare_geography
from app.services.cartogram_authoring.layout import bounded_receipt
from app.services.cartogram_authoring.quantities import (
    build_area_scale,
    reference_circles,
    scan_quantities,
)
from app.services.cartogram_authoring.render import render_export
from app.services.cartogram_authoring.snapshot import (
    FrozenSource,
    check_anchor,
    freeze_source,
    source_ref_of,
)

logger = logging.getLogger(__name__)

TASK_TYPE = "cartogram_layout"
AUTHORING_KEY = "cartogram_authoring"
_MAX_FIELDS = 120
_MAX_DISCLOSURES = 12
_VECTOR_LAYER_TYPES = {"fill", "circle", "line", "symbol"}
_RUNNERS: Dict[int, "asyncio.Task[Any]"] = {}


@dataclass
class LayerContext:
    session_id: str
    mapspec: Dict[str, Any]
    layer: Dict[str, Any]
    source_id: str
    source_entry: Dict[str, Any]

    @property
    def layer_type(self) -> str:
        return str(self.layer.get("type") or "")


async def _read_mapspec(session_id: str) -> Optional[Dict[str, Any]]:
    from app.services.mapspec_store import mapspec_store
    return await mapspec_store.get_mapspec(session_id)


async def _current_revision(session_id: str) -> int:
    from app.services.session_data import session_data_manager
    getter = getattr(session_data_manager, "get_state_field", None)
    raw = await getter(session_id, "_cartographic_mutation_revision") if callable(getter) else None
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def resolve_layer(session_id: str, layer_id: str) -> LayerContext:
    mapspec = await _read_mapspec(session_id)
    if not isinstance(mapspec, dict):
        raise AuthoringError(
            "NO_SESSION_MAPSPEC", f"会话 {session_id!r} 没有权威 MapSpec",
            status_code=404)
    layer = None
    for cand in mapspec.get("layers") or []:
        if isinstance(cand, dict) and cand.get("id") == layer_id:
            layer = cand
            break
    if layer is None:
        raise AuthoringError(
            "UNKNOWN_LAYER", f"层 {layer_id!r} 不在权威 MapSpec 中", status_code=404)
    if layer.get("type") not in _VECTOR_LAYER_TYPES:
        raise AuthoringError(
            "NOT_POLYGON_LAYER",
            f"层 {layer_id!r} 类型 {layer.get('type')!r} 不是矢量层",
            status_code=409,
            correction_hint="cartogram 需要面要素的矢量层（fill/line/circle/symbol）。")
    source_id = layer.get("source")
    sources = mapspec.get("sources") or {}
    entry = sources.get(source_id) if isinstance(source_id, str) else None
    if not isinstance(entry, dict):
        raise AuthoringError(
            "SOURCE_NOT_FOUND", f"层引用的源 {source_id!r} 不存在", status_code=409)
    return LayerContext(session_id=session_id, mapspec=mapspec, layer=layer,
                        source_id=str(source_id), source_entry=entry)


def _base_ref(ctx: LayerContext) -> str:
    ev = ctx.layer.get(AUTHORING_KEY)
    if isinstance(ev, dict):
        ref = ev.get("base_source_ref")
        if isinstance(ref, str) and ref.startswith("ref:"):
            return ref
    ref = source_ref_of(ctx.source_entry)
    if not ref:
        raise AuthoringError(
            "NO_STRONG_REVISION",
            "层源不是会话 ref（纯 URL/瓦片/内联载荷没有权威单调版本）",
            status_code=409,
            correction_hint="把数据注册为 session ref 源后再进入 cartogram 工作台。")
    return ref


async def _freeze(
    ctx: LayerContext, *, owner_token: Optional[str], trusted_dispatch: bool,
) -> FrozenSource:
    return await freeze_source(
        ctx.session_id, _base_ref(ctx),
        owner_token=owner_token, trusted_dispatch=trusted_dispatch)


def _value_kind(raw: Any) -> str:
    if raw is None:
        return "missing"
    if isinstance(raw, bool):
        return "boolean"
    if isinstance(raw, (int, float)):
        v = float(raw)
        if not math.isfinite(v):
            return "nonfinite"
        if v < 0:
            return "negative"
        if v == 0:
            return "zero"
        return "number"
    return "nonnumeric"


def _field_profile(features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """有界字段画像：显式分型计数（不猜语义）。"""
    seen: Dict[str, Dict[str, int]] = {}
    for feat in features:
        props = feat.get("properties")
        if not isinstance(props, dict):
            continue
        for key, value in props.items():
            name = str(key)
            if name not in seen:
                if len(seen) >= _MAX_FIELDS:
                    break
                seen[name] = {}
            kind = _value_kind(value)
            seen[name][kind] = seen[name].get(kind, 0) + 1
        if len(seen) >= _MAX_FIELDS:
            break
    out: List[Dict[str, Any]] = []
    for name, kinds in seen.items():
        numeric = kinds.get("number", 0) + kinds.get("zero", 0)
        out.append({
            "name": name,
            "numeric_count": numeric,
            "positive_count": kinds.get("number", 0),
            "zero_count": kinds.get("zero", 0),
            "missing_count": kinds.get("missing", 0),
            "nonnumeric_count": sum(v for k, v in kinds.items()
                                    if k not in ("number", "zero", "missing")),
            "kinds": {k: v for k, v in kinds.items() if v},
            "usable_as_quantity": numeric > 0 and kinds.get("number", 0) > 0,
        })
    out.sort(key=lambda f: (-f["positive_count"], f["name"]))
    return out


def _supported(ctx: LayerContext) -> Dict[str, Any]:
    return {
        "mode": "dorling_circle",
        "dorling_circle": True,
        "proportional_symbol_in_place": True,
        "generic_cartogram": False,
        "continuous_diffusion": False,
        "note": ("只支持 Dorling 圆面积图（示意位移布局）；Gastner-Newman 扩散/"
                 "矩形/六边形 cartogram 仍未实现，泛 cartogram 请求继续诚实降级"
                 "（cartogram_unsupported）。"),
        "layout_coordinates": "diagram units（非地理）；图面禁止距离/面积/路由计算",
    }


async def inspect_layer(
    session_id: str, layer_id: str, *,
    owner_token: Optional[str] = None, trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    ctx = await resolve_layer(session_id, layer_id)
    frozen = await _freeze(ctx, owner_token=owner_token,
                           trusted_dispatch=trusted_dispatch)
    fields = _field_profile(frozen.features)
    ev = ctx.layer.get(AUTHORING_KEY)
    geo_types: Dict[str, int] = {}
    for feat in frozen.features[:500]:
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        gtype = str((geom or {}).get("type") or "none") if isinstance(geom, dict) else "none"
        geo_types[gtype] = geo_types.get(gtype, 0) + 1
    return {
        "layer_id": layer_id,
        "layer_type": ctx.layer_type,
        "source": {
            "source_id": ctx.source_id,
            "base_ref": frozen.ref_id,
            "content_revision": frozen.content_revision,
            "content_fingerprint": frozen.content_fingerprint,
            "feature_count": frozen.feature_count,
            "declared_crs": ctx.source_entry.get("crs"),
            "snapshot_guarantee": frozen.anchor().get("snapshot_guarantee"),
        },
        "source_anchor": frozen.anchor(),
        "geometry_types": geo_types,
        "fields": fields,
        "fields_truncated": len(fields) >= _MAX_FIELDS,
        "current": ev if isinstance(ev, dict) else None,
        "supported": _supported(ctx),
        "contract": {
            "quantity": "非负、同单位、同一总体的可加总量（count/amount）",
            "unit_required": True,
            "zero_policy": "list_only（保留身份，不画正面积圆）",
            "missing_policy": "list_only（不参与总量，不与零合并）",
            "layout": "coordinate_space=diagram；不是经纬度，不可用于分析",
            "quality_gate": "面积相对误差 + 残余重叠（独立 oracle 复核）",
        },
        "revision": await _current_revision(session_id),
        "algorithm_version": ALGORITHM_VERSION,
    }


def _plan_receipt(
    ctx: LayerContext, plan: AuthoringPlan, frozen: FrozenSource,
    *, scan, prepared, scale,
) -> Dict[str, Any]:
    sample = sorted(scan.positives, key=lambda e: -float(e.value or 0))[:5]
    return {
        "success": True,
        "layer_id": ctx.layer.get("id"),
        "mode": plan.mode,
        "algorithm": {"id": ALGORITHM_ID, "version": ALGORITHM_VERSION},
        "source_anchor": frozen.anchor(),
        "plan_digest": plan.plan_digest(),
        "candidate_digest": plan.candidate_digest(frozen.anchor()),
        "quantity": scan.summary(),
        "units_for_layout": {
            "total": len(prepared.units),
            "with_positive_value": scan.valid_units,
            "zero": len(scan.zeros),
            "missing": len(scan.missing),
            "excluded": len(plan.scope.exclude_ids),
        },
        "geometry": {
            "source_crs": prepared.source_crs,
            "preparation_crs": prepared.target_crs,
            "adjacency_edges": len(prepared.edges),
            "multi_part_units": prepared.diagnostics.get("multi_part_units"),
            "vertex_count": prepared.vertex_count,
            "adjacency_contract": prepared.diagnostics.get("adjacency_contract"),
        },
        "area_scale": {
            **scale.as_receipt(),
            "reference_circles": [
                {"value": r["value"], "radius": r["radius"]}
                for r in reference_circles(scale)
            ],
        },
        "examples": [
            {"feature_id": e.feature_id, "label": e.label, "value": e.value,
             "radius": round(scale.radius(float(e.value)), 4)}
            for e in sample
        ],
        "solver": plan.solver.model_dump(mode="json"),
        "quality_gate": plan.quality_gate.model_dump(mode="json"),
        "budgets": plan.budgets.model_dump(mode="json"),
        "disclosures": [
            "布局为示意图：圆心不是地理位置，圆不保留原边界/距离/全部邻接",
            "同单位、同一总体、可加总量；率/密度/混合单位会被拒绝",
            f"源 {frozen.ref_id} @ rev {frozen.content_revision}",
        ],
        "revision": None,
        "preview_only": True,
        "estimated_circles": scan.valid_units,
    }


async def plan_layout(
    session_id: str, layer_id: str, plan: AuthoringPlan, *,
    owner_token: Optional[str] = None, trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """资格预检 + 面积比例预览（零 mutation、零求解）。"""
    ctx = await resolve_layer(session_id, layer_id)
    frozen = await _freeze(ctx, owner_token=owner_token,
                           trusted_dispatch=trusted_dispatch)
    check_anchor(plan.source_anchor, frozen)
    from app.services.cartogram_authoring.compute import scoped_features

    scoped = scoped_features(frozen.features, plan)
    scan = scan_quantities(
        scoped, field_name=plan.quantity.field, unit=plan.quantity.unit,
        zero_policy=plan.zero_policy, missing_policy=plan.missing_policy)
    prepared = prepare_geography(
        scoped, source_crs=plan.source_crs,
        target_crs=plan.projection.crs,
        snap_tolerance_m=plan.adjacency.snap_tolerance_m,
        min_shared_length_m=plan.adjacency.min_shared_length_m)
    clamp_plan_budget(plan, features=len(prepared.units),
                      adjacency_edges=len(prepared.edges))
    scale = build_area_scale(scan, max_radius=plan.area_scale.max_radius)
    receipt = _plan_receipt(ctx, plan, frozen, scan=scan, prepared=prepared,
                            scale=scale)
    receipt["revision"] = await _current_revision(session_id)
    return receipt


async def start_layout(
    session_id: str, layer_id: str, plan: AuthoringPlan, *,
    owner_token: Optional[str] = None, trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """提交 durable 布局作业（幂等：计划 + 源代次 + 算法版本）。"""
    from app.services.data_operations.runner import submit_async_job
    from app.services.jobs.lifecycle import JobKind

    ctx = await resolve_layer(session_id, layer_id)
    frozen = await _freeze(ctx, owner_token=owner_token,
                           trusted_dispatch=trusted_dispatch)
    check_anchor(plan.source_anchor, frozen)
    # 入队前做一次完整资格预检：不可修复的输入不占用 job 槽位。
    from app.services.cartogram_authoring.compute import scoped_features

    scoped = scoped_features(frozen.features, plan)
    scan = scan_quantities(
        scoped, field_name=plan.quantity.field, unit=plan.quantity.unit,
        zero_policy=plan.zero_policy, missing_policy=plan.missing_policy)
    prepared = prepare_geography(
        scoped, source_crs=plan.source_crs,
        target_crs=plan.projection.crs,
        snap_tolerance_m=plan.adjacency.snap_tolerance_m,
        min_shared_length_m=plan.adjacency.min_shared_length_m)
    clamp_plan_budget(plan, features=len(prepared.units),
                      adjacency_edges=len(prepared.edges))
    build_area_scale(scan, max_radius=plan.area_scale.max_radius)

    anchor = frozen.anchor()
    idem = (f"g47:{session_id}:{plan.plan_digest()}:{frozen.content_revision}:"
            f"{ALGORITHM_VERSION}")
    job_id, status, reused = submit_async_job(
        task_type=TASK_TYPE,
        kind=JobKind.cartogram_layout.value,
        display_name=f"Dorling cartogram · {(plan.title or scan.field)[:24]}",
        params={
            "session_id": session_id,
            "layer_id": layer_id,
            "plan": plan.canonical(),
            "source_anchor": anchor,
            "algorithm_version": ALGORITHM_VERSION,
        },
        idempotency_key=idem,
        owner_token=owner_token,
        session_id=session_id,
    )
    if not reused or status in ("pending", "queued"):
        task = asyncio.get_running_loop().create_task(
            _run_layout_job(job_id, session_id, plan, anchor),
            name=f"g47-cartogram:{job_id}",
        )
        _RUNNERS[job_id] = task
        task.add_done_callback(lambda t: _RUNNERS.pop(job_id, None))
    return {
        "success": True,
        "job_id": job_id,
        "status": status,
        "idempotent_reuse": bool(reused),
        "source_anchor": anchor,
        "plan_digest": plan.plan_digest(),
        "candidate_digest": plan.candidate_digest(anchor),
        "poll": f"/api/v1/tasks/jobs/{job_id}",
        "note": ("布局是示意坐标；作业完成不等于已应用，也不等于 publication-ready"
                 "（需 quality.status=pass 且 converged）"),
    }


async def _run_layout_job(
    job_id: int, session_id: str, plan: AuthoringPlan, anchor: Dict[str, Any],
) -> None:
    from app.lib.cancellation import OperationCancelled
    from app.services.data_operations.runner import run_async_job
    from app.services.session_data import session_data_manager

    async def body(handle):  # noqa: ANN001 - runner 句柄
        from app.services.cartogram_authoring.compute import compute_layout, summarize

        handle.checkpoint()
        frozen = await freeze_source(session_id, anchor["ref_id"],
                                     trusted_dispatch=True)
        check_anchor(anchor, frozen)
        built = await asyncio.to_thread(
            compute_layout, plan, frozen,
            should_cancel=lambda: handle.cancelled,
            cancellation_exception=lambda: OperationCancelled("cancelled"),
        )
        layout_ref = await session_data_manager.store(
            session_id, built.layout, prefix="g47layout")
        summary = summarize(built)
        summary.update({
            "layout_ref": str(layout_ref),
            "layer_id": plan.layer_id,
            "job_id": job_id,
        })
        return summary

    try:
        final = await run_async_job(
            job_id, body, result_ref_builder=lambda r: str(r.get("layout_ref") or ""))
        logger.info("[g47] layout job %s finished status=%s", job_id,
                    getattr(final, "value", final))
    except Exception:  # noqa: BLE001 - runner 已落 failed 终态，这里只记日志
        logger.warning("[g47] layout job %s failed", job_id, exc_info=True)


async def layout_status(session_id: str, job_id: int) -> Dict[str, Any]:
    """读权威 job 行（不接受别人的 job id）。"""
    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    with SessionLocal() as db:
        row = DurableJobStore.get_sync(db, int(job_id))
        if row is None or str(getattr(row, "session_id", "") or "") != session_id:
            raise AuthoringError(
                "JOB_NOT_FOUND", f"job {job_id} 不存在或不属于本会话", status_code=404)
        return {
            "job_id": int(job_id),
            "task_type": getattr(row, "task_type", None),
            "status": str(getattr(row, "status", "")),
            "progress": getattr(row, "progress", None),
            "message": getattr(row, "progress_message", None),
            "result_ref": getattr(row, "result_ref", None),
            "result_summary": getattr(row, "result_summary", None),
            "error": getattr(row, "error_summary", None),
            "attempt": getattr(row, "attempt", None),
            "created_at": _iso(getattr(row, "created_at", None)),
            "updated_at": _iso(getattr(row, "updated_at", None)),
        }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else (
        str(value) if value is not None else None)


async def load_layout(session_id: str, layout_ref: str) -> Dict[str, Any]:
    """读布局 artifact（ref 必须是本会话的；ID/hash 不是权限）。"""
    from app.services.session_data import session_data_manager

    if not (isinstance(layout_ref, str) and layout_ref.startswith("ref:")):
        raise AuthoringError(
            "LAYOUT_NOT_FOUND", f"布局引用 {layout_ref!r} 不是会话 ref", status_code=404)
    payload = await session_data_manager.get(session_id, layout_ref)
    if not isinstance(payload, dict) or payload.get("kind") != "cartogram_layout":
        raise AuthoringError(
            "LAYOUT_NOT_FOUND",
            f"布局 {layout_ref!r} 不存在或不是 cartogram_layout", status_code=404)
    return payload


async def get_layout(
    session_id: str, layout_ref: str, *, full: bool = False,
) -> Dict[str, Any]:
    layout = await load_layout(session_id, layout_ref)
    if full:
        return {**layout, "layout_ref": layout_ref}
    return {**bounded_receipt(layout), "layout_ref": layout_ref}


async def list_layouts(session_id: str, *, limit: int = 20) -> Dict[str, Any]:
    """本会话的历史布局（来自权威 job 行；不新建第二套记录表）。"""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask

    cap = max(1, min(int(limit), 50))
    with SessionLocal() as db:
        rows = db.execute(
            select(AnalysisTask)
            .where(AnalysisTask.session_id == session_id,
                   AnalysisTask.task_type == TASK_TYPE)
            .order_by(AnalysisTask.created_at.desc())
            .limit(cap)
        ).scalars().all()
        items = [{
            "job_id": int(r.id),
            "status": str(getattr(r, "status", "")),
            "layout_ref": getattr(r, "result_ref", None),
            "summary": getattr(r, "result_summary", None),
            "created_at": _iso(getattr(r, "created_at", None)),
        } for r in rows]
    return {"session_id": session_id, "items": items, "truncated": len(items) >= cap}


async def apply_layout(
    session_id: str, layer_id: str, *, layout_ref: str,
    expected_revision: int,
    allow_diagnostic: bool = False,
    acknowledge_stale_source: bool = False,
    origin: str = "user",
    actor: str = "dorling_cartogram_panel",
    mutation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """把布局作为**参考成果**应用到层（不改原几何/字段/样式）。

    同代性在准入时复核：用 ref descriptor 的**当前** revision 与布局锚定的
    revision 比对（O(1)，不重新深拷贝全量内容）；descriptor 读不到或代次不可判定
    时 typed 拒绝，绝不"读不到就当没过期"。
    """
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    ctx = await resolve_layer(session_id, layer_id)
    layout = await load_layout(session_id, layout_ref)
    ready = bool(layout.get("publication_ready"))
    if not ready and not allow_diagnostic:
        metrics = layout.get("metrics") or {}
        raise AuthoringError(
            "QUALITY_GATE_FAILED",
            f"布局未达发布资格门（status={layout.get('status')}，"
            f"质量门={((metrics.get('gates') or {}))}）——"
            "可保存为诊断草稿，但不能当作已完成成果",
            status_code=409,
            correction_hint="调整参数/范围/字段后重新 run，或用 allow_diagnostic=true "
                            "明确以草稿身份应用（回执会标 diagnostic）。")

    base_ref = _base_ref(ctx)
    source = layout.get("source") or {}
    if source.get("ref_id") and source.get("ref_id") != base_ref:
        raise AuthoringError(
            "STALE_SOURCE",
            f"布局锚定源 {source.get('ref_id')!r} ≠ 当前层源 {base_ref!r}；"
            "不能把另一份源的布局贴到本层",
            status_code=409,
            correction_hint="在当前层源上重新 run。")

    source_stale = False
    # 同代复核是**准入条件**，不是尽力而为：读不到 descriptor/代次就无法证明
    # 布局与当前源同代，此时放行等于把"可能过期"的成果写上生产层。
    base_rev = source.get("content_revision")
    try:
        from app.services.session_data import session_data_manager
        descriptor = await session_data_manager.get_ref_descriptor(session_id, base_ref)
    except Exception as exc:  # noqa: BLE001 - 统统一成 typed 拒绝
        raise AuthoringError(
            "SOURCE_VERIFY_FAILED",
            "无法读取源引用描述符，因而无法确认布局与当前源同代——拒绝写入",
            status_code=409,
            details={"base_ref": base_ref},
            correction_hint="重新 inspect + run 后再 apply；若源刚被替换，请以新源重建。",
        ) from exc
    cur_rev = descriptor.get("content_revision") if isinstance(descriptor, dict) else None
    if not isinstance(cur_rev, int) or not isinstance(base_rev, int):
        raise AuthoringError(
            "SOURCE_VERIFY_FAILED",
            "源当前代次不可判定（descriptor 缺失或代次非整数）——拒绝写入",
            status_code=409,
            details={"base_ref": base_ref, "layout_revision": base_rev,
                     "current_revision": cur_rev},
            correction_hint="重新 inspect 取当前 revision，再 run 一份同代布局。",
        )
    source_stale = cur_rev != base_rev
    if source_stale and not acknowledge_stale_source:
        raise AuthoringError(
            "STALE_SOURCE",
            "源内容代次在布局生成后变化——旧布局仍锚定旧代次；"
            "请重新 run，或显式 acknowledge_stale_source=true 以 stale 身份应用",
            status_code=409,
            correction_hint="重新 inspect/run 得到同代布局。")

    cur = await _current_revision(session_id)
    if int(expected_revision) != cur:
        raise AuthoringError(
            "CAS_SUPERSEDED",
            "MapSpec 修订在应用期间变化（用户/agent 并发或层已锁）；nothing was written",
            status_code=409,
            correction_hint="重新读取 revision 与表达后重试。")

    updated = dict(ctx.layer)
    previous = ctx.layer.get(AUTHORING_KEY)
    applied_at = datetime.now(timezone.utc).isoformat()
    metrics = layout.get("metrics") or {}
    updated[AUTHORING_KEY] = {
        "schema_version": 1,
        "mode": "dorling_circle",
        "layout_ref": layout_ref,
        "layout_fingerprint": (layout.get("metrics") or {}).get("layout_fingerprint"),
        "plan_digest": layout.get("plan_digest"),
        "candidate_digest": layout.get("candidate_digest"),
        "algorithm": layout.get("algorithm"),
        "base_source_ref": base_ref,
        "base_content_revision": source.get("content_revision"),
        "base_content_fingerprint": source.get("content_fingerprint"),
        "publication_ready": ready,
        "diagnostic": not ready,
        "stale_source": bool(source_stale),
        "status": layout.get("status"),
        "metrics": {
            "area": metrics.get("area"),
            "overlap": metrics.get("overlap"),
            "displacement": metrics.get("displacement"),
            "adjacency": metrics.get("adjacency"),
        },
        "coordinate_space": layout.get("coordinate_space"),
        "decided_by": origin,
        "actor": actor,
        "applied_at": applied_at,
        "previous": previous if isinstance(previous, dict) else None,
    }
    source_data = {
        "ref_id": base_ref,
        "data_fingerprint": source.get("content_fingerprint"),
    }
    result = await apply_gis_mutation(
        session_id, UpsertLayerIntent(layer=updated, source_data=source_data),  # type: ignore[arg-type]
        origin=origin, actor=actor, expected_revision=int(expected_revision),
        mutation_id=mutation_id)
    if result.superseded:
        raise AuthoringError(
            "CAS_SUPERSEDED", "MapSpec 修订在提交期间变化；nothing was written",
            status_code=409)
    if result.is_error:
        raise AuthoringError(
            "MUTATION_REJECTED", str(result.error_msg or "MapSpec mutation rejected"),
            status_code=422)
    # 幂等重放（同一 mutation_id 已提交过）不是新写入：如实报 0 次 mutation、
    # 不可再 undo 一次，否则调用方会以为重试产生了新提交。
    duplicate = bool(getattr(result, "duplicate", False))
    return {
        "success": True,
        "layer_id": layer_id,
        "layout_ref": layout_ref,
        "committed": not duplicate,
        "duplicate": duplicate,
        "publication_ready": ready,
        "diagnostic": not ready,
        "stale_source": bool(source_stale),
        "revision": int(result.mutation_revision or cur),
        "mapspec_fingerprint": result.mapspec_fingerprint,
        "can_undo": not duplicate,
        "mutations": 0 if duplicate else 1,
        "disclosures": list(layout.get("disclosures") or [])[:_MAX_DISCLOSURES],
        "coordinate_space": layout.get("coordinate_space"),
    }


async def export_layout(
    session_id: str, layout_ref: str, fmt: str, *, dpi: int = 96,
) -> Tuple[bytes, str, str]:
    """从冻结布局生成导出字节（真实内容，不是占位）。"""
    layout = await load_layout(session_id, layout_ref)
    data, media, filename = await asyncio.to_thread(
        render_export, layout, fmt, dpi=int(dpi))
    return data, media, filename
