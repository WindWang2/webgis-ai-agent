"""Goal Satisfaction Evaluator 单测（ADR-0183 M1/M2）。

覆盖：契约派生确定性、证据投影、deterministic 评估规则、multi-goal
ledger、fail-closed 红线（visual/assisted 不背书 PASS）、反作弊锚
（数据空 / 比较缺位 / scope 错 / stale export / fallback 不可比）。
全部离线、零 LLM、确定性。
"""
from __future__ import annotations

import json

import pytest

from app.services.gis_harness.goal_satisfaction import (
    EvidenceClass,
    EvidenceStatus,
    GoalContract,
    GoalRequirement,
    GoalVerdict,
    HarnessSignal,
    RequirementKind,
    RequirementState,
    build_evidence_registry,
    contract_fingerprint,
    derive_goal_contract,
    evaluate_goal_satisfaction,
    resolve_goal_contract,
)
from app.services.gis_harness.goal_satisfaction.contracts import (
    PASS_CAPABLE_CLASSES,
    EvidenceKind,
    GoalEvidence,
)
from app.services.gis_harness.goal_satisfaction.requirements import (
    classify_capability,
)


def _chapter(**overrides):
    ch = {
        "query": "成都各区学校分布并比较，再导出PNG",
        "intent": {
            "query": "成都各区学校分布并比较，再导出PNG",
            "task": "distribution_overview",
            "scope": {"name": "成都", "level": "city"},
            "group_by": "district",
            "comparison": "各区学校数量对比",
            "output_intents": ["map", "statistics"],
            "export_intents": ["png"],
        },
        "analysis_steps": [
            {"capability": "poi.query", "purpose": "查询学校",
             "status": "complete"},
            {"capability": "admin_aggregation", "purpose": "各区统计",
             "status": "complete"},
            {"capability": "change_detection", "purpose": "各区对比",
             "status": "complete"},
        ],
    }
    ch.update(overrides)
    return ch


def _ready_product(**overrides):
    block = {
        "product_verdict": {"verdict": "READY"},
        "final_map_status": "verified",
        "render_status": "verified",
        "checked_revision": "7",
    }
    block.update(overrides)
    return block


# ── 分类与契约派生（G1）────────────────────────────────────────────────

def test_classify_capability_deterministic():
    assert classify_capability("admin_aggregation") == "statistics"
    assert classify_capability("rate_aggregation") == "statistics"
    assert classify_capability("global_morans_i") == "statistics"  # registry category
    assert classify_capability("change_detection") == "comparison"
    assert classify_capability("temporal_change_point") == "comparison"
    assert classify_capability("unknown_cap_xyz", "各区对比") == "comparison"
    assert classify_capability("geometry_buffer") == "analysis"


def test_derive_contract_sources_and_determinism():
    ch = _chapter()
    c1 = derive_goal_contract(ch)
    c2 = derive_goal_contract(ch)
    assert c1 is not None and c2 is not None
    assert contract_fingerprint(c1) == contract_fingerprint(c2)
    kinds = {r.id: r.kind for r in c1.requirements}
    assert kinds["map"] == RequirementKind.MAP
    assert kinds["analysis:admin_aggregation"] == RequirementKind.ANALYSIS
    assert kinds["comparison"] == RequirementKind.COMPARISON
    assert kinds["statistics"] == RequirementKind.STATISTICS
    assert kinds["export:png"] == RequirementKind.EXPORT
    assert c1.goal_id  # goal_key 同源


def test_derive_map_requirement_from_layers_only():
    ch = _chapter()
    ch["intent"] = {"output_intents": []}
    ch["map_layers"] = [{"layer_id": "l1", "role": "primary"}]
    ch["analysis_steps"] = []
    contract = derive_goal_contract(ch)
    assert contract is not None
    assert [r.kind for r in contract.requirements] == [RequirementKind.MAP]


def test_derive_none_without_gis_facts():
    assert derive_goal_contract(None) is None
    assert derive_goal_contract({}) is None
    assert derive_goal_contract({"intent": {}, "analysis_steps": []}) is None


def test_explicit_contract_override_and_invalid_fallback():
    ch = _chapter()
    explicit = GoalContract(
        goal_id="x|y|z", summary="explicit",
        requirements=[GoalRequirement(
            id="analysis:forbidden_cap", kind=RequirementKind.ANALYSIS,
            capability="forbidden_cap", polarity="must_not")],
    )
    ch["goal_contract"] = explicit.model_dump()
    contract = resolve_goal_contract(ch)
    assert contract is not None
    assert contract.summary == "explicit"
    # 非法显式契约 → 回退派生（不假用）。
    ch["goal_contract"] = {"schema_version": "bogus", "requirements": "nope"}
    fallback = resolve_goal_contract(ch)
    assert fallback is not None
    assert fallback.summary != "explicit"


