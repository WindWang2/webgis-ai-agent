"""Feature-edit service 面 —— REST 路由与 Agent 工具共用的预览/提交编排。

单一编排点防两套语义漂移：目标解析（selection IR 投影 / 显式 ids）、
snap 目标预取、plan 构建与暂存、策略闸、提交。
"""
from __future__ import annotations

from app.services.feature_edit.wire_types import EditFinding, SelectionView, PlanSummary

from typing import Any, Dict, List, Optional, Tuple, cast

from app.services.data_ingest.pipeline import compute_payload_fingerprint
from app.services.feature_edit import executor as fe_executor
from app.services.feature_edit import policy as fe_policy
from app.services.feature_edit.contracts import PLAN_TTL_S
from app.services.feature_edit.plan import build_plan, plan_expired, summarize_plan
from app.services.session_data import session_data_manager


async def load_selection_view(session_id: str) -> Optional[SelectionView]:
    """IR 投影的当前 selection（无 ledger / 无选中 → None）。"""
    from app.services.gis_harness.interaction_ir.projection import interaction_snapshot_via_store

    try:
        snapshot = await interaction_snapshot_via_store(session_data_manager, session_id)
    except Exception:  # noqa: BLE001 — 投影面失败按无选中处理（诚实降级）
        return None
    selection = (snapshot or {}).get("selection")
    # Interaction IR owns this stored projection; retain its extensions.
    return cast(SelectionView, selection) if isinstance(selection, dict) else None


async def build_and_store_plan(
    session_id: str,
    *,
    ref_id: Optional[str],
    feature_ids: Optional[List[str]],
    ops: List[Dict[str, Any]],
    selection_view: Optional[SelectionView] = None,
) -> Tuple[Dict[str, Any], List[EditFinding]]:
    """预览编排：解析 ref → 载荷/revision → snap 预取 → build_plan → 暂存。

    返回 (plan_summary, findings)；不可建 plan 时 plan={}。
    committable plan 会被暂存（TTL）并携带 plan_id。
    """
    if selection_view is None and not feature_ids:
        selection_view = await load_selection_view(session_id)
    if not ref_id:
        sel_ref = (selection_view or {}).get("dataset_ref")
        if isinstance(sel_ref, str) and sel_ref.startswith("ref:"):
            ref_id = sel_ref
    if not ref_id or not str(ref_id).startswith("ref:"):
        return {}, [
            {"code": "invalid_target", "message": "ref_id (ref:…) is required (explicit or via live selection)"}
        ]
    payload = await session_data_manager.get(session_id, ref_id)
    if payload is None:
        return {}, [{"code": "dataset_not_found", "message": f"ref not resolvable in this session: {str(ref_id)[:48]}"}]
    # G14/V20：只读源拒绝直接编辑 —— typed finding + 派生副本指引（原源零写入）。
    # R3 P2-2：envelope 与 artifact metadata 两个标记面都对齐（qualification
    # 同款判定），ledger 读失败按可编辑处理（诚实降级不阻断）。
    read_only = isinstance(payload, dict) and payload.get("read_only") is True
    if not read_only:
        try:
            from app.services.artifact_registry import get_artifact

            rec = await get_artifact(session_id, str(ref_id))
            read_only = bool(rec and rec.metadata and rec.metadata.get("read_only") is True)
        except Exception:  # noqa: BLE001 — ledger 不可达不改变可编辑性判定
            read_only = False
    if read_only:
        return {}, [{
            "code": "dataset_read_only_source",
            "message": (
                "this dataset is read-only; create an editable copy "
                "(feature-edits datasets editable-copy) before editing"
            ),
            "detail": {"ref_id": str(ref_id)[:64]},
        }]
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
    revision = int(descriptor.get("content_revision") or 0)

    target_payloads: Dict[str, Any] = {}
    for op in ops:
        # R1 P2-6：非 dict op（模型垃圾输入）在预取段就跳过 —— 形状校验在
        # build_plan 里 fail-closed，这里不得先崩。
        if not isinstance(op, dict):
            continue
        target_ref = op.get("ref_id")
        if op.get("op") == "snap_to_ref" and isinstance(target_ref, str):
            target_payloads[target_ref] = await session_data_manager.get(session_id, target_ref)

    # G44：active 合同解析（DB 不可读 → fail-closed typed finding，F36）。
    from app.services.field_contracts import enforcement as fc_enforcement

    try:
        enforced = await fc_enforcement.resolve_active_contract(session_id, ref_id)
    except fc_enforcement.ContractStateUnavailable as exc:
        return {}, [{
            "code": "contract_state_unavailable",
            "message": f"field-contract state unreadable; preview blocked: {exc}"[:200],
        }]

    plan, findings = build_plan(
        ref_id=ref_id,
        payload=payload,
        ops=ops,
        requested_ids=feature_ids,
        selection=selection_view,
        dataset_fingerprint=compute_payload_fingerprint(payload),
        content_revision=revision,
        target_payloads=target_payloads,
        field_contract=enforced,
    )
    if plan.get("committable"):
        await fe_executor.store_plan(session_id, plan)
    return plan, findings


