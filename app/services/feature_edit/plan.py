"""EditPlan —— 预览/提交的两相中枢（/goal 03）。

Preview 是纯读：解析目标 → 校验词表 → 计算 before/after diff → 绑定
(expected_content_revision, expected_fingerprint) 后落 plan store（TTL）。
Commit 由 executor 在会话锁内对 plan 做 CAS 复核——预览与提交之间载荷
可能已变，过期即 conflict，绝不盲目套用。

plan store 持久在 map_state ``_feature_edit_plans``（多 worker 共享；
决策 REST 可能落在另一 worker，进程内存不可用）。
"""
from __future__ import annotations

from app.contracts.geojson import GeoJSONFeatureCollection, GeoJSONGeometry

from app.services.feature_edit.wire_types import EditFinding, FeatureDelta, RenameField, OperationSummary, DeltaPreview, SelectionView, PlanSummary

from pydantic import JsonValue
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple, cast

from app.services.feature_edit.attr_ops import (
    build_attr_deltas,
    profile_fields,
    validate_rename_field,
    validate_set_fields,
)
from app.services.feature_edit.contracts import (
    EDIT_OPS,
    MAX_CREATED_PER_PLAN,
    MAX_DELETED_PER_PLAN,
    MAX_JOURNAL_ENTRY_BYTES,
    MAX_MERGE_PARENTS,
    MAX_OPS_PER_PLAN,
    MAX_PLAN_TARGETS,
    MAX_SAFE_COMMIT_TARGETS,
    PLAN_STATUSES,
    PLAN_TTL_S,
    RESERVED_FIELD_NAMES,
    WFID_PROP,
    finding,
)
from app.services.feature_edit.geometry_ops import (
    merge_features,
    move_by,
    replace_geometry,
    repair_geometry,
    snap_to_ref,
    split_feature,
)
from app.services.feature_edit.identity import (
    has_wfids,
    materialize_wfids,
    resolve_feature_ids,
)
from app.services.feature_edit.journal import apply_deltas_to_features, apply_schema_ops_to_features
from app.services.feature_edit.topology import check_topology, extract_topology_declaration

__all__ = [
    "build_plan",
    "derive_changes",
    "needs_materialization",
    "plan_safe_classification",
    "summarize_plan",
    "extract_fc",
    "plan_expired",
]


def needs_materialization(fc: GeoJSONFeatureCollection) -> bool:
    features = fc.get("features") or []
    if not features:
        # 空数据集无事可物化 —— 首个 create_features 才引入身份。
        return False
    return not has_wfids(fc)


def plan_safe_classification(
    ops: List[Dict[str, Any]], findings: List[EditFinding], n_targets: int
) -> bool:
    """safe = 免确认可自动执行（policy 放行前提下）。

    判据（保守）：纯属性词表 + 校验零 findings + 目标数 ≤ safe 上限。
    几何操作（repair/move/snap）恒不 safe —— 坐标变更必须显式确认。
    """
    if findings:
        return False
    if n_targets > MAX_SAFE_COMMIT_TARGETS:
        return False
    # R1 P2-4：rename_field 是数据集级 schema 变更（全库列名），爆炸半径与
    # 目标要素数无关 —— 恒不 safe（仅 set_fields 可 auto）。
    return all(op.get("op") == "set_fields" for op in ops)


def _validate_op_shapes(ops: List[Any]) -> List[EditFinding]:
    """词表/参数形状校验（缺数值单位即拒绝 —— fail-closed）。"""
    findings: List[EditFinding] = []
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
            isinstance(op.get("ref_id"), str)
            and isinstance(op.get("tolerance_m"), (int, float))
        ):
            findings.append(
                finding(
                    "unit_required",
                    "snap_to_ref requires ref_id and numeric tolerance_m (meters)",
                )
            )
        # ── G14 结构性操作形状校验 ──
        elif kind == "create_features":
            feats = op.get("features")
            if not isinstance(feats, list) or not feats:
                findings.append(finding("invalid_op", "create_features requires a non-empty features list"))
            elif len(feats) > MAX_CREATED_PER_PLAN:
                findings.append(
                    finding("invalid_op", "too many features in one create op", limit=MAX_CREATED_PER_PLAN)
                )
            else:
                for item in feats:
                    if not isinstance(item, dict) or not isinstance(item.get("geometry"), dict):
                        findings.append(finding("invalid_op", "each created feature requires a geometry object"))
                        break
                    props = item.get("properties")
                    if props is not None and not isinstance(props, dict):
                        findings.append(finding("invalid_op", "created feature properties must be an object"))
                        break
                    if isinstance(props, dict):
                        reserved = sorted(set(props) & set(RESERVED_FIELD_NAMES))
                        if reserved:
                            findings.append(
                                finding("reserved_id", "created feature properties must not set reserved identity fields", fields=reserved)
                            )
                            break
        elif kind == "delete_features":
            pass  # 目标集来自 plan 目标解析（显式 ids / selection）
        elif kind == "replace_geometry":
            if not isinstance(op.get("geometry"), dict):
                findings.append(finding("invalid_op", "replace_geometry requires a GeoJSON geometry"))
        elif kind == "split_feature":
            if not isinstance(op.get("wfid"), str) or not isinstance(op.get("line"), dict):
                findings.append(finding("invalid_op", "split_feature requires target wfid and split line geometry"))
        elif kind == "merge_features":
            policy = op.get("field_policy")
            if policy is not None:
                if not isinstance(policy, dict):
                    findings.append(finding("invalid_op", "field_policy must be an object"))
                elif policy.get("mode", "first") not in ("first", "require_identical"):
                    findings.append(finding("invalid_op", "field_policy.mode must be first|require_identical"))
                elif not isinstance(policy.get("overrides", {}), dict):
                    findings.append(finding("invalid_op", "field_policy.overrides must be an object"))
    return findings


