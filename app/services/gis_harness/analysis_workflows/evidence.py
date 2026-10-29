"""StepReceipt —— 每步执行证据（goal-02 M8 的基础件）。

设计（对齐 run_3d_analysis 的 MethodUseContract 摘要先例）：

- receipt = 「已执行证明」：未执行的步骤**不得**出现在 receipt 集合里
  （Agent 只能依据 receipt 断言已发生的事——防文字推断未执行步骤）；
- method_use_contract 摘要 fail-open：算法目录缺失该 id 时 receipt 带
  ``method_use_contract_available=False`` + 警告，不吞分析结果；
- 确定性：无时间戳（turn 序号是会话事实）；指纹引用 recompute 层。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.analysis_workflows.contracts import (
    AnalysisStepSpec,
    AnalysisWorkflowContract,
)

#: 工具 → 算法目录 id（receipt 的 MethodUseContract 锚点；工具族演化
#: 时 additive 扩展——未列工具按 unknown 处理并披露）。
TOOL_ALGORITHM_IDS: Dict[str, Tuple[str, ...]] = {
    "spatial_aggregate": ("spatial.aggregate.admin", "spatial.aggregate.rates"),
    "isochrone_network": ("network.isochrone.local",),
    "overlay_analysis": ("geometry.overlay",),
    "buffer_analysis": ("geometry.buffer",),
    "h3_binning": ("spatial.grid.h3",),
    "hotspot_analysis": ("spatial.hotspot.local",),
    "zonal_stats": ("remote.change.raster",),   # 非本任务族，占位对齐目录
}

#: receipt 有界预算（警告/证据键截断——大科学证据块走 ref 不进 receipt）。
MAX_RECEIPT_WARNINGS = 8
MAX_EVIDENCE_KEYS = 12


class StepReceipt(BaseModel):
    """一个已执行步骤的证据封套（进入 state.receipts 与 Pi 有界投影）。"""

    step_id: str
    workflow_id: str
    tool_name: str
    algorithm_ids: Tuple[str, ...] = ()
    method_use_contract_available: bool = False
    method_use_contract: Dict[str, Any] = Field(default_factory=dict)
    params_applied: Dict[str, Any] = Field(default_factory=dict)
    input_refs: Tuple[str, ...] = ()
    input_fingerprints: Dict[str, str] = Field(default_factory=dict)
    output_ref: str = ""
    output_fingerprint: str = ""
    stats_keys: Tuple[str, ...] = ()
    scientific_evidence_keys: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    executed_in_turn: int = 0


def _method_digest(algorithm_id: str) -> Optional[Dict[str, Any]]:
    """MethodUseContract 摘要（静态目录投影；失败返回 None 不抛）。"""
    try:
        from app.lib.gis.method_use_contract import build_method_use_contract
        contract = build_method_use_contract(algorithm_id)
        return {
            "algorithm_id": contract.algorithm_id,
            "capabilities": list(contract.capabilities),
            "scientific_preconditions": list(contract.scientific_preconditions),
            "assumptions": list(contract.assumptions),
            "limitations": list(contract.limitations),
            "method_references": list(contract.method_references),
        }
    except Exception:  # noqa: BLE001 — 披露面失败不吞执行结果
        return None


def build_step_receipt(
    contract: AnalysisWorkflowContract,
    step: AnalysisStepSpec,
    *,
    result: Dict[str, Any],
    input_refs: Tuple[str, ...],
    input_fingerprints: Dict[str, str],
    output_ref: str,
    output_fingerprint: str,
    executed_in_turn: int = 0,
    extra_warnings: Tuple[str, ...] = (),
) -> StepReceipt:
    """从真实 dispatch 结果构建 receipt（不修饰、不推断）。"""
    algorithm_ids = TOOL_ALGORITHM_IDS.get(step.tool_name, ())
    digest: Dict[str, Any] = {}
    available = False
    for aid in algorithm_ids:
        d = _method_digest(aid)
        if d is not None:
            digest[aid] = d
            available = True
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    stats = result.get("stats") if isinstance(result.get("stats"), dict) else (
        data.get("stats") if isinstance(data.get("stats"), dict) else {})
    sci = result.get("scientific_evidence")
    warnings: List[str] = list(extra_warnings)
    if not available:
        warnings.append(
            f"算法目录无 {step.tool_name} 的 MethodUseContract 映射"
            "（方法适用性披露缺失——不阻断执行，如实披露）")
    return StepReceipt(
        step_id=step.step_id,
        workflow_id=contract.workflow_id,
        tool_name=step.tool_name,
        algorithm_ids=algorithm_ids,
        method_use_contract_available=available,
        method_use_contract=digest,
        params_applied=dict(step.step_params),
        input_refs=tuple(input_refs[:16]),
        input_fingerprints=dict(input_fingerprints),
        output_ref=output_ref,
        output_fingerprint=output_fingerprint,
        stats_keys=tuple(sorted(stats.keys()))[:MAX_EVIDENCE_KEYS],
        scientific_evidence_keys=(
            tuple(sorted(sci.keys()))[:MAX_EVIDENCE_KEYS]
            if isinstance(sci, dict) else ()),
        warnings=tuple(warnings[:MAX_RECEIPT_WARNINGS]),
        executed_in_turn=executed_in_turn,
    )


def receipts_projection(
    receipts: Dict[str, StepReceipt], *, limit: int = 24,
) -> List[Dict[str, Any]]:
    """Pi 上下文有界投影（refs/统计键/警告——不含载荷本体）。"""
    out: List[Dict[str, Any]] = []
    for receipt in list(receipts.values())[:limit]:
        out.append({
            "step_id": receipt.step_id,
            "tool": receipt.tool_name,
            "algorithms": list(receipt.algorithm_ids),
            "method_use_contract_available":
                receipt.method_use_contract_available,
            "output_ref": receipt.output_ref,
            "fingerprint": receipt.output_fingerprint[:16],
            "stats_keys": list(receipt.stats_keys),
            "warnings": list(receipt.warnings),
            "executed_in_turn": receipt.executed_in_turn,
        })
    return out


def evidence_summary(
    contract: AnalysisWorkflowContract,
    receipts: Dict[str, StepReceipt],
) -> Dict[str, Any]:
    """终局证据摘要（喂 G02 final gate 输入投影 / 用户回看）。"""
    executed = set(receipts)
    declared = {s.step_id for s in contract.steps}
    return {
        "workflow_id": contract.workflow_id,
        "family": contract.family,
        "steps_declared": sorted(declared),
        "steps_executed": sorted(executed),
        "steps_not_executed": sorted(declared - executed),
        "all_have_output_ref": all(r.output_ref for r in receipts.values()),
        "method_contracts_available": {
            r.step_id: r.method_use_contract_available
            for r in receipts.values()},
        "warnings": {
            r.step_id: list(r.warnings) for r in receipts.values()
            if r.warnings},
        "risk_notes": list(contract.risk_notes),
    }


#: 执行层科学硬拒前缀（对齐 workflow_engine.SCIENCE_REJECTION_PREFIXES
#: 单一语义——此处只引用不复制判定逻辑）。
def _science_hard_block_reason(reason_code: str) -> str:
    prefixes = (
        "scientific_precondition:", "crs_class_mismatch:",
        "insufficient_features:", "geometry_mismatch:")
    for prefix in prefixes:
        if reason_code.startswith(prefix):
            return reason_code
    return ""


def final_gate_execution_evidence(
    state: Any,
) -> Dict[str, Dict[str, Any]]:
    """运行态 → G02 ``run_science_final_gate`` 的 execution_evidence 投影。

    键契约（science_final_gate.py:11）：``capability`` / ``profile_digest``
    （输入指纹合成——freshness 面）/ ``science_hard_block``（执行层科学
    硬拒前缀命中时非空 → G-SCIENCE-BLOCKED）。
    """
    projection: Dict[str, Dict[str, Any]] = {}
    steps_by_id: Dict[str, Any] = {}
    snapshot_steps = (state.contract_snapshot or {}).get("steps") or []
    for step in snapshot_steps:
        if isinstance(step, dict) and step.get("step_id"):
            steps_by_id[step["step_id"]] = step
    for step_id, entry in state.steps.items():
        spec = steps_by_id.get(step_id, {})
        receipt = state.receipts.get(step_id) or {}
        ev: Dict[str, Any] = {
            "capability": str(spec.get("capability", "") or ""),
            "profile_digest": str(entry.fingerprint or "")[:64],
        }
        block = _science_hard_block_reason(entry.reason_code or "")
        if block:
            ev["science_hard_block"] = block
        elif receipt:
            ev["method_algorithm_ids"] = list(
                receipt.get("algorithm_ids") or [])
        if entry.state == "satisfied" or block:
            projection[step_id] = ev
    return projection


def run_final_gate(state: Any) -> Dict[str, Any]:
    """对工作流运行态跑 G02 终局门（fail-open：门内部绝不抛错）。"""
    from app.lib.gis.science_final_gate import run_science_final_gate
    result = run_science_final_gate(
        plan=None, execution_evidence=final_gate_execution_evidence(state))
    return {
        "blocked": result.blocked,
        "findings": [
            {"code": f.code, "severity": f.severity,
             "subject": f.subject, "detail": f.detail}
            for f in result.findings],
        "disclosures": list(result.disclosures),
    }
