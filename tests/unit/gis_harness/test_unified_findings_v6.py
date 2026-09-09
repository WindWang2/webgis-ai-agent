"""Unified Findings + Completion 单一化（V6 Wave 6–7）回归锁。

不变式（对应 04-unified-findings.md 验收）：
1. 任一 domain finding 可投影为 UnifiedFinding 且不丢 code/evidence（有界）；
2. blocks_completion 单点推导：error 阻断，degradation 域（导出诊断）永不
   阻断，runtime stale 是 warning 但阻断（保守）；
3. Completion verdict 唯一：runtime stale 节点 → READY* 压成 NEEDS_REPAIR；
   无运行态块的旧章节语义零漂移（parity）；
4. analysis 维把 runtime stale 作为硬输入（工具成功不再充分）。
"""
from __future__ import annotations

from typing import Any, Dict

from app.services.gis_harness.completion.contracts import (
    F_ARTIFACT_EXPIRED,
    F_LAYER_MISSING,
    F_RENDER_SOURCE_MISSING,
    STATUS_COMPLETE,
    STATUS_FAILED,
    VERDICT_BLOCKED_BY_DATA,
    VERDICT_NEEDS_REPAIR,
    VERDICT_READY,
    MapCompletionFinding,
    MapCompletionResult,
    derive_product_verdict,
    evaluate_completion_contract,
)
from app.services.gis_harness.completion.unified_findings import (
    REPAIR_RECOMPUTE_NODE,
    collect_unified_findings,
    from_completion_finding,
    from_render_diagnostic,
    from_runtime_node,
    stale_runtime_nodes,
)
from app.services.gis_harness.runtime_bridge import WORKFLOW_RUNTIME_KEY


def _runtime_block(*node_specs: Dict[str, Any]) -> Dict[str, Any]:
    return {"schema": "workflow_runtime.v1",
            "nodes": [dict(n) for n in node_specs]}


def _stale_node(node_id: str = "cap:density_mapping",
                reason: str = "evidence_drift") -> Dict[str, Any]:
    return {"node_id": node_id, "kind": "analysis", "state": "stale",
            "stale_reason": reason}


# ── 1. 投影保真 ──────────────────────────────────────────────────────────

def test_completion_finding_projection() -> None:
    f = MapCompletionFinding(
        code=F_ARTIFACT_EXPIRED, severity="error", target="ref:x-1",
        detail="artifact expired")
    uf = from_completion_finding(f)
    d = uf.to_dict()
    assert d["domain"] == "harness_finalizer"
    assert d["code"] == F_ARTIFACT_EXPIRED
    assert d["scope"] == "data"
    assert d["affected_entity"] == "ref:x-1"
    assert d["evidence"] == "artifact expired"
    assert d["repair_class"] == "retry"
    assert d["retryable"] is True
    assert d["blocks_completion"] is True
    assert d["degradation_only"] is False


def test_render_diagnostic_projection_never_blocks() -> None:
    uf = from_render_diagnostic({
        "code": "export_timeout_partial", "severity": "error",
        "detail": "timeout at 30s"})
    assert uf.domain == "render_diagnostic"
    assert uf.severity == "error"
    assert uf.degradation_only is True
    assert uf.blocks_completion is False  # 导出降级不阻断 live 完成


def test_runtime_node_projection_stale_blocks() -> None:
    uf = from_runtime_node(_stale_node())
    assert uf is not None
    assert uf.domain == "workflow_runtime"
    assert uf.code == "runtime_node_stale"
    assert uf.severity == "warning"
    assert uf.blocks_completion is True  # warning 级但保守阻断
    assert uf.repair_class == REPAIR_RECOMPUTE_NODE
    assert uf.scope == "node"
    assert uf.affected_entity == "cap:density_mapping"
    # 健康节点不成 finding
    assert from_runtime_node({"node_id": "cap:x", "state": "satisfied"}) is None


def test_collect_order_and_budget() -> None:
    result = MapCompletionResult(
        status=STATUS_FAILED,
        findings=[
            MapCompletionFinding(code=F_LAYER_MISSING, severity="warning"),
            MapCompletionFinding(code=F_RENDER_SOURCE_MISSING, severity="error"),
        ],
    )
    block = _runtime_block(_stale_node())
    ufs = collect_unified_findings(
        result=result, runtime_block=block,
        render_diagnostics=[{"code": "label_truncated", "severity": "info"}])
    domains = [u.domain for u in ufs]
    # error 优先、runtime 随后、export degradation 收尾（确定性序）
    assert domains == [
        "harness_finalizer", "harness_finalizer",
        "workflow_runtime", "render_diagnostic",
    ]
    assert ufs[0].severity == "error"


# ── 2/3/4. Completion Engine 消费统一投影 ────────────────────────────────

def _ready_result() -> MapCompletionResult:
    return MapCompletionResult(
        status=STATUS_COMPLETE,
        findings=[],
        layer_status="valid",
        component_status="valid",
        render_status="verified",
    )


def test_verdict_parity_without_runtime_block() -> None:
    # 旧章节（无运行态块）：语义零漂移。
    out = derive_product_verdict(_ready_result(), [], chapter={})
    assert out["verdict"] == VERDICT_READY
    assert out["runtime_stale_nodes"] == []
    assert out["completion_dimensions"]["analysis"] is True


def test_verdict_needs_repair_on_runtime_stale() -> None:
    chapter = {WORKFLOW_RUNTIME_KEY: _runtime_block(_stale_node())}
    out = derive_product_verdict(_ready_result(), [], chapter=chapter)
    assert out["verdict"] == VERDICT_NEEDS_REPAIR
    assert "runtime_node_stale" in out["reasons"]
    assert out["runtime_stale_nodes"] == ["cap:density_mapping"]
    assert out["completion_dimensions"]["analysis"] is False


def test_evaluate_contract_analysis_dim_hardened() -> None:
    chapter = {WORKFLOW_RUNTIME_KEY: _runtime_block(_stale_node())}
    contract = evaluate_completion_contract(_ready_result(), [], chapter)
    assert contract["dimensions"]["analysis"] is False
    assert contract["runtime_stale_nodes"] == ["cap:density_mapping"]
    # 无块 → 维度不受影响（parity）
    contract2 = evaluate_completion_contract(_ready_result(), [], {})
    assert contract2["dimensions"]["analysis"] is True


def test_verdict_data_block_precedence_survives() -> None:
    result = MapCompletionResult(
        status=STATUS_FAILED,
        findings=[MapCompletionFinding(
            code=F_ARTIFACT_EXPIRED, severity="error")],
    )
    chapter = {WORKFLOW_RUNTIME_KEY: _runtime_block(_stale_node())}
    out = derive_product_verdict(result, [], chapter=chapter)
    # failed + 数据族错误 → BLOCKED_BY_DATA（数据先行不被 runtime 追加掩盖）
    assert out["verdict"] == VERDICT_BLOCKED_BY_DATA


def test_stale_runtime_nodes_helper() -> None:
    assert stale_runtime_nodes(None) == []
    assert stale_runtime_nodes({}) == []
    block = _runtime_block(_stale_node(), {"node_id": "cap:ok", "state": "satisfied"})
    assert stale_runtime_nodes(block) == ["cap:density_mapping"]
