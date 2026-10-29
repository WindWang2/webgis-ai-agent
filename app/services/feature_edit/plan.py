"""EditPlan —— 预览/提交的两相中枢（/goal 03）。

Preview 是纯读：解析目标 → 校验词表 → 计算 before/after diff → 绑定
(expected_content_revision, expected_fingerprint) 后落 plan store（TTL）。
Commit 由 executor 在会话锁内对 plan 做 CAS 复核——预览与提交之间载荷
可能已变，过期即 conflict，绝不盲目套用。

plan store 持久在 map_state ``_feature_edit_plans``（多 worker 共享；
决策 REST 可能落在另一 worker，进程内存不可用）。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.services.feature_edit.attr_ops import (
    build_attr_deltas,
    profile_fields,
    validate_rename_field,
    validate_set_fields,
)
from app.services.feature_edit.contracts import (
    ATTRIBUTE_OPS,
    EDIT_OPS,
    MAX_OPS_PER_PLAN,
    MAX_PLAN_TARGETS,
    MAX_SAFE_COMMIT_TARGETS,
    PLAN_STATUSES,
    PLAN_TTL_S,
    finding,
)
from app.services.feature_edit.geometry_ops import move_by, repair_geometry, snap_to_ref
from app.services.feature_edit.identity import (
    has_wfids,
    materialize_wfids,
    resolve_feature_ids,
)
from app.services.feature_edit.journal import apply_deltas_to_features, apply_schema_ops_to_features

__all__ = [
    "build_plan",
    "derive_changes",
    "needs_materialization",
    "plan_safe_classification",
    "summarize_plan",
    "extract_fc",
    "plan_expired",
]


def needs_materialization(fc: Dict[str, Any]) -> bool:
    return not has_wfids(fc)


def plan_safe_classification(
    ops: List[Dict[str, Any]], findings: List[Dict[str, Any]], n_targets: int
) -> bool:
    """safe = 免确认可自动执行（policy 放行前提下）。

    判据（保守）：纯属性词表 + 校验零 findings + 目标数 ≤ safe 上限。
    几何操作（repair/move/snap）恒不 safe —— 坐标变更必须显式确认。
    """
    if findings:
        return False
    if n_targets > MAX_SAFE_COMMIT_TARGETS:
        return False
    return all(op.get("op") in ATTRIBUTE_OPS for op in ops)


def _validate_op_shapes(ops: List[Any]) -> List[Dict[str, Any]]:
    """词表/参数形状校验（缺数值单位即拒绝 —— fail-closed）。"""
    findings: List[Dict[str, Any]] = []
    if not isinstance(ops, list) or not ops:
        return [finding("invalid_op", "plan requires at least one edit op")]
    if len(ops) > MAX_OPS_PER_PLAN:
        findings.append(finding("op_limit_exceeded", "too many ops in one plan", limit=MAX_OPS_PER_PLAN))
    for op in ops:
        if not isinstance(op, dict):
            findings.append(finding("invalid_op", "op must be an object"))
            continue
        kind = op.get("op")
        if kind not in EDIT_OPS:
            findings.append(finding("invalid_op", "unknown edit op", op=str(kind)[:32]))
            continue
        if kind == "set_fields" and not isinstance(op.get("fields"), dict):
            findings.append(finding("invalid_op", "set_fields requires fields map"))
        elif kind == "rename_field" and not (
            isinstance(op.get("from"), str) and isinstance(op.get("to"), str)
        ):
            findings.append(finding("invalid_op", "rename_field requires from/to strings"))
        elif kind == "repair_geometry":
            fixes = op.get("fixes") or ["make_valid"]
            if not isinstance(fixes, list) or not fixes:
                findings.append(finding("invalid_op", "repair_geometry requires fixes list"))
        elif kind == "move_by" and not (
            isinstance(op.get("dx_m"), (int, float)) and isinstance(op.get("dy_m"), (int, float))
        ):
            findings.append(
                finding("unit_required", "move_by requires numeric dx_m/dy_m (meters)")
            )
        elif kind == "snap_to_ref" and not (
            isinstance(op.get("target_ref"), str)
            and isinstance(op.get("tolerance_m"), (int, float))
        ):
            findings.append(
                finding(
                    "unit_required",
                    "snap_to_ref requires target_ref and numeric tolerance_m (meters)",
                )
            )
    return findings


def derive_changes(
    fc: Dict[str, Any],
    ops: List[Dict[str, Any]],
    target_wfids: List[str],
    target_payloads: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """对已物化 fc 顺序求 ops 的完整 diff（preview 与 commit 同一路径）。

    返回 (deltas, schema_ops, findings, op_summaries)。target_payloads 为
    snap 目标 ref 的预取载荷（{ref_id: payload}；缺失项出 dataset_not_found）。
    deltas 是绝对态（before=操作前值, after=操作后值），按序应用即顺序语义。
    """
    findings: List[Dict[str, Any]] = []
    deltas: List[Dict[str, Any]] = []
    schema_ops: List[Dict[str, Any]] = []
    op_summaries: List[Dict[str, Any]] = []
    working = fc
    target_set = set(target_wfids)
    field_profiles = profile_fields(working)
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in EDIT_OPS:
            continue
        kind = op.get("op")
        if kind == "set_fields":
            op_findings = validate_set_fields(op.get("fields") or {}, field_profiles)
            findings.extend(op_findings)
            if not op_findings:
                deltas.extend(build_attr_deltas(working, target_set, op.get("fields") or {}))
            op_summaries.append({"op": kind, "fields": sorted((op.get("fields") or {}).keys())})
        elif kind == "rename_field":
            rename_findings = validate_rename_field(op.get("from"), op.get("to"), field_profiles)
            findings.extend(rename_findings)
            if not rename_findings:
                schema_ops.append({"op": "rename_field", "from": op.get("from"), "to": op.get("to")})
            op_summaries.append({"op": kind, "from": op.get("from"), "to": op.get("to")})
        elif kind == "repair_geometry":
            geom_deltas, geom_findings = repair_geometry(
                working, target_set, op.get("fixes") or ["make_valid"]
            )
            findings.extend(geom_findings)
            deltas.extend(geom_deltas)
            op_summaries.append({"op": kind, "fixes": op.get("fixes") or ["make_valid"]})
        elif kind == "move_by":
            geom_deltas, geom_findings = move_by(working, target_set, op.get("dx_m"), op.get("dy_m"))
            findings.extend(geom_findings)
            deltas.extend(geom_deltas)
            op_summaries.append({"op": kind, "dx_m": op.get("dx_m"), "dy_m": op.get("dy_m")})
        elif kind == "snap_to_ref":
            target_ref = op.get("target_ref")
            if target_ref not in target_payloads:
                findings.append(finding("dataset_not_editable", "snap target ref unavailable", target_ref=str(target_ref)[:64]))
            else:
                geom_deltas, geom_findings, summary = snap_to_ref(
                    working, target_set, _extract_fc(target_payloads[target_ref]), op.get("tolerance_m")
                )
                findings.extend(geom_findings)
                deltas.extend(geom_deltas)
                op_summaries.append(
                    {"op": kind, **{k: summary[k] for k in ("snapped", "tolerance_m") if k in summary}}
                )
        # 顺序语义：把已产生的 delta 应用到工作视图，后续 op 在最新态上求值。
        if deltas or schema_ops:
            feats, _ = apply_deltas_to_features(working.get("features") or [], deltas)
            feats, _ = apply_schema_ops_to_features(feats, schema_ops)
            working = {**working, "features": feats}
    return deltas, schema_ops, findings, op_summaries


def build_plan(
    *,
    ref_id: str,
    payload: Any,
    ops: List[Dict[str, Any]],
    requested_ids: Optional[List[str]],
    selection: Optional[Dict[str, Any]],
    dataset_fingerprint: str,
    content_revision: int,
    target_payloads: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """构造 EditPlan（纯读路径）。

    payload：session ref 的原始载荷（信封或裸 FC，本函数内部归一）。
    selection：IR 投影的 selection 视图（含 dataset_ref/feature_id）。
    target_payloads：snap 目标 ref 的预取载荷。
    返回 (plan_dict, findings)；findings 非空时 plan.committable=False。
    """
    fc = _extract_fc(payload)
    if fc is None:
        return {}, [finding("dataset_not_editable", "payload is not a geojson FeatureCollection")]
    n_features = len(fc.get("features") or [])
    if n_features > 20_000:
        return {}, [
            finding("dataset_too_large_to_edit", "dataset exceeds editable size", features=n_features)
        ]

    findings: List[Dict[str, Any]] = list(_validate_op_shapes(ops))

    # ── 目标解析：显式 ids 优先，否则 selection（含 selected_ids 扩展） ──
    materialize = needs_materialization(fc)
    preview_fc = fc
    if materialize:
        # 预览阶段即对「物化后」形态求值（commit 时同样先物化），
        # 保证 preview diff 与 commit 结果一致。
        preview_fc, _ = materialize_wfids(fc, dataset_fingerprint)

    ids: List[str]
    if requested_ids:
        ids = [str(x) for x in requested_ids][:MAX_PLAN_TARGETS]
    elif selection and isinstance(selection.get("feature_id"), str):
        ids = [str(selection["feature_id"])]
        extra = selection.get("selected_ids")
        if isinstance(extra, list):
            ids.extend(str(x) for x in extra if isinstance(x, str))
        ids = list(dict.fromkeys(ids))[:MAX_PLAN_TARGETS]
    else:
        return {}, [finding("invalid_target", "no explicit ids and no live selection")]

    wfid_map = resolve_feature_ids(preview_fc, ids)
    missing = [i for i in ids if i not in wfid_map]
    if missing:
        findings.append(
            finding("feature_not_found", "requested ids not resolvable in dataset", ids=missing[:8])
        )
    wfids = sorted(set(wfid_map.values()))
    if len(wfids) > MAX_PLAN_TARGETS:
        findings.append(finding("target_limit_exceeded", "too many target features", limit=MAX_PLAN_TARGETS))

    deltas, schema_ops, derive_findings, op_summaries = derive_changes(
        preview_fc, ops if isinstance(ops, list) else [], wfids, target_payloads or {}
    )
    findings.extend(derive_findings)

    n_targets = len(wfids)
    plan: Dict[str, Any] = {
        "plan_id": f"fep-{uuid.uuid4().hex[:12]}",
        "ref_id": ref_id,
        "ops": [op for op in ops if isinstance(op, dict)][:MAX_OPS_PER_PLAN],
        "target_wfids": wfids[:MAX_PLAN_TARGETS],
        "target_ids": ids[:MAX_PLAN_TARGETS],
        "needs_materialization": materialize,
        "validation": findings,
        "committable": not findings,
        "safe": plan_safe_classification(ops if isinstance(ops, list) else [], findings, n_targets),
        "preview": _bounded_preview(deltas),
        "schema_ops": schema_ops,
        "op_summaries": op_summaries,
        "expected_content_revision": int(content_revision),
        "expected_fingerprint": dataset_fingerprint,
        "status": "pending" if not findings else "invalid",
        "created_at": time.time(),
    }
    return plan, findings


def _bounded_preview(deltas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """diff 的有界投影（属性全量 before/after；几何只给摘要 + 顶点数，不搬全量）。"""
    from app.services.feature_edit.journal import count_positions

    out: List[Dict[str, Any]] = []
    for delta in deltas[:MAX_PLAN_TARGETS]:
        item: Dict[str, Any] = {"wfid": delta.get("wfid")}
        if delta.get("fields"):
            item["fields"] = {
                k: {"before": v.get("before"), "after": v.get("after")}
                for k, v in delta["fields"].items()
            }
        if delta.get("geometry_after") is not None:
            item["geometry"] = {
                "positions_before": count_positions(delta.get("geometry_before")),
                "positions_after": count_positions(delta.get("geometry_after")),
                "bbox_after": _bbox(delta["geometry_after"]),
            }
        out.append(item)
    return out


def _bbox(geometry: Dict[str, Any]) -> Optional[List[float]]:
    xs: List[float] = []
    ys: List[float] = []

    def walk(node):
        if not isinstance(node, (list, tuple)):
            return
        if len(node) >= 2 and isinstance(node[0], (int, float)) and isinstance(node[1], (int, float)):
            xs.append(float(node[0]))
            ys.append(float(node[1]))
            return
        for c in node:
            walk(c)

    walk(geometry.get("coordinates"))
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def summarize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 可见的有界摘要（不含未编辑字段的属性值）。"""
    return {
        "plan_id": plan.get("plan_id"),
        "ref_id": plan.get("ref_id"),
        "status": plan.get("status"),
        "committable": plan.get("committable"),
        "safe": plan.get("safe"),
        "n_targets": len(plan.get("target_wfids") or []),
        "ops": plan.get("op_summaries") or [],
        "validation": plan.get("validation") or [],
        "expected_content_revision": plan.get("expected_content_revision"),
        "changed_features": len(plan.get("preview") or []),
        "schema_ops": [
            {"from": s.get("from"), "to": s.get("to")} for s in plan.get("schema_ops") or []
        ],
    }


def _extract_fc(data: Any) -> Optional[Dict[str, Any]]:
    fc = data
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict):
            fc = nested
    if isinstance(fc, dict) and fc.get("type") == "FeatureCollection":
        return fc
    return None


def extract_fc(data: Any) -> Optional[Dict[str, Any]]:
    """公开信封归一（executor/routes 复用）。"""
    return _extract_fc(data)


def plan_expired(plan: Dict[str, Any], now: Optional[float] = None) -> bool:
    return (now or time.time()) - float(plan.get("created_at") or 0) > PLAN_TTL_S


def plan_status_valid(status: Any) -> bool:
    return status in PLAN_STATUSES or status == "invalid"
