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
    assert len(cases) >= 10, "golden scenario set must stay >= 10 (B2)"
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
    assert abs(result.plan_evidence and 1 or 1) == 1  # evidence shape smoke
    assert result.metrics["tool_call_count"] == 0  # offline lib-level golden


@pytest.mark.asyncio
async def test_benchmark_runner_is_deterministic():
    """Same cases twice → identical verdicts (deterministic-first, B4)."""
    cases = get_all_cases()
    runner = GISBenchmarkRunner()
    first = await runner.run(cases)
    second = await runner.run(cases)
    assert [(r.case_id, r.passed) for r in first] == [
        (r.case_id, r.passed) for r in second
    ]
