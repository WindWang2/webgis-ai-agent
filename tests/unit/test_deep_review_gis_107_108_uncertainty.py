"""GIS-107/GIS-108 regression: no synthetic robustness numbers.

GIS-107: with no declared uncertain parameters the engine injected N(0,0.02)
noise and printed fabricated probabilities/regret. Robustness is now marked
``simulated=False`` (not_simulated).
GIS-108: compute_distribution_summary returned ``[0.0]`` stats for all-NaN
samples; it now returns None statistics + an explicit disclosure.
"""
import numpy as np
import pytest

from app.services.spatial_decision.decision_engine_v3 import DecisionEngineV3
from app.services.spatial_decision.models_v3 import (
    Alternative,
    Criterion,
    CriterionDirection,
    DecisionProblem,
    TargetAreaSpec,
    UncertainParameter,
)
from app.services.spatial_decision.uncertainty import compute_distribution_summary


def _problem(*, with_uncertainty: bool = False) -> DecisionProblem:
    return DecisionProblem(
        problem_id=f"gis107_{'u' if with_uncertainty else 'det'}",
        goal="Robustness honesty test",
        target_area=TargetAreaSpec(query="Zone"),
        alternatives=[
            Alternative(id="A", name="A", attributes={"score": 80.0}),
            Alternative(id="B", name="B", attributes={"score": 60.0}),
        ],
        criteria=[
            Criterion(id="score", name="Score", direction=CriterionDirection.MAXIMIZE),
        ],
        uncertain_parameters=(
            [UncertainParameter(
                param_id="score",
                name="Score uncertainty",
                params={"min": 70.0, "max": 90.0},
            )]
            if with_uncertainty else []
        ),
        mc_sample_count=50,
    )


@pytest.mark.asyncio
async def test_robustness_not_simulated_without_declared_uncertainty():
    res = await DecisionEngineV3().solve_problem(_problem(with_uncertainty=False))
    robustness = res.recommendation.robustness
    assert robustness is not None
    assert robustness.simulated is False
    assert robustness.alternative_regrets == {}
    assert robustness.prob_top_ranked == {}
    assert robustness.prob_feasible == {}
    assert robustness.robust_winner_id is None
    assert "not_simulated" in robustness.summary
    assert "not_simulated" in res.report_markdown
    # no fabricated composite distributions either
    assert all(
        "mcda_composite" not in s.outcome_distributions
        for s in res.recommendation.scores.values()
    )


@pytest.mark.asyncio
async def test_declared_uncertainty_still_simulates_robustness():
    res = await DecisionEngineV3().solve_problem(_problem(with_uncertainty=True))
    robustness = res.recommendation.robustness
    assert robustness.simulated is True
    assert robustness.prob_top_ranked
    assert "not_simulated" not in robustness.summary


def test_all_non_finite_samples_return_none_statistics():
    summary = compute_distribution_summary(
        np.array([np.nan, np.inf, -np.inf, np.nan]), "delta"
    )
    assert summary.mean is None
    assert summary.median is None
    assert summary.std is None
    assert summary.p05 is None
    assert summary.p25 is None
    assert summary.p75 is None
    assert summary.p95 is None
    assert summary.note and "no finite samples" in summary.note


def test_empty_samples_return_none_statistics():
    summary = compute_distribution_summary(np.array([]), "delta")
    assert summary.mean is None
    assert summary.note and "no samples" in summary.note


def test_finite_samples_still_summarized():
    summary = compute_distribution_summary(np.array([1.0, 2.0, 3.0]), "delta")
    assert summary.mean == pytest.approx(2.0)
    assert summary.note is None