def summarize(plan: Dict[str, Any], findings: List[EditFinding]) -> PlanSummary:
    summary: PlanSummary = summarize_plan(plan) if plan else {"committable": False, "safe": False, "validation": findings}
    summary["preview"] = plan.get("preview") or []
    summary["validation"] = findings
    if plan.get("committable"):
        summary["expires_in_s"] = PLAN_TTL_S
    return summary


# ── 共享拓扑约束面（goal-gis-topology-authoring；REST 与工具共用的只读编排） ──

async def _constraint_payload(session_id: str, ref_id: str) -> Any:
    payload = await session_data_manager.get(session_id, ref_id)
    if payload is None:
        return None
    return payload


def _apply_match_defaults(ops: List[Dict[str, Any]], tolerance_m: Any, level_field: Any) -> List[Dict[str, Any]]:
    """调用级匹配规则默认注入（op 未显式声明时才补 —— op 级优先）。

    仅注入**共享拓扑 op**（R1-F8：snap_to_ref 等既有 op 有自己的容差语义，
    注入会让 impact 投影偏离真实 preview 路径）。
    """
    import copy as _copy

    from app.services.feature_edit.contracts import SHARED_GEOMETRY_OPS

    out = _copy.deepcopy(ops)
    for op in out:
        if not isinstance(op, dict) or op.get("op") not in SHARED_GEOMETRY_OPS:
            continue
        if tolerance_m is not None and op.get("tolerance_m") is None:
            op["tolerance_m"] = tolerance_m
        if level_field is not None and op.get("level_field") is None:
            op["level_field"] = level_field
    return out


