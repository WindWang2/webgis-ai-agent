"""
Strict Unit, Property, and Numerical Golden Tests for Spatial Decision Intelligence V3.
Verifies normalization, weight summation, WSM & TOPSIS numerical precision,
generalized Pareto non-dominance, spatial & numeric constraints, Monte Carlo determinism,
sensitivity tipping points, and minimax regret.
"""
import math
import numpy as np
import pytest

from app.services.spatial_decision.models_v3 import (
    Alternative,
    Constraint,
    ConstraintCategory,
    ConstraintType,
    Criterion,
    CriterionDirection,
    DistributionType,
    NormalizationStrategy,
    SpatialPredicate,
    UncertainParameter,
    WeightSource,
)
from app.services.spatial_decision.normalization import (
    normalize_criterion_values,
    normalize_weights,
    NormalizationError,
)
from app.services.spatial_decision.constraints import evaluate_alternative_constraints
from app.services.spatial_decision.spatial_constraints import evaluate_spatial_constraint
from app.services.spatial_decision.mcda import MultiCriteriaDecisionEngine
from app.services.spatial_decision.pareto import compute_pareto_frontier
from app.services.spatial_decision.uncertainty import (
    sample_parameter_distribution,
    compute_distribution_summary,
)
from app.services.spatial_decision.sensitivity import analyze_weight_sensitivity
from app.services.spatial_decision.robustness import compute_robustness_and_regret
from app.services.spatial_decision.evidence_hardening import (
    detect_rule_conflicts,
)
from app.services.spatial_decision.models import DomainRule


# --- 1. Normalization & Unit Safety Tests ---

def test_normalization_benefit_min_max():
    crit = Criterion(
        id="pop",
        name="Population",
        direction=CriterionDirection.MAXIMIZE,
        normalization_strategy=NormalizationStrategy.MIN_MAX_BENEFIT,
    )
    raw = {"A": 100.0, "B": 200.0, "C": 150.0}
    norm = normalize_criterion_values(raw, crit)
    assert norm["A"] == 0.0
    assert norm["B"] == 1.0
    assert norm["C"] == 0.5


def test_normalization_cost_min_max():
    crit = Criterion(
        id="cost",
        name="Cost",
        direction=CriterionDirection.MINIMIZE,
        normalization_strategy=NormalizationStrategy.MIN_MAX_COST,
    )
    raw = {"A": 100.0, "B": 200.0, "C": 150.0}
    norm = normalize_criterion_values(raw, crit)
    assert norm["A"] == 1.0   # Lowest cost gets best normalized utility
    assert norm["B"] == 0.0   # Highest cost gets worst utility
    assert norm["C"] == 0.5


def test_normalization_zero_range_tied():
    crit = Criterion(id="equal", name="Equal", direction=CriterionDirection.MAXIMIZE)
    raw = {"A": 50.0, "B": 50.0, "C": 50.0}
    norm = normalize_criterion_values(raw, crit)
    assert norm["A"] == 1.0
    assert norm["B"] == 1.0
    assert norm["C"] == 1.0


def test_normalization_negative_values():
    crit = Criterion(id="net_benefit", name="Net Benefit", direction=CriterionDirection.MAXIMIZE)
    raw = {"A": -50.0, "B": 50.0, "C": 0.0}
    norm = normalize_criterion_values(raw, crit)
    assert norm["A"] == 0.0
    assert norm["B"] == 1.0
    assert norm["C"] == 0.5


def test_normalization_unknown_direction_raises():
    crit = Criterion(id="unknown", name="Unknown", direction=CriterionDirection.UNKNOWN)
    raw = {"A": 10.0, "B": 20.0}
    with pytest.raises(NormalizationError):
        normalize_criterion_values(raw, crit)


# --- 2. Weight Normalization Tests ---

def test_weight_normalization_sums_to_one():
    crits = [
        Criterion(id="c1", name="C1", weight=2.0),
        Criterion(id="c2", name="C2", weight=3.0),
        Criterion(id="c3", name="C3", weight=5.0),
    ]
    w_norm, note = normalize_weights(crits)
    assert abs(sum(w_norm.values()) - 1.0) < 1e-6
    assert w_norm["c1"] == 0.2
    assert w_norm["c2"] == 0.3
    assert w_norm["c3"] == 0.5


def test_weight_normalization_rejects_negative():
    # Bypass pydantic validation with model_construct to test normalize_weights validation
    crit = Criterion.model_construct(id="c1", name="C1", weight=-1.0, weight_source=WeightSource.USER_DECLARED)
    with pytest.raises(ValueError):
        normalize_weights([crit])


