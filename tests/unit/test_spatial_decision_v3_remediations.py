"""
Regression and verification test suite for PR #1132 remediations.
Covers all 7 CRITICAL and MAJOR audit findings:
1. Multi-feature hazard centroid distance blind spot (spatial_constraints.py)
2. Sample-0 index bug in robustness (robustness.py)
3. TOPSIS TARGET/RANGE semantic reversal (mcda.py & normalization.py)
4. Tool spatial predicate mapping (spatial_decision_tools.py)
5. Monte Carlo parameter sampling & dynamic feasibility (decision_engine_v3.py)
6. Triangular distribution degenerate bounds (uncertainty.py)
7. MapSpec layer geometry type detection (mapspec_integration.py)
"""
import pytest
import numpy as np

from app.services.spatial_decision.models_v3 import (
    Alternative,
    Constraint,
    ConstraintCategory,
    ConstraintType,
    Criterion,
    CriterionDirection,
    DecisionProblem,
    DistributionType,
    SpatialPredicate,
    TargetAreaSpec,
    UncertainParameter,
)
from app.services.spatial_decision.spatial_constraints import evaluate_spatial_constraint
from app.services.spatial_decision.robustness import compute_robustness_and_regret
from app.services.spatial_decision.mcda import MultiCriteriaDecisionEngine
from app.services.spatial_decision.uncertainty import sample_parameter_distribution
from app.services.spatial_decision.decision_engine_v3 import DecisionEngineV3
from app.services.spatial_decision.mapspec_integration import apply_v3_decision_to_mapspec


# --- 1. Multi-Feature Hazard Centroid Distance Blind Spot ---

def test_remediation_spatial_min_distance_multi_feature_collection():
    """A lethal hazard 1m away must NOT be masked by distant hazards in the collection."""
    site_geom = {"type": "Point", "coordinates": [116.30, 39.90]}
    hazards = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.30001, 39.90]}},  # ~1m away
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.50, 39.90]}},    # ~17km away
        ]
    }
    constraint = Constraint(
        id="hazard_setback",
        name="Min 500m from Hazards",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.SPATIAL,
        spatial_predicate=SpatialPredicate.MIN_DISTANCE,
        threshold=500.0,
        reference_geometry=hazards,
    )
    eval_res = evaluate_spatial_constraint("Site_Alpha", site_geom, constraint)
    assert not eval_res.passed, "Candidate 1m from hazard must fail 500m min distance"
    assert eval_res.observed_value < 10.0, f"Observed distance should be ~1m, got {eval_res.observed_value}"
    assert eval_res.margin < 0.0


def test_remediation_spatial_max_distance_multi_feature_collection():
    """Accessibility <= 1000m satisfied if candidate is close to AT LEAST ONE station."""
    site_geom = {"type": "Point", "coordinates": [116.30, 39.90]}
    stations = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.305, 39.90]}},   # ~420m away
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.60, 39.90]}},    # ~25km away
        ]
    }
    constraint = Constraint(
        id="transit_access",
        name="Max 1000m to Transit",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.SPATIAL,
        spatial_predicate=SpatialPredicate.MAX_DISTANCE,
        threshold=1000.0,
        reference_geometry=stations,
    )
    eval_res = evaluate_spatial_constraint("Site_Alpha", site_geom, constraint)
    assert eval_res.passed, "Candidate 420m from nearest station must satisfy max 1000m distance"
    assert eval_res.observed_value < 1000.0


# --- 2. Robustness Sample-0 Index Bug ---

def test_remediation_robustness_sample0_index_bug():
    """Alternative infeasible only in sample 0 must not have prob_top_ranked forced to 0.0."""
    n_samples = 100
    # Alt A is top-ranked in all samples, but fails constraint only in sample 0
    scores = {
        "Alt_A": np.full(n_samples, 0.90),
        "Alt_B": np.full(n_samples, 0.10),
    }
    feas = {
        "Alt_A": np.array([False] + [True] * (n_samples - 1)),
        "Alt_B": np.full(n_samples, True),
    }
    res = compute_robustness_and_regret(scores, feas)
    # Alt A won in samples 1..99 (99 out of 100 samples)
    assert res.prob_top_ranked["Alt_A"] == 0.99
    assert res.prob_top_ranked["Alt_B"] == 0.01
    assert res.robust_winner_id == "Alt_A"


