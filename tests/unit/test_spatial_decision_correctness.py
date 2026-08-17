"""Correctness regression tests for Spatial Decision Intelligence V2 (GIS-03, GIS-20).

These exercise the MetricEvaluator directly (no network / geocoding) to verify
the false-success fix: when no real baseline evidence exists, a metric must NOT
be simulated against fabricated default values. Instead it is reported as
unsimulated (baseline=None, simulated=None, delta_pct=None) with an explicit
evidence-gap note.

CONTEXT.md contract: "Baseline ... Derived from real GeoJSON datasets ...
Never defaults to arbitrary dummy values."
"""
import pytest

from app.services.spatial_decision.metric_evaluator import MetricEvaluator
from app.services.spatial_decision.models import (
    MetricDeltaV2,
    MetricRange,
    TargetAreaSpec,
)


def _target_area():
    return TargetAreaSpec(
        query="q",
        resolved_name="Test Area",
        source="test",
        center=(116.3, 39.9),
        bbox=[116.3, 39.9, 116.4, 40.0],
        confidence=0.9,
    )


def _evaluator():
    return MetricEvaluator()


def _real_baseline(key, name, val, unit):
    """A baseline_metrics entry that the resolver would produce for real data."""
    return MetricDeltaV2(
        metric_key=key,
        metric_name=name,
        baseline=val,
        simulated=val,
        delta_abs=0.0,
        delta_pct=0.0,
        unit=unit,
        missing_baseline=False,
    )


def test_missing_baseline_metric_is_not_simulated():
    """A metric with no real baseline must not produce a fabricated forecast."""
    me = _evaluator()
    # baseline_metrics empty -> every key is missing_baseline.
    metrics, assumptions, confidence, uncertainty = me.evaluate(
        scenario_type="subway",
        baseline_metrics={},
        rules=[],
        parameters={},
        target_area=_target_area(),
        evidence_chain=[],
    )
    hp = metrics["housing_price"]
    assert hp.missing_baseline is True
    # GIS-03 core invariant: no fabricated baseline/simulated values.
    assert hp.baseline is None
    assert hp.simulated is None
    assert hp.delta_pct is None
    assert hp.delta_abs is None
    # The gap must be observable, not hidden behind a fake "success".
    assert hp.evidence_gap_note is not None
    assert any(("缺失" in a or "未伪造" in a or "实测" in a) for a in assumptions)


def test_real_baseline_metric_is_simulated_normally():
    """When a real baseline IS provided, simulation still runs."""
    me = _evaluator()
    baseline_metrics = {
        "housing_price": _real_baseline("housing_price", "Housing Price", 50000.0, "RMB/m2"),
    }
    metrics, _, _, _ = me.evaluate(
        scenario_type="subway",
        baseline_metrics=baseline_metrics,
        rules=[],
        parameters={},
        target_area=_target_area(),
        evidence_chain=[],
    )
    hp = metrics["housing_price"]
    assert hp.missing_baseline is False
    assert hp.baseline == 50000.0
    assert hp.simulated is not None
    assert hp.simulated != hp.baseline  # the default pct range produced a delta
    assert hp.delta_pct is not None
    assert hp.range is not None


def test_mixed_real_and_missing_baselines_simulate_only_real():
    """Real metrics simulate; missing ones don't — mixed scenarios are honest."""
    me = _evaluator()
    baseline_metrics = {
        "commute_time": _real_baseline("commute_time", "Commute Time", 40.0, "min"),
        # housing_price intentionally absent -> must be unsimulated
    }
    metrics, _, _, _ = me.evaluate(
        scenario_type="subway",
        baseline_metrics=baseline_metrics,
        rules=[],
        parameters={},
        target_area=_target_area(),
        evidence_chain=[],
    )
    ct = metrics["commute_time"]
    hp = metrics["housing_price"]
    assert ct.missing_baseline is False and ct.simulated is not None
    assert hp.missing_baseline is True and hp.simulated is None