def test_weight_normalization_rejects_nan_inf():
    crit = Criterion.model_construct(id="c1", name="C1", weight=float("nan"), weight_source=WeightSource.USER_DECLARED)
    with pytest.raises(ValueError):
        normalize_weights([crit])


def test_weight_normalization_equal_fallback():
    crits = [
        Criterion(id="c1", name="C1", weight=0.0),
        Criterion(id="c2", name="C2", weight=0.0),
    ]
    w_norm, note = normalize_weights(crits)
    assert "equal" in note.lower()
    assert abs(w_norm["c1"] - 0.5) < 1e-5
    assert abs(w_norm["c2"] - 0.5) < 1e-5


# --- 3. MCDA Precision & Golden Matrix Tests ---

def test_mcda_wsm_golden():
    """Reference numerical golden case: 3 alternatives, 3 criteria."""
    mcda = MultiCriteriaDecisionEngine()
    norm_matrix = {
        "c1": {"A": 1.0, "B": 0.5, "C": 0.0},
        "c2": {"A": 0.2, "B": 0.8, "C": 0.6},
        "c3": {"A": 0.5, "B": 0.5, "C": 1.0},
    }
    weights = {"c1": 0.5, "c2": 0.3, "c3": 0.2}
    feasible = {"A": True, "B": True, "C": True}

    # Expected:
    # A = 0.5*1.0 + 0.3*0.2 + 0.2*0.5 = 0.50 + 0.06 + 0.10 = 0.66
    # B = 0.5*0.5 + 0.3*0.8 + 0.2*0.5 = 0.25 + 0.24 + 0.10 = 0.59
    # C = 0.5*0.0 + 0.3*0.6 + 0.2*1.0 = 0.00 + 0.18 + 0.20 = 0.38
    scores = mcda.evaluate_wsm(norm_matrix, weights, feasible)
    assert math.isclose(scores["A"], 0.66, abs_tol=1e-4)
    assert math.isclose(scores["B"], 0.59, abs_tol=1e-4)
    assert math.isclose(scores["C"], 0.38, abs_tol=1e-4)


def test_mcda_topsis_golden():
    """Reference numerical golden for TOPSIS closeness ordering."""
    mcda = MultiCriteriaDecisionEngine()
    criteria = [
        Criterion(id="c1", name="C1", direction=CriterionDirection.MAXIMIZE),
        Criterion(id="c2", name="C2", direction=CriterionDirection.MINIMIZE),
    ]
    raw_matrix = {
        "c1": {"A": 10.0, "B": 20.0, "C": 30.0}, # Maximize
        "c2": {"A": 50.0, "B": 30.0, "C": 20.0}, # Minimize: lower is better
    }
    weights = {"c1": 0.5, "c2": 0.5}
    feasible = {"A": True, "B": True, "C": True}

    # C has highest c1 (30) AND lowest c2 (20) -> C must dominate TOPSIS with highest closeness!
    # A has lowest c1 (10) AND highest c2 (50) -> A must have lowest closeness!
    scores = mcda.evaluate_topsis(raw_matrix, criteria, weights, feasible)
    assert scores["C"] > scores["B"] > scores["A"]
    assert 0.0 <= scores["A"] <= 1.0
    assert 0.0 <= scores["C"] <= 1.0


# --- 4. Generalized Pareto Tests ---

def test_pareto_dominance_golden():
    """Alternative A strictly dominates C; B and A are non-dominated trade-offs."""
    matrix = {
        "c1": {"A": 0.9, "B": 0.7, "C": 0.5},
        "c2": {"A": 0.8, "B": 0.95, "C": 0.4},
    }
    feasible = {"A": True, "B": True, "C": True}
    res = compute_pareto_frontier(matrix, feasible)
    assert "A" in res.frontier
    assert "B" in res.frontier
    assert "C" in res.dominated
    assert "C" in res.dominates_map["A"]
    assert "A" in res.dominated_by_map["C"]


def test_infeasible_excluded_from_pareto():
    """Even if C has best scores, if it is infeasible it cannot be in Pareto set."""
    matrix = {
        "c1": {"A": 0.8, "C": 1.0},
        "c2": {"A": 0.8, "C": 1.0},
    }
    feasible = {"A": True, "C": False}
    res = compute_pareto_frontier(matrix, feasible)
    assert "A" in res.frontier
    assert "C" not in res.frontier
    assert "C" in res.infeasible