# ── 证据注册（G2）────────────────────────────────────────────────────────

def test_evidence_row_status_mapping():
    ch = _chapter()
    ch["analysis_steps"] = [
        {"capability": "a", "status": "complete"},
        {"capability": "b", "status": "failed"},
        {"capability": "c", "status": "unavailable"},
        {"capability": "d", "status": "pending"},
    ]
    evidence = build_evidence_registry(ch)
    by_id = {e.id: e for e in evidence}
    assert by_id["row:a"].status == EvidenceStatus.PRESENT
    assert by_id["row:b"].status == EvidenceStatus.FAILED
    assert by_id["row:c"].status == EvidenceStatus.ABSENT
    assert by_id["row:d"].status == EvidenceStatus.ABSENT
    assert all(e.evidence_class == EvidenceClass.DETERMINISTIC
               for e in evidence)


def test_evidence_export_receipt_stale():
    ch = _chapter()
    ch["export_receipts"] = [{"format": "png", "revision": "3"}]
    evidence = build_evidence_registry(ch, _ready_product())
    receipt = next(e for e in evidence if e.id == "export_receipt:png")
    assert receipt.status == EvidenceStatus.STALE  # 3 != 7
    ch["export_receipts"] = [{"format": "png", "revision": "7"}]
    receipt = next(e for e in build_evidence_registry(ch, _ready_product())
                   if e.id == "export_receipt:png")
    assert receipt.status == EvidenceStatus.PRESENT


def test_evidence_registry_bounded():
    ch = _chapter()
    ch["analysis_steps"] = [{"capability": f"c{i}", "status": "complete"}
                            for i in range(100)]
    assert len(build_evidence_registry(ch)) <= 64


# ── 评估器（G3/G4）────────────────────────────────────────────────────────

def test_happy_path_single_goal_satisfied():
    ch = _chapter()
    ch["export_receipts"] = [{"format": "png", "revision": "7"}]
    report = evaluate_goal_satisfaction(ch, map_product=_ready_product())
    assert report is not None
    assert report.verdict == GoalVerdict.SATISFIED
    assert report.signal == HarnessSignal.COMPLETE
    assert all(v.verdict == RequirementState.FULFILLED
               for v in report.requirements)


def test_map_pretty_but_comparison_missing_is_not_pass():
    """反作弊锚：「地图好看但缺各区对比」不得全局 PASS（G6）。"""
    ch = _chapter()
    ch["analysis_steps"] = [
        {"capability": "poi.query", "purpose": "查询学校", "status": "complete"},
        {"capability": "change_detection", "purpose": "各区对比",
         "status": "pending"},  # 比较没跑
    ]
    report = evaluate_goal_satisfaction(ch, map_product=_ready_product())
    assert report.verdict in (GoalVerdict.PARTIAL, GoalVerdict.NOT_EVALUATED)
    assert report.signal != HarnessSignal.COMPLETE
    comparison = next(v for v in report.requirements
                      if v.requirement_id == "comparison")
    assert comparison.verdict != RequirementState.FULFILLED
    assert comparison.missing


def test_empty_data_tools_200_is_not_pass():
    """反作弊锚：工具都 200 但数据为空（empty_result 阻断）→ 分析不 PASS。"""
    ch = _chapter()
    product = _ready_product(
        product_verdict={"verdict": "READY",
                         "reasons": ["empty_result"]})
    report = evaluate_goal_satisfaction(ch, map_product=product)
    analysis = next(v for v in report.requirements
                    if v.requirement_id == "analysis:admin_aggregation")
    assert analysis.verdict == RequirementState.PARTIAL
    assert analysis.failed_rule == "data_insufficient"
    assert report.verdict != GoalVerdict.SATISFIED


def test_stale_export_receipt_is_not_fulfilled():
    """反作弊锚：export 文件存在但旧 revision → 不算交付兑现。"""
    ch = _chapter()
    ch["export_receipts"] = [{"format": "png", "revision": "3"}]
    report = evaluate_goal_satisfaction(ch, map_product=_ready_product())
    export = next(v for v in report.requirements
                  if v.requirement_id == "export:png")
    assert export.verdict == RequirementState.PARTIAL
    assert export.failed_rule == "export_receipt_stale"
    assert report.signal != HarnessSignal.COMPLETE


