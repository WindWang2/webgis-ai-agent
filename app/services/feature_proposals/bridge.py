"""提交桥 —— 批准绑定与真实 G14 提交（G32 WP07/WP08）。

merge 是唯一写路径之外的**唯一受控入口**（仍走 G14 executor.commit_plan 的
锁/CAS/derive/journal/盖章全链，绝不旁路 overwrite）。流程：

1. 复核：proposal status=approved 且 approval.digest == 当前 candidate_digest
   且 approval.revision == 当前 revision（内容变了旧批准失效 —— 不存在
   「批准过一次、后续换内容仍可提交」）。
2. 冲突复核：在当前载荷上重跑 classify_conflicts —— verdict 必须 clean
   （未解决冲突拒绝；依赖变化拒绝）。
3. 提交：build_plan(plan_seed=op_seed)（出生身份与候选一致）→
   executor.commit_plan(idempotency_key=f"fpr:{pid}:{revision}:{digest12}")。
   幂等键由服务端绑定提案版本 —— 重试/双击命中 journal 幂等重放。
4. 回执：持久 MergeOutcome 到提案（与 journal 幂等双保险 —— journal 是
   LRU/易失的，长期提案重放**只**依赖持久回执，P20）。

崩溃窗口纪律（任务 §六）：提交前先落 merging + merge attempt 记录（租约同
写）；commit_plan 抛出**非 typed 冲突**异常（网络/进程崩溃等价）时进入
recovery 判定 recover_or_raise：
- journal/receipt 能证已执行 → 恢复相同回执（幂等键命中重放）；
- 能证未执行（载荷指纹未变且 journal 无键）→ 回 approved 允许重试；
- 无法证明 → recovery_required（阻断盲重试，提供只读证据视图）。
绝不能因 HTTP 失败就再次执行；绝不为凑状态覆盖用户新编辑。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.feature_proposal_schema import FeatureProposalRecord
from app.services.feature_proposals.candidate import (
    materialize_preview,
    resolve_candidate_targets,
)
from app.services.feature_proposals.conflicts import (
    check_dependency_changes,
    classify_conflicts,
)
from app.services.feature_proposals.contracts import (
    FeatureProposalBlocked,
    FeatureProposalConflict,
)

logger = logging.getLogger(__name__)


def _dep_refs(ops: List[Dict[str, Any]]) -> List[str]:
    refs: List[str] = []
    for op in ops:
        if isinstance(op, dict) and op.get("op") == "snap_to_ref" and isinstance(op.get("ref_id"), str):
            refs.append(op["ref_id"])
    return list(dict.fromkeys(refs))


async def build_conflict_report(
    proposal: FeatureProposalRecord,
    *,
    current_payload: Any,
    current_fingerprint: str,
    base_payload: Any,
    dep_payloads: Optional[Dict[str, Any]] = None,
    dep_fingerprints_current: Optional[Dict[str, str]] = None,
    geometry_type: Optional[str] = None,
) -> Dict[str, Any]:
    """在给定当前载荷上组装完整冲突报告（含依赖比较）。"""
    from app.services.feature_proposals.candidate import extract_payload_fc

    base_fc = extract_payload_fc(base_payload)
    base_mat, _ = materialize_preview(base_fc, proposal.base.fingerprint)
    ordered, _missing, _mat = resolve_candidate_targets(
        base_payload, proposal.ops, dataset_fingerprint=proposal.base.fingerprint,
        requested_ids=proposal.feature_ids,
    )
    dep_payloads = dep_payloads or {}
    report = classify_conflicts(
        base_payload=base_payload,
        base_fingerprint=proposal.base.fingerprint,
        current_payload=current_payload,
        current_fingerprint=current_fingerprint,
        ops=proposal.ops,
        ordered_targets=ordered,
        op_seed=proposal.op_seed,
        resolutions=[r.model_dump() for r in proposal.resolutions],
        target_payloads=dep_payloads,
        geometry_type=geometry_type,
    )
    # 依赖（snap 参考源）指纹比较：base 依赖指纹在创建/rebase 时持久于
    # proposal.base（dep_fingerprints 存 ops_dependencies 键 —— 见 service
    # 捕获；此处从 proposal 决策记录不可得时按缺失处理）。
    refs = _dep_refs(proposal.ops)
    if refs:
        base_fps = dict(proposal.base.dep_fingerprints or {})
        cur_fps = dep_fingerprints_current or {}
        dep_conflicts = check_dependency_changes(
            base_dep_fingerprints=base_fps,
            current_dep_fingerprints=cur_fps,
            dep_refs=refs,
        )
        resolved = {str(r.conflict_id) for r in proposal.resolutions}
        for dc in dep_conflicts:
            if dc["conflict_id"] not in resolved:
                report["conflicts"].append(dc)
                report["unresolved_conflicts"].append(dc)
        if report["unresolved_conflicts"]:
            report["verdict"] = (
                "blocked" if any(
                    c["kind"] in ("schema_incompatible", "identity_unprovable")
                    for c in report["unresolved_conflicts"]
                ) else "conflicted"
            )
    return report


async def merge_proposal(
    proposal: FeatureProposalRecord,
    *,
    session_id: str,
    actor_id: str,
) -> Tuple[FeatureProposalRecord, Dict[str, Any]]:
    """执行合并（服务端复核 → 提交 → 回执落账）。

    返回 (updated_proposal, receipt)。调用方（service）负责状态迁移合法性
    （approved → merging）与持久化；本函数专注提交链与恢复判据。
    """
    from app.services.data_ingest.pipeline import compute_payload_fingerprint
    from app.services.feature_edit import executor as fe_executor
    from app.services.feature_edit.plan import build_plan
    from app.services.feature_proposals.snapshot import load_base_snapshot
    from app.services.session_data import session_data_manager

    # ── 批准绑定复核（内容级）：digest + revision 双绑定。
    approval = proposal.approval
    if approval is None or approval.approved_digest != proposal.candidate_digest \
            or approval.approved_revision != proposal.revision:
        raise FeatureProposalConflict(
            "approval_invalid",
            "approval does not bind the current candidate content — re-approval required",
            {"approved_digest": approval.approved_digest if approval else None,
             "current_digest": proposal.candidate_digest},
        )

    # ── 载荷与冲突复核。
    payload = await session_data_manager.get(session_id, proposal.ref_id)
    if payload is None:
        raise FeatureProposalBlocked(
            "dataset_not_found", "proposal target ref no longer resolvable",
            {"ref_id": proposal.ref_id},
        )
    descriptor = await session_data_manager.get_ref_descriptor(session_id, proposal.ref_id) or {}
    current_revision = int(descriptor.get("content_revision") or 0)
    current_fingerprint = compute_payload_fingerprint(payload)
    base_payload = load_base_snapshot(proposal.base)

    dep_payloads: Dict[str, Any] = {}
    cur_dep_fps: Dict[str, str] = {}
    for ref in _dep_refs(proposal.ops):
        dep_payload = await session_data_manager.get(session_id, ref)
        if dep_payload is not None:
            dep_payloads[ref] = dep_payload
            cur_dep_fps[ref] = compute_payload_fingerprint(dep_payload)

    from app.services.feature_edit.datasets import extract_geometry_type_declaration

    report = await build_conflict_report(
        proposal,
        current_payload=payload,
        current_fingerprint=current_fingerprint,
        base_payload=base_payload,
        dep_payloads=dep_payloads,
        dep_fingerprints_current=cur_dep_fps,
        geometry_type=extract_geometry_type_declaration(payload),
    )
    if report["verdict"] != "clean":
        raise FeatureProposalConflict(
            "unresolved_conflicts",
            "proposal has unresolved conflicts on the current data — resolve and re-approve first",
            {"verdict": report["verdict"],
             "conflicts": report["unresolved_conflicts"][:8]},
        )

    # ── 构造 G14 plan（seed = 持久 op_seed → 出生身份与候选一致）。
    plan, findings = build_plan(
        ref_id=proposal.ref_id,
        payload=payload,
        ops=proposal.ops,
        requested_ids=_proposal_target_ids(proposal),
        selection=None,
        dataset_fingerprint=current_fingerprint,
        content_revision=current_revision,
        target_payloads=dep_payloads,
        plan_seed=proposal.op_seed,
    )
    if not plan or not plan.get("committable"):
        raise FeatureProposalConflict(
            "candidate_not_committable",
            "candidate no longer evaluates to a committable plan on current data",
            {"findings": (findings or [])[:8]},
        )

    idem_key = f"fpr:{proposal.proposal_id}:{proposal.revision}:{proposal.candidate_digest[:12]}"
    try:
        receipt = await fe_executor.commit_plan(
            session_id, plan,
            actor="user", origin="feature_proposal",
            idempotency_key=idem_key,
        )
    except (fe_executor.FeatureEditConflict, fe_executor.FeatureEditBlocked) as exc:
        # typed 冲突/阻断 —— 数据未被写入（CAS/验证拒绝先于 overwrite），
        # 诚实回失败，可重新预览。
        raise FeatureProposalConflict(
            f"upstream_{exc.code}" if isinstance(exc, fe_executor.FeatureEditConflict) else f"blocked_{exc.code}",
            f"G14 commit rejected: {exc.code}",
            {"detail": exc.detail if hasattr(exc, "detail") else {}},
        ) from exc
    except Exception as exc:  # noqa: BLE001 — 网络/进程级失败 → 恢复判据
        logger.error(
            "[feature_proposals] merge window failure for %s: %r — entering recovery",
            proposal.proposal_id, exc,
        )
        verdict, _receipt = await assess_recovery(proposal, session_id=session_id)
        if verdict == "executed":
            return _receipt or {}
        raise FeatureProposalConflict(
            "merge_window_unknown",
            "merge outcome could not be proven — proposal is in recovery_required; "
            "see the recovery view before any retry",
        ) from exc
    return receipt


def _proposal_target_ids(proposal: FeatureProposalRecord) -> List[str]:
    """提交时的 requested_ids：op 级 wfids + split wfid + merge 父集
    （按候选求值序，保 merge first 策略的用户语义）。"""
    ids: List[str] = list(proposal.feature_ids or [])  # plan 级目标（merge 父集）
    for op in proposal.ops:
        if not isinstance(op, dict):
            continue
        if isinstance(op.get("wfid"), str):
            ids.append(op["wfid"])
        raw = op.get("wfids")
        if isinstance(raw, list):
            ids.extend(str(w) for w in raw)
    return list(dict.fromkeys(i for i in ids if i))


async def assess_recovery(
    proposal: FeatureProposalRecord,
    *,
    session_id: str,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """提交窗口失败后的恢复判据（三态，绝不模糊）。

    返回 ("executed", receipt)：journal 幂等键命中或持久成功回执在场 →
    已执行的权威证据，恢复相同回执（不重复写）。
    返回 ("not_executed", None)：journal 可读无键 + 载荷指纹仍等于批准时
    指纹 → 可证未执行，允许回 approved 重试。
    返回 ("unprovable", None)：以上都不满足 —— recovery_required，阻断盲
    重试（只读证据视图：受影响对象/原候选/当前版本/缺失证据）。
    """
    from app.services.data_ingest.pipeline import compute_payload_fingerprint
    from app.services.feature_edit import executor as fe_executor
    from app.services.session_data import session_data_manager

    # 持久成功回执在场（journal 可能已逐出 —— 持久回执才是长期真相，P20）。
    if proposal.merge is not None and proposal.merge.status == "succeeded" and proposal.merge.receipt:
        return "executed", proposal.merge.receipt

    idem_key = f"fpr:{proposal.proposal_id}:{proposal.revision}:{proposal.candidate_digest[:12]}"
    journal_readable = False
    try:
        journals = await fe_executor._load_journals(session_id)
        journal = journals.get(proposal.ref_id)
        journal_readable = journal is not None or proposal.ref_id not in journals
        if journal:
            from app.services.feature_edit.journal import entry_for_idempotency_key

            hit = entry_for_idempotency_key(journal, f"{idem_key}:{proposal.op_seed}")
            if hit is not None:
                receipt = fe_executor._receipt(hit, stamp_status="replayed")
                return "executed", receipt
    except Exception as exc:  # noqa: BLE001 — journal 不可读 = 不可证
        logger.warning(
            "[feature_proposals] journal unreadable during recovery of %s: %r",
            proposal.proposal_id, exc,
        )
        return "unprovable", None

    # journal 在场但无该键：若 ref 的 journal 完全没有该 ref（从未编辑）也
    # 视为可读无键。结合指纹证据判定未执行。
    if not journal_readable:
        return "unprovable", None
    approval = proposal.approval
    pre_fp = None
    if approval is not None and isinstance(approval.detail, dict):
        pre_fp = approval.detail.get("current_fingerprint_at_approval")
    if not pre_fp:
        # 没有批准时指纹证据 —— journal 无键但载荷可能已被写。
        return "unprovable", None
    try:
        payload = await session_data_manager.get(session_id, proposal.ref_id)
    except Exception:  # noqa: BLE001
        return "unprovable", None
    if payload is not None and compute_payload_fingerprint(payload) == pre_fp:
        return "not_executed", None
    return "unprovable", None