async def inspect_edit_constraints(
    session_id: str,
    ref_id: str,
    *,
    tolerance_m: Any = None,
    level_field: Any = None,
    neighbor_wfids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """约束面检查（只读）：声明规则 + 共享结构摘要 + 基线违规 + 悬挂端点。"""
    payload = await _constraint_payload(session_id, ref_id)
    if payload is None:
        return {"success": False, "code": "dataset_not_found", "message": f"ref not resolvable: {str(ref_id)[:48]}"}
    from app.services.feature_edit.plan import extract_fc
    from app.services.feature_edit.shared_topology import build_shared_index, inspect_shared_structure
    from app.services.feature_edit.topology import check_topology, extract_topology_declaration

    fc = extract_fc(payload)
    if fc is None:
        return {"success": False, "code": "dataset_not_editable", "message": "payload is not a geojson FeatureCollection"}
    n_features = len(fc.get("features") or [])
    if n_features > 20_000:
        return {"success": False, "code": "dataset_too_large_to_edit", "message": "dataset exceeds editable size", "detail": {"features": n_features}}
    # 索引对物化形态构建（与编辑路径同源）；未物化数据集先确定性物化。
    from app.services.feature_edit.plan import needs_materialization
    from app.services.feature_edit.identity import materialize_wfids
    from app.services.data_ingest.pipeline import compute_payload_fingerprint

    preview_fc = fc
    if needs_materialization(fc):
        preview_fc, _ = materialize_wfids(fc, compute_payload_fingerprint(payload))
    index, index_findings = build_shared_index(preview_fc, tolerance_m=tolerance_m, level_field=level_field)
    declaration = extract_topology_declaration(payload)
    out: Dict[str, Any] = {
        "ref_id": ref_id,
        "declared_rules": list(declaration.get("rules") or []),
        "topology_tolerance_m": declaration.get("tolerance_m"),
    }
    # 调用者的 wfids 可能是自然 id —— 归一到物化身份空间再查邻接。
    resolved_neighbors: Optional[List[str]] = None
    if neighbor_wfids:
        from app.services.feature_edit.identity import natural_to_wfid_index

        nat_index = natural_to_wfid_index(preview_fc)
        resolved_neighbors = [nat_index.get(w, w) for w in neighbor_wfids]
    if index is not None:
        view = inspect_shared_structure(index, neighbor_wfids=resolved_neighbors)
        out.update(view)
    if index_findings:
        # index_too_large / 容差非法等 typed 否定面照实披露（fail-closed 不降级）。
        out["index_findings"] = index_findings[:8]
    if declaration.get("rules"):
        baseline = check_topology(preview_fc, list(index.paths) if index else [], declaration)
        out["baseline_findings"] = baseline[:24]
        out["baseline_total"] = len(baseline)
    return {"success": True, **out}


async def impact_preview(
    session_id: str,
    ref_id: str,
    ops: List[Dict[str, Any]],
    *,
    feature_ids: Optional[List[str]] = None,
    tolerance_m: Any = None,
    level_field: Any = None,
) -> Dict[str, Any]:
    """get_impact：候选 op 的有界影响投影（纯读 —— 绝不落 plan store）。"""
    payload = await _constraint_payload(session_id, ref_id)
    if payload is None:
        return {"success": False, "code": "dataset_not_found", "message": f"ref not resolvable: {str(ref_id)[:48]}"}
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
    revision = int(descriptor.get("content_revision") or 0)
    from app.services.data_ingest.pipeline import compute_payload_fingerprint
    from app.services.feature_edit.plan import build_plan, summarize_plan

    norm_ops = _apply_match_defaults(ops, tolerance_m, level_field)
    plan, findings = build_plan(
        ref_id=ref_id,
        payload=payload,
        ops=norm_ops,
        requested_ids=feature_ids,
        selection=None,
        dataset_fingerprint=compute_payload_fingerprint(payload),
        content_revision=revision,
    )
    out: Dict[str, Any] = {
        "success": True,
        "ref_id": ref_id,
        "committable": bool(plan.get("committable")),
        "findings": findings[:8],
        "op_summaries": plan.get("op_summaries") or [],
    }
    impact = plan.get("impact")
    if isinstance(impact, dict):
        out["touched_features"] = impact.get("touched_features")
        out["affected_neighbors"] = impact.get("affected_neighbors") or []
        out["neighbors_truncated"] = impact.get("neighbors_truncated")
        out["measure_delta"] = impact.get("measure_delta")
        out["atomicity"] = impact.get("atomicity")
    topology_context = plan.get("topology_context")
    if isinstance(topology_context, dict):
        out["new_violations"] = topology_context.get("new_violations")
        out["existing_violations"] = topology_context.get("existing_violations")
        out["existing"] = topology_context.get("existing") or []
    summary = summarize_plan(plan)
    out["preview"] = summary.get("preview") or []
    return out


async def revalidate_edit_constraints(session_id: str, ref_id: str) -> Dict[str, Any]:
    """revalidate：当前修订版的声明规则复检（编辑后质量闭环；只读）。"""
    payload = await _constraint_payload(session_id, ref_id)
    if payload is None:
        return {"success": False, "code": "dataset_not_found", "message": f"ref not resolvable: {str(ref_id)[:48]}"}
    from app.services.feature_edit.identity import materialize_wfids
    from app.services.feature_edit.plan import needs_materialization
    from app.services.feature_edit.plan import extract_fc
    from app.services.feature_edit.topology import check_topology, extract_topology_declaration
    from app.services.data_ingest.pipeline import compute_payload_fingerprint

    fc = extract_fc(payload)
    if fc is None:
        return {"success": False, "code": "dataset_not_editable", "message": "payload is not a geojson FeatureCollection"}
    declaration = extract_topology_declaration(payload)
    rules = list(declaration.get("rules") or [])
    if not rules:
        return {
            "success": True,
            "ref_id": ref_id,
            "rules": [],
            "findings": [],
            "baseline_total": 0,
            "note": "no topology rules declared on this dataset (declared-rules-only discipline)",
        }
    preview_fc = fc
    if needs_materialization(fc):
        preview_fc, _ = materialize_wfids(fc, compute_payload_fingerprint(payload))
    from app.services.feature_edit.contracts import WFID_PROP as _WFID_PROP

    all_wfids = []
    for feature in preview_fc.get("features") or []:
        if isinstance(feature, dict):
            props = feature.get("properties")
            wfid = props.get(_WFID_PROP) if isinstance(props, dict) else None
            if isinstance(wfid, str):
                all_wfids.append(wfid)
    findings = check_topology(preview_fc, all_wfids, declaration)
    return {
        "success": True,
        "ref_id": ref_id,
        "rules": rules,
        "findings": findings[:24],
        "baseline_total": len(findings),
        "checked_wfids": len(all_wfids),
    }


async def agent_commit(session_id: str, plan_id: str) -> Dict[str, Any]:
    """Agent 工具提交：policy 闸（safe 属性面）+ 幂等 + 同一 executor 路径。"""
    plan = await fe_executor.get_plan(session_id, plan_id)
    if plan is None:
        return {"success": False, "code": "PLAN_NOT_FOUND",
                "message": f"plan {plan_id} not found (expired or never created)"}
    if plan_expired(plan):
        await fe_executor._mark_plan_status(session_id, plan_id, "expired")
        return {"success": False, "code": "PLAN_EXPIRED",
                "message": "plan expired; request a fresh preview"}
    if plan.get("status") == "applied":
        return {"success": True, "code": "ALREADY_APPLIED", "receipt": plan.get("receipt")}
    if plan.get("status") != "pending" or not plan.get("committable"):
        return {"success": False, "code": "PLAN_STATE_INVALID", "status": plan.get("status")}

    policy = await fe_policy.get_policy(session_id)
    if not fe_policy.policy_allows_auto_commit(policy.get("mode", ""), plan):
        return {
            "success": False,
            "code": "CONFIRMATION_REQUIRED",
            "message": (
                "This edit needs explicit user approval (geometry change, validation findings, "
                "or session policy confirm_all). Present the plan preview to the user; they can "
                "approve it in the map/chat edit proposal card, or the owner can set policy "
                "allow_safe_attribute for pure-attribute auto-commit."
            ),
            "plan_id": plan_id,
            "safe": plan.get("safe"),
            "policy_mode": policy.get("mode"),
        }
    try:
        receipt = await fe_executor.commit_plan(
            session_id, plan, actor="agent", origin="agent", idempotency_key=f"plan:{plan_id}"
        )
        return {"success": True, "receipt": receipt}
    except fe_executor.FeatureEditConflict as exc:
        return {"success": False, "code": "STALE_REVISION", "detail": exc.detail}
    except fe_executor.FeatureEditBlocked as exc:
        return {"success": False, "code": exc.code.upper(), "message": exc.message, "detail": exc.detail}
