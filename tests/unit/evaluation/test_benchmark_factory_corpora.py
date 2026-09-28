"""Benchmark Factory V2 语料族 schema（G08）。

覆盖入口：anti_claim（案例面）/ security_corpus / skill_policy_corpus /
hard_negative_corpus / evidence_corpus / chaos_corpus / closed_loop_corpus /
cartography_axes_corpus / mission_corpus。断言方向：id 卫生、闭合词表、
互斥期望字段、chaos 场景指向的测试文件真实存在（消费路径锚）。
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _assert_unique_ids(ids: list) -> None:
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate corpus ids: {sorted(dupes)[:5]}"


# ── anti_claim 案例面（门禁正反例见 test_anti_claim_gates.py）───────────


def test_anti_claim_plan_cases_expectation_symmetry():
    from app.evaluation.anti_claim import build_anti_claim_plan_cases

    cases = build_anti_claim_plan_cases()
    assert len(cases) == 7
    _assert_unique_ids([c.id for c in cases])
    for c in cases:
        assert c.group == "anti-claim" and c.plan_only, c.id
        # 正声明（必须出现）与反声明（禁止出现）互斥 —— 词表面不双写。
        assert bool(c.expected_warning_codes) != bool(c.forbidden_warning_codes), c.id
    positive_codes = {code for c in cases for code in c.expected_warning_codes}
    assert positive_codes == {
        "EQUITY_MISSING_DENOMINATOR",
        "SITE_SELECTION_CRITERIA_UNDECLARED",
        "SUITABILITY_WEIGHT_PROVENANCE",
        "RISK_RECEPTORS_UNCONFIRMED",
    }


def test_workflow_contract_cases_carry_expectations():
    from app.evaluation.anti_claim import build_workflow_contract_cases

    cases = build_workflow_contract_cases()
    assert len(cases) >= 10
    _assert_unique_ids([c.case_id for c in cases])
    for c in cases:
        assert c.case_id.startswith("WC-") and c.recipe_id and c.query, c.case_id
        expectation_faces = (
            c.expect_roles,
            c.expect_obligation_status,
            c.expect_method_blockers,
            c.expect_data_blockers,
            c.expect_warning_codes,
            c.expect_fallback_codes,
            c.forbid_warning_codes,
        )
        assert any(expectation_faces), f"零期望契约案例: {c.case_id}"


# ── security / skill_policy / hard_negative / evidence ──────────────────


def test_security_corpus_adjudication_mix():
    from app.evaluation.security_corpus import build_security_corpus

    cases = build_security_corpus()
    assert len(cases) == 5
    _assert_unique_ids([c.id for c in cases])
    # 手工审定真值必须两态齐备（全 True = 检出回归不可锁；全 False 同理）。
    assert {c.security_expectation.expected_contained for c in cases} == {True, False}
    for c in cases:
        assert c.security_expectation.benign_twin.strip(), c.id


def test_skill_policy_corpus_policy_contract():
    from app.evaluation.skill_policy_corpus import build_skill_policy_corpus

    cases = build_skill_policy_corpus()
    assert len(cases) == 11
    _assert_unique_ids([c.id for c in cases])
    assert all(c.policy_expectation is not None for c in cases)


def test_hard_negative_corpus_scope_or_turns_contract():
    from app.evaluation.hard_negative_corpus import build_hard_negative_corpus

    cases = build_hard_negative_corpus()
    assert len(cases) == 12
    _assert_unique_ids([c.id for c in cases])
    flagged = sum(1 for c in cases if c.expected_scope is not None or c.turns)
    assert flagged >= 7


def test_evidence_corpus_scenarios_closed_and_proof_anchored():
    from app.evaluation.evidence_corpus import build_evidence_corpus

    cases = build_evidence_corpus()
    assert len(cases) == 8
    _assert_unique_ids([c.id for c in cases])
    proof_by_scenario = {
        c.expected_evidence[0].scenario: c.expected_evidence[0].require_positive_proof
        for c in cases
    }
    assert {
        "supported", "unsupported", "missing_evidence",
        "cross_tenant", "stale", "contradicted",
    } <= set(proof_by_scenario)
    assert proof_by_scenario["supported"] is True
    assert proof_by_scenario["unsupported"] is False


# ── chaos / closed_loop / cartography 轴 / mission ──────────────────────


def test_chaos_corpus_nodes_point_to_existing_tests():
    from app.evaluation.chaos_corpus import build_chaos_corpus, corpus_node_ids

    scenarios = build_chaos_corpus()
    assert len(scenarios) == 17
    assert len({s.scenario_id for s in scenarios}) == len(scenarios)
    nodes = corpus_node_ids()
    assert len(nodes) == len(scenarios)
    for node in nodes:
        assert node.startswith("tests/"), node
        assert (REPO_ROOT / node.split("::")[0]).exists(), f"消费路径断链: {node}"


def test_closed_loop_corpus_partitions_and_closed_vocabulary():
    from app.evaluation.closed_loop_corpus import (
        CLOSED_LOOP_VERDICTS,
        FAULT_KIND_IDS,
        MAP_TYPE_IDS,
        build_closed_loop_corpus,
        scenarios_by_fault,
        scenarios_by_map_type,
    )

    scenarios = build_closed_loop_corpus()
    assert len(scenarios) >= 200
    _assert_unique_ids([s.scenario_id for s in scenarios])
    assert {s.map_type for s in scenarios} <= set(MAP_TYPE_IDS)
    assert {s.injected_failure for s in scenarios} <= set(FAULT_KIND_IDS)
    assert {s.expected_verdict for s in scenarios} <= set(CLOSED_LOOP_VERDICTS)
    by_map = scenarios_by_map_type(scenarios)
    by_fault = scenarios_by_fault(scenarios)
    assert sum(len(v) for v in by_map.values()) == len(scenarios)
    assert sum(len(v) for v in by_fault.values()) == len(scenarios)


def test_cartography_axes_corpus_covers_four_axes():
    from app.evaluation.cartography_axes_corpus import build_cartography_axes_corpus

    cases = build_cartography_axes_corpus()
    assert {c.axis for c in cases} == {"layout", "cvd", "template", "honesty"}
    _assert_unique_ids([c.case_id for c in cases])


def test_cartography_axes_layout_and_cvd_anchors_replay():
    from app.evaluation.cartography_axes_corpus import (
        build_cartography_axes_corpus,
        run_cartography_axes_case,
    )

    cases = {c.case_id: c for c in build_cartography_axes_corpus()}
    assert run_cartography_axes_case(cases["CARTX-layout-overlap-detected"]).passed
    assert run_cartography_axes_case(
        cases["CARTX-cvd-default-ylorrd-deuteranopia"]
    ).passed


def test_mission_corpus_step_integrity():
    from app.evaluation.mission_corpus import build_mission_corpus

    scenarios = build_mission_corpus()
    assert len(scenarios) == 9
    _assert_unique_ids([s.scenario_id for s in scenarios])
    for s in scenarios:
        assert s.steps and s.root_goal and s.expected_final_state, s.scenario_id
