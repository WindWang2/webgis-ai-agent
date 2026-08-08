"""
Unit tests for SpatialImpactEngine and MetricEvaluator.
Verifies spatial impact zone generation, UTM surface area calculations,
distance decay models, uncertainty propagation, rule integration, and confidence scoring.
"""
import pytest
import math
from app.services.spatial_decision.models import (
    TargetAreaSpec,
    DomainRule,
    EvidenceItem,
    MetricRange,
    MetricDeltaV2,
    SpatialImpactZone,
)


# ─── SpatialImpactEngine Tests ────────────────────────────────────

def test_generate_impact_zones_point():
    from app.services.spatial_decision.impact_engine import SpatialImpactEngine

    engine = SpatialImpactEngine()
    target_area = TargetAreaSpec(
        query="Beijing CBD Center",
        geometry_type="Point",
        center=(116.4074, 39.9042),
        geometry={"type": "Point", "coordinates": [116.4074, 39.9042]},
        resolved_name="Beijing CBD",
        source="geocode"
    )

    zones, geojson = engine.generate_impact_zones(
        target_area=target_area,
        direct_radius_m=500.0,
        indirect_radius_m=1500.0,
        properties={"scenario": "subway_line_1"}
    )

    # Check returned zones list
    assert len(zones) == 2
    direct_zone = next(z for z in zones if z.zone_type == "direct")
    indirect_zone = next(z for z in zones if z.zone_type == "indirect")

    assert direct_zone.radius_m == 500.0
    assert indirect_zone.radius_m == 1500.0
    assert direct_zone.impact_level == "high"
    assert indirect_zone.impact_level == "medium"

    # Verify direct area ~ pi * 0.5^2 = 0.7854 km2
    expected_direct_area = math.pi * (0.5 ** 2)
    assert pytest.approx(direct_zone.area_km2, abs=0.05) == expected_direct_area

    # Verify indirect area ~ pi * (1.5^2 - 0.5^2) = pi * 2.0 = 6.283 km2
    expected_indirect_area = math.pi * (1.5 ** 2 - 0.5 ** 2)
    assert pytest.approx(indirect_zone.area_km2, abs=0.1) == expected_indirect_area

    # Check GeoJSON structure
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 2

    direct_feat = geojson["features"][0]
    indirect_feat = geojson["features"][1]

    assert direct_feat["geometry"]["type"] == "Polygon"
    assert indirect_feat["geometry"]["type"] == "Polygon"

    # Indirect ring polygon should have inner hole (at least 2 coordinate rings)
    indirect_coords = indirect_feat["geometry"]["coordinates"]
    assert len(indirect_coords) >= 2, "Indirect ring polygon should contain outer boundary and inner hole"


def test_utm_area_calculation():
    from app.services.spatial_decision.impact_engine import SpatialImpactEngine

    engine = SpatialImpactEngine()
    # A small square polygon in WGS84 around (116.40, 39.90)
    # Approx 0.01 deg lat ~ 1.11 km, 0.01 deg lng ~ 0.85 km -> Area ~ 0.94 km2
    polygon_geojson = {
        "type": "Polygon",
        "coordinates": [[
            [116.400, 39.900],
            [116.410, 39.900],
            [116.410, 39.910],
            [116.400, 39.910],
            [116.400, 39.900]
        ]]
    }

    area_km2 = engine.compute_utm_area_km2(polygon_geojson)
    assert 0.8 < area_km2 < 1.1


def test_distance_decay_models():
    from app.services.spatial_decision.impact_engine import SpatialImpactEngine

    engine = SpatialImpactEngine()

    # Exponential decay: delta(r) = delta_max * exp(-r / decay_radius)
    # At r = 0, delta = 100. At r = 500 with decay_radius = 500, delta = 100 * exp(-1) = 36.7879
    d_0 = engine.evaluate_distance_decay(distance_m=0.0, delta_max=100.0, decay_radius=500.0)
    d_500 = engine.evaluate_distance_decay(distance_m=500.0, delta_max=100.0, decay_radius=500.0)
    d_1000 = engine.evaluate_distance_decay(distance_m=1000.0, delta_max=100.0, decay_radius=500.0)

    assert d_0 == 100.0
    assert pytest.approx(d_500, rel=1e-3) == 36.7879
    assert pytest.approx(d_1000, rel=1e-3) == 13.5335

    # Step decay
    intervals = [
        (0.0, 500.0, 20.0),
        (500.0, 1500.0, 10.0),
        (1500.0, 3000.0, 5.0)
    ]
    assert engine.evaluate_step_decay(250.0, intervals) == 20.0
    assert engine.evaluate_step_decay(1000.0, intervals) == 10.0
    assert engine.evaluate_step_decay(2000.0, intervals) == 5.0
    assert engine.evaluate_step_decay(5000.0, intervals) == 0.0


def test_generate_impact_zones_polygon():
    from app.services.spatial_decision.impact_engine import SpatialImpactEngine

    engine = SpatialImpactEngine()
    poly_geom = {
        "type": "Polygon",
        "coordinates": [[
            [121.47, 31.23],
            [121.48, 31.23],
            [121.48, 31.24],
            [121.47, 31.24],
            [121.47, 31.23]
        ]]
    }
    target_area = TargetAreaSpec(
        query="Shanghai Station Zone",
        geometry_type="Polygon",
        geometry=poly_geom,
        resolved_name="Shanghai Station Zone",
        source="geojson"
    )

    zones, geojson = engine.generate_impact_zones(
        target_area=target_area,
        direct_radius_m=300.0,
        indirect_radius_m=800.0
    )

    assert len(zones) == 2
    assert geojson["type"] == "FeatureCollection"
    assert zones[0].area_km2 > 0.0
    assert zones[1].area_km2 > 0.0