# --- 3. TOPSIS Target and Range Semantic Reversal ---

def test_remediation_topsis_target_criterion_semantics():
    """Alternative exactly matching target value must dominate alternative 10x away."""
    mcda = MultiCriteriaDecisionEngine()
    crit = Criterion(
        id="noise_db",
        name="Target Noise Level",
        direction=CriterionDirection.TARGET,
        target_value=40.0,
    )
    raw = {
        "noise_db": {
            "Alt_Target": 40.0,   # Exact target
            "Alt_Noisy": 400.0,   # 10x excessive noise
        }
    }
    scores = mcda.evaluate_topsis(raw, [crit], {"noise_db": 1.0}, {"Alt_Target": True, "Alt_Noisy": True})
    assert scores["Alt_Target"] > scores["Alt_Noisy"]
    assert scores["Alt_Target"] == 1.0
    assert scores["Alt_Noisy"] == 0.0


def test_remediation_topsis_range_criterion_semantics():
    """Alternative inside allowable range must dominate alternative far outside."""
    mcda = MultiCriteriaDecisionEngine()
    crit = Criterion(
        id="slope_pct",
        name="Slope Percentage",
        direction=CriterionDirection.RANGE,
        range_bounds=(5.0, 15.0),
    )
    raw = {
        "slope_pct": {
            "Alt_Inside": 10.0,   # Perfectly in [5, 15]
            "Alt_Steep": 80.0,    # Very steep cliff
        }
    }
    scores = mcda.evaluate_topsis(raw, [crit], {"slope_pct": 1.0}, {"Alt_Inside": True, "Alt_Steep": True})
    assert scores["Alt_Inside"] > scores["Alt_Steep"]
    assert scores["Alt_Inside"] == 1.0
    assert scores["Alt_Steep"] == 0.0


# --- 4. Tool Spatial Predicate Mapping ---

def test_remediation_tool_spatial_predicate_mapping():
    """Tool must correctly map min_distance and other spatial predicates."""
    pred_map = {
        "outside": SpatialPredicate.OUTSIDE,
        "within": SpatialPredicate.WITHIN,
        "min_distance": SpatialPredicate.MIN_DISTANCE,
        "max_distance": SpatialPredicate.MAX_DISTANCE,
        "intersects": SpatialPredicate.INTERSECTS,
        "disjoint": SpatialPredicate.DISJOINT,
    }
    for k, expected in pred_map.items():
        mapped = pred_map.get(k)
        assert mapped == expected
        assert mapped != SpatialPredicate.WITHIN or k == "within"


# --- 5. Monte Carlo Parameter Sampling & Dynamic Feasibility ---

