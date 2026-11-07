"""验收桥（C2 / GC-08）—— analysis workflow 运行回执 → goal 验收链。

BASELINE C2 断链：``WorkflowRunState`` 持久化在 ``gis_chapter`` 之外，
``goal_satisfaction`` 只读 chapter 行——族执行成功对目标验收**不可见**。

本桥是**只读投影**（零第二事实源）：

    WorkflowRunState ──project_workflow_rows──▶ chapter analysis_steps 行
        （capability 命名空间化 ``awf.<family>.<step_id>``，与 plan 行
        零冲突；status 直映 satisfied→complete / failed→failed /
        blocked→unavailable / skipped→skipped；bound_ref=envelope ref；
        resolved_algorithm=envelope.method_algorithm_id）

下游（``derive_goal_contract`` / ``build_evidence_registry`` /
``_eval_row_requirement``）**零改动**即消费——节点成功（步骤 satisfied）
≠ 目标达成（map/export/比较义务仍按既有规则裁决）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.gis_harness.analysis_workflows.contracts import (
    AnalysisWorkflowContract,
)
from app.services.gis_harness.analysis_workflows.state import (
    WorkflowRunState,
)

__all__ = [
    "workflow_capability_id",
    "project_workflow_rows",
    "merge_workflow_rows",
    "workflow_disclosures",
]

#: 步骤状态 → chapter 行状态（与 plan_graph._ROW_STATUS_TO_NODE 消费面兼容）。
_STEP_TO_ROW_STATUS = {
    "satisfied": "complete",
    "failed": "failed",
    "blocked": "unavailable",
    "skipped": "skipped",
    "pending": "pending",
    "running": "pending",
    "stale": "pending",
    "cancelled": "failed",
}


def workflow_capability_id(family: str, step_id: str) -> str:
    """族步骤 → 命名空间化 capability（≤64；与 plan 行零冲突）。"""
    return f"awf.{family}.{step_id}"[:64]


def _contract_of(
    run_state: WorkflowRunState,
    contract: Optional[AnalysisWorkflowContract],
) -> Optional[AnalysisWorkflowContract]:
    if contract is not None:
        return contract
    snapshot = run_state.contract_snapshot
    if isinstance(snapshot, dict) and snapshot:
        try:
            return AnalysisWorkflowContract.model_validate(snapshot)
        except Exception:  # noqa: BLE001 — 快照损坏 = 退化（只用 state 面）
            return None
    return None


def project_workflow_rows(
    run_state: WorkflowRunState,
    contract: Optional[AnalysisWorkflowContract] = None,
) -> List[Dict[str, Any]]:
    """WorkflowRunState → chapter analysis_steps 行（纯投影，幂等）。"""
    if not isinstance(run_state, WorkflowRunState):
        return []
    wf_contract = _contract_of(run_state, contract)
    optional_steps = {
        s.step_id: s.optional for s in wf_contract.steps} if wf_contract else {}
    rows: List[Dict[str, Any]] = []
    for step_id in sorted(run_state.steps):
        entry = run_state.steps[step_id]
        envelope = run_state.envelopes.get(step_id)
        status = _STEP_TO_ROW_STATUS.get(
            str(entry.state or "pending"), "pending")
        row: Dict[str, Any] = {
            "capability": workflow_capability_id(run_state.family, step_id),
            "status": status,
            "purpose": f"{run_state.family}:{step_id}"[:200],
            "optional": bool(optional_steps.get(step_id, False)),
        }
        if envelope is not None:
            if envelope.ref:
                row["bound_ref"] = envelope.ref[:128]
            if envelope.method_algorithm_id:
                row["resolved_algorithm"] = envelope.method_algorithm_id[:64]
            row["output_roles"] = list(envelope.output_roles[:4])
        if entry.reason_code:
            row["reason_code"] = entry.reason_code[:64]
        rows.append(row)
    return rows


def merge_workflow_rows(
    chapter: Dict[str, Any],
    runs: Iterable[Tuple[WorkflowRunState,
                         Optional[AnalysisWorkflowContract]]],
) -> Dict[str, Any]:
    """chapter + 族运行行 → 合并章节（返回新 dict；输入不被改写）。

    幂等：capability 已在场（重复投影/重复合并）不追加第二行。
    """
    if not isinstance(chapter, dict):
        chapter = {}
    rows: List[Dict[str, Any]] = [
        r for r in (chapter.get("analysis_steps") or [])
        if isinstance(r, dict)]
    existing = {str(r.get("capability") or "") for r in rows}
    appended = 0
    for run_state, contract in runs:
        for row in project_workflow_rows(run_state, contract):
            cap = str(row.get("capability") or "")
            if not cap or cap in existing:
                continue
            existing.add(cap)
            rows.append(row)
            appended += 1
            if len(rows) >= 64:          # 行上限（对齐 derive 上限 48 + 余量）
                break
        if len(rows) >= 64:
            break
    merged = dict(chapter)
    merged["analysis_steps"] = rows
    merged["analysis_workflow_row_count"] = appended
    return merged


def workflow_disclosures(
    run_state: WorkflowRunState,
) -> List[str]:
    """族运行态 → 有界披露（科学终审 / 部分完成 / 未匹配面）。"""
    out: List[str] = []
    gate = run_state.science_validation or {}
    if gate and str(gate.get("final_gate")) != "pass":
        out.append(
            f"science_final_gate={gate.get('final_gate')} "
            f"(findings_n={gate.get('findings_n', 0)})")
    unmatched = 0
    for envelope in run_state.envelopes.values():
        raw_unmatched = envelope.network.get("unmatched_demand_count")
        if isinstance(raw_unmatched, (int, float)):
            unmatched += int(raw_unmatched)
    if unmatched:
        out.append(f"unmatched_demand_count={unmatched}（部分总体，"
                   "不得当作完整覆盖结论）")
    skipped = [sid for sid, e in sorted(run_state.steps.items())
               if e.state == "skipped"]
    if skipped:
        out.append(
            f"skipped_steps={','.join(skipped[:4])}"
            "（义务缺席的 typed skipped，不伪装输出）")
    return out[:6]


async def load_workflow_runs(
    session_id: str, *, limit: int = 8,
) -> List[WorkflowRunState]:
    """会话 store → 族运行态清单（确定性序；失败诚实空——评估面 fail-open，
    披露由 goal_satisfaction 的证据缺席语义承载）。"""
    if not session_id:
        return []
    try:
        from app.services.session_data import session_data_manager
        from app.services.gis_harness.analysis_workflows.state import (
            state_from_payload,
        )

        refs = await session_data_manager.list_refs(session_id)
    except Exception:  # noqa: BLE001 — store 缺席 = 无运行可见（诚实空）
        return []
    workflows: List[WorkflowRunState] = []
    # refs: ref_id → alias；按 alias 枚举（analysis_workflow.v1:<id>）
    candidates = sorted(
        (alias, ref) for ref, alias in (refs or {}).items()
        if str(alias).startswith("analysis_workflow.v1:")
    )[: max(0, limit)]
    for alias, ref in candidates:
        try:
            resolved = await session_data_manager.resolve_alias(
                session_id, alias)
        except Exception:  # noqa: BLE001
            resolved = ref
        try:
            payload = await session_data_manager.get(session_id, resolved)
        except Exception:  # noqa: BLE001
            continue
        state = state_from_payload(payload if isinstance(payload, dict) else {})
        if state is not None:
            workflows.append(state)
    workflows.sort(key=lambda s: s.workflow_id)
    return workflows


async def evaluation_view_with_workflow_rows(
    session_id: str,
    chapter: Dict[str, Any],
    *,
    limit: int = 8,
) -> Tuple[Dict[str, Any], List[str]]:
    """生产入口：chapter + 族运行行 → 评估视图（不写回持久 chapter）。

    返回 (merged_chapter, disclosures)。零运行/加载失败 → 原样返回
    （既有行为零漂移）。
    """
    runs = await load_workflow_runs(session_id, limit=limit)
    if not runs:
        return chapter, []
    merged = merge_workflow_rows(chapter, [(run, None) for run in runs])
    disclosures: List[str] = []
    for run in runs:
        disclosures.extend(workflow_disclosures(run))
    return merged, disclosures[:6]