# ─── MetricEvaluator Tests ────────────────────────────────────────

def test_metric_evaluation_rule_interval():
    from app.services.spatial_decision.metric_evaluator import MetricEvaluator

    evaluator = MetricEvaluator()

    # Rule: Subway housing price increase +15% ~ +25%, expected +20%
    rule = DomainRule(
        id="rule_subway_housing_01",
        domain="real_estate",
        name="Subway Impact on Housing",
        statement="Subway opening increases housing prices by 15% to 25% within direct zone.",
        parameters={
            "pct_change_min": 0.15,
            "pct_change_expected": 0.20,
            "pct_change_max": 0.25
        },
        confidence=0.85
    )

    metric_delta = evaluator.evaluate_metric(
        metric_key="housing_price",
        metric_name="Avg Housing Price",
        baseline=50000.0,
        rule=rule,
        unit="RMB/m2"
    )

    assert metric_delta.metric_key == "housing_price"
    assert metric_delta.baseline == 50000.0
    assert metric_delta.simulated == 60000.0  # 50000 * (1 + 0.20)
    assert metric_delta.delta_abs == 10000.0
    assert metric_delta.delta_pct == 20.0
    assert metric_delta.unit == "RMB/m2"
    assert metric_delta.missing_baseline is False

    assert isinstance(metric_delta.range, MetricRange)
    assert metric_delta.range.min_val == 57500.0   # 50000 * 1.15
    assert metric_delta.range.expected_val == 60000.0
    assert metric_delta.range.max_val == 62500.0   # 50000 * 1.25


def test_metric_evaluation_absolute_delta():
    from app.services.spatial_decision.metric_evaluator import MetricEvaluator

    evaluator = MetricEvaluator()

    rule = DomainRule(
        id="rule_park_access_01",
        domain="urban_planning",
        name="Park Accessibility Delta",
        statement="Park construction increases accessibility score by 10 to 20 points.",
        parameters={
            "delta_min": 10.0,
            "delta_expected": 15.0,
            "delta_max": 20.0
        },
        confidence=0.9
    )

    metric_delta = evaluator.evaluate_metric(
        metric_key="accessibility_index",
        metric_name="Accessibility Index",
        baseline=65.0,
        rule=rule,
        unit="points"
    )

    assert metric_delta.baseline == 65.0
    assert metric_delta.simulated == 80.0
    assert metric_delta.delta_abs == 15.0
    assert pytest.approx(metric_delta.delta_pct, rel=1e-3) == (15.0 / 65.0 * 100.0)

    assert metric_delta.range.min_val == 75.0
    assert metric_delta.range.expected_val == 80.0
    assert metric_delta.range.max_val == 85.0


def test_missing_baseline_handling():
    from app.services.spatial_decision.metric_evaluator import MetricEvaluator

    evaluator = MetricEvaluator()

    metric_delta = evaluator.evaluate_metric(
        metric_key="noise_level",
        metric_name="Ambient Noise Level",
        baseline=0.0,
        custom_delta_abs=(3.0, 5.0, 8.0),
        unit="dB",
        missing_baseline=True
    )

    assert metric_delta.missing_baseline is True
    assert metric_delta.evidence_gap_note is not None
    assert "missing" in metric_delta.evidence_gap_note.lower() or "estimated" in metric_delta.evidence_gap_note.lower()


def test_decision_confidence_calculation():
    from app.services.spatial_decision.metric_evaluator import MetricEvaluator

    evaluator = MetricEvaluator()

    evidence_items = [
        EvidenceItem(
            id="ev_01",
            type="observed_fact",
            domain="transportation",
            statement="Subway station planning document verified.",
            source="City Planning Bureau",
            confidence=0.95
        ),
        EvidenceItem(
            id="ev_02",
            type="retrieved_rule",
            domain="real_estate",
            statement="Hedonic pricing model for transit proximity.",
            source="Academic Journal",
            confidence=0.85
        )
    ]

    rules = [
        DomainRule(
            id="r1",
            domain="real_estate",
            name="Rule 1",
            statement="Statement 1",
            confidence=0.9
        )
    ]

    conf = evaluator.calculate_decision_confidence(
        evidence_chain=evidence_items,
        baseline_available=True,
        rules_applied=rules,
        target_resolution_confidence=0.95
    )

    assert 0.0 <= conf <= 1.0
    assert conf > 0.8  # Should be high confidence given strong evidence and rules


def test_uncertainty_description_generation():
    from app.services.spatial_decision.metric_evaluator import MetricEvaluator

    evaluator = MetricEvaluator()
    rule = DomainRule(
        id="r1", domain="test", name="Rule", statement="Test",
        parameters={"pct_change_min": 0.1, "pct_change_expected": 0.15, "pct_change_max": 0.2},
        confidence=0.8
    )

    metric = evaluator.evaluate_metric(
        metric_key="price", metric_name="Price", baseline=100.0, rule=rule
    )

    desc = evaluator.generate_uncertainty_description(
        metric_deltas={"price": metric},
        overall_confidence=0.85
    )

    assert isinstance(desc, str)
    assert len(desc) > 0
    assert "85" in desc or "0.85" in desc or "confidence" in desc.lower()
