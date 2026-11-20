"""G39 采样服务 — describe/plan/preview/execute/status/results/cancel（WP10）。

API 与 Pi 工具共用本 service（单一生效路径）。纪律：
- 每次读取/执行/下载重新核 actor 与 session 归属（owner_token 路由面 /
  trusted_dispatch 工具面 —— 与 raster_authoring 同款双通道）；
- 预览非权威：bounded 内联采样 + revision 校验；完整成果只经 execute 发布；
- 执行幂等：收据短路 → 幂等 job（G23 runner）→ 单次有效发布；
- 历史成果源更新后旧收据仍可读（标旧版）；内容被 GC → typed 失效。
"""
from __future__ import annotations

import asyncio
import secrets
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.raster_sampling import freeze, publish, stations as stations_mod
from app.services.raster_sampling import reader_schedule, store
from app.services.raster_sampling.contracts import (
    DISTANCE_MODE_GEODESIC,
    INTERPOLATING_METHODS,
    MAX_BANDS_PER_SOURCE,
    MAX_PREVIEW_STATIONS,
    MAX_SOURCES,
    MAX_STATIONS,
    MAX_VALUE_CELLS,
    METHOD_SOURCE_PIXEL,
    RasterSamplingError,
    SAMPLING_ALGORITHM_VERSION,
    SamplingPlan,
    SourceBinding,
    StationSpec,
    STATION_MODE_LINE_STATIONS,
    STATION_MODE_SOURCE_POINTS,
    compute_params_digest,
    validate_sources,
)

_PREVIEW_ROW_CAP = 400  # 预览行输出帽（站位 × 波段上限内的显示切片）


# ── describe ────────────────────────────────────────────────────────