def test_evaluate_metric_zero_baseline_delta_pct_is_none():
    """GIS-20: delta_pct is None (not a misleading number) when baseline is 0."""
    me = _evaluator()
    res = me.evaluate_metric(
        metric_key="custom",
        metric_name="Custom",
        baseline=0.0,
        custom_pct=(0.1, 0.2, 0.3),
    )
    assert res.delta_pct is None
    # delta_abs is still well-defined for pct intervals at baseline 0.
    assert res.delta_abs == 0.0


@pytest.mark.asyncio
async def test_missing_metric_in_comparison_does_not_crash():
    """Comparison engine must tolerate None simulated/delta_pct (mixed evidence)."""
    from app.services.spatial_decision.comparison_engine import ScenarioComparisonEngine
    from app.services.spatial_decision.models import (
        SpatialDecisionResult,
        ScenarioSpec,
    )

    def _make_result(sid: str, simulated_housing):
        return SpatialDecisionResult(
            decision_id=sid,
            scenario=ScenarioSpec(
                scenario_id=sid,
                scenario_type="subway",
                name=f"Scenario {sid}",
                description="d",
                target_area=_target_area(),
            ),
            target_area=_target_area(),
            metrics={
                "housing_price": MetricDeltaV2(
                    metric_key="housing_price",
                    metric_name="Housing Price",
                    baseline=50000.0 if simulated_housing is not None else None,
                    simulated=simulated_housing,
                    delta_abs=(simulated_housing - 50000.0) if simulated_housing else None,
                    delta_pct=20.0 if simulated_housing else None,
                    range=(
                        MetricRange(min_val=49000.0, expected_val=simulated_housing or 0.0, max_val=51000.0)
                        if simulated_housing
                        else None
                    ),
                    missing_baseline=simulated_housing is None,
                ),
            },
            spatial_impacts=[],
            simulation_geojson={"type": "FeatureCollection", "features": []},
            simulation_ref_id="",
            confidence=0.8,
            uncertainty_description="test",
        )

    cmp = ScenarioComparisonEngine()
    # One scenario with real housing data, one without — must not raise.
    result = await cmp.compare_scenarios(
        results=[_make_result("A", 60000.0), _make_result("B", None)]
    )
    assert len(result.scenarios) == 2
    assert result.recommended_scenario_id in {"A", "B"}


@pytest.mark.asyncio
async def test_comparison_report_unsimulated_metrics_renders_placeholder():
    """P1 (#579): comparison report must not crash on f"{None:.2f}".

    With the default tool params there is no baseline data, so every metric is
    unsimulated (simulated=None). The metric matrix must render a "—"
    placeholder instead of raising TypeError, and the report must still be
    produced end to end (engine -> report).
    """
    from app.services.spatial_decision.comparison_engine import ScenarioComparisonEngine
    from app.services.spatial_decision.models import (
        SpatialDecisionResult,
        ScenarioSpec,
    )
    from app.services.spatial_decision.report_integration import (
        generate_comparison_report_markdown,
    )

    def _make_unsimulated_result(sid: str):
        return SpatialDecisionResult(
            decision_id=sid,
            scenario=ScenarioSpec(
                scenario_id=sid,
                scenario_type="subway",
                name=f"Scenario {sid}",
                description="d",
                target_area=_target_area(),
            ),
            target_area=_target_area(),
            metrics={
                "housing_price": MetricDeltaV2(
                    metric_key="housing_price",
                    metric_name="Housing Price",
                    baseline=None,
                    simulated=None,
                    delta_abs=None,
                    delta_pct=None,
                    missing_baseline=True,
                ),
            },
            spatial_impacts=[],
            simulation_geojson={"type": "FeatureCollection", "features": []},
            simulation_ref_id="",
            confidence=0.8,
            uncertainty_description="test",
        )

    cmp = ScenarioComparisonEngine()
    result = await cmp.compare_scenarios(
        results=[_make_unsimulated_result("A"), _make_unsimulated_result("B")]
    )
    # Every matrix cell is None — must render "—", never raise TypeError.
    report = generate_comparison_report_markdown(result)
    assert "housing_price" in report
    assert "—" in report
    # And the report still names both scenarios in the matrix header.
    assert "Scenario A" in report
    assert "Scenario B" in report
