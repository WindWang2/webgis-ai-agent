"""Runtime Scenario Corpus（V4 Wave 9）回归锁。

不变式（任务书 Wave 9）：
- runtime 层语料 ≥3,000 条确定性案例（情境 × 语义族 × scope × 语言 ×
  句式），plan-tier 全量经 GISBenchmarkRunner 真门零失败；
- 语言地板：en ≥30%（修 execute/plan 语料 zh 漂移）；
- 情境期望码有**可追溯性**：指向真实存在的回归套件路径（语料索引
  回归地基，不自证）；
- 复合 E2E 场景 ≥100（7 base × 9 变体 × 2 语言），每场景 ≥2 turn，
  id/内容确定性；
- 组合语料（conformance 20,088 + runtime + 意图 306）≥23,000。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.evaluation.runtime_corpus import (
    RUNTIME_SITUATIONS,
    build_e2e_scenario_corpus,
    build_runtime_corpus,
)


def test_runtime_corpus_size_and_determinism():
    cases = build_runtime_corpus()
    assert len(cases) >= 3000, f"runtime corpus must stay >= 3000, got {len(cases)}"
    again = build_runtime_corpus()
    assert [c.runtime_id for c in cases] == [c.runtime_id for c in again]
    assert [c.query for c in cases] == [c.query for c in again]
    assert len({c.runtime_id for c in cases}) == len(cases)


def test_language_floor():
    cases = build_runtime_corpus()
    en = sum(1 for c in cases if c.lang == "en")
    assert en / len(cases) >= 0.30, f"en floor 30%, got {en}/{len(cases)}"


def test_situation_table_covers_wave9_categories():
    ids = {s.situation_id for s in RUNTIME_SITUATIONS}
    required = {
        "missing-data", "data-later-arrives", "wrong-crs", "invalid-geometry",
        "conflicting-intent", "explicit-algorithm", "algorithm-unavailable",
        "backend-downgrade", "timeout", "cancellation", "tool-failure",
        "provider-fallback", "context-overflow", "stale-artifact",
        "style-only-edit", "layer-visibility-edit", "chart-edit",
        "observation-failure", "repair-success", "repair-failure",
        "no-progress-recovery", "multi-turn-followup",
        "uncertainty-owed-disclosure", "param-change-recompute",
    }
    assert required <= ids, required - ids
    assert all(s.expectation for s in RUNTIME_SITUATIONS)
    assert all(s.traceability for s in RUNTIME_SITUATIONS)


def test_traceability_targets_exist():
    """每个情境指向的回归套件必须真实存在（防索引腐化）。"""
    repo_root = Path(__file__).resolve().parents[3]
    missing = [
        s.traceability for s in RUNTIME_SITUATIONS
        if not (repo_root / s.traceability).exists()
    ]
    assert not missing, f"traceability targets missing: {missing}"


@pytest.mark.asyncio
async def test_runtime_corpus_full_plan_run_green():
    """全量 runtime 语料过 plan 真门（~3.5K 案例实测秒级，默认车道）。"""
    from app.evaluation.runner import GISBenchmarkRunner

    cases = build_runtime_corpus()
    results = await GISBenchmarkRunner().run([c.plan_case for c in cases])
    failures = [r for r in results if not r.passed]
    assert not failures, [
        (r.case_id, r.failures[:2]) for r in failures[:8]
    ]


def test_e2e_scenario_corpus_size_determinism_and_turns():
    scenarios = build_e2e_scenario_corpus()
    assert len(scenarios) >= 100, f"e2e corpus >= 100, got {len(scenarios)}"
    again = build_e2e_scenario_corpus()
    assert [s.scenario_id for s in scenarios] == [s.scenario_id for s in again]
    assert len({s.scenario_id for s in scenarios}) == len(scenarios)
    assert all(len(s.turns) >= 2 for s in scenarios)
    assert all(t.expectation for s in scenarios for t in s.turns)
    en = sum(1 for s in scenarios if s.lang == "en")
    assert en / len(scenarios) >= 0.30


def test_combined_corpus_exceeds_20k():
    """conformance（20,088 plan）+ runtime（≥3K plan）+ 意图（306）≥ 23K。"""
    from app.evaluation.conformance import build_conformance_corpus
    from app.evaluation.golden_cases import get_all_cases

    total = len(build_conformance_corpus()) + len(build_runtime_corpus()) + len(
        get_all_cases())
    assert total >= 23000, f"combined corpus {total} < 23000"


def test_plan_corpus_unique_query_honesty():
    """review R3 MAJOR 诚实计数：plan 层是 144 个唯一查询 × 24 情境标签的
    **情境索引身份回归** —— 唯一查询数、情境期望码与可追溯性如实断言，
    不冒充 3K 个独立执行场景。"""
    cases = build_runtime_corpus()
    unique_queries = {c.query for c in cases}
    assert len(unique_queries) >= 140
    assert all(c.expectation for c in cases)  # 情境元数据全程在场
    # 同一查询在不同情境标签下的 plan 契约完全一致（身份不随标签漂移）
    by_query: dict = {}
    for c in cases:
        by_query.setdefault(c.query, set()).add(
            (c.plan_case.expected_task, c.plan_case.expected_recipe))
    assert all(len(v) == 1 for v in by_query.values())


@pytest.mark.asyncio
async def test_runtime_execution_corpus_real_dispatch():
    """review R3 MAJOR：真实执行层 —— 60 条案例经 simulate_agent_loop 在
    真实 registry 上派发（失败注入/缺 ref/重复失败/依赖链/大载荷），
    全量不变量零违规。"""
    from app.evaluation.replay import simulate_agent_loop
    from app.evaluation.runtime_corpus import build_runtime_execution_corpus
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()

    def echo(v: int = 0, ref: str = "", geojson: object = None) -> dict:
        return {"success": True, "value": v}

    def make_data(name: str) -> dict:
        return {"success": True, "ref": f"ref:{name}"}

    def big(n: int = 100) -> dict:
        return {"success": True, "features": [{"i": i} for i in range(n)]}

    def boom(p: int = 0) -> dict:
        return {"success": False, "error": "boom"}

    for name, fn, kw in [
        ("echo", echo, {"side_effect": "cacheable_read"}),
        ("make_data", make_data, {"side_effect": "state_mutation"}),
        ("big", big, {"cost": "heavy"}),
        ("boom", boom, {}),
    ]:
        registry.register(name=name, description=name, func=fn, **kw)

    cases = build_runtime_execution_corpus()
    assert len(cases) >= 60
    sid = "rtx-exec"
    for case in cases:
        report = await simulate_agent_loop(registry, sid, case.script)
        assert report.invariants_held, (case.case_id, report.violations[:2])
