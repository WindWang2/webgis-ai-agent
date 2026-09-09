"""Repair Planner（V6 Wave 10–11）回归锁。

不变式（对应 §17/§18/§19 + 04-unified-findings.md）：
1. finding → 修复分类表驱动（code 精确 → scope 兜底）；全部确定性；
2. 安全分级五类；用户锁/override 硬约束 → not_allowed（user-wins，
   任何路径不得绕过）；软视觉发现默认 requires_user_approval；
3. 防循环：同 finding 同 epoch 重复 → no_progress；尝试 ≥3 →
   repair_exhausted → abort_with_disclosure（绝不无限对抗）；
4. 计划有界（actions ≤6）；执行通道只列既有 executor（不建第三通道）。
"""
from __future__ import annotations

from typing import Any, Dict

from app.services.gis_harness.completion.unified_findings import UnifiedFinding
from app.services.gis_harness.repair_planner import (
    LOOP_EXHAUSTED,
    LOOP_NO_PROGRESS,
    LOOP_OK,
    MAX_REPAIR_ATTEMPTS_PER_FINDING,
    REPAIR_CLASSES,
    RepairPlan,
    classify_repair,
    evaluate_repair_loop,
    finding_fingerprint,
    plan_repairs,
)


def _uf(code: str, *, domain: str = "harness_finalizer", severity: str = "error",
        entity: str = "", scope: str = "map", degradation: bool = False,
        repair_class: str = "") -> UnifiedFinding:
    return UnifiedFinding(
        domain=domain, code=code, severity=severity, source="test",
        scope=scope, affected_entity=entity, evidence="ev",
        blocks_completion=(severity == "error"), degradation_only=degradation,
        repair_class=repair_class,
    )


# ── 1. 分类表 ────────────────────────────────────────────────────────────

def test_classification_table() -> None:
    a = classify_repair(_uf("runtime_node_stale", domain="workflow_runtime",
                            severity="warning", entity="cap:x", scope="node"))
    assert (a.repair_class, a.safety, a.executor) == (
        "recompute_node", "semantics_preserving", "pi_recompute")
    a = classify_repair(_uf("crs_not_wgs84", scope="data"))
    assert a.repair_class == "reproject"
    a = classify_repair(_uf("layout_conflict", scope="component", entity="c1"))
    assert (a.repair_class, a.safety) == ("relayout_component", "presentation_only")
    a = classify_repair(_uf("semantic_legend_missing"))
    assert a.repair_class == "regenerate_legend"
    # scope 兜底 + 保底
    a = classify_repair(_uf("unknown_code_xyz", scope="layer", entity="l1"))
    assert a.repair_class == "rerender"
    # degradation_only 非 error → 不产生自动动作
    a = classify_repair(_uf("label_truncated", severity="info", degradation=True))
    assert a.executor == "none"
    assert a.safety == "safe_automatic"


def test_visual_soft_requires_user_approval() -> None:
    a = classify_repair(_uf("V_WEAK_VISUAL_HIERARCHY", domain="visual",
                            severity="warning", scope="map"))
    assert a.safety in ("requires_user_approval", "safe_automatic")
    assert a.executor == "none" or a.safety == "requires_user_approval"


# ── 2. user-wins 硬约束 ──────────────────────────────────────────────────

def test_locked_entity_not_allowed() -> None:
    a = classify_repair(
        _uf("layer_hidden", scope="layer", entity="layer-a", severity="warning"),
        locked_entities=frozenset({"layer-a"}))
    assert a.safety == "not_allowed"
    assert a.executor == "none"
    assert "user-wins" in a.detail


def test_user_overridden_not_allowed() -> None:
    a = classify_repair(
        _uf("layout_conflict", entity="chart-1", severity="warning"),
        user_overridden=frozenset({"chart-1"}))
    assert a.safety == "not_allowed"


# ── 3. 防循环护栏 ────────────────────────────────────────────────────────

def test_loop_guard_no_progress_and_exhausted() -> None:
    fp = "fp-1"
    ledger: Dict[str, Any] = {}
    # 第 1 次：ok，入账
    ledger, verdicts = evaluate_repair_loop(ledger, [fp], state_epoch="1:3")
    assert verdicts[fp] == LOOP_OK
    # 同 finding 同 epoch 再来 → no_progress（状态没动）
    ledger, verdicts = evaluate_repair_loop(ledger, [fp], state_epoch="1:3")
    assert verdicts[fp] == LOOP_NO_PROGRESS
    # epoch 推进（修复生效/编辑发生）→ 重新 ok
    ledger, verdicts = evaluate_repair_loop(ledger, [fp], state_epoch="2:3")
    assert verdicts[fp] == LOOP_OK
    # 尝试计数达上限 → exhausted（跨 epoch 也耗尽）
    ledger, verdicts = evaluate_repair_loop(ledger, [fp], state_epoch="3:3")
    assert verdicts[fp] == LOOP_EXHAUSTED
    assert MAX_REPAIR_ATTEMPTS_PER_FINDING == 3


def test_exhausted_finding_aborts_with_disclosure() -> None:
    uf = _uf("runtime_node_stale", domain="workflow_runtime",
             severity="warning", entity="cap:x", scope="node")
    fp = finding_fingerprint(uf)
    plan = plan_repairs([uf], loop_verdicts={fp: LOOP_EXHAUSTED})
    assert not plan.actions
    assert fp in plan.exhausted
    refused = plan.refused[0]
    assert refused.repair_class == "abort_with_disclosure"
    assert refused.safety == "not_allowed"
    assert refused.executor == "none"


# ── 4. 计划形状 ──────────────────────────────────────────────────────────