def derive_changes(
    fc: GeoJSONFeatureCollection,
    ops: List[Dict[str, Any]],
    target_wfids: List[str],
    target_payloads: Dict[str, Any],
    *,
    plan_seed: str = "",
) -> Tuple[List[FeatureDelta], List[RenameField], List[EditFinding], List[OperationSummary], List[Dict[str, Any]]]:
    """对已物化 fc 顺序求 ops 的完整 diff（preview 与 commit 同一路径）。

    返回 (deltas, schema_ops, findings, op_summaries, lineage_events)。
    target_payloads 为 snap 目标 ref 的预取载荷（{ref_id: payload}；缺失项
    出 dataset_not_found）。deltas 是绝对态（before=操作前值, after=操作后值），
    按序应用即顺序语义。plan_seed（=plan_id）驱动 create/split/merge 新生
    身份的决定性派生 —— preview 与 commit 重求值得到同一批 wfid。
    """
    findings: List[EditFinding] = []
    deltas: List[FeatureDelta] = []
    schema_ops: List[RenameField] = []
    op_summaries: List[OperationSummary] = []
    lineage_events: List[Dict[str, Any]] = []
    working = fc
    target_set = set(target_wfids)
    field_profiles = profile_fields(working)
    seed = plan_seed or "unseeded"
    for op_index, op in enumerate(ops):
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
            # 字段名 ref_id：registry 参数解引用的既有 skip 名单覆盖嵌套
            # dict 键 —— 游标身份直达工具（transparent resolution 会把
            # ref: 字符串内联成整个 FeatureCollection）。
            target_ref = op.get("ref_id")
            if target_ref not in target_payloads:
                findings.append(finding("dataset_not_editable", "snap target ref unavailable", target_ref=str(target_ref)[:64]))
            else:
                geom_deltas, geom_findings, summary = snap_to_ref(
                    working, target_set, _extract_fc(target_payloads[target_ref]), op.get("tolerance_m")
                )
                findings.extend(geom_findings)
                deltas.extend(geom_deltas)
                snap_summary: OperationSummary = {"op": kind}
                if "snapped" in summary:
                    snap_summary["snapped"] = summary["snapped"]
                if "tolerance_m" in summary:
                    snap_summary["tolerance_m"] = summary["tolerance_m"]
                op_summaries.append(snap_summary)
        elif kind == "create_features":
            # 新生要素：服务端决定性身份（plan_seed:op_index:child_index）；
            # 属性面按数据集字段 profile 校验（类型/枚举，与 set_fields 同纪律）。
            created: List[FeatureDelta] = []
            create_findings: List[EditFinding] = []
            existing_wfids = _existing_wfid_set(working)
            from app.services.feature_edit.identity import wfid_for_created

            for child_index, item in enumerate(op.get("features") or []):
                if not isinstance(item, dict) or not isinstance(item.get("geometry"), dict):
                    create_findings.append(finding("invalid_op", "created feature requires a geometry"))
                    continue
                props = dict(item.get("properties") or {})
                new_wfid = wfid_for_created(seed, op_index, child_index)
                while new_wfid in existing_wfids:  # 防御：与既有身份撞车即偏移
                    child_index += 1000
                    new_wfid = wfid_for_created(seed, op_index, child_index)
                existing_wfids.add(new_wfid)
                reserved = sorted(set(props) & set(RESERVED_FIELD_NAMES))
                if reserved:
                    create_findings.append(
                        finding("reserved_id", "created feature properties must not set reserved identity fields", fields=reserved)
                    )
                    continue
                if props:
                    prop_findings = validate_set_fields(props, field_profiles, allow_new_fields=True)
                    # 新要素允许携带数据集尚未观测到的字段（自然生长面）；
                    # 已有 profile 的字段仍受类型/枚举约束。
                    create_findings.extend(prop_findings)
                    if prop_findings:
                        continue
                props[WFID_PROP] = new_wfid
                child_feature = {"type": "Feature", "geometry": item["geometry"], "properties": props}
                created.append({"wfid": new_wfid, "presence": "created", "feature_after": child_feature})
            deltas.extend(created)
            findings.extend(create_findings)
            op_summaries.append({"op": kind, "count": len(created)})
        elif kind == "delete_features":
            snapshot = _delete_deltas(working, target_set)
            missing = sorted(target_set - {d.get("wfid") for d in snapshot})[:8]
            if missing:
                findings.append(finding("feature_not_found", "delete targets missing in current payload", wfid=missing))
            elif len(snapshot) > MAX_DELETED_PER_PLAN:
                findings.append(
                    finding("invalid_op", "too many delete targets in one plan", limit=MAX_DELETED_PER_PLAN)
                )
            else:
                deltas.extend(snapshot)
            op_summaries.append({"op": kind, "count": len(snapshot)})
        elif kind == "replace_geometry":
            geom_deltas, geom_findings = replace_geometry(working, target_set, op.get("geometry"))
            findings.extend(geom_findings)
            deltas.extend(geom_deltas)
            op_summaries.append({"op": kind, "targets": len(geom_deltas)})
        elif kind == "split_feature":
            split_deltas, split_findings, lineage = split_feature(
                working, str(op.get("wfid") or ""), op.get("line"),
                plan_seed=seed, op_index=op_index,
            )
            findings.extend(split_findings)
            deltas.extend(split_deltas)
            if lineage:
                lineage_events.append(lineage)
                op_summaries.append(
                    {"op": kind, "wfid": op.get("wfid"), "children": len(lineage.get("child_wfids") or [])}
                )
            else:
                op_summaries.append({"op": kind, "wfid": op.get("wfid")})
        elif kind == "merge_features":
            merge_targets = [w for w in target_wfids if w in target_set]
            if len(merge_targets) < 2:
                findings.append(finding("invalid_op", "merge_features requires >=2 target features"))
            elif len(merge_targets) > MAX_MERGE_PARENTS:
                findings.append(
                    finding("invalid_op", "too many merge parents", limit=MAX_MERGE_PARENTS)
                )
            else:
                merge_deltas, merge_findings, merge_out = merge_features(
                    working, merge_targets, op.get("field_policy") or {},
                    plan_seed=seed, op_index=op_index,
                )
                findings.extend(merge_findings)
                deltas.extend(merge_deltas)
                if merge_out and merge_out.get("lineage"):
                    lineage_events.append(merge_out["lineage"])
                    summary: OperationSummary = {"op": kind, "parents": len(merge_targets)}
                    extra = merge_out.get("extra") or {}
                    if extra.get("conflict_fields"):
                        summary["conflict_fields"] = extra["conflict_fields"]
                    op_summaries.append(summary)
                else:
                    op_summaries.append({"op": kind, "parents": len(merge_targets)})
        # 顺序语义：把已产生的 delta 应用到工作视图，后续 op 在最新态上求值。
        if deltas or schema_ops:
            feats, _ = apply_deltas_to_features(working.get("features") or [], deltas)
            feats, _ = apply_schema_ops_to_features(feats, schema_ops)
            working = {**working, "features": feats}
            if schema_ops:
                # R1 P2-5：rename 改变字段面 —— 校验证据必须随工作视图重算
                # （否则 rename 后按旧 profile 误拒/静默重建旧列）。
                field_profiles = profile_fields(working)
    return deltas, schema_ops, findings, op_summaries, lineage_events


