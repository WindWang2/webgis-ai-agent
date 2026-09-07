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
