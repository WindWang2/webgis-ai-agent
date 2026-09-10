"""Test GIS-02: MCDA zero-variance normalization.

Verifies that normalize_criterion_values handles zero-variance inputs correctly:
- MINIMIZE: assigns 0.5 (neutral baseline) rather than awarding 1.0 to prohibitive costs.
- MAXIMIZE: assigns 0.5 (neutral baseline) rather than 1.0.
- RANGE:
  - If identical value is within range_bounds -> 1.0.
  - If identical value is outside range_bounds -> penalized / 0.0 rather than 1.0.
- TARGET:
  - If target_value is matched -> 1.0.
  - If target_value is deviated -> penalized according to deviation.
"""
from app.services.spatial_decision.models import Criterion, CriterionDirection
from app.services.spatial_decision.normalization import normalize_criterion_values


def test_zero_variance_maximize():
    crit = Criterion(id="benefit", name="Benefit", direction=CriterionDirection.MAXIMIZE)
    raw = {"site_1": 42.0, "site_2": 42.0, "site_3": 42.0}
    res = normalize_criterion_values(raw, crit)
    assert res == {"site_1": 0.5, "site_2": 0.5, "site_3": 0.5}


def test_zero_variance_minimize_not_awarded_perfect_score():
    """Hazardous or costly sites with uniform values should get 0.5 neutral baseline, not 1.0."""
    crit = Criterion(id="toxic_emission", name="Emissions", direction=CriterionDirection.MINIMIZE)
    raw = {"factory_a": 1000.0, "factory_b": 1000.0}
    res = normalize_criterion_values(raw, crit)
    assert res == {"factory_a": 0.5, "factory_b": 0.5}


def test_zero_variance_range_inside_bounds():
    """When all alternatives fall comfortably within allowable range, score should be 1.0."""
    crit = Criterion(
        id="slope",
        name="Allowable Slope",
        direction=CriterionDirection.RANGE,
        range_bounds=(2.0, 10.0),
    )
    raw = {"site_a": 5.0, "site_b": 5.0}
    res = normalize_criterion_values(raw, crit)
    assert res == {"site_a": 1.0, "site_b": 1.0}


def test_zero_variance_range_outside_bounds_penalized():
    """When all alternatives violate allowable range, they must NOT get 1.0."""
    crit = Criterion(
        id="slope",
        name="Allowable Slope",
        direction=CriterionDirection.RANGE,
        range_bounds=(2.0, 10.0),
    )
    # Severe violation: vertical cliff at 85 degrees
    raw = {"site_a": 85.0, "site_b": 85.0}
    res = normalize_criterion_values(raw, crit)
    assert res["site_a"] == 0.0
    assert res["site_b"] == 0.0

    # Mild violation below min: 1.0 degree
    raw_below = {"site_a": 1.0, "site_b": 1.0}
    res_below = normalize_criterion_values(raw_below, crit)
    assert res_below["site_a"] < 1.0


def test_zero_variance_target():
    crit = Criterion(
        id="ph",
        name="Target pH",
        direction=CriterionDirection.TARGET,
        target_value=7.0,
    )
    raw_hit = {"water_1": 7.0, "water_2": 7.0}
    res_hit = normalize_criterion_values(raw_hit, crit)
    assert res_hit == {"water_1": 1.0, "water_2": 1.0}

    raw_miss = {"water_1": 14.0, "water_2": 14.0}
    res_miss = normalize_criterion_values(raw_miss, crit)
    assert res_miss["water_1"] == 0.0
    assert res_miss["water_2"] == 0.0
