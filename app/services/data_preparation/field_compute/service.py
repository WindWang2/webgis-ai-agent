"""字段计算工作台服务层（G19 / WP06–WP10）—— API、UI、Pi 工具的**同一业务真相**。

发布顺序（独立评审 P1-3 + 复审 P1）：
``compute → verify_anchor → 发布面探测 + 收据草稿落盘闸门 → store →
additive 登记 → 项目保存 → 已发布收据 upsert（失败则回滚 ref/清单）→ 返回``。
收据草稿在任何 store/登记之前写入且 ``published=False``（不参与幂等、不对
外可见）；稳定键先写、ring 后写并在 ring 失败时回滚，因此「收据落盘失败」
时**没有任何可见半成品**，也不会有「未发布成果被幂等命中」。仅已发布收据
对外暴露 ``output_ref``。

阶段：``describe → compile → preview → execute``。预览与全量共用
``interpreter.evaluate_rows``；CPU 密集计算经 ``asyncio.to_thread`` offload。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
import uuid
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lib.cancellation import OperationCancelled, current_token

from app.services.data_preparation.field_compute import receipt as store
from app.services.data_preparation.field_compute.contracts import (
    FUNCTION_SET_VERSION, IR_VERSION, MAX_BATCH_ROWS, MAX_PREVIEW_ROWS,
    MAX_ROW_OPS, MAX_ROWS, ColumnResult, CompileError,
    ComputePlan, ComputeReceipt, ExecError, ScopeSpec,
)
from app.services.data_preparation.field_compute.functions import (
    function_help, node_count, static_type, static_unit, uses_geometry,
)
from app.services.data_preparation.field_compute.geometry import (
    gate_reason_detail, geometry_gate,
)
from app.services.data_preparation.field_compute.interpreter import (
    BatchResult, ColumnStats, EvalConfig, RowAbort, evaluate_rows,
)
from app.services.data_preparation.field_compute.parser import (
    CompileContext, compile_columns, render_expression,
)
from app.services.data_preparation.field_compute.scope import resolve_scope
from app.services.data_preparation.field_compute.snapshot import (
    build_anchor, build_output_payload, canonical_hash, extract_rows, freeze,
    parse_payload, verify_anchor,
)
from app.services.data_preparation.field_compute.units import describe as unit_describe

logger = logging.getLogger(__name__)

MAX_ESTIMATED_OPS = 5_000_000

#: 弱引用锁表：锁对象在最后一次 execute 结束后可被 GC，避免长驻进程无界累积。
_EXEC_LOCKS: "weakref.WeakValueDictionary[str, asyncio.Lock]" = \
    weakref.WeakValueDictionary()
#: 活跃执行登记（execute 入口 +1 → finally -1）；同一计划可能有排队执行，
#: 用计数而不是集合，避免先完成的调用把仍在执行/排队的登记摘掉。
_ACTIVE_EXEC: "Dict[str, int]" = {}
#: 协作取消请求：key → 登记时刻（单调查时钟）。带 TTL 且只对活跃执行登记，
#: 避免「对已结束计划取消」把 key 永久留在集合里、并让同一计划的后续重提
#: 第一批就误判 CANCELLED（独立复审 P2）。
_CANCEL_REQUESTS: "Dict[str, float]" = {}
_CANCEL_TTL_SECONDS = 300.0


def _exec_lock(session_id: str, fingerprint: str) -> "asyncio.Lock":
    key = f"{session_id}:{fingerprint}"
    lock = _EXEC_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _EXEC_LOCKS[key] = lock
    return lock


def _cancel_key(session_id: str, plan_id: str) -> str:
    return f"{session_id}:{plan_id}"


def _prune_cancel_requests(now: Optional[float] = None) -> None:
    """丢弃过期取消请求（防御性：正常路径由 execute 的 finally 清理）。

    有活跃执行的 key 不剪枝 —— 长任务不能被 TTL 静默丢掉取消意图。
    """
    cutoff = (time.monotonic() if now is None else now) - _CANCEL_TTL_SECONDS
    stale = [k for k, ts in _CANCEL_REQUESTS.items()
             if ts < cutoff and _ACTIVE_EXEC.get(k, 0) <= 0]
    for k in stale:
        _CANCEL_REQUESTS.pop(k, None)


def is_executing(session_id: str, plan_id: str) -> bool:
    return _ACTIVE_EXEC.get(_cancel_key(session_id, plan_id), 0) > 0


def _mark_active(key: str) -> None:
    _ACTIVE_EXEC[key] = _ACTIVE_EXEC.get(key, 0) + 1


def _unmark_active(key: str) -> None:
    remaining = _ACTIVE_EXEC.get(key, 0) - 1
    if remaining > 0:
        _ACTIVE_EXEC[key] = remaining
    else:
        _ACTIVE_EXEC.pop(key, None)


def request_cancel(session_id: str, plan_id: str) -> bool:
    """登记协作取消请求；仅对**正在执行**的计划生效（返回 False 表示无活跃执行）。"""
    key = _cancel_key(session_id, plan_id)
    _prune_cancel_requests()
    if _ACTIVE_EXEC.get(key, 0) <= 0:
        return False
    _CANCEL_REQUESTS[key] = time.monotonic()
    return True


def _check_cancel(key: str = "") -> None:
    _prune_cancel_requests()
    if key and key in _CANCEL_REQUESTS:
        raise OperationCancelled("cancelled by user request", job_id=key)
    token = current_token()
    if token is not None and token.cancelled:
        raise OperationCancelled(token.reason or "field compute cancelled",
                                 job_id=token.job_id)


async def _payload(session_id: str, ref: str) -> Any:
    from app.services.session_data import session_data_manager
    payload = await session_data_manager.get(session_id, ref)
    if payload is None:
        raise ExecError("INPUT_REF_MISSING", ref)
    return payload


async def _descriptor_fingerprint(session_id: str, ref: str) -> str:
    try:
        from app.services.dataset_semantics import get_dataset_semantic_store
        rec = await get_dataset_semantic_store().get(session_id, ref)
        if rec.ok and rec.descriptor is not None:
            return rec.descriptor.descriptor_fingerprint or ""
    except Exception:  # noqa: BLE001 — additive evidence
        pass
    return ""


async def _selection_payload(session_id: str, scope: ScopeSpec) -> Any:
    if scope.mode != "selection" or not scope.selection_ref:
        return None
    from app.services.session_data import session_data_manager
    return await session_data_manager.get(session_id, scope.selection_ref)


def _infer_types(rows: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for r in rows[:200]:
        for k, v in r.items():
            if v is None:
                t = "any"
            elif isinstance(v, bool):
                t = "boolean"
            elif isinstance(v, (int, float)):
                t = "number"
            elif isinstance(v, str):
                t = "string"
            else:
                t = "any"
            prev = out.get(k)
            if prev is None:
                out[k] = t
            elif prev != t:
                out[k] = "any"
    return out


def _scope_features(rows: Sequence[Tuple[Dict[str, Any], Any]]
                    ) -> List[Dict[str, Any]]:
    return [{"properties": props} for props, _ in rows]


# ── describe ───────────────────────────────────────────────────────────────

async def describe_source(session_id: str, source_ref: str) -> Dict[str, Any]:
    """有界源描述：schema/字段/单位提示/函数/范围/预算/CRS 资格（无数据本体）。"""
    payload = await _payload(session_id, source_ref)
    shape, rows, crs, crs_source, _meta = parse_payload(payload)
    types = _infer_types(rows)
    fields = []
    for name in sorted({k for r in rows for k in r.keys()}):
        fields.append({
            "name": name,
            "dtype": types.get(name, "any"),
            "unit_hint": "",              # 字段名不作可信单位证据
            "unit_confidence": "undeclared",
            "sample": [r.get(name) for r in rows[:3] if name in r][:3],
        })
    mode, reason = geometry_gate(crs, crs_source)
    return {
        "input_ref": source_ref, "data_shape": shape, "row_count": len(rows),
        "crs": crs, "crs_source": crs_source,
        "fields": fields[:64], "sample_rows": [dict(r) for r in rows[:5]],
        "function_set_version": FUNCTION_SET_VERSION, "ir_version": IR_VERSION,
        "functions": function_help(),
        "scope_modes": sorted({"all_rows", "selection", "predicate"}),
        "error_policies": ["null", "abort"],
        "limits": {"max_rows": MAX_ROWS, "max_columns": 8,
                   "max_ops_per_row": MAX_ROW_OPS,
                   "preview_rows": MAX_PREVIEW_ROWS},
        "geometry": {"mode": mode, "block_reason": reason or None,
                     "detail": gate_reason_detail(mode, reason, crs)},
        "unit_declaration_required": True,
        "note": "字段名只是提示；科学单位必须由 field_units 显式声明（user-declared）",
    }


# ── compile ────────────────────────────────────────────────────────────────

async def create_plan(req: Dict[str, Any]) -> Dict[str, Any]:
    """编译 + 版本锚 + 落盘（不执行、不产出公开 ref）。"""
    session_id = str(req.get("session_id") or "")
    source_ref = str(req.get("source_ref") or "")
    owner = str(req.get("owner") or "anonymous")
    if not session_id or not source_ref.startswith("ref:"):
        raise CompileError("INVALID_PREDICATE", "session_id / source_ref(ref:) 必填")
    payload = await _payload(session_id, source_ref)
    frozen = freeze(payload)
    shape, rows, crs, crs_source, _meta = parse_payload(frozen)
    fp_desc = await _descriptor_fingerprint(session_id, source_ref)
    anchor = build_anchor(frozen, source_ref, fp_desc)

    scope = ScopeSpec.model_validate(req.get("scope") or {})
    rows_with_geom, _ = extract_rows(frozen)
    feats = _scope_features(rows_with_geom)
    selection_payload = await _selection_payload(session_id, scope)
    mask, scope_summary = resolve_scope(
        scope, feats, anchor.available_fields, selection_payload)

    ctx = CompileContext(
        available_fields=anchor.available_fields,
        field_types=_infer_types(rows),
        field_units=dict(req.get("field_units") or {}),
        timezone=str(req.get("timezone") or "UTC"),
        date_format=str(req.get("date_format") or ""), crs=crs,
    )
    columns, order = compile_columns(req.get("columns") or [], ctx)
    estimated_ops = len(rows) * sum(node_count(c.expression) for c in columns)
    if estimated_ops > MAX_ESTIMATED_OPS:
        raise CompileError("BUDGET_EXCEEDED",
                           f"预估操作数 {estimated_ops} > {MAX_ESTIMATED_OPS}",
                           hint="缩小作用范围或减少计算列")

    plan = ComputePlan(
        plan_id=f"fc-{uuid.uuid4().hex[:16]}",
        session_id=session_id, owner=owner, input_ref=source_ref,
        anchor=anchor, scope=scope, columns=columns,
        field_units=dict(req.get("field_units") or {}),
        timezone=ctx.timezone, date_format=ctx.date_format,
        error_policy=str(req.get("error_policy") or "null"),
        created_at=time.time(),
    )
    plan.compute_fingerprint = plan.compute_hash()
    if not await store.save_plan(plan):
        raise ExecError("WRITE_FAILED", "计划台账落盘失败")
    mode, reason = geometry_gate(crs, crs_source)
    return {
        "plan": plan, "data_shape": shape, "column_order": order,
        "scope_summary": scope_summary,
        "geometry": {"mode": mode, "block_reason": reason or None,
                     "detail": gate_reason_detail(mode, reason, crs)},
        "estimated_ops": estimated_ops,
        "warnings": _compile_warnings(columns, crs, mode, reason),
    }


def _compile_warnings(columns, crs: str, mode: str, reason: str) -> List[str]:
    out: List[str] = []
    if reason and any(uses_geometry(c.expression) for c in columns):
        out.append(f"几何列被阻断：{gate_reason_detail(mode, reason, crs)}"
                   "（其他不依赖几何的列仍可执行）")
    return out


# ── 预览 / 执行共用 ────────────────────────────────────────────────────────

async def _prepare(plan: ComputePlan, session_id: str
                   ) -> Tuple[Any, List[Tuple[Dict[str, Any], Any]],
                              List[bool], Dict[str, Any], str, str, str]:
    payload = await _payload(session_id, plan.input_ref)
    _shape, _rows, crs, crs_source, _meta = parse_payload(payload)
    now = build_anchor(payload, plan.input_ref)
    if (now.content_hash != plan.anchor.content_hash
            or now.schema_fingerprint != plan.anchor.schema_fingerprint):
        raise ExecError("STALE_PLAN",
                        "预览后源/schema 已改变 —— 旧计划被拒绝，请刷新预览")
    frozen = freeze(payload)
    rows_with_geom, _ = extract_rows(frozen)
    feats = _scope_features(rows_with_geom)
    selection_payload = await _selection_payload(session_id, plan.scope)
    mask, scope_summary = resolve_scope(
        plan.scope, feats, plan.anchor.available_fields, selection_payload)
    return frozen, rows_with_geom, mask, scope_summary, crs, crs_source, _shape


def _eval_config(plan: ComputePlan, crs: str, crs_source: str) -> EvalConfig:
    mode, reason = geometry_gate(crs, crs_source)
    field_units = dict(plan.field_units)
    for c in plan.columns:
        if c.declared_unit:
            field_units[c.name] = c.declared_unit
    return EvalConfig(
        field_units=field_units,
        column_units={c.name: c.declared_unit for c in plan.columns},
        timezone=plan.timezone, date_format=plan.date_format,
        error_policy=plan.error_policy,
        geometry_mode=mode, geometry_block_reason=reason, crs=crs,
        unselected_policy=plan.scope.unselected_policy,
    )


def _column_meta(plan: ComputePlan) -> List[Dict[str, Any]]:
    out = []
    for c in plan.columns:
        info = static_unit(c.expression, plan.field_units,
                           declared_column_units={
                               x.name: x.declared_unit for x in plan.columns})
        out.append({
            "name": c.name, "expression": c.expression.canonical(),
            "formula": render_expression(c.expression),
            "dtype": static_type(c.expression, {}),
            "unit": info.unit, "unit_status": info.status,
            "unit_dimension": info.dimension,
            "measurement_kind": info.measurement_kind,
            "derived_from": dict(info.derived_from),
            "declared_unit": c.declared_unit,
        })
    return out


async def preview(plan_id: str, session_id: str, *, generation: int = 0
                  ) -> Dict[str, Any]:
    """采样预览（sample/estimate 明确标注；不落任何 store、不产出公开 ref）。"""
    plan = await store.get_plan(session_id, plan_id)
    if plan is None:
        raise ExecError("PLAN_NOT_FOUND", plan_id)
    # cancelled 是可恢复态（与 execute 一致）：取消后仍可刷新预览再执行；
    # 只有 executed / rejected 是终态。
    if plan.state in ("executed", "rejected"):
        raise ExecError("PLAN_REJECTED", f"计划状态 {plan.state}")
    _frozen, rows_wg, mask, scope_summary, crs, crs_source, shape = await _prepare(
        plan, session_id)
    sample = rows_wg[:MAX_PREVIEW_ROWS]
    cfg = _eval_config(plan, crs, crs_source)
    batch = await asyncio.to_thread(
        evaluate_rows, sample, list(plan.columns), mask[:len(sample)], cfg,
        batch_size=min(len(sample), MAX_BATCH_ROWS))
    columns_meta = _column_meta(plan)
    before_after = []
    for i in range(min(10, len(sample))):
        before = {k: sample[i][0].get(k) for k in
                  list(plan.anchor.available_fields)[:8]}
        after = {c.name: batch.rows[i].get(c.name) for c in plan.columns}
        before_after.append({"row": i, "before": before, "after": after})
    total_errors = sum(s.errors for s in batch.stats.values())
    total_nulls = sum(s.nulls for s in batch.stats.values())
    degraded: List[str] = []
    if cfg.geometry_block_reason and any(
            uses_geometry(c.expression) for c in plan.columns):
        degraded.append("GEOMETRY_BLOCKED")
    plan.state = "previewed"
    await store.save_plan(plan)
    return {
        "plan_id": plan.plan_id, "generation": generation,
        "compute_fingerprint": plan.compute_fingerprint,
        "kind": "sample", "sampled_rows": len(sample),
        "total_rows": len(rows_wg), "truncated": len(rows_wg) > len(sample),
        "data_shape": shape,
        "columns": [{**m, "computed": batch.stats[m["name"]].computed,
                     "nulls": batch.stats[m["name"]].nulls,
                     "errors": batch.stats[m["name"]].errors}
                    for m in columns_meta],
        "before_after": before_after,
        "null_count_sample": total_nulls,
        "error_count_sample": total_errors,
        "error_samples": [e for s in batch.stats.values()
                          for e in s.samples][:8],
        "sample_error_rate_estimate": (
            round(total_errors / max(len(sample) * len(plan.columns), 1), 6)),
        "scope": scope_summary,
        "unit_summary": {m["name"]: unit_describe(
            m["unit"], m["unit_status"], m["unit_dimension"])
            for m in columns_meta},
        "precision_policy": batch.precision_policy,
        "geometry": batch.geometry_evidence,
        "budget": {
            "row_count": len(rows_wg),
            "estimated_ops": len(rows_wg) * sum(
                node_count(c.expression) for c in plan.columns),
            "max_rows": MAX_ROWS, "max_ops_per_row": MAX_ROW_OPS,
            "within_budget": len(rows_wg) <= MAX_ROWS,
        },
        "degraded": degraded,
        "confirmation": {
            "params_fingerprint": plan.compute_fingerprint,
            "note": "确认执行的参数必须与本次预览的 params_fingerprint 一致；"
                    "表单/范围/单位任一变更都会使旧预览失效",
        },
        "next_actions": ["execute", "refresh_preview", "edit_expression"],
    }


async def execute(plan_id: str, session_id: str, *, project_id: str = "",
                  owner: str = "", expected_fingerprint: str = ""
                  ) -> ComputeReceipt:
    """有界批执行 → 原子派生登记（取消/失败不发布半成品）。

    ``owner`` 是**提示性元数据**：数据隔离由 session 归属与 ref 命名空间保证，
    不把它当权限门禁（跨 UI / Pi 入口复用同一 plan 不得被误拒）。
    """
    plan = await store.get_plan(session_id, plan_id)
    if plan is None:
        raise ExecError("PLAN_NOT_FOUND", plan_id)
    # cancelled 是可恢复态：取消请求在 finally 中清理，用户可原计划重提（正确恢复）；
    # rejected（行级 abort）是终态，不自动复活。
    if plan.state == "rejected":
        raise ExecError("PLAN_REJECTED", f"计划状态 {plan.state}")
    if expected_fingerprint and expected_fingerprint != plan.compute_fingerprint:
        raise ExecError("STALE_PLAN", "确认参数与已预览参数不一致（generation 失效）")

    cancel_key = _cancel_key(session_id, plan.plan_id)
    # 活跃登记在取锁之前：排队等待期间也允许用户取消；用计数保证先完成的
    # 调用不会摘掉仍在排队/执行的登记，执行全部结束后才摘除并清理取消键。
    _mark_active(cancel_key)
    try:
        async with _exec_lock(session_id, plan.compute_fingerprint):
            return await _execute_locked(plan, session_id,
                                         project_id=project_id,
                                         cancel_key=cancel_key)
    finally:
        _unmark_active(cancel_key)
        if not is_executing(session_id, plan.plan_id):
            _CANCEL_REQUESTS.pop(cancel_key, None)


async def _maybe_upgrade_project(existing: ComputeReceipt, session_id: str,
                                 project_id: str, plan: ComputePlan
                                 ) -> ComputeReceipt:
    """幂等命中后补做项目保存（修「先生成、后决定保存项目」旅程）。"""
    if not project_id or existing.persistence == "project_artifact":
        return existing
    ok = await save_to_project(session_id, existing.output_ref, project_id, plan)
    if not ok:
        degraded = tuple(existing.degraded)
        if "PROJECT_SAVE_FAILED" not in degraded:
            degraded += ("PROJECT_SAVE_FAILED",)
        updated = existing.model_copy(update={"degraded": degraded})
        # 回写台账：之后的重开/查询也要看到真实降级，而不是只在本次响应里。
        if not await store.append_receipt(session_id, updated):
            logger.warning("[field_compute] degraded receipt not persisted for "
                           "%s", updated.output_ref)
        return updated
    # 升级成功必须清掉早前失败留下的 PROJECT_SAVE_FAILED，否则 UI 会一边显示
    # 「已持久化到项目」一边显示「保存失败」（独立复审 P2）。
    cleaned = tuple(d for d in existing.degraded if d != "PROJECT_SAVE_FAILED")
    updated = existing.model_copy(
        update={"persistence": "project_artifact", "degraded": cleaned})
    if not await store.append_receipt(session_id, updated):
        logger.warning("[field_compute] project upgrade receipt not persisted "
                       "for %s", updated.output_ref)
    return updated


async def _execute_locked(plan: ComputePlan, session_id: str,
                          *, project_id: str, cancel_key: str = ""
                          ) -> ComputeReceipt:
    cancel_key = cancel_key or _cancel_key(session_id, plan.plan_id)
    try:
        current = await _payload(session_id, plan.input_ref)
        content_hash = canonical_hash(current)
        existing = await store.find_receipt(
            session_id, plan.compute_fingerprint, plan.input_ref, content_hash)
        if existing is not None:
            # 取消意图优先于幂等短路：排队执行期间用户已取消时，不能因为
            # 「成果已存在」就把这次提交判成成功（否则取消被静默吞掉）。
            try:
                _check_cancel(cancel_key)
            except OperationCancelled as exc:
                plan.state = "cancelled"
                await store.save_plan(plan)
                raise ExecError("CANCELLED", str(exc)[:200]) from exc
            return await _maybe_upgrade_project(
                existing, session_id, project_id, plan)

        frozen, rows_wg, mask, scope_summary, crs, crs_source, _shape = \
            await _prepare(plan, session_id)
        cfg = _eval_config(plan, crs, crs_source)
        columns = list(plan.columns)

        # ① compute：分批 offload + 每批协作取消。
        new_rows: List[Dict[str, Any]] = []
        stats: Dict[str, ColumnStats] = {c.name: ColumnStats() for c in columns}
        precision = ""
        geometry_evidence: Dict[str, Any] = {}
        computed_rows = 0
        try:
            for start in range(0, len(rows_wg), MAX_BATCH_ROWS):
                _check_cancel(cancel_key)
                chunk = rows_wg[start:start + MAX_BATCH_ROWS]
                chunk_mask = mask[start:start + len(chunk)]
                batch: BatchResult = await asyncio.to_thread(
                    evaluate_rows, chunk, columns, chunk_mask, cfg,
                    batch_size=len(chunk))
                new_rows.extend(batch.rows)
                computed_rows += batch.computed_rows
                for name, st in batch.stats.items():
                    acc = stats[name]
                    acc.computed += st.computed
                    acc.nulls += st.nulls
                    acc.errors += st.errors
                    acc.samples.extend(st.samples)
                precision = precision or batch.precision_policy
                geometry_evidence = batch.geometry_evidence
        except OperationCancelled as exc:
            plan.state = "cancelled"
            await store.save_plan(plan)
            raise ExecError("CANCELLED", str(exc)[:200]) from exc
        except RowAbort as exc:
            plan.state = "rejected"
            await store.save_plan(plan)
            raise ExecError("ABORTED_BY_ROW_ERROR",
                            f"row {exc.row_index} / {exc.column}: {exc.code}") from exc
        if len(new_rows) != len(rows_wg):
            raise ExecError("WRITE_FAILED", "结果行数与输入行数不一致")

        # ② 发布前二次版本核对。
        latest = await _payload(session_id, plan.input_ref)
        verify_anchor(latest, plan.anchor)

        subset = mask if plan.scope.subset_export else None
        output_payload = build_output_payload(frozen, new_rows, subset=subset)
        column_results = _column_results(plan, stats)

        def _make_receipt(output_ref: str, published: bool,
                          persistence: str, degraded: Tuple[str, ...],
                          minted: bool) -> ComputeReceipt:
            return ComputeReceipt(
                plan_id=plan.plan_id,
                compute_fingerprint=plan.compute_fingerprint,
                input_ref=plan.input_ref, output_ref=output_ref,
                input_content_hash=canonical_hash(frozen),
                output_content_hash=canonical_hash(output_payload),
                row_count=len(rows_wg), computed_rows=computed_rows,
                selected_rows=sum(1 for s in mask if s),
                skipped_rows=sum(1 for s in mask if not s),
                null_count=sum(s.nulls for s in stats.values()),
                error_count=sum(s.errors for s in stats.values()),
                columns=tuple(column_results), scope=scope_summary,
                geometry=geometry_evidence,
                formula_summary=tuple(
                    f"{m['name']} = {m['formula']}" for m in _column_meta(plan)),
                unit_summary={m["name"]: unit_describe(
                    m["unit"], m["unit_status"], m["unit_dimension"])
                    for m in _column_meta(plan)},
                precision_policy=precision, persistence=persistence,
                descriptor_minted=minted, degraded=degraded,
                published=published, created_at=time.time(),
            )

        # ③ 收据草稿闸门：必须在 store / 任何登记之前，且 published=False。
        # 先探测 ring 与 by_key 两个写入面（复审 P1：只探测 ring 时最终 upsert
        # 仍可能在稳定键目录失败），再落草稿。
        if not await store.probe_publish(session_id):
            raise ExecError("WRITE_FAILED",
                            "收据目录不可写 —— 未写入任何成果（无可见半成品）")
        draft = _make_receipt("", False, "session_ref", (), False)
        if not await store.append_receipt(session_id, draft):
            raise ExecError("WRITE_FAILED",
                            "收据草稿落盘失败 —— 未写入任何成果（无可见半成品）")

        # ④ write：一次性写入新 ref（原件绝不覆写）。
        from app.services.session_data import session_data_manager
        output_ref = await session_data_manager.store(
            session_id, output_payload, prefix="fc")
        if not output_ref:
            raise ExecError("STORE_FAILED", "session store 拒绝派生成果")

        # ⑤ additive 登记（不含项目保存）。
        minted, degraded = await _register_output(
            session_id, plan, output_ref, output_payload, crs,
            cfg.geometry_block_reason, precision)

        # ⑥ 项目保存（在最终收据之前）：失败必须降级 persistence，不谎称可找回。
        project_saved = False
        if project_id:
            project_saved = await save_to_project(
                session_id, output_ref, project_id, plan)
            if not project_saved:
                degraded.append("PROJECT_SAVE_FAILED")
        persistence = ("project_artifact" if project_id and project_saved
                       else "session_ref")

        # ⑦ 已发布收据 upsert（同键替换草稿）。失败即回滚已写入的可见面：
        # 稳定键先写、ring 失败时已由 receipt 层删除该键，因此「未发布成果」
        # 不会通过幂等被命中；这里再撤掉 session ref 与项目清单条目。
        final = _make_receipt(output_ref, True, persistence,
                              tuple(degraded), minted)
        if not await store.append_receipt(session_id, final):
            residual = await _rollback_unpublished(
                session_id, output_ref, project_id, final)
            logger.warning("[field_compute] final receipt persist failed; "
                           "rolled back %s (residual=%s)", output_ref, residual)
            raise ExecError(
                "WRITE_FAILED",
                "收据发布失败 —— 成果已回滚" if not residual else
                "收据发布失败 —— 回滚不完整（残留：%s）" % ",".join(residual))
        plan.state = "executed"
        await store.save_plan(plan)
        return final
    finally:
        # 只有「本次是最后一个活跃执行」时才清理取消键：同一计划排队执行时
        # 保留用户已登记的取消，交给队列中的下一次执行检查。
        if _ACTIVE_EXEC.get(cancel_key, 0) <= 1:
            _CANCEL_REQUESTS.pop(cancel_key, None)


async def _rollback_unpublished(session_id: str, output_ref: str,
                                project_id: str, receipt: ComputeReceipt
                                ) -> List[str]:
    """撤销未发布成果的全部可见面（稳定键 + session ref + 项目清单条目）。

    §3 的 additive 登记（descriptor/provenance/artifact）没有删除 API，属
    只追加证据；它们指向已删除的 ref，不构成可读成果 —— 真实限制在
    PR 正文与 `REVIEW.md` 披露，不声称「完全无痕」。
    """
    residual: List[str] = []
    if not await store.drop_published(session_id, receipt):
        residual.append("STABLE_KEY_ROLLBACK_FAILED")
    try:
        from app.services.session_data import session_data_manager
        if not await session_data_manager.delete_ref(session_id, output_ref):
            residual.append("REF_DELETE_FAILED")
    except Exception:  # noqa: BLE001
        logger.warning("[field_compute] ref rollback failed", exc_info=True)
        residual.append("REF_DELETE_FAILED")
    if project_id:
        try:
            removed = await asyncio.to_thread(
                _remove_from_project_sync,
                _manifest_path(session_id, project_id),
                project_id, output_ref)
            if not removed:
                residual.append("MANIFEST_ROLLBACK_FAILED")
        except Exception:  # noqa: BLE001
            logger.warning("[field_compute] manifest rollback failed",
                           exc_info=True)
            residual.append("MANIFEST_ROLLBACK_FAILED")
    return residual


def _column_results(plan: ComputePlan,
                    stats: Dict[str, ColumnStats]) -> List[ColumnResult]:
    out = []
    for m in _column_meta(plan):
        st = stats[m["name"]]
        out.append(ColumnResult(
            name=m["name"], dtype=m["dtype"], unit=m["unit"],
            unit_status=m["unit_status"],
            measurement_kind=m["measurement_kind"],
            derived_from=m["derived_from"],
            dependencies=tuple(_deps_of(plan, m["name"])),
            expression=m["expression"], computed=st.computed,
            nulls=st.nulls, errors=st.errors,
            error_samples=tuple(st.samples[:8])))
    return out


def _deps_of(plan: ComputePlan, name: str) -> List[str]:
    from app.services.data_preparation.field_compute.functions import dependencies
    for c in plan.columns:
        if c.name == name:
            return list(dependencies(c.expression))
    return []


async def _register_output(session_id: str, plan: ComputePlan, output_ref: str,
                           output_payload: Any, crs: str,
                           geometry_block_reason: str, precision: str
                           ) -> Tuple[bool, List[str]]:
    """既有 descriptor / provenance / artifact 入口（增值面，失败不阻断）。"""
    degraded: List[str] = []
    minted = False
    try:
        from app.lib.data.profile import DatasetProfileV3, profile_features
        from app.lib.gis.dataset_descriptor import SourceRef
        from app.services.dataset_semantics import (
            derive_descriptor_from_v3, get_dataset_semantic_store,
        )
        _shape, rows, _c, _s, _m = parse_payload(output_payload)
        vp, _q = profile_features(
            [{"type": "Feature", "geometry": None, "properties": r} for r in rows],
            crs=crs)
        out_profile = DatasetProfileV3(target_ref=output_ref, category="vector",
                                       crs=crs, extent=vp.extent, vector=vp)
        descriptor = derive_descriptor_from_v3(
            out_profile, dataset_key=output_ref, features=[],
            source_refs=[SourceRef(type="ref", ref=plan.input_ref,
                                   fingerprint=plan.anchor.descriptor_fingerprint)],
            provenance=[{"producer": "field_compute",
                         "method": f"plan:{plan.compute_fingerprint}"}],
        )
        put = await get_dataset_semantic_store().put(
            session_id, output_ref, descriptor.with_fingerprints())
        minted = bool(put.ok)
        if not minted:
            degraded.append("DESCRIPTOR_MINT_SKIPPED")
    except Exception as exc:  # noqa: BLE001
        degraded.append("DESCRIPTOR_MINT_SKIPPED")
        logger.warning("[field_compute] descriptor mint skipped: %s", exc)
    try:
        from app.services.dataset_semantics.provenance import record_transformation
        await record_transformation(
            session_id, output_ref=output_ref,
            output_fingerprint=plan.anchor.descriptor_fingerprint,
            inputs=[{"ref": plan.input_ref,
                     "descriptor_fingerprint": plan.anchor.descriptor_fingerprint}],
            algorithm="field_compute", algorithm_version=FUNCTION_SET_VERSION,
            parameters=plan.compute_fingerprint,
        )
    except Exception:  # noqa: BLE001
        degraded.append("PROVENANCE_SKIPPED")
    try:
        from app.services.artifact_registry import register_artifact
        await register_artifact(
            session_id, artifact_id=output_ref, artifact_type="dataset",
            producer_tool="compute_fields", inputs=[plan.input_ref],
            metadata={
                "field_compute_plan_id": plan.plan_id,
                "compute_fingerprint": plan.compute_fingerprint,
                "ir_version": IR_VERSION,
                "function_set_version": FUNCTION_SET_VERSION,
                "formula_summary": [f"{c.name} = {render_expression(c.expression)}"
                                    for c in plan.columns],
                "scope": plan.scope.canonical(),
                "precision_policy": precision,
            },
        )
    except Exception:  # noqa: BLE001
        degraded.append("ARTIFACT_REGISTER_SKIPPED")
    if geometry_block_reason:
        degraded.append(f"GEOMETRY_BLOCKED:{geometry_block_reason}")
    if precision:
        degraded.append(f"PRECISION:{precision}")
    return minted, degraded


# ── 项目持久化（按 project 独立清单，不截断、不静默丢历史）─────────────────

def _manifest_path(session_id: str, project_id: str) -> Path:
    from app.services.mapspec.store import BASE_STORAGE_DIR
    root = BASE_STORAGE_DIR.resolve()
    d = (root / str(session_id)).resolve()
    if d.parent != root:
        raise ValueError("invalid session id for project manifest")
    key = hashlib.sha256(str(project_id).encode("utf-8")).hexdigest()[:24]
    return d / "field_compute" / "projects" / f"{key}.jsonl"


def _append_manifest_sync(path: Path, record: Dict[str, Any]) -> None:
    records: List[Dict[str, Any]] = []
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception:  # noqa: BLE001
            records = []
    records = [r for r in records
               if not (r.get("project_id") == record.get("project_id")
                       and r.get("output_ref") == record.get("output_ref"))]
    records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in records:              # 不截断：重开找回不能静默丢历史
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


async def save_to_project(session_id: str, output_ref: str, project_id: str,
                          plan: ComputePlan) -> bool:
    """项目级持久清单（真实文件 + 可回读）；失败 False 如实披露。"""
    record = {
        "project_id": project_id, "output_ref": output_ref,
        "input_ref": plan.input_ref, "plan_id": plan.plan_id,
        "compute_fingerprint": plan.compute_fingerprint,
        "formula_summary": [f"{c.name} = {render_expression(c.expression)}"
                            for c in plan.columns],
        "field_units": plan.field_units, "scope": plan.scope.canonical(),
        "timezone": plan.timezone, "error_policy": plan.error_policy,
        "input_content_hash": plan.anchor.content_hash,
        "saved_at": time.time(),
    }
    try:
        await asyncio.to_thread(_append_manifest_sync,
                                _manifest_path(session_id, project_id), record)
        return True
    except Exception:  # noqa: BLE001
        return False


def _remove_from_project_sync(path: Path, project_id: str,
                              output_ref: str) -> bool:
    """删除单条项目记录（发布失败回滚用）；无匹配或文件不存在视为成功。"""
    if not path.exists():
        return True
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception:  # noqa: BLE001
        return False
    kept = [r for r in records
            if not (r.get("project_id") == project_id
                    and r.get("output_ref") == output_ref)]
    if len(kept) == len(records):
        return True
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in kept:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        return True
    except Exception:  # noqa: BLE001
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        return False


async def list_project_results(session_id: str, project_id: str
                               ) -> List[Dict[str, Any]]:
    """重开项目后找回公式/来源/成果 ref（不冒充 session ref 长期有效）。"""
    path = _manifest_path(session_id, project_id)
    if not path.exists():
        return []
    try:
        lines = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return [json.loads(line) for line in lines.splitlines()
                if line.strip() and json.loads(line).get("project_id") == project_id]
    except Exception:  # noqa: BLE001
        return []


# ── 结果与计划查询 ─────────────────────────────────────────────────────────

async def get_plan(session_id: str, plan_id: str) -> ComputePlan:
    plan = await store.get_plan(session_id, plan_id)
    if plan is None:
        raise ExecError("PLAN_NOT_FOUND", plan_id)
    return plan


async def list_plans(session_id: str) -> List[ComputePlan]:
    return await store.list_plans(session_id)


async def get_result(session_id: str, output_ref: str) -> Dict[str, Any]:
    """回读验证：真实数据可读 + 字段可查 + 有界样本（不把 unknown 当通过）。"""
    payload = await _payload(session_id, output_ref)
    shape, rows, crs, _src, _meta = parse_payload(payload)
    fields = sorted({k for r in rows for k in r.keys()})
    return {
        "output_ref": output_ref, "data_shape": shape, "crs": crs,
        "row_count": len(rows), "fields": fields[:64],
        "sample_rows": [dict(r) for r in rows[:5]], "readable": True,
    }


async def discard_plan(session_id: str, plan_id: str) -> Optional[ComputePlan]:
    plan = await store.get_plan(session_id, plan_id)
    if plan is None:
        return None
    if plan.state == "executed":
        raise CompileError("INVALID_PREDICATE", "已执行计划不可作废")
    plan.state = "rejected"
    await store.save_plan(plan)
    return plan


__all__ = [
    "describe_source", "create_plan", "preview", "execute", "get_plan",
    "list_plans", "get_result", "discard_plan", "save_to_project",
    "list_project_results", "request_cancel", "is_executing",
    "MAX_ESTIMATED_OPS",
]