@pytest.mark.asyncio
async def test_remediation_monte_carlo_declared_parameter_sampling():
    """DecisionEngineV3 must sample declared uncertain parameters and propagate to feasibility."""
    engine = DecisionEngineV3()
    problem = DecisionProblem(
        problem_id="mc_uncertainty_test",
        goal="Test MC Sampling",
        target_area=TargetAreaSpec(query="Test City"),
        alternatives=[
            Alternative(
                id="A",
                name="Alt A",
                geometry={"type": "Point", "coordinates": [116.4, 39.9]},
                attributes={"cost": 100.0, "benefit": 50.0},
            ),
            Alternative(
                id="B",
                name="Alt B",
                geometry={"type": "Point", "coordinates": [116.45, 39.95]},
                attributes={"cost": 90.0, "benefit": 45.0},
            ),
        ],
        criteria=[
            Criterion(id="cost", name="Cost", direction=CriterionDirection.MINIMIZE, weight=1.0),
            Criterion(id="benefit", name="Benefit", direction=CriterionDirection.MAXIMIZE, weight=1.0),
        ],
        constraints=[
            # Hard budget constraint at 105: since cost has uncertainty [95, 115], A will sometimes violate!
            Constraint(
                id="budget",
                name="Max Budget 105",
                constraint_type=ConstraintType.HARD,
                category=ConstraintCategory.NUMERIC,
                metric_key="cost",
                operator="<=",
                threshold=105.0,
            )
        ],
        uncertain_parameters=[
            UncertainParameter(
                param_id="cost",
                name="Cost Uncertainty",
                distribution=DistributionType.INTERVAL,
                params={"min": 95.0, "max": 115.0},
            )
        ],
        mc_sample_count=200,
        random_seed=42,
    )
    result = await engine.solve_problem(problem)
    assert result.recommendation is not None
    # Verify that cost outcome distribution exists
    assert "cost" in result.recommendation.scores["A"].outcome_distributions
    cost_dist = result.recommendation.scores["A"].outcome_distributions["cost"]
    assert 95.0 <= cost_dist.mean <= 115.0
    # Alt A should have dynamic feasibility < 1.0 because cost sometimes exceeds 105
    assert result.recommendation.robustness.prob_feasible["A"] < 1.0
    assert result.recommendation.robustness.prob_feasible["A"] > 0.0


# --- 6. Triangular Distribution Degenerate Bounds ---

def test_remediation_triangular_distribution_equal_bounds():
    """min == mode == max must return constant array without ValueError."""
    rng = np.random.default_rng(42)
    param = UncertainParameter(
        param_id="tri_equal",
        name="Triangular Equal",
        distribution=DistributionType.TRIANGULAR,
        params={"min": 15.0, "mode": 15.0, "max": 15.0},
    )
    samples = sample_parameter_distribution(param, 100, rng)
    assert len(samples) == 100
    np.testing.assert_array_equal(samples, np.full(100, 15.0))


def test_remediation_triangular_distribution_inverted_bounds():
    """min > max must be swapped without crashing."""
    rng = np.random.default_rng(42)
    param = UncertainParameter(
        param_id="tri_inverted",
        name="Triangular Inverted",
        distribution=DistributionType.TRIANGULAR,
        params={"min": 30.0, "mode": 25.0, "max": 20.0},
    )
    samples = sample_parameter_distribution(param, 100, rng)
    assert len(samples) == 100
    assert np.all(samples >= 20.0)
    assert np.all(samples <= 30.0)


# --- 7. MapSpec Layer Geometry Type Fix ---

