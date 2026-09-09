"""Unified Findings（V6 Wave 6）—— 多域 findings 的统一投影层。

现状（Phase-0 审计 00-baseline §K）：Harness Finalizer finding codes（34）、
Cartography Render Diagnostic（18）、组合校验码、语义 check 码、Quality
Finding、Workflow Runtime 节点态各自表述。本模块不迁移任何 domain 词表
（红线：词表原地保留，唯一事实源不动），只提供 **adapter 投影**：

    domain-specific finding
        ↓ project_*（纯函数，确定性，有界）
    UnifiedFinding（domain/code/severity/source/scope/affected_entity/
                    evidence/repair_class/retryable/blocks_completion/
                    degradation_only）

Completion Engine（``derive_product_verdict`` /
``evaluate_completion_contract``）与后续 Repair Planner（W10）只消费统一
投影，不再各自解释 domain 细节。

词汇纪律：

- ``code`` 原样携带 domain 原生码（不翻译、不新造）；
- ``repair_class`` 复用 failure_taxonomy.RemediationAction 词表 +
  completion repair codes（add_component/enable_component/show_layer）+
  局部重算类（recompute_node/recompute_subgraph —— W10 正式化前的最小
  增补，与 recompute.RecomputePlan 同源语义）；
- ``blocks_completion`` 由本模块单点推导（``_blocks``），禁止调用方
  各自判断；
- 全部确定性、有界、零 I/O。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

#: domain 词表（投影来源域；封闭集合）。
UNIFIED_DOMAINS = (
    "harness_finalizer",   # completion MapCompletionFinding（34 码）
    "render_diagnostic",   # 导出/渲染诊断（18 码，含前端子集）
    "workflow_runtime",    # runtime_bridge 节点态（stale/failed）
    "semantic_check",      # cartography 语义/组合校验（预留，W8 接入）
    "visual",              # 视觉评估（预留，W9 接入）
)

#: repair_class 增补词（复用 RemediationAction 之外的局部重算类；
#: 与 workflow_v4.recompute.RecomputePlan 同源语义）。
REPAIR_RECOMPUTE_NODE = "recompute_node"
REPAIR_RECOMPUTE_SUBGRAPH = "recompute_subgraph"

_MAX_FINDINGS = 24
_MAX_EVIDENCE = 200


@dataclass
class UnifiedFinding:
    """跨域统一 finding 投影（bounded、可序列化）。"""

    domain: str
    code: str
    severity: str                      # info | warning | error
    source: str = ""                   # 产生器（map_finalizer/frontend_runtime/
                                       # export_sidecar/runtime_bridge/...）
    scope: str = "map"                 # node|layer|component|source|chart|map|
                                       # workflow|export|data
    affected_entity: str = ""          # node_id / layer_id / component_id / ref
    evidence: str = ""
    repair_class: str = ""             # RemediationAction / R_* / recompute_*
    retryable: bool = False
    blocks_completion: bool = False
    degradation_only: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "code": self.code[:64],
            "severity": self.severity,
            "source": self.source[:48],
            "scope": self.scope,
            "affected_entity": self.affected_entity[:64],
            "evidence": self.evidence[:_MAX_EVIDENCE],
            "repair_class": self.repair_class[:48],
            "retryable": self.retryable,
            "blocks_completion": self.blocks_completion,
            "degradation_only": self.degradation_only,
        }


def _blocks(domain: str, severity: str, degradation_only: bool) -> bool:
    """blocks_completion 唯一推导点：error 且非纯降级域 → 阻断。

    render_diagnostic 全域 degradation_only（导出件降级不阻断 live 地图
    完成 —— export parity 是披露面不是完成门）；workflow_runtime 的
    stale 是 warning 级但**阻断**（证据不再当前，保守正确）——该例外
    由投影器显式声明（见 from_runtime_node），不进本通用规则。
    """
    if degradation_only:
        return False
    return severity == "error"


# ── 各域投影器 ───────────────────────────────────────────────────────────

def from_completion_finding(
    finding: Any,
    *,
    source: str = "map_finalizer",
) -> UnifiedFinding:
    """MapCompletionFinding → UnifiedFinding（domain=harness_finalizer）。"""
    code = str(getattr(finding, "code", "") or "")
    severity = str(getattr(finding, "severity", "") or "warning")
    target = str(getattr(finding, "target", "") or "")
    repair = getattr(finding, "repair", None)
    scope = "map"
    if code.startswith("render_") or code == "chart_data_missing":
        scope = "map"
    if code in ("layer_missing", "no_result_layer", "layer_hidden",
                "layer_transparent", "layer_order_issue", "stale_overlay"):
        scope = "layer"
    elif code in ("component_missing", "component_disabled", "layout_conflict",
                  "orphan_binding"):
        scope = "component"
    elif code in ("source_missing",):
        scope = "source"
    elif code in ("artifact_missing", "artifact_expired", "empty_result",
                  "crs_not_wgs84"):
        scope = "data"
    elif code in ("needs_execution", "execution_blocked"):
        scope = "workflow"
    repair_class = str(repair or "")
    if not repair_class:
        if code.startswith("render_") or code == "chart_data_missing":
            repair_class = "reobserve"
        elif code in ("artifact_expired",):
            repair_class = "retry"
        elif code in ("needs_execution", "execution_blocked"):
            repair_class = "replan"
    return UnifiedFinding(
        domain="harness_finalizer",
        code=code,
        severity=severity,
        source=source,
        scope=scope,
        affected_entity=target,
        evidence=str(getattr(finding, "detail", "") or ""),
        repair_class=repair_class,
        retryable=repair_class in (
            "retry", "retry_with_backoff", "reobserve", "fallback_tool"),
        blocks_completion=_blocks("harness_finalizer", severity, False),
        degradation_only=False,
    )


def from_render_diagnostic(
    diagnostic: Dict[str, Any],
    *,
    source: str = "export_sidecar",
) -> UnifiedFinding:
    """render diagnostics 18 码 → UnifiedFinding（全域 degradation_only）。"""
    code = str(diagnostic.get("code") or "")
    severity = str(diagnostic.get("severity") or "info")
    scope = "export"
    if code.startswith("chart_"):
        scope = "chart"
    elif code.startswith("table_"):
        scope = "component"
    elif code.startswith(("label_", "legend_", "component_")):
        scope = "component"
    elif code.startswith("features_"):
        scope = "layer"
    return UnifiedFinding(
        domain="render_diagnostic",
        code=code,
        severity=severity,
        source=source,
        scope=scope,
        affected_entity=str(diagnostic.get("component_id")
                            or diagnostic.get("layer_id") or ""),
        evidence=str(diagnostic.get("detail") or diagnostic.get("message") or ""),
        repair_class="reobserve" if severity == "error" else "",
        retryable=severity == "error",
        blocks_completion=False,
        degradation_only=True,
    )


def from_runtime_node(
    node: Dict[str, Any],
    *,
    source: str = "runtime_bridge",
) -> Optional[UnifiedFinding]:
    """runtime_bridge 节点 → UnifiedFinding（仅 stale/failed 成为 finding）。

    stale：warning 级但 blocks_completion=True（证据不再当前，保守阻断
    READY —— 这是 `_blocks` 通用规则之外由投影器显式声明的例外）；
    repair_class=recompute_node（参数/算法/数据维漂移的精确重算单元）。
    """
    state = str(node.get("state") or "")
    node_id = str(node.get("node_id") or "")
    if state == "stale":
        reason = str(node.get("stale_reason") or "")
        return UnifiedFinding(
            domain="workflow_runtime",
            code="runtime_node_stale",
            severity="warning",
            source=source,
            scope="node",
            affected_entity=node_id,
            evidence=reason,
            repair_class=REPAIR_RECOMPUTE_NODE,
            retryable=True,
            blocks_completion=True,
            degradation_only=False,
        )
    if state == "failed":
        return UnifiedFinding(
            domain="workflow_runtime",
            code="runtime_node_failed",
            severity="error",
            source=source,
            scope="node",
            affected_entity=node_id,
            evidence=str(node.get("failure_class") or ""),
            repair_class="retry",
            retryable=True,
            blocks_completion=True,
            degradation_only=False,
        )
    return None


# ── 统一收集（Completion Engine / Repair Planner 的单一入口）─────────────

def collect_unified_findings(
    *,
    result: Any = None,
    runtime_block: Optional[Dict[str, Any]] = None,
    render_diagnostics: Optional[List[Dict[str, Any]]] = None,
    budget: int = _MAX_FINDINGS,
) -> List[UnifiedFinding]:
    """各域 findings → UnifiedFinding 列表（确定性序：error 优先、域序稳定）。

    ``result``：MapCompletionResult（findings 逐条投影）；
    ``runtime_block``：gis_chapter["workflow_runtime_v6"]（stale/failed 节点）；
    ``render_diagnostics``：导出诊断 sidecar 载荷（可选，degradation 面）。
    """
    out: List[UnifiedFinding] = []
    findings = list(getattr(result, "findings", None) or [])
    for f in findings:
        if str(getattr(f, "severity", "")) == "error":
            out.append(from_completion_finding(f))
    for f in findings:
        if str(getattr(f, "severity", "")) != "error":
            out.append(from_completion_finding(f))
    if isinstance(runtime_block, dict):
        for node in (runtime_block.get("nodes") or [])[:64]:
            if not isinstance(node, dict):
                continue
            uf = from_runtime_node(node)
            if uf is not None:
                out.append(uf)
    for d in (render_diagnostics or [])[:16]:
        if isinstance(d, dict):
            out.append(from_render_diagnostic(d))
    return out[:budget]


def stale_runtime_nodes(runtime_block: Optional[Dict[str, Any]]) -> List[str]:
    """运行态块中的 stale 节点 id（Completion Engine 的硬约束输入）。"""
    if not isinstance(runtime_block, dict):
        return []
    return [
        str(n.get("node_id") or "")
        for n in (runtime_block.get("nodes") or [])
        if isinstance(n, dict) and n.get("state") == "stale"
    ][:8]


__all__ = [
    "UNIFIED_DOMAINS",
    "REPAIR_RECOMPUTE_NODE",
    "REPAIR_RECOMPUTE_SUBGRAPH",
    "UnifiedFinding",
    "collect_unified_findings",
    "from_completion_finding",
    "from_render_diagnostic",
    "from_runtime_node",
    "stale_runtime_nodes",
]
