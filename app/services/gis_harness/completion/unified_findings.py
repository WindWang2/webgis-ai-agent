"""Unified Findings（V6 Wave 6 + 契约补全）—— 多域 findings 的统一投影层。

现状（Phase-0 审计 00-baseline §K）：Harness Finalizer finding codes（34）、
Cartography Render Diagnostic（18）、组合校验码、语义 check 码、Quality
Finding、Workflow Runtime 节点态各自表述。本模块不迁移任何 domain 词表
（红线：词表原地保留，唯一事实源不动），只提供 **adapter 投影**：

    domain-specific finding
        ↓ project_*（纯函数，确定性，有界）
    UnifiedFinding（domain/finding_class/code/severity/source/scope/
                    affected_entity/evidence/repair_class/retryable/
                    blocks_completion/degradation_only/user_owned/
                    finding_id/recurrence_fingerprint）

Completion Engine（``derive_product_verdict`` /
``evaluate_completion_contract``）与 Repair Planner（W10）只消费统一
投影，不再各自解释 domain 细节。

词汇纪律：

- ``code`` 原样携带 domain 原生码（不翻译、不新造）；
- ``finding_class`` 是**类别轴**（semantic / gis_correctness / cartographic /
  visual / runtime_display / export，封闭词表），与生产者轴 ``domain``
  正交；单一推导点 ``derive_finding_class``，未知组合保守归
  ``runtime_display``（不猜）；
- ``repair_class`` 复用 failure_taxonomy.RemediationAction 词表 +
  completion repair codes（add_component/enable_component/show_layer）+
  局部重算类（recompute_node/recompute_subgraph，与
  recompute.RecomputePlan 同源语义）；
- ``blocks_completion`` 由本模块单点推导（``_blocks``），禁止调用方
  各自判断；
- ``finding_id`` / ``recurrence_fingerprint`` 在 ``__post_init__`` 单点
  铸造：后者与 repair_planner.finding_fingerprint 同构同值
  （canonical_fingerprint({"domain","code","entity"})）——跨轮追同因、
  防循环账本共用一把指纹，不建第二套哈希；
- ``user_owned`` 只是**声明**（受影响实体在调用方给出的用户
  锁/override 集内），权威裁决仍在突变层统一 guard；
- 全部确定性、有界、零 I/O。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional

from app.services.gis_harness.workflow_instance import canonical_fingerprint

#: domain 词表（投影来源域；封闭集合）。
UNIFIED_DOMAINS = (
    "harness_finalizer",   # completion MapCompletionFinding（34 码）
    "render_diagnostic",   # 导出/渲染诊断（18 码，含前端子集）
    "workflow_runtime",    # runtime_bridge 节点态（stale/failed）
    "semantic_check",      # 制图语义/组合校验（quality loop review checks）
    "visual",              # 视觉评估（visual_evaluator seam 白名单产物）
)

#: finding 类别轴（封闭；与 domain 正交的消费/报告维度）。
FINDING_CLASSES = (
    "semantic",          # 意图/目标/执行义务覆盖
    "gis_correctness",   # CRS/数据在场/几何/字段/证据正确性
    "cartographic",      # 图型/图例/样式/标注/出版件完整性
    "visual",            # 视觉软评估（恒 degradation_only）
    "runtime_display",   # 观测/runtime 缺口（挂载/可见性/组件/布局）
    "export",            # 导出件/一致性（degradation_only）
)

#: repair_class 增补词（复用 RemediationAction 之外的局部重算类；
#: 与 workflow_v4.recompute.RecomputePlan 同源语义）。
REPAIR_RECOMPUTE_NODE = "recompute_node"
REPAIR_RECOMPUTE_SUBGRAPH = "recompute_subgraph"

# ── finding_class 推导表（单一推导点；code 精确 → scope 兜底 → 域默认）────
_CLASS_BY_CODE: Dict[str, str] = {
    # 执行义务 → 任务语义层
    "needs_execution": "semantic",
    "execution_blocked": "semantic",
    # 数据/证据正确性
    "crs_not_wgs84": "gis_correctness",
    "artifact_missing": "gis_correctness",
    "artifact_expired": "gis_correctness",
    "empty_result": "gis_correctness",
    "orphan_binding": "gis_correctness",
    "blank_map_risk": "gis_correctness",
    "invalid_result_bounds": "gis_correctness",
    "runtime_node_stale": "gis_correctness",
    "runtime_node_failed": "gis_correctness",
    # 制图/呈现语义
    "semantic_legend_missing": "cartographic",
    "semantic_legend_mismatch": "cartographic",
    "title_missing_report_product": "cartographic",
    "layer_transparent": "cartographic",
    "layer_order_issue": "cartographic",
    "stale_overlay": "cartographic",
    "map_model_mismatch": "cartographic",
    "label_collision": "cartographic",
    # runtime/display
    "layer_missing": "runtime_display",
    "no_result_layer": "runtime_display",
    "layer_hidden": "runtime_display",
    "component_missing": "runtime_display",
    "component_disabled": "runtime_display",
    "layout_conflict": "runtime_display",
    "viewport_no_bbox": "runtime_display",
    "chart_data_missing": "runtime_display",
    "planned_observed_mismatch": "runtime_display",
}
_CLASS_BY_SCOPE: Dict[str, str] = {
    "data": "gis_correctness",
    "node": "gis_correctness",
    "workflow": "semantic",
    "export": "export",
    "layer": "runtime_display",
    "source": "runtime_display",
    "component": "runtime_display",
    "chart": "runtime_display",
    "map": "runtime_display",
}
_DEFAULT_FINDING_CLASS = "runtime_display"


def derive_finding_class(domain: str, code: str, scope: str) -> str:
    """(domain, code, scope) → finding 类别（确定性、封闭词表）。

    domain 自带类别语义时优先（visual/render_diagnostic），否则 code
    精确表 → scope 兜底 → 保守默认 ``runtime_display``。
    """
    if domain == "visual":
        return "visual"
    if domain == "render_diagnostic":
        return "export"
    if domain == "semantic_check":
        return "cartographic"
    if code and code in _CLASS_BY_CODE:
        return _CLASS_BY_CODE[code]
    if scope and scope in _CLASS_BY_SCOPE:
        return _CLASS_BY_SCOPE[scope]
    return _DEFAULT_FINDING_CLASS


_MAX_FINDINGS = 24
_MAX_EVIDENCE = 200
_MAX_REVIEW_CHECKS = 24


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
    # ── 契约补全（additive；全部有默认值 → 旧构造零漂移）─────────────────
    finding_class: str = ""            # ⊆ FINDING_CLASSES（空则自动推导）
    user_owned: bool = False           # 受影响实体在用户锁/override 集（声明）
    finding_id: str = ""               # 稳定短 id（铸造于 __post_init__）
    recurrence_fingerprint: str = ""   # 跨轮同因指纹（与 W11 账本同构）

    def __post_init__(self) -> None:
        if not self.finding_class:
            self.finding_class = derive_finding_class(
                self.domain, self.code, self.scope)
        if not self.recurrence_fingerprint:
            self.recurrence_fingerprint = canonical_fingerprint({
                "domain": self.domain, "code": self.code,
                "entity": self.affected_entity,
            })
        if not self.finding_id:
            self.finding_id = (
                f"{self.domain[:12]}:{self.code[:24]}"
                f":{self.recurrence_fingerprint[:12]}"
            )

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
            "finding_class": self.finding_class,
            "user_owned": self.user_owned,
            "finding_id": self.finding_id[:96],
            "recurrence_fingerprint": self.recurrence_fingerprint[:32],
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
    user_owned_entities: FrozenSet[str] = frozenset(),
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
        user_owned=bool(target and target in user_owned_entities),
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


# ── 制图 review check 投影（semantic_check 域接入）───────────────────────

#: quality_loop / runtime lane suggested_fix.operation → REPAIR_CLASSES 词。
#: 只收既有 6 op 白名单 + runtime legend 刷新；未知 op → 空（分类走
#: repair_planner 的 scope 兜底，不新造动作）。
_CARTO_OP_TO_REPAIR_CLASS: Dict[str, str] = {
    "normalize_opacity": "reapply_style",
    "refresh_style_from_legend": "reapply_style",
    "set_layer_visibility": "reapply_style",
    "change_palette": "reapply_style",
    "set_map_legend_visibility": "relayout_component",
    "resolve_floating_layout": "relayout_component",
    "refresh_runtime_legend": "regenerate_legend",
}


def from_cartographic_check(
    check: Any,
    *,
    source: str = "cartography_review",
    user_owned_entities: FrozenSet[str] = frozenset(),
) -> Optional[UnifiedFinding]:
    """制图 review check（quality loop / runtime lane）→ UnifiedFinding。

    只投影 fail / warning；pass / not_evaluated 不产 finding（诚实缺席：
    证据不全的检查以 review 原文披露，不在统一面冒充问题）。
    """
    if not isinstance(check, dict):
        return None
    rule = str(check.get("rule") or check.get("check") or "")
    if not rule:
        return None
    status = str(check.get("status") or "")
    if status in ("pass", "not_evaluated", ""):
        return None
    severity = "error" if status == "fail" else str(check.get("severity") or "warning")
    if severity not in ("info", "warning", "error"):
        severity = "warning"
    layer_id = str(check.get("layer_id") or "")
    source_id = str(check.get("source_id") or "")
    entity = layer_id or source_id
    scope = "layer" if layer_id else ("source" if source_id else "map")
    fix = check.get("suggested_fix")
    repair_class = ""
    if isinstance(fix, dict):
        repair_class = _CARTO_OP_TO_REPAIR_CLASS.get(
            str(fix.get("operation") or ""), "")
    return UnifiedFinding(
        domain="semantic_check",
        code=rule[:64],
        severity=severity,
        source=source[:48],
        scope=scope,
        affected_entity=entity[:64],
        evidence=str(check.get("message") or "")[:_MAX_EVIDENCE],
        repair_class=repair_class[:48],
        retryable=False,
        blocks_completion=_blocks("semantic_check", severity, False),
        degradation_only=False,
        user_owned=bool(entity and entity in user_owned_entities),
    )


def _iter_cartographic_checks(review: Any) -> List[Dict[str, Any]]:
    """制图评审载荷 → 去重后的 checks（有界 ≤24）。

    接受三种合法形状（与 derive_product_verdict._cartography_review_summary
    的双形状纪律同源，外加 runtime lane 证据包）：
    - ``CartographicLoopResult.to_dict()``：review 嵌套 + attempts；
    - 内层 ``CartographyReport.to_dict()``：checks 直挂；
    - ``_cartographic_review`` map_state 块：``cartography``（runtime
      证据，checks + desired_review.checks）。
    """
    if not isinstance(review, dict):
        return []
    shapes: List[Dict[str, Any]] = []
    inner = review.get("review")
    if isinstance(inner, dict):
        shapes.append(inner)
    carto = review.get("cartography")
    if isinstance(carto, dict):
        shapes.append(carto)
        desired = carto.get("desired_review")
        if isinstance(desired, dict):
            shapes.append(desired)
    shapes.append(review)
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for shape in shapes:
        raw_checks = shape.get("checks")
        # review #4：checks 必须是 list —— dict/畸形载荷跳过该形状，
        # 绝不让 TypeError 穿透投影层报废整个 repair plan。
        if not isinstance(raw_checks, list):
            continue
        for check in raw_checks[:_MAX_REVIEW_CHECKS * 2]:
            if not isinstance(check, dict):
                continue
            rule = str(check.get("rule") or check.get("check") or "")
            if not rule:
                continue
            key = (rule, str(check.get("status") or ""),
                   str(check.get("layer_id") or ""), str(check.get("source_id") or ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(check)
            if len(out) >= _MAX_REVIEW_CHECKS:
                return out
    return out


# ── 统一收集（Completion Engine / Repair Planner 的单一入口）─────────────

def collect_unified_findings(
    *,
    result: Any = None,
    runtime_block: Optional[Dict[str, Any]] = None,
    render_diagnostics: Optional[List[Dict[str, Any]]] = None,
    cartographic_review: Any = None,
    visual_findings: Optional[List[UnifiedFinding]] = None,
    user_owned_entities: FrozenSet[str] = frozenset(),
    budget: int = _MAX_FINDINGS,
) -> List[UnifiedFinding]:
    """各域 findings → UnifiedFinding 列表（确定性序：阻断优先、域序稳定）。

    ``result``：MapCompletionResult（findings 逐条投影）；
    ``runtime_block``：gis_chapter["workflow_runtime_v6"]（stale/failed 节点）；
    ``render_diagnostics``：导出诊断 sidecar 载荷（可选，degradation 面）；
    ``cartographic_review``：制图评审（三形状见 _iter_cartographic_checks；
    fail/warning 规则入投影，pass/not_evaluated 不产 finding）；
    ``visual_findings``：visual_evaluator seam 的白名单产物（只接受
    UnifiedFinding 实例 —— dict 形状必须先过 seam 白名单校验）；
    ``user_owned_entities``：用户锁/override 实体集（user_owned 声明来源；
    权威裁决仍在突变层 guard / classify_repair）。

    截断纪律（review #5）：先收集后按「阻断（error 或 blocks_completion）
    优先、其余保持域序稳定」排序再截断 —— runtime stale 节点过多时，
    后 appended 域（制图/视觉）的阻断发现不会被纯位置截断静默丢弃。
    """
    out: List[UnifiedFinding] = []
    findings = list(getattr(result, "findings", None) or [])
    for f in findings:
        if str(getattr(f, "severity", "")) == "error":
            out.append(from_completion_finding(
                f, user_owned_entities=user_owned_entities))
    for f in findings:
        if str(getattr(f, "severity", "")) != "error":
            out.append(from_completion_finding(
                f, user_owned_entities=user_owned_entities))
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
    for check in _iter_cartographic_checks(cartographic_review):
        uf = from_cartographic_check(
            check, user_owned_entities=user_owned_entities)
        if uf is not None:
            out.append(uf)
    for vf in (visual_findings or [])[:12]:
        if isinstance(vf, UnifiedFinding):
            out.append(vf)
    if len(out) > budget:
        out.sort(key=lambda uf: 0 if (uf.severity == "error"
                                      or uf.blocks_completion) else 1)
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
    "FINDING_CLASSES",
    "REPAIR_RECOMPUTE_NODE",
    "REPAIR_RECOMPUTE_SUBGRAPH",
    "UnifiedFinding",
    "derive_finding_class",
    "collect_unified_findings",
    "from_completion_finding",
    "from_render_diagnostic",
    "from_runtime_node",
    "from_cartographic_check",
    "stale_runtime_nodes",
]
