"""feature_proposals 编排服务（G32）—— REST 与 Agent 工具共用的单一编排点。

职责：创建（base 快照捕获）/列表/详情/差异分页/冲突检查/裁决/rebase/
审阅决定（actor 由服务端 auth 上下文裁决 —— agent 恒不可批准几何与结构性
提案）/合并（经 bridge）/撤回/恢复视图/审计导出。

授权纪律：全部入口经 require_owned_session（路由层）；actor_kind 在 HTTP 面
恒 "user"；agent 工具面调 create/list/diff/rebase_preview（无 decision）。
提案的 author_kind=agent 只说明起草者，不授予任何批准权（P22/P23）。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.feature_proposal_schema import (
    FeatureProposalRecord,
    ProposalComment,
    ProposalDecision,
    ProposalResolution,
)
from app.services.feature_proposals import bridge
from app.services.feature_proposals.candidate import (
    candidate_digest,
    extract_payload_fc,
    materialize_preview,
    resolve_candidate_targets,
)
from app.services.feature_proposals.snapshot import snapshot_identity_summary
from app.services.feature_proposals.contracts import (
    APPROVED,
    CHANGES_REQUESTED,
    DRAFT,
    MERGED,
    MERGING,
    RECOVERY_REQUIRED,
    SUBMITTED,
    WITHDRAWN,
    FeatureProposalBlocked,
    FeatureProposalConflict,
    FeatureProposalError,
    FeatureProposalNotFound,
    MAX_DRAFT_PROPOSALS_PER_REF,
    can_transition,
    validate_proposal_ops,
)
from app.services.feature_proposals.snapshot import (  # noqa: F401
    base_snapshot_exists,
    capture_base_snapshot,
    load_base_snapshot,
)
from app.services.feature_proposals.store import proposal_store

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _append_decision(rec: FeatureProposalRecord, decision: ProposalDecision) -> None:
    """decisions 有界追加（R1-P0-1：pydantic max_length 只在加载时校验 ——
    无界 append 会让下一次存储读取抛 ValidationError → 整会话 500）。"""
    from app.services.feature_proposals.contracts import MAX_DECISIONS_PER_PROPOSAL

    if len(rec.decisions) >= MAX_DECISIONS_PER_PROPOSAL:
        raise FeatureProposalBlocked(
            "decision_history_full",
            f"proposal already holds {MAX_DECISIONS_PER_PROPOSAL} decisions — "
            "withdraw it and create a fresh proposal",
        )
    rec.decisions.append(decision)


def _new_proposal_id() -> str:
    return f"fpr-{uuid.uuid4().hex[:12]}"


def _new_comment_id() -> str:
    return f"fpc-{uuid.uuid4().hex[:10]}"


def _read_only_check(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, dict) and payload.get("read_only") is True:
        return {
            "code": "dataset_read_only_source",
            "message": "source is read-only; create an editable copy first (feature-edits datasets editable-copy)",
        }
    return None


async def _load_payload_checked(session_id: str, ref_id: str) -> Tuple[Any, Dict[str, Any]]:
    from app.services.session_data import session_data_manager

    if not ref_id or not str(ref_id).startswith("ref:"):
        raise FeatureProposalBlocked("invalid_target", "ref_id (ref:…) is required")
    payload = await session_data_manager.get(session_id, ref_id)
    if payload is None:
        raise FeatureProposalBlocked(
            "dataset_not_found", "ref not resolvable in this session",
            {"ref_id": str(ref_id)[:64]},
        )
    ro = _read_only_check(payload)
    if ro is None:
        try:
            from app.services.artifact_registry import get_artifact

            rec = await get_artifact(session_id, ref_id)
            if rec and rec.metadata and rec.metadata.get("read_only") is True:
                ro = {
                    "code": "dataset_read_only_source",
                    "message": "source is read-only; create an editable copy first",
                }
        except Exception:  # noqa: BLE001 — ledger 不可达不改变判定
            ro = None
    if ro is not None:
        raise FeatureProposalBlocked(ro["code"], ro["message"], {"ref_id": str(ref_id)[:64]})
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
    return payload, descriptor


async def _dep_fingerprints_for(session_id: str, ops: List[Dict[str, Any]]) -> Dict[str, str]:
    from app.services.data_ingest.pipeline import compute_payload_fingerprint
    from app.services.session_data import session_data_manager

    out: Dict[str, str] = {}
    for op in ops:
        if isinstance(op, dict) and op.get("op") == "snap_to_ref" and isinstance(op.get("ref_id"), str):
            dep = await session_data_manager.get(session_id, op["ref_id"])
            if dep is not None:
                out[op["ref_id"]] = compute_payload_fingerprint(dep)
    return out


# ── 创建 ────────────────────────────────────────────────────────────────────


async def create_proposal(
    session_id: str,
    *,
    ref_id: str,
    ops: List[Dict[str, Any]],
    feature_ids: Optional[List[str]],
    title: Optional[str],
    author_kind: str,
    author_id: str,
    submit: bool,
) -> FeatureProposalRecord:
    from app.services.data_ingest.pipeline import compute_payload_fingerprint
    from app.services.feature_edit.identity import has_wfids

    ops = [dict(op) for op in ops if isinstance(op, dict)]
    findings = validate_proposal_ops(ops)
    if findings:
        raise FeatureProposalBlocked("invalid_ops", "proposal ops rejected", {"findings": findings})
    # R2-2：merge 提案收敛 —— merge 的父集 = plan 级 feature_ids（G14 语义：
    # plan targets 即 merge 父）。与其他 op 混排会把无关 op 目标静默吸并为
    # 父，因此 merge_features 必须是唯一 op 且显式 feature_ids ≥2（对其他
    # 要素的编辑放独立提案）。
    if any(op.get("op") == "merge_features" for op in ops):
        if len(ops) > 1:
            raise FeatureProposalBlocked(
                "invalid_op",
                "merge_features must be the sole op of a proposal — put edits to other "
                "features in a separate proposal (merge parents are the plan-level feature_ids)",
            )
        if not feature_ids or len(feature_ids) < 2:
            raise FeatureProposalBlocked(
                "invalid_op",
                "merge_features proposals require feature_ids (>=2 merge parents)",
            )
    payload, descriptor = await _load_payload_checked(session_id, ref_id)
    revision_now = int(descriptor.get("content_revision") or 0)
    fingerprint = compute_payload_fingerprint(payload)
    fc = extract_payload_fc(payload)
    # op 级 wfids 与 plan 级 feature_ids 一并归一化到稳定身份空间：物化后
    # `_wfid` 置于 FEATURE_ID_KEYS 链首（G24），自然 id 不再寻址 —— 创建时
    # （在 base 快照同一形态上）归一化并持久 wfid 形态，diff/rebase/merge
    # 全程一致（P14 防线：不按数组位置迁移）。feature_ids 是 merge 父集/
    # 共享目标的 plan 级载体（R1-P1-5：此前被静默丢弃 → merge 提案不可创建）。
    normalized_feature_ids: Optional[List[str]] = None
    if fc is not None:
        from app.services.feature_proposals.candidate import normalize_op_wfids

        ordered, resolve_missing, mat_fc = resolve_candidate_targets(
            payload, ops, dataset_fingerprint=fingerprint,
            requested_ids=feature_ids,
        )
        if resolve_missing:
            raise FeatureProposalBlocked(
                "feature_not_found", "op targets not resolvable on the current data",
                {"missing": resolve_missing[:4]},
            )
        # 目标预算（R1-P2-8）：解析后的目标并集 ≤ MAX_PROPOSAL_TARGETS ——
        # 否则 diff 分类与 merge 时 G14 plan 截断（200）会静默分叉。
        from app.services.feature_proposals.contracts import MAX_PROPOSAL_TARGETS

        if len(ordered) > MAX_PROPOSAL_TARGETS:
            raise FeatureProposalBlocked(
                "target_limit_exceeded",
                f"proposal targets exceed the budget ({len(ordered)} > {MAX_PROPOSAL_TARGETS})",
                {"limit": MAX_PROPOSAL_TARGETS},
            )
        ops = normalize_op_wfids(ops, mat_fc)
        # feature_ids 归一化：requested→wfid 映射（保持 ordered 顺序语义）。
        if feature_ids:
            from app.services.feature_proposals.candidate import resolve_ids_on_payload

            fmap, _ = resolve_ids_on_payload(payload, [str(i) for i in feature_ids], fingerprint)
            normalized_feature_ids = list(dict.fromkeys(
                fmap.get(str(i), str(i)) for i in feature_ids
            ))
        # 落地检测（R1-P1-5）：候选在 base 形态上必须可求值 —— 死提案（如
        # merge 父集不足）在创建时拒绝，而不是产出不可批准的 blocked 候选。
        dep_payloads: Dict[str, Any] = {}
        for op in ops:
            if op.get("op") == "snap_to_ref" and isinstance(op.get("ref_id"), str):
                from app.services.session_data import session_data_manager

                dep = await session_data_manager.get(session_id, op["ref_id"])
                if dep is not None:
                    dep_payloads[op["ref_id"]] = dep
        from app.services.feature_proposals.candidate import evaluate_candidate
        from app.services.feature_edit.datasets import extract_geometry_type_declaration

        _deltas, eval_findings, _lineage = evaluate_candidate(
            mat_fc, ops, ordered, op_seed="fpr-precheck",
            target_payloads=dep_payloads,
            geometry_type=extract_geometry_type_declaration(payload),
        )
        if eval_findings:
            raise FeatureProposalBlocked(
                "invalid_ops", "proposal ops do not evaluate on the current data",
                {"findings": eval_findings[:6]},
            )
    materialized = fc is not None and has_wfids(fc)
    base = capture_base_snapshot(
        session_id, ref_id, payload,
        content_revision=revision_now, fingerprint=fingerprint,
        materialized=materialized,
    )
    base.dep_fingerprints = await _dep_fingerprints_for(session_id, ops)

    op_seed = f"fpr-{uuid.uuid4().hex[:12]}"  # 服务端生成；模型不能指定 seed
    resolved_title = (title or "").strip() or _default_title(ops)
    digest = candidate_digest(
        ops, [], base_fingerprint=base.fingerprint, base_revision=base.content_revision, op_seed=op_seed,
    )
    now = _now()
    record = FeatureProposalRecord(
        proposal_id=_new_proposal_id(),
        session_id=session_id,
        ref_id=ref_id,
        title=resolved_title,
        author_kind=author_kind,
        author_id=author_id,
        created_at=now, updated_at=now,
        revision=1,
        status=SUBMITTED if submit else DRAFT,
        ops=ops,
        feature_ids=normalized_feature_ids,
        op_seed=op_seed,
        candidate_digest=digest,
        base=base,
        decisions=[ProposalDecision(
            action="submit", actor_kind=author_kind, actor_id=author_id, at=now,
        )],
    )
    # draft 容量闸（同一 ref 并发 draft ≤8）。
    existing = await proposal_store.list_proposals(session_id)
    drafts_same_ref = [
        p for p in existing if p.ref_id == ref_id and p.status == DRAFT
    ]
    if record.status == DRAFT and len(drafts_same_ref) >= MAX_DRAFT_PROPOSALS_PER_REF:
        raise FeatureProposalBlocked(
            "draft_limit_reached",
            f"already {MAX_DRAFT_PROPOSALS_PER_REF} open drafts on this ref — submit or withdraw one first",
        )
    return await proposal_store.save_new_proposal(session_id, record)


def _default_title(ops: List[Dict[str, Any]]) -> str:
    kinds = [str(op.get("op")) for op in ops if isinstance(op, dict)]
    head = kinds[0] if kinds else "edit"
    return f"Feature change proposal ({head}{f' +{len(kinds)-1}' if len(kinds) > 1 else ''})"


# ── 查询 ────────────────────────────────────────────────────────────────────


async def list_proposals(
    session_id: str, *, status: Optional[str] = None, limit: int = 50, offset: int = 0,
) -> Dict[str, Any]:
    items = await proposal_store.list_proposals(session_id)
    if status:
        items = [p for p in items if p.status == status]
    items = sorted(items, key=lambda p: p.updated_at, reverse=True)
    total = len(items)
    page = items[offset: offset + max(1, min(limit, 100))]
    return {
        "total": total,
        "offset": offset,
        "items": [proposal_summary(p) for p in page],
    }


def proposal_summary(p: FeatureProposalRecord) -> Dict[str, Any]:
    return {
        "proposal_id": p.proposal_id,
        "ref_id": p.ref_id,
        "title": p.title,
        "author_kind": p.author_kind,
        "status": p.status,
        "revision": p.revision,
        "candidate_digest": p.candidate_digest,
        "base": snapshot_identity_summary(p.base),
        "ops_count": len(p.ops),
        "op_kinds": [str(op.get("op")) for op in p.ops],
        "merge": (p.merge.model_dump(mode="json") if p.merge else None),
        "has_approval": p.approval is not None
        and p.approval.approved_digest == p.candidate_digest,
        "resolutions_count": len(p.resolutions),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


async def get_proposal_detail(
    session_id: str, proposal_id: str,
) -> Dict[str, Any]:
    p = await _get_or_404(session_id, proposal_id)
    detail = proposal_summary(p)
    detail.update({
        "ops": p.ops,
        "op_seed": p.op_seed,
        "resolutions": [r.model_dump(mode="json") for r in p.resolutions],
        "decisions": [d.model_dump(mode="json") for d in p.decisions[-20:]],
        "comments": [c.model_dump(mode="json") for c in p.comments[-20:]],
        "approval": p.approval.model_dump(mode="json") if p.approval else None,
        "withdrawn_at": p.withdrawn_at,
    })
    return detail


async def _get_or_404(session_id: str, proposal_id: str) -> FeatureProposalRecord:
    p = await proposal_store.get_proposal(session_id, proposal_id)
    if p is None:
        raise FeatureProposalNotFound(
            "proposal_not_found", "proposal not found in this session",
            {"proposal_id": proposal_id},
        )
    return p


# ── 差异与冲突 ──────────────────────────────────────────────────────────────


async def get_diff(
    session_id: str, proposal_id: str, *,
    page: int = 1, page_size: int = 20, include_current: bool = True,
) -> Dict[str, Any]:
    """三方差异（分页，要素级）。几何走摘要/按需（不搬全量到列表页）。"""
    from app.services.data_ingest.pipeline import compute_payload_fingerprint
    from app.services.feature_edit.datasets import extract_geometry_type_declaration

    p = await _get_or_404(session_id, proposal_id)
    base_payload = load_base_snapshot(p.base)
    current_payload = None
    current_fingerprint = None
    if include_current:
        from app.services.session_data import session_data_manager

        current_payload = await session_data_manager.get(session_id, p.ref_id)
        current_fingerprint = (
            compute_payload_fingerprint(current_payload) if current_payload is not None else None
        )
    dep_payloads: Dict[str, Any] = {}
    cur_dep_fps: Dict[str, str] = {}
    for ref in bridge._dep_refs(p.ops):
        from app.services.session_data import session_data_manager

        dep = await session_data_manager.get(session_id, ref)
        if dep is not None:
            dep_payloads[ref] = dep
            cur_dep_fps[ref] = compute_payload_fingerprint(dep)

    report = await bridge.build_conflict_report(
        p,
        current_payload=current_payload if current_payload is not None else base_payload,
        current_fingerprint=current_fingerprint or p.base.fingerprint,
        base_payload=base_payload,
        dep_payloads=dep_payloads,
        dep_fingerprints_current=cur_dep_fps,
        geometry_type=extract_geometry_type_declaration(current_payload or base_payload),
    )
    # 候选差异（base → candidate）。
    base_fc = extract_payload_fc(base_payload)
    base_mat, _ = materialize_preview(base_fc, p.base.fingerprint)
    ordered, _missing, _mat = resolve_candidate_targets(
        base_payload, p.ops, dataset_fingerprint=p.base.fingerprint,
        requested_ids=p.feature_ids,
    )
    from app.services.feature_proposals.candidate import evaluate_candidate

    deltas, _findings, lineage = evaluate_candidate(
        base_mat, p.ops, ordered, op_seed=p.op_seed,
        target_payloads=dep_payloads,
        geometry_type=extract_geometry_type_declaration(base_payload),
    )
    page_size = max(1, min(page_size, 100))
    page = max(1, page)
    start = (page - 1) * page_size
    paged = deltas[start: start + page_size]
    return {
        "proposal_id": proposal_id,
        "revision": p.revision,
        "base": snapshot_identity_summary(p.base),
        "current": {
            "fingerprint": current_fingerprint,
            "available": current_payload is not None,
        },
        "candidate_deltas": [_bounded_delta(d) for d in paged],
        "candidate_delta_count": len(deltas),
        "page": page, "page_size": page_size,
        "conflict_report": _bounded_report(report),
        "lineage": lineage[:8],
    }


def _bounded_delta(delta: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"wfid": delta.get("wfid")}
    presence = delta.get("presence")
    if presence:
        out["presence"] = presence
    fields = delta.get("fields") or {}
    out["fields"] = {
        k: v for k, v in list(fields.items())[:64]
    }
    if delta.get("geometry_before") is not None or delta.get("geometry_after") is not None:
        from app.services.feature_proposals.candidate import geometry_digest

        out["geometry_before_digest"] = geometry_digest(delta.get("geometry_before"))
        out["geometry_after_digest"] = geometry_digest(delta.get("geometry_after"))
        out["geometry_ref"] = f"proposal-delta:{delta.get('wfid')}"
    if presence == "created":
        out["feature_after"] = delta.get("feature_after")
    if presence == "removed":
        out["feature_before_summary"] = {
            "properties": dict(list((delta.get("feature_before") or {}).get("properties", {}).items())[:16]),
        }
    return out


def _bounded_report(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "verdict": report.get("verdict"),
        "conflicts": report.get("conflicts", [])[:32],
        "unresolved_conflicts": report.get("unresolved_conflicts", [])[:32],
        "satisfied": report.get("satisfied", [])[:32],
        "independent_current_changes": report.get("independent_current_changes"),
        "identity": report.get("identity"),
        "current_changed": report.get("current_changed"),
    }


async def get_conflicts(session_id: str, proposal_id: str) -> Dict[str, Any]:
    payload = await get_diff(session_id, proposal_id, include_current=True)
    return {
        "proposal_id": proposal_id,
        "revision": payload["revision"],
        "conflict_report": payload["conflict_report"],
        "resolutions_applied": [
            r.model_dump(mode="json") for r in (await _get_or_404(session_id, proposal_id)).resolutions
        ],
    }


# ── 裁决与内容更新 ──────────────────────────────────────────────────────────


async def update_proposal(
    session_id: str, proposal_id: str, *,
    expected_revision: int,
    ops: Optional[List[Dict[str, Any]]] = None,
    feature_ids: Optional[List[str]] = None,
    title: Optional[str] = None,
    actor_kind: str, actor_id: str,
) -> FeatureProposalRecord:
    """候选内容更新 —— revision CAS + 内容变更使旧批准失效（新 digest）。

    approved 状态的更新自动降级回 submitted（批准是具体内容的批准 ——
    内容变了旧批准失效，重新审阅；P16）。
    """

    p0 = await _get_or_404(session_id, proposal_id)
    if p0.status not in (DRAFT, SUBMITTED, CHANGES_REQUESTED, APPROVED):
        raise FeatureProposalBlocked(
            "state_invalid", f"cannot update a proposal in status {p0.status}"
        )
    new_ops = [dict(op) for op in (ops or p0.ops)]
    findings = validate_proposal_ops(new_ops)
    if findings:
        raise FeatureProposalBlocked("invalid_ops", "proposal ops rejected", {"findings": findings})
    if any(op.get("op") == "merge_features" for op in new_ops):
        merged_ids = feature_ids if feature_ids is not None else p0.feature_ids
        if len(new_ops) > 1 or not merged_ids or len(merged_ids) < 2:
            raise FeatureProposalBlocked(
                "invalid_op",
                "merge_features must be the sole op with explicit feature_ids (>=2 parents)",
            )
    # 归一化到 base 身份空间（ops 可能带自然 id；base 是稳定的身份锚）。
    base_payload = load_base_snapshot(p0.base)
    new_feature_ids: Optional[List[str]] = p0.feature_ids
    if extract_payload_fc(base_payload) is not None:
        from app.services.feature_proposals.candidate import normalize_op_wfids

        ids_for_resolve = feature_ids if feature_ids is not None else p0.feature_ids
        ordered, resolve_missing, mat_fc = resolve_candidate_targets(
            base_payload, new_ops, dataset_fingerprint=p0.base.fingerprint,
            requested_ids=ids_for_resolve,
        )
        if resolve_missing:
            raise FeatureProposalBlocked(
                "feature_not_found", "op targets not resolvable on the proposal base",
                {"missing": resolve_missing[:4]},
            )
        from app.services.feature_proposals.contracts import MAX_PROPOSAL_TARGETS

        if len(ordered) > MAX_PROPOSAL_TARGETS:
            raise FeatureProposalBlocked(
                "target_limit_exceeded", "proposal targets exceed the budget",
                {"limit": MAX_PROPOSAL_TARGETS},
            )
        new_ops = normalize_op_wfids(new_ops, mat_fc)
        if feature_ids is not None:
            from app.services.feature_proposals.candidate import resolve_ids_on_payload

            fmap, _ = resolve_ids_on_payload(
                base_payload, [str(i) for i in feature_ids], p0.base.fingerprint,
            )
            new_feature_ids = list(dict.fromkeys(
                fmap.get(str(i), str(i)) for i in feature_ids
            )) or None

    def _mutate(p: FeatureProposalRecord) -> FeatureProposalRecord:
        # R1-P2-7：锁内复核状态（TOCTOU —— 并发 withdraw/reject 不得被覆盖）。
        if p.status not in (DRAFT, SUBMITTED, CHANGES_REQUESTED, APPROVED):
            raise FeatureProposalConflict(
                "state_conflict",
                f"cannot update a proposal in status {p.status} (changed concurrently)",
                {"current": p.status},
            )
        if ops is not None:
            p.ops = new_ops
        if feature_ids is not None:
            p.feature_ids = new_feature_ids
        if title is not None:
            p.title = title.strip() or p.title
        p.revision += 1
        if p.status == APPROVED:
            p.status = SUBMITTED  # 内容变更 → 重新审阅
        p.candidate_digest = candidate_digest(
            p.ops, [r.model_dump() for r in p.resolutions],
            base_fingerprint=p.base.fingerprint,
            base_revision=p.base.content_revision, op_seed=p.op_seed,
        )
        p.updated_at = _now()
        # 内容变了 → 旧批准失效。
        if p.approval is not None and (
            p.approval.approved_digest != p.candidate_digest
            or p.approval.approved_revision != p.revision
        ):
            p.approval = None
        _append_decision(p, ProposalDecision(
            action="rebase" if ops is None else "submit",
            actor_kind=actor_kind, actor_id=actor_id, at=_now(),
            approved_revision=p.revision,
        ))
        return p

    return await proposal_store.mutate_proposal(
        session_id, proposal_id, _mutate, expected_revision=expected_revision,
    )


async def submit_resolutions(
    session_id: str, proposal_id: str, *,
    expected_revision: int,
    resolutions: List[Dict[str, Any]],
    actor_kind: str, actor_id: str,
) -> Dict[str, Any]:
    """提交显式裁决 —— 应用到候选 ops（新 revision + digest；丢弃留决策记录）。"""
    from app.services.feature_proposals.rebase import apply_resolutions_to_ops

    p = await _get_or_404(session_id, proposal_id)
    if p.status not in (SUBMITTED, CHANGES_REQUESTED):
        raise FeatureProposalBlocked(
            "state_invalid", f"resolutions require status submitted/changes_requested, got {p.status}"
        )
    if len(p.resolutions) + len(resolutions) > 64:
        raise FeatureProposalBlocked(
            "resolution_history_full",
            "resolution history is full — withdraw and create a fresh proposal",
        )
    report = await _current_report(session_id, p)
    new_ops, applied, rejected = apply_resolutions_to_ops(
        p.ops, resolutions, report.get("conflicts") or [], base_payload=load_base_snapshot(p.base),
    )
    if rejected:
        raise FeatureProposalBlocked(
            "resolution_rejected",
            "one or more resolutions cannot be applied — draft preserved",
            {"rejected": rejected[:8]},
        )
    if not new_ops:
        raise FeatureProposalBlocked(
            "resolution_emptied",
            "all proposed changes were dropped — withdraw the proposal instead",
        )
    # R1-P0-1：裁决可能追加 op（use_new_value 的 _set_field_value）—— 重新
    # 校验 ops 上限（pydantic 只在加载时校验，无界 append 会破坏存储）。
    post_findings = validate_proposal_ops(new_ops)
    if post_findings:
        raise FeatureProposalBlocked(
            "resolution_rejected",
            "resolutions would produce an invalid op set (too many ops) — "
            "reduce the proposal first",
            {"findings": post_findings[:4]},
        )
    # 裁决者身份由服务端上下文填充（R1-P1-3：客户端不声明 decided_by）。
    stamped: List[Dict[str, Any]] = []
    for r in resolutions:
        item = dict(r)
        item["decided_by"] = actor_id   # R2-5：审计归属服务端无条件覆盖
        item["decided_at"] = _now()
        stamped.append(item)
    new_resolutions = p.resolutions + [
        ProposalResolution.model_validate(r) for r in stamped
    ]

    def _mutate(rec: FeatureProposalRecord) -> FeatureProposalRecord:
        if rec.status not in (SUBMITTED, CHANGES_REQUESTED):
            raise FeatureProposalConflict(
                "state_conflict",
                f"cannot resolve a proposal in status {rec.status} (changed concurrently)",
                {"current": rec.status},
            )
        rec.ops = new_ops
        rec.resolutions = new_resolutions
        rec.revision += 1
        rec.candidate_digest = candidate_digest(
            rec.ops, [r.model_dump() for r in rec.resolutions],
            base_fingerprint=rec.base.fingerprint,
            base_revision=rec.base.content_revision, op_seed=rec.op_seed,
        )
        rec.updated_at = _now()
        rec.approval = None  # 内容变了 → 重新批准
        _append_decision(rec, ProposalDecision(
            action="resolve", actor_kind=actor_kind, actor_id=actor_id, at=_now(),
            approved_revision=rec.revision,
            detail={"applied": applied[:16]},
        ))
        return rec

    updated = await proposal_store.mutate_proposal(
        session_id, proposal_id, _mutate, expected_revision=expected_revision,
    )
    return {"proposal": proposal_summary(updated), "applied": applied}


async def _current_report(session_id: str, p: FeatureProposalRecord) -> Dict[str, Any]:
    from app.services.data_ingest.pipeline import compute_payload_fingerprint
    from app.services.feature_edit.datasets import extract_geometry_type_declaration
    from app.services.session_data import session_data_manager

    current_payload = await session_data_manager.get(session_id, p.ref_id)
    if current_payload is None:
        raise FeatureProposalBlocked(
            "dataset_not_found", "proposal target ref no longer resolvable",
        )
    dep_payloads: Dict[str, Any] = {}
    cur_dep_fps: Dict[str, str] = {}
    for ref in bridge._dep_refs(p.ops):
        dep = await session_data_manager.get(session_id, ref)
        if dep is not None:
            dep_payloads[ref] = dep
            cur_dep_fps[ref] = compute_payload_fingerprint(dep)
    return await bridge.build_conflict_report(
        p,
        current_payload=current_payload,
        current_fingerprint=compute_payload_fingerprint(current_payload),
        base_payload=load_base_snapshot(p.base),
        dep_payloads=dep_payloads,
        dep_fingerprints_current=cur_dep_fps,
        geometry_type=extract_geometry_type_declaration(current_payload),
    )


# ── rebase ──────────────────────────────────────────────────────────────────


async def rebase_proposal(
    session_id: str, proposal_id: str, *,
    expected_revision: int, apply: bool,
    actor_kind: str, actor_id: str,
) -> Dict[str, Any]:
    """rebase 到当前数据：全部冲突已裁决（或 clean）才可执行。

    新 base = 当前载荷快照；新 revision/digest；旧批准失效。裁决应用后
    候选在**新 base** 上必须可求值（复检，失败即拒绝 — 不留坏候选）。
    """
    from app.services.data_ingest.pipeline import compute_payload_fingerprint
    from app.services.feature_edit.identity import has_wfids
    from app.services.session_data import session_data_manager

    p = await _get_or_404(session_id, proposal_id)
    if p.status not in (SUBMITTED, CHANGES_REQUESTED, APPROVED):
        raise FeatureProposalBlocked(
            "state_invalid", f"cannot rebase a proposal in status {p.status}"
        )
    report = await _current_report(session_id, p)
    unresolved = report.get("unresolved_conflicts") or []
    if unresolved and not apply:
        from app.services.feature_proposals.rebase import rebase_preview

        return rebase_preview(report=report, resolutions=[r.model_dump() for r in p.resolutions])
    if unresolved and apply:
        raise FeatureProposalConflict(
            "unresolved_conflicts",
            "resolve all conflicts before applying a rebase",
            {"remaining": [c.get("conflict_id") for c in unresolved[:8]]},
        )
    if not apply:
        from app.services.feature_proposals.rebase import rebase_preview

        return rebase_preview(report=report, resolutions=[])

    # apply=True 且 clean → 迁移 base 到当前。
    payload = await session_data_manager.get(session_id, p.ref_id)
    if payload is None:
        raise FeatureProposalBlocked("dataset_not_found", "target ref no longer resolvable")
    descriptor = await session_data_manager.get_ref_descriptor(session_id, p.ref_id) or {}
    fingerprint = compute_payload_fingerprint(payload)
    fc = extract_payload_fc(payload)
    new_base = capture_base_snapshot(
        session_id, p.ref_id, payload,
        content_revision=int(descriptor.get("content_revision") or 0),
        fingerprint=fingerprint, materialized=fc is not None and has_wfids(fc),
    )
    new_base.dep_fingerprints = await _dep_fingerprints_for(session_id, p.ops)

    def _mutate(rec: FeatureProposalRecord) -> FeatureProposalRecord:
        rec.base = new_base
        rec.revision += 1
        rec.candidate_digest = candidate_digest(
            rec.ops, [r.model_dump() for r in rec.resolutions],
            base_fingerprint=new_base.fingerprint,
            base_revision=new_base.content_revision, op_seed=rec.op_seed,
        )
        rec.updated_at = _now()
        rec.approval = None
        rec.status = SUBMITTED
        rec.decisions.append(ProposalDecision(
            action="rebase", actor_kind=actor_kind, actor_id=actor_id, at=_now(),
            approved_revision=rec.revision,
            detail={"new_base_fingerprint": new_base.fingerprint,
                    "new_base_revision": new_base.content_revision},
        ))
        return rec

    updated = await proposal_store.mutate_proposal(
        session_id, proposal_id, _mutate, expected_revision=expected_revision,
    )
    return {
        "proposal": proposal_summary(updated),
        "rebased_to": snapshot_identity_summary(new_base),
    }


# ── 审阅决定 ────────────────────────────────────────────────────────────────


async def decide(
    session_id: str, proposal_id: str, *,
    action: str, note: Optional[str],
    actor_kind: str, actor_id: str,
) -> Dict[str, Any]:
    """审阅决定（submit/approve/request_changes/reject/withdraw）。

    actor_kind 恒由服务端 auth 上下文传入；agent 调用（工具面）没有此入口。
    approve 绑定（digest, revision, base 指纹 + 当前指纹快照）—— 内容变更
    后旧批准自动失效。
    """
    p = await _get_or_404(session_id, proposal_id)
    target_status = {
        "submit": SUBMITTED,
        "approve": APPROVED,
        "request_changes": CHANGES_REQUESTED,
        "reject": "rejected",
        "withdraw": WITHDRAWN,
    }.get(action)
    if target_status is None:
        raise FeatureProposalBlocked("invalid_action", f"unknown decision action {action}")
    if not can_transition(p.status, target_status):
        raise FeatureProposalConflict(
            "state_conflict",
            f"cannot {action} a proposal in status {p.status}",
            {"current": p.status, "target": target_status},
        )
    approval_payload: Optional[ProposalDecision] = None
    detail: Dict[str, Any] = {}
    if action == "approve":
        # 批准前强制冲突复核：verdict 必须 clean。
        report = await _current_report(session_id, p)
        if report["verdict"] != "clean":
            raise FeatureProposalConflict(
                "unresolved_conflicts",
                "cannot approve a proposal with unresolved conflicts — resolve/rebase first",
                {"verdict": report["verdict"],
                 "conflicts": report["unresolved_conflicts"][:8]},
            )
        from app.services.data_ingest.pipeline import compute_payload_fingerprint
        from app.services.session_data import session_data_manager

        current_payload = await session_data_manager.get(session_id, p.ref_id)
        cur_descriptor = await session_data_manager.get_ref_descriptor(session_id, p.ref_id) or {}
        detail = {
            "current_fingerprint_at_approval": (
                compute_payload_fingerprint(current_payload) if current_payload is not None else None
            ),
            "current_revision_at_approval": int(cur_descriptor.get("content_revision") or 0),
            "ops_count": len(p.ops),
        }
        approval_payload = ProposalDecision(
            action="approve", actor_kind=actor_kind, actor_id=actor_id, at=_now(),
            approved_digest=p.candidate_digest, approved_revision=p.revision,
            note=note, detail=detail,
        )

    def _mutate(rec: FeatureProposalRecord) -> FeatureProposalRecord:
        # R1-P2-7：锁内复核迁移（并发 withdraw/reject 不得被覆盖出非法终态复活）。
        if not can_transition(rec.status, target_status):
            raise FeatureProposalConflict(
                "state_conflict",
                f"cannot {action} a proposal in status {rec.status} (changed concurrently)",
                {"current": rec.status, "target": target_status},
            )
        rec.status = target_status
        rec.updated_at = _now()
        decision = approval_payload or ProposalDecision(
            action=action.replace("request_changes", "request_changes"),
            actor_kind=actor_kind, actor_id=actor_id, at=_now(), note=note,
        )
        _append_decision(rec, decision)
        if action == "approve":
            rec.approval = decision
        if action == "withdraw":
            rec.withdrawn_at = _now()
        return rec

    updated = await proposal_store.mutate_proposal(session_id, proposal_id, _mutate)
    return {"proposal": proposal_summary(updated)}


# ── 合并（服务端编排 bridge）───────────────────────────────────────────────


async def merge(
    session_id: str, proposal_id: str, *,
    actor_kind: str, actor_id: str,
) -> Dict[str, Any]:
    """approved → merging → merged（提交桥 + 恢复判据 + 持久回执）。"""
    p = await _get_or_404(session_id, proposal_id)
    # 幂等：已 merged → 重放持久回执（不依赖 journal 仍在，P20）。
    if p.status == MERGED and p.merge is not None and p.merge.receipt:
        return {
            "status": MERGED, "already_merged": True,
            "receipt": p.merge.receipt,
            "proposal_id": proposal_id,
        }
    if p.status == RECOVERY_REQUIRED:
        # 盲重试阻断（P19）：recovery_required 只能经 resolve_recovery 的
        # 显式证据判据离开，merge 入口直接拒绝。
        raise FeatureProposalConflict(
            "recovery_required",
            "proposal is in recovery_required — inspect the recovery view and use "
            "resolve-recovery (evidence-gated) before any retry",
            {"proposal_id": proposal_id},
        )
    if p.status == MERGING:
        # R1-P0-2：进程在 merging 窗口内硬崩溃（kill -9/断电）后没有进程内
        # 异常处理可走 —— 这里把 merging 路由到恢复判据（executed → 完成为
        # merged；not_executed → 回 approved；unprovable → recovery_required），
        # 提案不再永久卡死，且绝不盲目重执行。
        return await _settle_failed_merge(
            session_id, proposal_id, p, actor_kind, actor_id,
        )
    if not can_transition(p.status, MERGING):
        raise FeatureProposalConflict(
            "state_conflict",
            f"merge requires approved status, got {p.status}",
            {"current": p.status},
        )

    # 第一步：approved → merging（租约同写；attempt 记录）。
    def _to_merging(rec: FeatureProposalRecord) -> FeatureProposalRecord:
        # R1-P2-7：锁内复核（并发 withdraw/reject 不得被 merging 覆盖复活）。
        if not can_transition(rec.status, MERGING):
            raise FeatureProposalConflict(
                "state_conflict",
                f"merge requires approved status, got {rec.status} (changed concurrently)",
                {"current": rec.status},
            )
        rec.status = MERGING
        rec.merge_attempts += 1
        rec.merge = bridge_merge_outcome(rec, status="unknown")
        rec.updated_at = _now()
        _append_decision(rec, ProposalDecision(
            action="merge_start", actor_kind=actor_kind, actor_id=actor_id, at=_now(),
            approved_revision=rec.revision, approved_digest=rec.candidate_digest,
        ))
        return rec

    merging_rec = await proposal_store.mutate_proposal(session_id, proposal_id, _to_merging)

    # 第二步：提交桥。
    try:
        receipt = await bridge.merge_proposal(merging_rec, session_id=session_id, actor_id=actor_id)
    except FeatureProposalError:
        # typed 失败（冲突/阻断/未知窗口）→ 恢复判据决定终态。
        return await _settle_failed_merge(
            session_id, proposal_id, merging_rec, actor_kind, actor_id,
        )
    except Exception:  # noqa: BLE001 — 意外异常与进程崩溃同窗口 → 恢复判据
        logger.exception(
            "[feature_proposals] unexpected failure in merge window for %s",
            proposal_id,
        )
        return await _settle_failed_merge(
            session_id, proposal_id, merging_rec, actor_kind, actor_id,
        )
    return await _finalize_merged(session_id, proposal_id, merging_rec, receipt, actor_kind, actor_id)


async def _settle_failed_merge(
    session_id: str, proposal_id: str,
    merging_rec: FeatureProposalRecord,
    actor_kind: str, actor_id: str,
):
    """提交窗口失败后的统一恢复判定（executed → merged；not_executed → approved
    允许重试；unprovable → recovery_required 阻断盲重试）。"""
    verdict, receipt_hit = await bridge.assess_recovery(merging_rec, session_id=session_id)
    if verdict == "executed" and receipt_hit is not None:
        return await _finalize_merged(session_id, proposal_id, merging_rec, receipt_hit, actor_kind, actor_id)
    if verdict == "not_executed":
        def _back(rec: FeatureProposalRecord) -> FeatureProposalRecord:
            rec.status = APPROVED
            if rec.merge is not None:
                rec.merge.status = "failed"
            rec.updated_at = _now()
            rec.decisions.append(ProposalDecision(
                action="merge_failed", actor_kind="system", actor_id="system", at=_now(),
                detail={"verdict": "not_executed"},
            ))
            return rec
        await proposal_store.mutate_proposal(session_id, proposal_id, _back)
        raise FeatureProposalConflict(
            "merge_failed_proven_not_executed",
            "merge did not touch the data (proven); proposal back to approved — retry is safe",
        )

    def _recovery(rec: FeatureProposalRecord) -> FeatureProposalRecord:
        rec.status = RECOVERY_REQUIRED
        rec.updated_at = _now()
        rec.decisions.append(ProposalDecision(
            action="merge_failed", actor_kind="system", actor_id="system", at=_now(),
            detail={"verdict": "unprovable"},
        ))
        return rec
    await proposal_store.mutate_proposal(session_id, proposal_id, _recovery)
    raise FeatureProposalConflict(
        "merge_window_unknown",
        "merge outcome could not be proven — proposal is in recovery_required; "
        "see the recovery view before any retry",
    )


def bridge_merge_outcome(rec: FeatureProposalRecord, *, status: str) -> Any:
    from app.schemas.feature_proposal_schema import MergeOutcome

    return MergeOutcome(
        attempt=rec.merge_attempts,
        started_at=_now(),
        status=status,
        idempotency_key=f"fpr:{rec.proposal_id}:{rec.revision}:{rec.candidate_digest[:12]}",
        approval_digest=rec.candidate_digest,
        base_revision_at_merge=rec.base.content_revision,
    )


async def _finalize_merged(
    session_id: str, proposal_id: str,
    merging_rec: FeatureProposalRecord, receipt: Dict[str, Any],
    actor_kind: str, actor_id: str,
) -> Dict[str, Any]:
    def _merged(rec: FeatureProposalRecord) -> FeatureProposalRecord:
        rec.status = MERGED
        rec.updated_at = _now()
        if rec.merge is not None:
            rec.merge.status = "succeeded"
            rec.merge.finished_at = _now()
            rec.merge.receipt = receipt
            rec.merge.recovery_state = "replayable"
        _append_decision(rec, ProposalDecision(
            action="merge_done", actor_kind=actor_kind, actor_id=actor_id, at=_now(),
            approved_revision=rec.revision, approved_digest=rec.candidate_digest,
            detail={"entry_seq": receipt.get("entry_seq"),
                    "content_revision_after": receipt.get("content_revision_after")},
        ))
        return rec

    updated = await proposal_store.mutate_proposal(session_id, proposal_id, _merged)
    return {
        "status": MERGED,
        "proposal_id": proposal_id,
        "receipt": receipt,
        "proposal": proposal_summary(updated),
    }


async def recovery_view(session_id: str, proposal_id: str) -> Dict[str, Any]:
    """recovery_required 的只读证据视图（受影响对象/原候选/当前版本/缺失证据）。"""
    p = await _get_or_404(session_id, proposal_id)
    # merging 同样可看（R1-P0-2：硬崩溃后 merging 是 recovery 的前置态）。
    if p.status not in (RECOVERY_REQUIRED, MERGING):
        raise FeatureProposalBlocked(
            "state_invalid",
            f"recovery view requires recovery_required/merging, got {p.status}",
        )
    verdict, receipt = await bridge.assess_recovery(p, session_id=session_id)
    return {
        "proposal_id": proposal_id,
        "verdict": verdict,
        "receipt": receipt,
        "merge": p.merge.model_dump(mode="json") if p.merge else None,
        "base": snapshot_identity_summary(p.base),
        "candidate_digest": p.candidate_digest,
        "ops_summary": [str(op.get("op")) for op in p.ops],
        "missing_evidence": (
            [] if verdict != "unprovable" else [
                "no journal entry with the server idempotency key",
                "no persisted success receipt",
                "payload fingerprint differs from approval-time fingerprint (or missing)",
            ]
        ),
        "guidance": (
            "compare the affected features against the candidate; if the data matches the "
            "candidate outcome, mark recovered via support tooling; otherwise re-create a "
            "fresh proposal — blind retry is disabled"
        ),
    }


async def resolve_recovery(
    session_id: str, proposal_id: str, *,
    actor_kind: str, actor_id: str,
) -> Dict[str, Any]:
    """recovery_required 的**证据门**恢复：只有判据可证时才允许离开。

    - executed（journal 幂等键/持久回执）→ 完成为 merged（相同回执）；
    - not_executed（指纹未变 + journal 无键）→ 回 approved 允许重试；
    - unprovable → 拒绝（继续阻断；人工核对后另行新建提案）。
    """
    p = await _get_or_404(session_id, proposal_id)
    if p.status not in (RECOVERY_REQUIRED, MERGING):
        raise FeatureProposalBlocked(
            "state_invalid",
            f"resolve-recovery requires recovery_required/merging, got {p.status}",
        )
    verdict, receipt_hit = await bridge.assess_recovery(p, session_id=session_id)
    if verdict == "executed" and receipt_hit is not None:
        return await _finalize_merged(
            session_id, proposal_id, p, receipt_hit, actor_kind, actor_id,
        )
    if verdict == "not_executed":
        def _back(rec: FeatureProposalRecord) -> FeatureProposalRecord:
            rec.status = APPROVED
            rec.updated_at = _now()
            _append_decision(rec, ProposalDecision(
                action="recover", actor_kind=actor_kind, actor_id=actor_id, at=_now(),
                detail={"verdict": "not_executed"},
            ))
            return rec
        updated = await proposal_store.mutate_proposal(session_id, proposal_id, _back)
        return {"proposal": proposal_summary(updated), "recovered_to": APPROVED}
    raise FeatureProposalConflict(
        "recovery_unprovable",
        "recovery evidence is still unprovable — compare the affected features against the "
        "candidate in the recovery view; create a fresh proposal if needed",
    )


# ── 评论与审计 ──────────────────────────────────────────────────────────────


async def add_comment(
    session_id: str, proposal_id: str, *,
    body: str, author_kind: str, author_id: str,
) -> Dict[str, Any]:
    from app.services.feature_proposals.contracts import MAX_COMMENTS_PER_PROPOSAL

    def _mutate(rec: FeatureProposalRecord) -> FeatureProposalRecord:
        # R1-P0-1：comments 上限在变更内强制（pydantic 只在加载时校验）。
        if len(rec.comments) >= MAX_COMMENTS_PER_PROPOSAL:
            raise FeatureProposalBlocked(
                "comment_limit_reached",
                f"proposal already holds {MAX_COMMENTS_PER_PROPOSAL} comments",
            )
        rec.comments.append(ProposalComment(
            comment_id=_new_comment_id(), author_kind=author_kind, author_id=author_id,
            body=body, at=_now(), revision=rec.revision,
        ))
        rec.updated_at = _now()
        return rec

    updated = await proposal_store.mutate_proposal(session_id, proposal_id, _mutate)
    return {"comments": [c.model_dump(mode="json") for c in updated.comments[-10:]]}


async def export_audit(session_id: str, proposal_id: str) -> Dict[str, Any]:
    """有界审阅证据导出（allowlist 投影 —— 不含内部存储路径/凭据）。"""
    p = await _get_or_404(session_id, proposal_id)
    return {
        "proposal_id": p.proposal_id,
        "ref_id": p.ref_id,
        "title": p.title,
        "author": {"kind": p.author_kind, "id": p.author_id},
        "status": p.status,
        "revision": p.revision,
        "candidate_digest": p.candidate_digest,
        "base": snapshot_identity_summary(p.base),
        "decisions": [
            {
                "action": d.action, "actor_kind": d.actor_kind, "actor_id": d.actor_id,
                "at": d.at, "approved_digest": d.approved_digest,
                "approved_revision": d.approved_revision, "note": d.note,
            }
            for d in p.decisions
        ],
        "resolutions": [r.model_dump(mode="json") for r in p.resolutions],
        "merge": (
            {
                "status": p.merge.status,
                "idempotency_key": p.merge.idempotency_key,
                "approval_digest": p.merge.approval_digest,
                "base_revision_at_merge": p.merge.base_revision_at_merge,
                "finished_at": p.merge.finished_at,
                "receipt_entry_seq": (p.merge.receipt or {}).get("entry_seq"),
                "receipt_content_revision_after": (p.merge.receipt or {}).get("content_revision_after"),
            }
            if p.merge else None
        ),
        "exported_at": _now(),
    }


# ── 生命周期：撤回与快照引用释放 ────────────────────────────────────────────


async def withdraw(
    session_id: str, proposal_id: str, *,
    actor_kind: str, actor_id: str, note: Optional[str],
) -> Dict[str, Any]:
    result = await decide(
        session_id, proposal_id, action="withdraw", note=note,
        actor_kind=actor_kind, actor_id=actor_id,
    )
    # 快照引用释放：先在锁内标记 released（同一原子写），再查共享 ——
    # list_snapshot_keys 跳过已 released 提案，因此本提案自身的 key 不再
    # 出现在 live 集（R1-P2-6：此前自引用导致唯一引用的 blob 永不删除）。
    # 物理 blob 是 CAS（内容寻址），仅当没有其他活跃提案引用同一 key 时
    # 才删（共享 blob 不得因一项提案回收，P27）。
    p = await _get_or_404(session_id, proposal_id)
    if p.status == WITHDRAWN and not p.snapshot_released:
        def _release(rec: FeatureProposalRecord) -> FeatureProposalRecord:
            if rec.status != WITHDRAWN:
                raise FeatureProposalConflict(
                    "state_conflict",
                    f"cannot release a proposal in status {rec.status}",
                    {"current": rec.status},
                )
            rec.snapshot_released = True
            rec.updated_at = _now()
            return rec
        await proposal_store.mutate_proposal(session_id, proposal_id, _release)
        live_keys = await proposal_store.list_snapshot_keys(session_id)
        key = p.base.snapshot_key
        still_referenced = key in live_keys
        deleted = False
        if not still_referenced:
            from app.services.feature_proposals.snapshot import _blob_store

            try:
                deleted = _blob_store().delete_blob(key)
            except Exception:  # noqa: BLE001 — 物理删除失败不阻断治理态
                deleted = False
        result["snapshot_released"] = True
        result["snapshot_blob_deleted"] = deleted
    return result
