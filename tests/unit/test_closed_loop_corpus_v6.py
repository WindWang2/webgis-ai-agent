"""闭环语料回归锁（V6 Wave 16a）。

不变式（任务书 W16a）：

- 总数 ≥100（当前 17 制图类型 × 12 故障 = 204）；
- 类型 × 故障矩阵全覆盖（无空洞；每故障类目 ≥2 条）；
- 期望词表合法性：finding 域 ⊆ W6 ``UNIFIED_DOMAINS``、修复类 ⊆ W10
  ``REPAIR_CLASSES``（16 类）、裁决 ⊆ W7 产品裁决 5 token、能力 id 全部
  命中 ``CapabilityRegistry``；
- 确定性：两次构建 id/全字段一致；``ClosedLoopScenario`` frozen。
"""
from __future__ import annotations

import dataclasses

from app.evaluation.closed_loop_corpus import (
    CLOSED_LOOP_VERDICTS,
    FAULT_KIND_IDS,
    MAP_TYPE_IDS,
    build_closed_loop_corpus,
    scenarios_by_fault,
    scenarios_by_map_type,
)
from app.lib.gis.capability_registry import get_capability_registry
from app.services.gis_harness.completion.contracts import (
    VERDICT_BLOCKED_BY_DATA,
    VERDICT_BLOCKED_BY_METHOD,
    VERDICT_NEEDS_REPAIR,
    VERDICT_READY,
    VERDICT_READY_WITH_WARNINGS,
)
from app.services.gis_harness.completion.unified_findings import UNIFIED_DOMAINS
from app.services.gis_harness.repair_planner import REPAIR_CLASSES


def test_corpus_size_floor():
    scenarios = build_closed_loop_corpus()
    assert len(scenarios) >= 100, f"closed-loop corpus must stay >= 100, got {len(scenarios)}"
    assert len(scenarios) == len(MAP_TYPE_IDS) * len(FAULT_KIND_IDS)


def test_map_fault_matrix_full_coverage():
    """类型 × 故障全矩阵：每对组合恰一条；每故障 ≥2 条；每类型全故障覆盖。"""
    scenarios = build_closed_loop_corpus()
    assert len(MAP_TYPE_IDS) >= 17, f"map types must stay >= 17, got {len(MAP_TYPE_IDS)}"
    pairs = {(s.map_type, s.injected_failure) for s in scenarios}
    assert len(pairs) == len(scenarios), "scenario matrix must have no duplicate pair"
    for mid in MAP_TYPE_IDS:
        for fid in FAULT_KIND_IDS:
            assert (mid, fid) in pairs, f"matrix hole: {mid} x {fid}"
    by_fault = scenarios_by_fault(scenarios)
    for fid, group in by_fault.items():
        assert len(group) >= 2, f"fault {fid} must have >= 2 scenarios, got {len(group)}"
    by_type = scenarios_by_map_type(scenarios)
    for mid, group in by_type.items():
        assert {s.injected_failure for s in group} == set(FAULT_KIND_IDS)


def test_expected_vocab_legality():
    """期望词表全部复用既有常量，不自创词汇。"""
    scenarios = build_closed_loop_corpus()
    assert set(CLOSED_LOOP_VERDICTS) == {
        VERDICT_READY, VERDICT_READY_WITH_WARNINGS, VERDICT_NEEDS_REPAIR,
        VERDICT_BLOCKED_BY_DATA, VERDICT_BLOCKED_BY_METHOD,
    }
    for s in scenarios:
        assert s.expected_finding_domain in UNIFIED_DOMAINS, s.scenario_id
        assert s.expected_repair_class in REPAIR_CLASSES, s.scenario_id
        assert s.expected_verdict in CLOSED_LOOP_VERDICTS, s.scenario_id


def test_expected_capabilities_registered():
    """期望能力全部命中 CapabilityRegistry（不虚构能力）。"""
    registry = get_capability_registry()
    assert registry.count > 0
    scenarios = build_closed_loop_corpus()
    for s in scenarios:
        assert s.expected_capabilities, s.scenario_id
        for cap in s.expected_capabilities:
            assert registry.has(cap), f"{s.scenario_id}: unknown capability {cap}"


def test_six_segment_expectations_complete():
    """六段式期望齐全：意图/骨架/能力/故障/域/修复类/裁决非空。"""
    for s in build_closed_loop_corpus():
        assert s.scenario_id and s.map_type and s.map_intent, s.scenario_id
        assert s.workflow_skeleton and len(s.workflow_skeleton) >= 3, s.scenario_id
        assert s.expected_capabilities, s.scenario_id
        assert s.injected_failure, s.scenario_id
        assert s.expected_finding_domain and s.expected_repair_class, s.scenario_id
        assert s.expected_verdict, s.scenario_id


def test_determinism_and_frozen():
    first = build_closed_loop_corpus()
    second = build_closed_loop_corpus()
    assert [s.scenario_id for s in first] == [s.scenario_id for s in second]
    assert first == second
    assert len({s.scenario_id for s in first}) == len(first)
    assert dataclasses.is_dataclass(first[0])
    try:
        first[0].map_intent = "篡改"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover — frozen 失效即红
        raise AssertionError("ClosedLoopScenario must be frozen")


def test_no_ready_without_cause():
    """故障注入场景不得期望无条件 READY（READY 仅作哨兵位保留在词表中）。

    降级面（render/chart 诊断）期望 READY_WITH_WARNINGS 而非 READY ——
    与 unified_findings degradation_only 语义一致。
    """
    for s in build_closed_loop_corpus():
        assert s.expected_verdict != VERDICT_READY, s.scenario_id