# --- 5. Constraints Tests ---

def test_hard_constraint_infeasible():
    alt = Alternative(
        id="alt_1",
        name="Test",
        geometry={"type": "Point", "coordinates": [116.22, 39.98]},
        attributes={"cost": 600.0},
    )
    budget_constraint = Constraint(
        id="c_budget",
        name="Budget <= 500",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.NUMERIC,
        metric_key="cost",
        operator="<=",
        threshold=500.0,
    )
    is_feas, hard_v, soft_v = evaluate_alternative_constraints(alt, [budget_constraint], {"cost": 600.0})
    assert is_feas is False
    assert len(hard_v) == 1
    assert hard_v[0].margin == -100.0


def test_spatial_outside_constraint():
    reserve_polygon = {
        "type": "Polygon",
        "coordinates": [[[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]],
    }
    c_outside = Constraint(
        id="c_out",
        name="Outside Reserve",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.SPATIAL,
        spatial_predicate=SpatialPredicate.OUTSIDE,
        reference_geometry=reserve_polygon,
    )
    # Point inside (15, 15)
    inside_pt = {"type": "Point", "coordinates": [15, 15]}
    eval_inside = evaluate_spatial_constraint("inside_alt", inside_pt, c_outside)
    assert eval_inside.passed is False

    # Point outside (5, 5)
    outside_pt = {"type": "Point", "coordinates": [5, 5]}
    eval_outside = evaluate_spatial_constraint("outside_alt", outside_pt, c_outside)
    assert eval_outside.passed is True


# --- 6. Monte Carlo & Determinism Tests ---

def test_monte_carlo_deterministic_seed():
    param = UncertainParameter(
        param_id="p1",
        name="Noise",
        distribution=DistributionType.TRIANGULAR,
        params={"min": 10.0, "mode": 15.0, "max": 25.0},
    )
    rng1 = np.random.default_rng(12345)
    draws1 = sample_parameter_distribution(param, 500, rng1)

    rng2 = np.random.default_rng(12345)
    draws2 = sample_parameter_distribution(param, 500, rng2)

    np.testing.assert_array_almost_equal(draws1, draws2)
    summary = compute_distribution_summary(draws1, "p1")
    assert 10.0 <= summary.mean <= 25.0
    assert summary.p05 <= summary.median <= summary.p95


# --- 7. Sensitivity & Robustness Tests ---

def test_sensitivity_weight_perturbation():
    norm_matrix = {
        "c1": {"A": 1.0, "B": 0.0},
        "c2": {"A": 0.0, "B": 1.0},
    }
    weights = {"c1": 0.8, "c2": 0.2}
    feasible = {"A": True, "B": True}

    res = analyze_weight_sensitivity(norm_matrix, weights, feasible, n_perturbations=500)
    assert res.rank_stability["A"] > 80.0
    assert len(res.critical_weight_thresholds) > 0


def test_minimax_regret_robust_winner():
    # A has lower expected variance and lower max regret than volatile B
    scores = {
        "A": np.array([0.75, 0.76, 0.74, 0.75]),
        "B": np.array([0.90, 0.30, 0.90, 0.30]), # B is volatile
    }
    res = compute_robustness_and_regret(scores)
    assert res.robust_winner_id == "A"
    assert res.alternative_regrets["A"] < res.alternative_regrets["B"]


# --- 8. Evidence Hardening & Conflict Tests ---

def test_detect_conflicting_rules():
    r1 = DomainRule(
        id="r1",
        domain="site_selection",
        name="Rule 1",
        statement="Setback 500m",
        parameters={"min_setback_m": 500},
    )
    r2 = DomainRule(
        id="r2",
        domain="site_selection",
        name="Rule 2",
        statement="Setback 200m",
        parameters={"min_setback_m": 200},
    )
    conflicts = detect_rule_conflicts([r1, r2])
    assert len(conflicts) >= 1
    assert conflicts[0]["type"] == "parameter_conflict"


def test_detect_expired_rule():
    r_expired = DomainRule(
        id="r_exp",
        domain="urban_planning",
        name="Expired Rule",
        statement="Old regulation",
        parameters={"valid_until": "2020-01-01"},
    )
    conflicts = detect_rule_conflicts([r_expired], reference_date="2026-09-03")
    assert len(conflicts) >= 1
    assert conflicts[0]["type"] == "temporal_expired"