def _existing_wfid_set(fc: GeoJSONFeatureCollection) -> set:
    out = set()
    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        wfid = props.get(WFID_PROP) if isinstance(props, dict) else None
        if isinstance(wfid, str):
            out.add(wfid)
    return out


def _delete_deltas(fc: GeoJSONFeatureCollection, wfids: set) -> List[FeatureDelta]:
    """delete 的 removed 快照（完整要素深拷贝；存在性由命中集合证明）。"""
    import copy as _copy

    out: List[FeatureDelta] = []
    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        wfid = props.get(WFID_PROP) if isinstance(props, dict) else None
        if isinstance(wfid, str) and wfid in wfids:
            out.append({"wfid": wfid, "presence": "removed", "feature_before": _copy.deepcopy(feature)})
    return out


def build_plan(
    *,
    ref_id: str,
    payload: Any,
    ops: List[Dict[str, Any]],
    requested_ids: Optional[List[str]],
    selection: Optional[SelectionView],
    dataset_fingerprint: str,
    content_revision: int,
    target_payloads: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[EditFinding]]:
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

    findings: List[EditFinding] = list(_validate_op_shapes(ops))
    # plan_id 先于求值生成：create/split/merge 新生身份由 plan_id 决定性派生，
    # preview 与 commit 重求值必须得到同一批 wfid（幂等重放/redo 身份稳定）。
    plan_id = f"fep-{uuid.uuid4().hex[:12]}"

    # ── 目标解析：显式 ids 优先，否则 selection（含 selected_ids 扩展） ──
    # G14：create-only plan 不需要既有目标（空数据集首个要素的合法路径）；
    # split op 自带目标 wfid，并入解析集合。
    op_kinds = [op.get("op") for op in ops if isinstance(op, dict)]
    create_only = bool(op_kinds) and all(k == "create_features" for k in op_kinds)
    split_wfids = [
        str(op.get("wfid"))
        for op in ops
        if isinstance(op, dict) and op.get("op") == "split_feature" and isinstance(op.get("wfid"), str)
    ]
    materialize = needs_materialization(fc)
    preview_fc = fc
    if materialize:
        # 预览阶段即对「物化后」形态求值（commit 时同样先物化），
        # 保证 preview diff 与 commit 结果一致。
        preview_fc, _ = materialize_wfids(fc, dataset_fingerprint)

    ids: List[str]
    if create_only:
        ids = []
    elif requested_ids:
        ids = [str(x) for x in requested_ids][:MAX_PLAN_TARGETS]
        ids.extend(split_wfids)
        ids = list(dict.fromkeys(ids))[:MAX_PLAN_TARGETS]
    elif selection and isinstance(selection.get("feature_id"), str):
        ids = [str(selection["feature_id"])]
        extra = selection.get("selected_ids")
        if isinstance(extra, list):
            ids.extend(str(x) for x in extra if isinstance(x, str))
        ids.extend(split_wfids)
        ids = list(dict.fromkeys(ids))[:MAX_PLAN_TARGETS]
    elif split_wfids:
        ids = list(dict.fromkeys(split_wfids))[:MAX_PLAN_TARGETS]
    else:
        return {}, [finding("invalid_target", "no explicit ids and no live selection")]

    wfid_map = resolve_feature_ids(preview_fc, ids) if ids else {}
    missing = [i for i in ids if i not in wfid_map]
    if missing:
        findings.append(
            finding("feature_not_found", "requested ids not resolvable in dataset", ids=missing[:8])
        )
    wfids = sorted(set(wfid_map.values()))
    if len(wfids) > MAX_PLAN_TARGETS:
        findings.append(finding("target_limit_exceeded", "too many target features", limit=MAX_PLAN_TARGETS))
    from app.services.feature_edit.contracts import GEOMETRY_OPS as _GEO_OPS, MAX_GEOMETRY_TARGETS

    geometry_bound_kinds = set(_GEO_OPS) | {"replace_geometry", "split_feature", "merge_features"}
    has_geometry = any(op.get("op") in geometry_bound_kinds for op in ops if isinstance(op, dict))
    if has_geometry and len(wfids) > MAX_GEOMETRY_TARGETS:
        findings.append(
            finding(
                "target_limit_exceeded",
                "geometry-edit plans are bounded to fewer targets (journal undo-fidelity budget)",
                limit=MAX_GEOMETRY_TARGETS,
            )
        )

    deltas, schema_ops, derive_findings, op_summaries, lineage_events = derive_changes(
        preview_fc, ops if isinstance(ops, list) else [], wfids, target_payloads or {},
        plan_seed=plan_id,
    )
    findings.extend(derive_findings)
    # G14：journal 单条字节闸 —— 结构性 delta 内嵌完整快照，超预算诚实拒绝
    # （undo 保真不可承受的编辑不进事务，绝不截断快照假装可撤销）。
    if deltas:
        import json as _json

        try:
            entry_bytes = len(
                _json.dumps({"deltas": deltas, "schema_ops": schema_ops}, separators=(",", ":"), ensure_ascii=False)
            )
        except (TypeError, ValueError):
            entry_bytes = MAX_JOURNAL_ENTRY_BYTES + 1
        if entry_bytes > MAX_JOURNAL_ENTRY_BYTES:
            findings.append(
                finding(
                    "entry_too_large",
                    "journal entry exceeds the undo-fidelity byte budget",
                    bytes=entry_bytes,
                    budget=MAX_JOURNAL_ENTRY_BYTES,
                )
            )

    # G14/WP06：声明拓扑规则的局部检查（未声明的层零规则 —— V10）。
    # 检查在「操作后」形态上做：受影响 = 目标 + 新生身份；邻域经空间索引
    # 限定。发现即 findings（定位 + 受影响对），修复必须走显式操作重新预览。
    topology_declaration = extract_topology_declaration(payload)
    if topology_declaration.get("rules") and deltas and not findings:
        after_feats, _ = apply_deltas_to_features(preview_fc.get("features") or [], deltas)
        after_feats, _ = apply_schema_ops_to_features(after_feats, schema_ops)
        after_fc = {**preview_fc, "features": after_feats}
        affected = list(
            set(wfids)
            | {d.get("wfid") for d in deltas if isinstance(d, dict) and d.get("presence") == "created"}
        )
        findings.extend(check_topology(after_fc, [w for w in affected if w], topology_declaration))

    n_targets = len(wfids)
    plan: Dict[str, Any] = {
        "plan_id": plan_id,
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
        "lineage": lineage_events[:MAX_OPS_PER_PLAN],
        "expected_content_revision": int(content_revision),
        "expected_fingerprint": dataset_fingerprint,
        "status": "pending" if not findings else "invalid",
        "created_at": time.time(),
    }
    return plan, findings