def test_scope_mismatch_rule():
    ch = _chapter()
    ch["analysis_steps"] = [
        {"capability": "poi.query", "purpose": "查询学校", "status": "complete",
         "params": {"scope": "绵阳"}},
    ]
    report = evaluate_goal_satisfaction(ch)
    analysis = next(v for v in report.requirements
                    if v.requirement_id == "analysis:poi.query")
    assert analysis.verdict == RequirementState.PARTIAL
    assert analysis.failed_rule == "scope_mismatch"


def test_filter_mismatch_rule():
    ch = _chapter()
    ch["analysis_steps"] = [
        {"capability": "admin_aggregation", "purpose": "各区统计",
         "status": "complete", "params": {"group_by": "grid"}},
    ]
    report = evaluate_goal_satisfaction(ch)
    analysis = next(v for v in report.requirements
                    if v.requirement_id == "analysis:admin_aggregation")
    assert analysis.failed_rule == "filter_mismatch"


def test_map_needs_repair_signal():
    report = evaluate_goal_satisfaction(
        _chapter(), map_product=_ready_product(
            product_verdict={"verdict": "NEEDS_REPAIR"},
            final_map_status="unknown"))
    map_v = next(v for v in report.requirements if v.requirement_id == "map")
    assert map_v.verdict == RequirementState.PARTIAL
    assert report.signal == HarnessSignal.REPAIR_CARTOGRAPHY


def test_blocked_by_data_signal():
    ch = _chapter()
    ch["analysis_steps"] = [
        {"capability": "admin_aggregation", "purpose": "各区统计",
         "status": "unavailable"},
    ]
    report = evaluate_goal_satisfaction(
        ch, map_product=_ready_product(
            product_verdict={"verdict": "BLOCKED_BY_DATA"},
            final_map_status="unknown"))
    assert report.verdict in (GoalVerdict.BLOCKED, GoalVerdict.PARTIAL)
    assert report.signal == HarnessSignal.BLOCKED_BY_DATA


def test_blocked_by_method_fails_and_replans():
    ch = _chapter()
    ch["workflow_contract"] = {"method_blockers": ["kriging_numeric_field"]}
    report = evaluate_goal_satisfaction(
        ch, map_product=_ready_product(
            product_verdict={"verdict": "BLOCKED_BY_METHOD"},
            final_map_status="unknown"))
    assert report.verdict == GoalVerdict.FAILED
    assert report.signal == HarnessSignal.REPLAN


def test_fallback_proxy_caps_partial_with_disclosure():
    ch = _chapter()
    ch["fallbacks"] = [{"downgrade_class": "proxy", "reason_code": "planned_cap"}]
    report = evaluate_goal_satisfaction(ch, map_product=_ready_product())
    analysis = next(v for v in report.requirements
                    if v.requirement_id == "analysis:admin_aggregation")
    assert analysis.verdict == RequirementState.PARTIAL
    assert analysis.failed_rule == "fallback_proxy_result"
    assert analysis.uncertainty  # proxy 结论必须披露
    assert report.verdict != GoalVerdict.SATISFIED


def test_fallback_not_allowed_partial_and_replan_with_blockers():
    ch = _chapter()
    ch["fallbacks"] = [{"downgrade_class": "not_allowed",
                        "reason_code": "method_violation"}]
    ch["workflow_contract"] = {"method_blockers": ["m1"]}
    report = evaluate_goal_satisfaction(ch, map_product=_ready_product())
    analysis = next(v for v in report.requirements
                    if v.requirement_id == "analysis:admin_aggregation")
    assert analysis.failed_rule == "fallback_non_comparable"
    assert report.signal == HarnessSignal.REPLAN


def test_visual_and_assisted_cannot_justify_pass():
    """fail-closed 红线：visual/assisted 证据永不单独把需求判 PASS。"""
    from app.services.gis_harness.goal_satisfaction.evaluator import (
        _eval_requirement,
    )

    requirement = GoalRequirement(
        id="analysis:x", kind=RequirementKind.ANALYSIS, capability="x")
    chapter = {"analysis_steps": [{"capability": "x", "status": "complete"}]}
    for cls in (EvidenceClass.VISUAL, EvidenceClass.ASSISTED):
        evidence = [GoalEvidence(
            id="row:x", kind=EvidenceKind.TOOL_RECEIPT,
            evidence_class=cls, status=EvidenceStatus.PRESENT)]
        verdict = _eval_requirement(requirement, evidence, chapter, None)
        assert verdict.verdict != RequirementState.FULFILLED, cls
    # deterministic 同形状 → fulfilled。
    evidence = [GoalEvidence(
        id="row:x", kind=EvidenceKind.TOOL_RECEIPT,
        evidence_class=EvidenceClass.DETERMINISTIC,
        status=EvidenceStatus.PRESENT)]
    verdict = _eval_requirement(requirement, evidence, chapter, None)
    assert verdict.verdict == RequirementState.FULFILLED
    assert PASS_CAPABLE_CLASSES == {"deterministic", "structural"}


