"""GIS Benchmark Harness regression lock (ADR-0092 Phase B).

The harness itself must stay deterministic and offline. Non-OD golden cases
must pass; OD cases pass once the flow tooling registers (they skip honestly
when it does not). A failing case here means the agent's *semantic* behavior
regressed — a task/classification/capability/algorithm/facet contract broke.
"""
import pytest

from app.evaluation import GISBenchmarkRunner, get_all_cases


@pytest.mark.asyncio
async def test_golden_cases_no_semantic_regression():
    cases = get_all_cases()
    # VNext §15 Phase 2：golden(33) + 确定性矩阵 ≥300（语义族×表述风格×
    # 语言×负例×形态×scope×决策披露×复合意图）。
    assert len(cases) >= 300, "case matrix must stay >= 300 (VNext evaluation V2)"
    runner = GISBenchmarkRunner()
    results = await runner.run(cases)
    by_id = {r.case_id: r for r in results}

    failed = [r.case_id for r in results if r.status == "fail"]
    assert failed == [], f"semantic regressions: {failed}"

    # Core scenarios must not silently skip (incl. OD flow vertical slice).
    for cid in ("G1", "G2", "G4", "G5", "G8", "G9", "G11", "G12"):
        assert by_id[cid].status == "pass", f"{cid}: {by_id[cid].failures}"


@pytest.mark.asyncio
async def test_g2_simple_view_no_forced_analysis():
    """G2 is the anti-over-analysis contract: a plain display request must
    classify simple_view and never plan KDE/hotspot machinery."""
    cases = {c.id: c for c in get_all_cases()}
    runner = GISBenchmarkRunner()
    result = await runner.run_case(cases["G2"])
    assert result.passed, result.failures
    evidence = result.plan_evidence
    assert evidence["task"] == "simple_view"
    assert all(not a.startswith(("spatial.kde", "stats.")) for a in evidence["algorithms"])


@pytest.mark.asyncio
async def test_g5_ndvi_numeric_golden():
    cases = {c.id: c for c in get_all_cases()}
    runner = GISBenchmarkRunner()
    result = await runner.run_case(cases["G5"])
    assert result.passed, result.failures
    assert result.metrics["numerical_correct"] is True
    # Real evidence-shape lock (replaces a tautological smoke check).
    ev = result.plan_evidence
    assert ev.get("task") == "vegetation_index"
    assert ev.get("recipe_id"), "plan evidence must carry a recipe"
    assert any(a.startswith("remote.ndvi") for a in ev.get("algorithms", []))
    assert result.metrics["tool_call_count"] == 0  # offline lib-level golden


@pytest.mark.asyncio
async def test_benchmark_runner_is_deterministic():
    """Independent runner instances → identical FULL evidence, not just
    verdicts (deterministic-first, B4)."""
    def _key(r):
        return (r.case_id, r.passed, r.status, r.metrics, r.plan_evidence)

    first = await GISBenchmarkRunner().run(get_all_cases())
    second = await GISBenchmarkRunner().run(get_all_cases())
    assert [_key(r) for r in first] == [_key(r) for r in second]