def _bounded_preview(deltas: List[FeatureDelta]) -> List[DeltaPreview]:
    """diff 的有界投影（属性全量 before/after；几何只给摘要 + 顶点数，不搬全量）。

    G14：结构性 delta 投影 presence + 新几何摘要（created 的几何来自
    feature_after；removed 只标记消失，不搬全量快照）。
    """
    from app.services.feature_edit.journal import count_positions

    out: List[DeltaPreview] = []
    for delta in deltas[:MAX_PLAN_TARGETS]:
        presence = delta.get("presence")
        item: DeltaPreview = {"wfid": delta.get("wfid")}
        if presence in ("created", "removed"):
            item["presence"] = presence
        if presence == "created":
            feature_after = delta.get("feature_after") or {}
            geometry = feature_after.get("geometry") if isinstance(feature_after, dict) else None
            if isinstance(geometry, dict):
                item["geometry"] = {
                    "positions_before": 0,
                    "positions_after": count_positions(geometry),
                    "bbox_after": _bbox(geometry),
                }
            props = feature_after.get("properties") if isinstance(feature_after, dict) else None
            if isinstance(props, dict) and len(props) > 1:
                item["fields"] = {
                    k: {"before": None, "after": v}
                    for k, v in list(props.items())[:16]
                    if k != "_wfid"
                }
            out.append(item)
            continue
        if delta.get("fields"):
            item["fields"] = {
                k: {"before": v.get("before"), "after": v.get("after")}
                for k, v in delta["fields"].items()
            }
        geometry_after = delta.get("geometry_after")
        if geometry_after is not None:
            item["geometry"] = {
                "positions_before": count_positions(delta.get("geometry_before")),
                "positions_after": count_positions(delta.get("geometry_after")),
                "bbox_after": _bbox(geometry_after),
            }
        out.append(item)
    return out


def _bbox(geometry: GeoJSONGeometry) -> Optional[List[float]]:
    xs: List[float] = []
    ys: List[float] = []

    def walk(node: JsonValue | tuple[float, ...]) -> None:
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


def summarize_plan(plan: Dict[str, Any]) -> PlanSummary:
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


def _extract_fc(data: Any) -> Optional[GeoJSONFeatureCollection]:
    fc = data
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict):
            fc = nested
    if isinstance(fc, dict) and fc.get("type") == "FeatureCollection":
        # Shape validation remains at ingestion; this adapter only unwraps.
        return cast(GeoJSONFeatureCollection, fc)
    return None


def extract_fc(data: Any) -> Optional[GeoJSONFeatureCollection]:
    """公开信封归一（executor/routes 复用）。"""
    return _extract_fc(data)


def plan_expired(plan: Dict[str, Any], now: Optional[float] = None) -> bool:
    return (now or time.time()) - float(plan.get("created_at") or 0) > PLAN_TTL_S


def plan_status_valid(status: Any) -> bool:
    return status in PLAN_STATUSES or status == "invalid"