def test_multi_goal_partial_ledger():
    """G4：三子目标各异状态 → 逐项 ledger + 全局 partial。"""
    ch = _chapter()
    ch["analysis_steps"] = [
        {"capability": "poi.query", "purpose": "分布", "status": "complete"},
        {"capability": "change_detection", "purpose": "对比", "status": "failed"},
        {"capability": "admin_aggregation", "purpose": "统计", "status": "pending"},
    ]
    ch["export_receipts"] = [{"format": "png", "revision": "7"}]
    report = evaluate_goal_satisfaction(ch, map_product=_ready_product())
    states = {v.requirement_id: v.verdict for v in report.requirements}
    assert states["analysis:poi.query"] == RequirementState.FULFILLED
    assert states["analysis:change_detection"] == RequirementState.FAILED
    assert states["analysis:admin_aggregation"] == RequirementState.NOT_EVALUATED
    assert states["export:png"] == RequirementState.FULFILLED
    assert report.counts.get("fulfilled") == 3  # poi + export + map
    assert report.counts.get("failed") == 1
    assert report.verdict == GoalVerdict.FAILED  # 任一 required failed → failed
    assert report.signal != HarnessSignal.COMPLETE


def test_user_hidden_layer_is_disclosure_not_blocker():
    """user-wins：用户主动隐藏层 = 披露面，不得阻断（与 intent_acceptance 同语义）。"""
    ch = _chapter()
    ch["export_receipts"] = [{"format": "png", "revision": "7"}]
    product = _ready_product()
    product["intent_acceptance"] = {
        "accepted": True, "intent_verified": False,
        "observed_confirmed": False,
        "disclosures": ["layer_hidden_by_user:l1"], "unmet": [],
    }
    report = evaluate_goal_satisfaction(ch, map_product=product)
    map_v = next(v for v in report.requirements if v.requirement_id == "map")
    assert map_v.verdict == RequirementState.FULFILLED
    assert map_v.uncertainty  # 隐藏行为进披露
    assert report.verdict == GoalVerdict.SATISFIED


def test_must_not_violated_fails_whole_goal():
    ch = _chapter()
    ch["goal_contract"] = GoalContract(
        goal_id="g",
        requirements=[
            GoalRequirement(id="map", kind=RequirementKind.MAP),
            GoalRequirement(id="no_heatmap", kind=RequirementKind.ANALYSIS,
                            polarity="must_not", capability="hotspot",
                            summary="禁止热力图"),
        ],
    ).model_dump()
    # hotspot 行 complete（违反禁令的事实发生且有据）。
    ch["analysis_steps"] = [
        {"capability": "hotspot", "purpose": "热力图", "status": "complete"},
    ]
    report = evaluate_goal_satisfaction(ch, map_product=_ready_product())
    assert report.verdict == GoalVerdict.FAILED
    forbidden = next(v for v in report.requirements
                     if v.requirement_id == "no_heatmap")
    assert forbidden.failed_rule == "forbidden_outcome_present"


def test_must_not_without_target_never_guesses():
    ch = _chapter()
    ch["goal_contract"] = GoalContract(
        goal_id="g",
        requirements=[GoalRequirement(
            id="no_thing", kind=RequirementKind.ANALYSIS,
            polarity="must_not")],
    ).model_dump()
    report = evaluate_goal_satisfaction(ch, map_product=_ready_product())
    forbidden = next(v for v in report.requirements
                     if v.requirement_id == "no_thing")
    assert forbidden.verdict == RequirementState.NOT_EVALUATED


def test_report_serializable_and_bounded():
    report = evaluate_goal_satisfaction(_chapter(), map_product=_ready_product())
    payload = report.to_bounded_dict()
    text = json.dumps(payload, ensure_ascii=False)
    assert len(text) < 8000
    assert payload["schema"] == "goal_satisfaction.v1"
    assert payload["summary_line"].startswith("[GIS Goal]")


def test_no_chapter_returns_none_zero_drift():
    assert evaluate_goal_satisfaction(None) is None
    assert evaluate_goal_satisfaction({}) is None
    assert evaluate_goal_satisfaction({"foo": "bar"}) is None