async def describe_sources(
    session_id: str,
    owner_token: Optional[str],
    *,
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """可采样数值栅格源清单（与 raster_authoring /refs 同款授权过滤）。"""
    from app.services.session_data import session_data_manager

    refs = await session_data_manager.list_refs(session_id)
    out: List[Dict[str, Any]] = []
    for ref_id in list(refs)[:32]:
        if trusted_dispatch:
            raw = await session_data_manager.get(session_id, ref_id)
            payload = raw if isinstance(raw, dict) else None
        else:
            res = await session_data_manager.get_ref_data(
                session_id, ref_id, owner_token=owner_token)
            payload = res.data if (res.success and isinstance(res.data, dict)) else None
        if payload is None:
            continue
        path = payload.get("file_path") or payload.get("path")
        if not isinstance(path, str) or not path:
            continue  # baked PNG / 非栅格 ref 不进采样源清单
        out.append({
            "ref_id": ref_id,
            "alias": refs.get(ref_id, ""),
            "kind": payload.get("kind"),
            "verified": False,  # 未开文件头；plan 阶段才做真实读取
        })
    return {
        "success": True,
        "session_id": session_id,
        "raster_refs": out,
        "truncated": len(refs) > 32,
        "budgets": {
            "max_sources": MAX_SOURCES,
            "max_bands_per_source": MAX_BANDS_PER_SOURCE,
            "max_stations": MAX_STATIONS,
            "max_value_cells": MAX_VALUE_CELLS,
            "max_preview_stations": MAX_PREVIEW_STATIONS,
        },
        "methods": ["source_pixel", "bilinear", "bilinear_renormalized"],
        "distance_modes": ["geodesic", "planar_metric"],
        "algorithm_version": SAMPLING_ALGORITHM_VERSION,
    }


# ── 计划构建 / 修订 ─────────────────────────────────────────────────

def _validate_bands_against_source(bands: List[int], band_count: int,
                                   ref_id: str) -> None:
    for b in bands:
        if not (1 <= int(b) <= int(band_count)):
            raise RasterSamplingError(
                "BAND_OUT_OF_RANGE",
                f"source {ref_id} has {band_count} bands; band {b} requested",
                field="bands", details={"band_count": int(band_count)},
                status_code=422)


async def _resolve_bindings(
    session_id: str,
    owner_token: Optional[str],
    source_specs: Sequence[Dict[str, Any]],
    *,
    trusted_dispatch: bool,
) -> List[SourceBinding]:
    from app.services.raster_authoring.source import resolve_source
    from app.schemas.raster_spec import RasterExpressionError

    def _map(exc: Exception, ref_id: str) -> RasterSamplingError:
        """G20 typed 错误 → 采样域 typed 错误（码语义对齐）。"""
        if isinstance(exc, RasterExpressionError):
            return RasterSamplingError(
                exc.code if exc.code in {
                    "SOURCE_UNAVAILABLE", "SOURCE_NOT_NUMERIC_RASTER",
                    "SOURCE_PATH_REJECTED", "BAND_OUT_OF_RANGE"}
                else "SOURCE_UNAVAILABLE",
                str(exc), field="ref_id",
                status_code=exc.status_code if hasattr(exc, "status_code") else 404,
                correction_hint=getattr(exc, "correction_hint", ""))
        return RasterSamplingError(
            "SOURCE_UNAVAILABLE", f"source {ref_id} could not be resolved",
            field="ref_id", status_code=404)

    bindings: List[SourceBinding] = []
    for spec in source_specs:
        if not isinstance(spec, dict) or not spec.get("ref_id"):
            raise RasterSamplingError(
                "INVALID_PLAN", "each source needs a ref_id", field="sources")
        try:
            source = await resolve_source(
                session_id, str(spec["ref_id"]), owner_token,
                trusted_dispatch=trusted_dispatch)
        except RasterSamplingError:
            raise
        except Exception as exc:  # noqa: BLE001 — G20 typed → 采样域 typed
            raise _map(exc, str(spec["ref_id"])) from exc
        bands = [int(b) for b in (spec.get("bands") or [1])]
        _validate_bands_against_source(bands, source.band_count, source.ref_id)
        semantics = spec.get("band_semantics") or {}
        for k, v in semantics.items():
            if v not in ("categorical", "continuous"):
                raise RasterSamplingError(
                    "INVALID_PLAN",
                    f"band_semantics[{k}] must be categorical|continuous",
                    field="band_semantics")
        bindings.append(freeze.freeze_source_binding(source, {
            "label": spec.get("label") or "",
            "bands": bands,
            "band_labels": spec.get("band_labels") or {},
            "band_units": spec.get("band_units") or {},
            "band_semantics": semantics,
        }))
    validate_sources(bindings)
    return bindings


async def _build_station_set(
    plan_id: str,
    spec: StationSpec,
    session_id: str,
    owner_token: Optional[str],
    *,
    trusted_dispatch: bool,
) -> stations_mod.StationSet:
    if spec.mode == STATION_MODE_SOURCE_POINTS:
        if not spec.points_ref:
            raise RasterSamplingError(
                "INVALID_PLAN", "source_points mode requires points_ref",
                field="points_ref")
        fc, _digest = await freeze.load_fc_ref(
            session_id, spec.points_ref, owner_token,
            trusted_dispatch=trusted_dispatch)
        return stations_mod.build_point_stations(fc, crs=spec.crs, plan_id=plan_id)
    if spec.mode == STATION_MODE_LINE_STATIONS:
        if not spec.lines_ref:
            raise RasterSamplingError(
                "INVALID_PLAN", "line_stations mode requires lines_ref",
                field="lines_ref")
        fc, _digest = await freeze.load_fc_ref(
            session_id, spec.lines_ref, owner_token,
            trusted_dispatch=trusted_dispatch)
        feature, fid = freeze.pick_line_feature(fc, spec.line_feature_id)
        return stations_mod.build_line_stations(
            feature, crs=spec.crs,
            distance_mode=spec.distance_mode,
            station_step=spec.station_step,
            include_vertices=spec.include_vertices,
            plan_id=plan_id, line_feature_id=fid)
    raise RasterSamplingError(
        "INVALID_PLAN", f"unknown station mode {spec.mode!r}", field="mode")


async def create_plan(
    *,
    session_id: str,
    owner_token: Optional[str],
    name: str,
    mode: str,
    points_ref: str = "",
    selection: Optional[Dict[str, Any]] = None,
    lines_ref: str = "",
    line_feature_id: str = "",
    station_crs: str = "EPSG:4326",
    distance_mode: str = DISTANCE_MODE_GEODESIC,
    station_step: Optional[float] = None,
    include_vertices: bool = True,
    method: str = METHOD_SOURCE_PIXEL,
    nodata_policy: str = "strict",
    sources: Optional[Sequence[Dict[str, Any]]] = None,
    project_id: str = "",
    owner: str = "",
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """创建采样计划（冻结站位输入 + 源绑定；selection 即时物化）。"""
    if mode not in (STATION_MODE_SOURCE_POINTS, STATION_MODE_LINE_STATIONS):
        raise RasterSamplingError(
            "INVALID_PLAN", f"unknown station mode {mode!r}", field="mode")
    materialized: Optional[Dict[str, Any]] = None
    if mode == STATION_MODE_SOURCE_POINTS and selection:
        # G24 handle 只在准备阶段消费：materialize → 冻结 ref（S22）
        layer_ref = str(selection.get("ref_id") or "")
        handle = str(selection.get("handle") or "")
        if not layer_ref or not handle:
            raise RasterSamplingError(
                "INVALID_PLAN", "selection needs ref_id and handle",
                field="selection")
        materialized = await freeze.materialize_selection_input(
            session_id, layer_ref, handle, owner_token)
        points_ref = str(materialized["ref_id"])
    spec = StationSpec(
        mode=mode, points_ref=points_ref, lines_ref=lines_ref,
        crs=station_crs, distance_mode=distance_mode,
        station_step=station_step, include_vertices=include_vertices,
        line_feature_id=line_feature_id)
    bindings = await _resolve_bindings(
        session_id, owner_token, sources or [], trusted_dispatch=trusted_dispatch)
    plan_id = f"rsp_{secrets.token_hex(6)}"
    station_set = await _build_station_set(
        plan_id, spec, session_id, owner_token, trusted_dispatch=trusted_dispatch)
    # 方法 × 波段语义早期校验（band_meta 覆盖内；执行期为权威）
    _early_method_check(bindings, method)
    now = store.now_ts()
    plan = SamplingPlan(
        plan_id=plan_id, session_id=session_id, owner=owner,
        project_id=project_id, revision=1, name=name or plan_id,
        mode=mode, station_spec=spec, sources=bindings,
        method=method, nodata_policy=nodata_policy,
        algorithm_version=SAMPLING_ALGORITHM_VERSION,
        station_digest=station_set.digest(),
        geometry_hash=station_set.geometry_hash(),
        created_at=now, updated_at=now,
    )
    plan.params_digest = compute_params_digest(
        plan=plan, station_digest=station_set.digest())
    if not await store.save_plan(plan):
        raise RasterSamplingError(
            "CONTENT_WRITE_FAILED", "plan could not be persisted",
            field="plan_id", status_code=500)
    return {
        "success": True,
        "plan": plan_to_payload(plan),
        "stations": _station_summary(station_set),
        "selection_materialized": materialized,
    }


def _early_method_check(bindings: Sequence[SourceBinding], method: str) -> None:
    if method not in (METHOD_SOURCE_PIXEL,) and method not in INTERPOLATING_METHODS:
        raise RasterSamplingError(
            "INVALID_PLAN", f"unknown method {method!r}", field="method")
    if method in INTERPOLATING_METHODS:
        for s in bindings:
            for b in s.bands:
                # band_semantics 于 freeze 期已解析有效语义（用户声明 >
                # 数据集 classification 元数据；15b），此处直接判定。
                semantic = s.band_semantics.get(str(b))
                if semantic == "categorical":
                    raise RasterSamplingError(
                        "METHOD_INCOMPATIBLE",
                        f"source {s.ref_id} band {b} is categorical — "
                        f"{method} would blend classes; use source_pixel",
                        field="method",
                        correction_hint="Declare the band continuous if it "
                                        "truly is, or keep source_pixel.")
                if semantic != "continuous":
                    raise RasterSamplingError(
                        "METHOD_INCOMPATIBLE",
                        f"source {s.ref_id} band {b} has undeclared "
                        "semantics — interpolation needs an explicit "
                        "continuous declaration (dtype is not evidence)",
                        field="method",
                        correction_hint="Declare band_semantics="
                                        "'continuous' or use source_pixel.")


async def revise_plan(
    *,
    session_id: str,
    plan_id: str,
    expected_revision: int,
    owner_token: Optional[str],
    updates: Dict[str, Any],
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """CAS 修订：方法/波段/步长/预算；站位输入 ref 本身不可改（新计划）。"""
    plan = await store.get_plan(session_id, plan_id)
    if plan is None:
        raise RasterSamplingError(
            "PLAN_NOT_FOUND", f"plan {plan_id} not found", field="plan_id",
            status_code=404)
    method = str(updates.get("method") or plan.method)
    nodata_policy = str(updates.get("nodata_policy") or plan.nodata_policy)
    spec = plan.station_spec.model_copy(deep=True)
    if updates.get("station_step") is not None:
        spec.station_step = float(updates["station_step"])
    if updates.get("include_vertices") is not None:
        spec.include_vertices = bool(updates["include_vertices"])
    if updates.get("distance_mode"):
        spec.distance_mode = str(updates["distance_mode"])
    new_sources = plan.sources
    if updates.get("sources"):
        new_sources = await _resolve_bindings(
            session_id, owner_token, updates["sources"],
            trusted_dispatch=trusted_dispatch)
    _early_method_check(new_sources, method)
    station_set = await _build_station_set(
        plan_id, spec, session_id, owner_token, trusted_dispatch=trusted_dispatch)
    now = store.now_ts()
    revised = plan.model_copy(update={
        "revision": plan.revision + 1,
        "method": method, "nodata_policy": nodata_policy,
        "station_spec": spec, "sources": new_sources,
        "updated_at": now,
    })
    revised.station_digest = station_set.digest()
    revised.geometry_hash = station_set.geometry_hash()
    revised.params_digest = compute_params_digest(
        plan=revised, station_digest=station_set.digest())
    saved = await store.save_plan_cas(revised, expected_revision=expected_revision)
    return {"success": True, "plan": plan_to_payload(saved),
            "stations": _station_summary(station_set)}


def plan_to_payload(plan: SamplingPlan) -> Dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "session_id": plan.session_id,
        "name": plan.name,
        "revision": plan.revision,
        "mode": plan.mode,
        "station_spec": plan.station_spec.model_dump(),
        "sources": [s.model_dump() for s in plan.sources],
        "method": plan.method,
        "nodata_policy": plan.nodata_policy,
        "algorithm_version": plan.algorithm_version,
        "station_digest": plan.station_digest,
        "geometry_hash": plan.geometry_hash,
        "params_digest": plan.params_digest,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


def _station_summary(station_set: stations_mod.StationSet) -> Dict[str, Any]:
    sample = [s.sample_id for s in station_set.stations[:8]]
    return {
        "count": len(station_set.stations),
        "crs": station_set.crs,
        "mode": station_set.mode,
        "part_ids": station_set.part_ids,
        "part_breaks": len(station_set.part_break_indices),
        "distance_mode": station_set.distance_mode,
        "line_length_m": round(station_set.line_length_m, 3),
        "notes": station_set.notes,
        "first_sample_ids": sample,
    }


async def get_plan_payload(session_id: str, plan_id: str) -> Dict[str, Any]:
    plan = await store.get_plan(session_id, plan_id)
    if plan is None:
        raise RasterSamplingError(
            "PLAN_NOT_FOUND", f"plan {plan_id} not found", field="plan_id",
            status_code=404)
    return {"success": True, "plan": plan_to_payload(plan)}


async def list_plans_payload(session_id: str) -> Dict[str, Any]:
    plans = await store.list_plans(session_id)
    receipts = {r.plan_id: r for r in await store.list_receipts(session_id)}
    items = []
    for p in plans:
        receipt = receipts.get(p.plan_id)
        items.append({
            **plan_to_payload(p),
            "latest_receipt": (receipt_summary(receipt)
                               if receipt else None),
        })
    return {"success": True, "plans": items}


def receipt_summary(receipt: Any) -> Dict[str, Any]:
    return {
        "execution_id": receipt.execution_id,
        "plan_id": receipt.plan_id,
        "plan_revision": receipt.plan_revision,
        "status": receipt.status,
        "method": receipt.method,
        "station_count": receipt.station_count,
        "params_digest": receipt.params_digest,
        "coverage": receipt.coverage.model_dump()
        if hasattr(receipt.coverage, "model_dump") else receipt.coverage,
        "outputs": {
            "sample_table_ref": receipt.outputs.get("sample_table_ref"),
            "station_layer_ref": receipt.outputs.get("station_layer_ref"),
        },
        "degraded": receipt.degraded,
        "job_id": receipt.job_id,
        "completed_at": receipt.completed_at,
    }


# ── 共享执行准备 ─────────────────────────────────────────────────────

async def _prepare_execution(
    plan: SamplingPlan,
    session_id: str,
    owner_token: Optional[str],
    *,
    trusted_dispatch: bool,
    expected_revision: Optional[int] = None,
) -> Tuple[stations_mod.StationSet, List[Any]]:
    """执行/预览共用的准备：修订校验 → 站位重建（digest 复核）→ 源解析
    （绑定代次复核）。"""
    if expected_revision is not None and int(expected_revision) != plan.revision:
        raise RasterSamplingError(
            "PLAN_REVISION_CONFLICT",
            f"plan {plan.plan_id} is at revision {plan.revision}, "
            f"expected {expected_revision}",
            field="expected_revision", status_code=409,
            correction_hint="Reload the plan and retry.")
    station_set = await _build_station_set(
        plan.plan_id, plan.station_spec, session_id, owner_token,
        trusted_dispatch=trusted_dispatch)
    if station_set.digest() != plan.station_digest:
        raise RasterSamplingError(
            "STALE_BINDING",
            "station inputs changed since the plan was created — the bound "
            "station set no longer matches (rebuild the plan)",
            field="stations", status_code=409)
    from app.services.raster_authoring.source import resolve_source

    resolved = []
    for binding in plan.sources:
        source = await resolve_source(
            session_id, binding.ref_id, owner_token,
            trusted_dispatch=trusted_dispatch)
        freeze.verify_binding_generation(binding, source)
        resolved.append(source)
    return station_set, resolved


# ── 预览（bounded、非权威） ──────────────────────────────────────────

async def preview(
    *,
    session_id: str,
    plan_id: str,
    owner_token: Optional[str],
    expected_revision: Optional[int] = None,
    limit: int = MAX_PREVIEW_STATIONS,
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    plan = await store.get_plan(session_id, plan_id)
    if plan is None:
        raise RasterSamplingError(
            "PLAN_NOT_FOUND", f"plan {plan_id} not found", field="plan_id",
            status_code=404)
    if expected_revision is not None and int(expected_revision) != plan.revision:
        raise RasterSamplingError(
            "PREVIEW_STALE",
            f"preview bound to revision {expected_revision} but plan is at "
            f"{plan.revision}",
            field="expected_revision", status_code=409)
    station_limit = max(1, min(int(limit), MAX_PREVIEW_STATIONS))
    station_set, resolved = await _prepare_execution(
        plan, session_id, owner_token,
        trusted_dispatch=trusted_dispatch)
    # 均匀选站（跨 part 代表性；预览非全量、显式披露）
    all_stations = station_set.stations
    if len(all_stations) > station_limit:
        step = len(all_stations) / station_limit
        picked_idx = sorted({
            min(len(all_stations) - 1, int(i * step))
            for i in range(station_limit)})
    else:
        picked_idx = list(range(len(all_stations)))
    preview_set = stations_mod.StationSet(
        mode=station_set.mode, crs=station_set.crs,
        stations=[all_stations[i] for i in picked_idx],
        part_ids=station_set.part_ids,
        part_break_indices=[], distance_mode=station_set.distance_mode,
        line_length_m=station_set.line_length_m, notes=station_set.notes)
    rows, metrics, cost = await asyncio.to_thread(
        reader_schedule.execute_sampling,
        resolved_sources=list(resolved),
        bindings=list(plan.sources),
        station_set=preview_set,
        method=plan.method,
    )
    row_cap = _PREVIEW_ROW_CAP
    shown_rows = rows[:row_cap]
    return {
        "success": True,
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "preview": True,
        "station_selection": {
            "requested_stations": len(all_stations),
            "previewed_stations": len(picked_idx),
            "method": "uniform_stride" if len(picked_idx) < len(all_stations) else "all",
        },
        "rows_shown": len(shown_rows),
        "rows_truncated": len(rows) > row_cap,
        "rows": [r.model_dump() for r in shown_rows],
        "read_metrics": metrics.to_payload(),
        "cost_estimate": cost,
        "coverage_preview": publish.coverage_from_rows(
            rows, len(picked_idx)).model_dump(),
    }


# ── 执行（幂等 job + 单次发布） ─────────────────────────────────────

async def execute(
    *,
    session_id: str,
    plan_id: str,
    owner_token: Optional[str],
    expected_revision: Optional[int] = None,
    background: bool = False,
    actor: Optional[Dict[str, Any]] = None,
    project_id: str = "",
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    from app.services.raster_sampling.jobs import submit_sampling_job

    plan = await store.get_plan(session_id, plan_id)
    if plan is None:
        raise RasterSamplingError(
            "PLAN_NOT_FOUND", f"plan {plan_id} not found", field="plan_id",
            status_code=404)
    return await submit_sampling_job(
        plan=plan, session_id=session_id, owner_token=owner_token,
        expected_revision=expected_revision, background=background,
        actor=actor, project_id=project_id or plan.project_id,
        trusted_dispatch=trusted_dispatch)


async def run_plan_execution(
    *,
    plan: SamplingPlan,
    session_id: str,
    owner_token: Optional[str],
    trusted_dispatch: bool,
    progress_hook: Any = None,
) -> Dict[str, Any]:
    """job body：准备 → 采样 → 发布 → 收据（收据落盘后才算完成）。"""
    station_set, resolved = await _prepare_execution(
        plan, session_id, owner_token, trusted_dispatch=trusted_dispatch)
    rows, metrics, cost = await asyncio.to_thread(
        reader_schedule.execute_sampling,
        resolved_sources=list(resolved),
        bindings=list(plan.sources),
        station_set=station_set,
        method=plan.method,
        progress_hook=progress_hook,
    )
    execution_id = f"rx_{secrets.token_hex(6)}"
    receipt = await publish.publish_products(
        session_id=session_id,
        plan_id=plan.plan_id,
        execution_id=execution_id,
        station_set=station_set,
        rows=rows,
        read_metrics_payload=metrics.to_payload(),
        cost_estimate=cost,
        params_digest=plan.params_digest,
        plan_revision=plan.revision,
        method=plan.method,
        job_id=None,
    )
    receipt.completed_at = store.now_ts()
    if not await store.append_receipt(receipt, published=True):
        raise RasterSamplingError(
            "CONTENT_WRITE_FAILED",
            "receipt could not be persisted after publish — refusing to "
            "claim completion",
            field="receipt", status_code=500)
    return {
        "execution_id": receipt.execution_id,
        "summary": receipt_summary(receipt),
    }


# ── 状态 / 结果 / 取消 ──────────────────────────────────────────────

async def _latest_job(
    session_id: str, plan_id: str
) -> Optional[Dict[str, Any]]:
    from app.services.data_operations.runner import _sync_session
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select

    with _sync_session() as db:
        row = db.execute(
            select(AnalysisTask).where(
                AnalysisTask.task_type == "raster_sampling_execute",
                AnalysisTask.session_id == session_id,
            ).order_by(AnalysisTask.id.desc()).limit(200)
        ).scalars().all()
        out = None
        for job in row:
            params = job.parameters if isinstance(job.parameters, dict) else {}
            if params.get("plan_id") == plan_id:
                out = {"job_id": str(job.id), "status": str(job.status),
                       "params": params}
                break
        db.commit()
    return out


async def status(
    *,
    session_id: str,
    plan_id: str,
    owner_token: Optional[str] = None,
) -> Dict[str, Any]:
    plan = await store.get_plan(session_id, plan_id)
    if plan is None:
        raise RasterSamplingError(
            "PLAN_NOT_FOUND", f"plan {plan_id} not found", field="plan_id",
            status_code=404)
    job = await _latest_job(session_id, plan_id)
    receipts = [r for r in await store.list_receipts(session_id)
                if r.plan_id == plan_id]
    latest = receipts[-1] if receipts else None
    payload: Dict[str, Any] = {
        "success": True,
        "plan_id": plan_id,
        "revision": plan.revision,
        "job": job,
        "receipt_count": len(receipts),
    }
    if latest is not None:
        payload["latest_receipt"] = receipt_summary(latest)
        payload["outputs_alive"] = await _outputs_alive(latest)
    else:
        payload["latest_receipt"] = None
        if job and job.get("status") == "completed":
            payload["recovery_required"] = True
            payload["recovery_reason"] = (
                "job reached a terminal completed state without a readable "
                "receipt — causal evidence is incomplete; re-execute instead "
                "of trusting the job row (S25)")
    return payload


async def _outputs_alive(receipt: Any) -> Dict[str, Any]:
    from app.services.artifact_registry import fabric_parquet_ref_exists

    ref = receipt.outputs.get("sample_table_ref") or ""
    alive = bool(ref) and fabric_parquet_ref_exists(receipt.session_id, ref)
    return {"sample_table": alive}


async def results(
    *,
    session_id: str,
    plan_id: str = "",
    execution_id: str = "",
    owner_token: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    include_curves: bool = True,
    source_ref: str = "",
    band: Optional[int] = None,
    valid: Optional[bool] = None,
) -> Dict[str, Any]:
    receipt = None
    if execution_id:
        receipt = await store.get_receipt(session_id, execution_id)
    elif plan_id:
        receipts = [r for r in await store.list_receipts(session_id)
                    if r.plan_id == plan_id]
        receipt = receipts[-1] if receipts else None
    if receipt is None:
        raise RasterSamplingError(
            "RESULTS_NOT_READY",
            "no published receipt for this plan/execution — execute first",
            field="plan_id", status_code=404)
    alive = await _outputs_alive(receipt)
    if not alive.get("sample_table"):
        raise RasterSamplingError(
            "RESULTS_EXPIRED",
            "the published sample table is no longer on disk (GC/cleanup) — "
            "the receipt is historical; re-execute to regenerate",
            field="sample_table_ref", status_code=410,
            details={"execution_id": receipt.execution_id,
                     "sample_table_ref": receipt.outputs.get("sample_table_ref")})
    from app.services.artifact_registry import fabric_parquet_path

    path = fabric_parquet_path(session_id,
                               receipt.outputs.get("sample_table_ref") or "")
    if path is None:
        raise RasterSamplingError(
            "RESULTS_EXPIRED", "sample table ref unresolvable",
            field="sample_table_ref", status_code=410)
    rows_page, total, pagination_facts = await asyncio.to_thread(
        _read_page, str(path), page, max(1, min(int(per_page), 500)),
        source_ref, band, valid)
    out: Dict[str, Any] = {
        "success": True,
        "execution_id": receipt.execution_id,
        "plan_id": receipt.plan_id,
        "receipt": receipt_summary(receipt),
        "rows": rows_page,
        "pagination": pagination_facts,
    }
    if include_curves:
        curves = await asyncio.to_thread(
            _build_curves_payload, str(path))
        out["curves"] = curves
        out["station_layer_ref"] = receipt.outputs.get("station_layer_ref") or ""
        out["band_columns"] = receipt.outputs.get("band_columns") or {}
    return out


def _read_page(
    path: str, page: int, per_page: int,
    source_ref: str, band: Optional[int], valid: Optional[bool],
) -> Tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    total = table.num_rows
    if source_ref:
        mask = table.column("source_ref").to_pylist()
        keep = [i for i, v in enumerate(mask) if v == source_ref]
        table = table.take(keep)
    if band is not None:
        mask = table.column("band").to_pylist()
        keep = [i for i, v in enumerate(mask) if int(v) == int(band)]
        table = table.take(keep)
    if valid is not None:
        mask = table.column("valid").to_pylist()
        keep = [i for i, v in enumerate(mask) if bool(v) == bool(valid)]
        table = table.take(keep)
    filtered = table.num_rows
    start = max(0, (page - 1) * per_page)
    end = min(filtered, start + per_page)
    page_table = table.slice(start, end - start)
    rows = page_table.to_pylist()
    facts = {
        "page": page, "per_page": per_page,
        "filtered_rows": filtered, "total_rows": total,
        "filters": {"source_ref": source_ref, "band": band, "valid": valid},
        "truncated": False,
    }
    return rows, filtered, facts


def _build_curves_payload(path: str) -> Dict[str, Any]:
    raw = publish.load_rows_for_curves(path)
    return publish.build_display_curves(raw)


async def cancel(
    *,
    session_id: str,
    plan_id: str,
    owner_token: Optional[str] = None,
) -> Dict[str, Any]:
    from app.services.data_operations.runner import find_active_jobs, _sync_session
    from app.services.jobs.store import DurableJobStore

    hits = find_active_jobs(
        task_type="raster_sampling_execute",
        session_id=session_id,
        parameter_match={"plan_id": plan_id},
    )
    if not hits:
        raise RasterSamplingError(
            "EXECUTION_NOT_RUNNING",
            f"no active sampling execution for plan {plan_id}",
            field="plan_id", status_code=409)
    cancelled: List[Dict[str, Any]] = []
    for job_id, _status in hits:
        with _sync_session() as db:
            ok, st = DurableJobStore.request_cancel_sync(db, job_id)
            db.commit()
        cancelled.append({"job_id": str(job_id),
                          "requested": bool(ok), "status": st.value})
    return {"success": True, "cancelled": cancelled}