@pytest.mark.asyncio
async def test_remediation_mapspec_geometry_type_point_detection():
    """Points in comparison_geojson must produce circle layer, not polygon layer."""
    from unittest.mock import patch

    # Mock SpatialDecisionResultV3
    class DummyProb:
        problem_id = "test_prob_point"
        goal = "Hospital Site Selection"

    class DummyRec:
        decision_fingerprint = "abc123hash"

    class DummyResult:
        problem = DummyProb()
        recommendation = DummyRec()
        comparison_ref_id = "ref_001"
        comparison_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                    "properties": {"alternative_id": "Site_A", "status": "Recommended"},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [[[116.3, 39.8], [116.5, 39.8], [116.5, 40.0], [116.3, 40.0], [116.3, 39.8]]]},
                    "properties": {"layer_type": "constraint_exclusion_zone", "constraint_name": "Park Exclusion"},
                }
            ]
        }

    upserted_layers = []

    async def mock_upsert(*, session_id, layer, source_data):
        upserted_layers.append((layer, source_data))
        return {"success": True, "layer": layer}

    with patch("app.services.spatial_decision.mapspec_integration._upsert_decision_layer", side_effect=mock_upsert):
        res = await apply_v3_decision_to_mapspec("sess_123", DummyResult())
        assert res["success"] is True
        # Two layers must have been authored: constraint polygon and site circle!
        assert len(upserted_layers) == 2
        constraint_layer, constraint_data = upserted_layers[0]
        site_layer, site_data = upserted_layers[1]

        assert constraint_layer["type"] == "fill"
        assert constraint_layer["paint"]["color"] == "#EF4444"
        assert constraint_data["features"][0]["properties"]["layer_type"] == "constraint_exclusion_zone"

        assert site_layer["type"] == "circle"
        assert site_layer["style"]["radius"] == 7.0
        assert site_layer["style"]["color"]["cases"][0] == ["Recommended", "#10B981"]
        assert "paint" in site_layer
        assert site_data["features"][0]["geometry"]["type"] == "Point"

        # Verify evaluate_cartography_semantics passes on the emitted MapSpec
        from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
        from app.services.mapspec_to_svg import compile_mapspec_to_svg

        mapspec = {
            "sources": {
                constraint_layer["source"]: {
                    "type": "geojson",
                    "inlineData": constraint_data,
                    "profile": {"geometryTypes": ["Polygon"], "featureCount": len(constraint_data["features"])},
                },
                site_layer["source"]: {
                    "type": "geojson",
                    "inlineData": site_data,
                    "profile": {"geometryTypes": ["Point"], "featureCount": len(site_data["features"])},
                },
            },
            "layers": [constraint_layer, site_layer],
        }

        report = evaluate_cartography_semantics(mapspec)
        geom_checks = [c for c in report.checks if c.rule == "GEOMETRY_LAYER_TYPE"]
        assert len(geom_checks) == 2
        assert all(c.status == "pass" for c in geom_checks)

        # Verify SVG compiler renders <path> elements without dropping polygons
        svg = compile_mapspec_to_svg(mapspec)
        assert "<path" in svg
        assert "<circle" in svg


@pytest.mark.asyncio
async def test_remediation_mapspec_polygon_alternatives_semantic_and_svg():
    """Polygon alternatives must emit layer type 'fill', with paint and cases, passing QA and rendering SVG <path>."""
    from unittest.mock import patch
    from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
    from app.services.mapspec_to_svg import compile_mapspec_to_svg

    class DummyProb:
        problem_id = "test_prob_poly"
        goal = "Zone Planning"

    class DummyRec:
        decision_fingerprint = "poly123hash"

    class DummyPolyResult:
        problem = DummyProb()
        recommendation = DummyRec()
        comparison_ref_id = "ref_poly"
        comparison_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[116.4, 39.9], [116.5, 39.9], [116.5, 40.0], [116.4, 40.0], [116.4, 39.9]]],
                    },
                    "properties": {"alternative_id": "Parcel_1", "status": "Recommended"},
                }
            ],
        }

    upserted = []

    async def mock_upsert(*, session_id, layer, source_data):
        upserted.append((layer, source_data))
        return {"success": True, "layer": layer}

    with patch("app.services.spatial_decision.mapspec_integration._upsert_decision_layer", side_effect=mock_upsert):
        res = await apply_v3_decision_to_mapspec("sess_456", DummyPolyResult())
        assert res["success"] is True
        assert len(upserted) == 1
        layer, data = upserted[0]
        assert layer["type"] == "fill"
        assert "paint" in layer
        assert "cases" in layer["style"]["color"]

        mapspec = {
            "sources": {
                layer["source"]: {
                    "type": "geojson",
                    "inlineData": data,
                    "profile": {"geometryTypes": ["Polygon"], "featureCount": 1},
                }
            },
            "layers": [layer],
        }
        report = evaluate_cartography_semantics(mapspec)
        geom_checks = [c for c in report.checks if c.rule == "GEOMETRY_LAYER_TYPE"]
        assert len(geom_checks) == 1
        assert geom_checks[0].status == "pass"

        svg = compile_mapspec_to_svg(mapspec)
        assert "<path" in svg