def test_plan_partitions_and_dedup() -> None:
    findings = [
        _uf("runtime_node_stale", domain="workflow_runtime", severity="warning",
            entity="cap:x", scope="node"),                       # → actions
        _uf("empty_result", scope="data", entity="cap:y"),       # → deferred
        _uf("layer_hidden", scope="layer", entity="l1",
            severity="warning"),                                  # locked → refused
        _uf("runtime_node_stale", domain="workflow_runtime", severity="warning",
            entity="cap:x", scope="node"),                       # 重复 → dedup
    ]
    plan = plan_repairs(findings, locked_entities=frozenset({"l1"}))
    assert isinstance(plan, RepairPlan)
    assert len(plan.actions) == 1
    assert plan.actions[0].repair_class == "recompute_node"
    assert len(plan.deferred) == 1
    assert plan.deferred[0].repair_class == "reselect_method"
    assert len(plan.refused) == 1
    assert plan.refused[0].safety == "not_allowed"
    d = plan.to_dict()
    assert len(d["actions"]) == 1 and len(d["deferred"]) == 1


# ── 5. review A/MAJOR-1：槽满不吞 refused/deferred ─────────────────────────

def test_full_actions_still_classifies_refused() -> None:
    """6 actions 槽满后，第 7 条 locked finding 仍进 refused（复现）。"""
    findings = [
        _uf("runtime_node_stale", domain="workflow_runtime", severity="warning",
            entity=f"cap:{i}", scope="node")
        for i in range(6)
    ]
    findings.append(
        _uf("layer_hidden", scope="layer", entity="locked-tail",
            severity="warning"))
    plan = plan_repairs(findings, locked_entities=frozenset({"locked-tail"}))
    assert len(plan.actions) == 6
    assert len(plan.refused) == 1
    assert plan.refused[0].target == "locked-tail"
    assert plan.refused[0].safety == "not_allowed"


def test_full_actions_still_classifies_deferred() -> None:
    """槽满后的 requires_user_approval 仍进 deferred。"""
    findings = [
        _uf("runtime_node_stale", domain="workflow_runtime", severity="warning",
            entity=f"cap:{i}", scope="node")
        for i in range(6)
    ]
    findings.append(_uf("empty_result", scope="data", entity="cap:tail"))
    plan = plan_repairs(findings)
    assert len(plan.actions) == 6
    assert len(plan.deferred) == 1
    assert plan.deferred[0].repair_class == "reselect_method"


# ── 6. review A/MAJOR-3：降级分支不跨词表混装 ─────────────────────────────

def test_degradation_foreign_repair_class_normalized() -> None:
    """外来 repair_class（retry/replan）归一化到 16 类，原文留 detail。"""
    for foreign in ("retry", "replan"):
        a = classify_repair(_uf(
            "V_SOFT_CONTRAST", domain="visual", severity="warning",
            entity="c1", scope="component", degradation=True,
            repair_class=foreign))
        assert a.repair_class in REPAIR_CLASSES, a.repair_class
        assert a.repair_class != foreign
        assert f"orig_repair={foreign}" in a.detail
        assert a.executor == "none"


def test_plan_outputs_all_in_repair_vocab() -> None:
    """planner 输出侧词表锁：actions/deferred/refused 全员 16 类。"""
    findings = [
        _uf("V_SOFT_CONTRAST", domain="visual", severity="warning",
            entity="c1", scope="component", degradation=True,
            repair_class="retry"),
        _uf("V_SOFT_LAYOUT", domain="visual", severity="info",
            entity="c2", scope="map", degradation=True,
            repair_class="replan"),
        _uf("runtime_node_stale", domain="workflow_runtime", severity="warning",
            entity="cap:x", scope="node"),
        _uf("empty_result", scope="data", entity="cap:y"),
        _uf("layer_hidden", scope="layer", entity="l1", severity="warning"),
    ]
    plan = plan_repairs(findings, locked_entities=frozenset({"l1"}))
    for a in plan.actions + plan.deferred + plan.refused:
        assert a.repair_class in REPAIR_CLASSES, a.repair_class


# ── 7. review B/Q2：双缺席保守缺省＋白名单 ─────────────────────────────────

def test_unknown_code_and_scope_conservative_default() -> None:
    """未知码＋未知域 → not_allowed/none（进 refused 披露，不静默丢弃）。"""
    a = classify_repair(_uf("unknown_code_xyz", scope="nope_scope", entity="e1"))
    assert (a.repair_class, a.safety, a.executor) == (
        "reobserve", "not_allowed", "none")
    plan = plan_repairs([_uf("unknown_code_xyz", scope="nope_scope", entity="e1")])
    assert len(plan.refused) == 1
    assert not plan.actions


def test_unknown_visual_code_not_softened() -> None:
    """未知视觉码（双缺席）不软化为 requires_user_approval。"""
    a = classify_repair(_uf("V_UNKNOWN_XYZ", domain="visual",
                            severity="warning", scope="nope_scope"))
    assert a.safety == "not_allowed"
    assert a.executor == "none"


def test_known_scope_whitelist_unchanged() -> None:
    """白名单：已知码/域行为不变（视觉软化只发生在已知域上）。"""
    a = classify_repair(_uf("unknown_code_xyz", scope="layer", entity="l1"))
    assert (a.repair_class, a.safety, a.executor) == (
        "rerender", "safe_automatic", "runtime_repair")
    a = classify_repair(_uf("V_WEAK_VISUAL_HIERARCHY", domain="visual",
                            severity="warning", scope="map"))
    assert a.safety == "requires_user_approval"
    assert a.executor == "user"
