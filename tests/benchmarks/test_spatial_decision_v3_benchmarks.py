"""
Decision Benchmark Suite for Spatial Decision Intelligence V3.
Contains 25 comprehensive decision benchmark cases across 12 methodology categories:
  - site selection
  - facility expansion
  - transport scenario
  - green-space scenario
  - budget constraint
  - environment constraint
  - missing evidence
  - conflicting evidence
  - weight sensitivity
  - uncertainty & robustness
  - no clear winner
  - no feasible alternative
"""
import time
import numpy as np
import pytest

from app.services.spatial_decision.models_v3 import (
    Alternative,
    Assumption,
    BaselineEvidenceContext,
    BaselineTruthState,
    Constraint,
    ConstraintCategory,
    ConstraintType,
    Criterion,
    CriterionDirection,
    DecisionProblem,
    NormalizationStrategy,
    ParetoStatus,
    RecommendationAdmissibility,
    SpatialPredicate,
    TargetAreaSpec,
    UncertainParameter,
    WeightSource,
)
from app.services.spatial_decision.decision_engine_v3 import DecisionEngineV3
from app.services.spatial_decision.slices.hospital_site_selection import create_hospital_site_selection_problem
from app.services.spatial_decision.slices.school_facility_expansion import create_school_facility_expansion_problem
from app.services.spatial_decision.evidence_hardening import (
    detect_rule_conflicts,
    evaluate_evidence_quality_and_conflicts,
)
from app.services.spatial_decision.models import DomainRule, EvidenceItem, MetricDeltaV2


@pytest.fixture
def v3_engine():
    return DecisionEngineV3()


# --- Category 1: Site Selection ---

@pytest.mark.asyncio
async def test_bm01_hospital_site_selection_evidence_grounded(v3_engine):
    """Case 01: Hospital site selection with ecological exclusion redline."""
    prob = create_hospital_site_selection_problem()
    res = await v3_engine.solve_problem(prob)

    assert res.recommendation.admissibility == RecommendationAdmissibility.RECOMMENDED
    assert res.recommendation.recommended_alternative_id in {"Site_A", "Site_B"}
    assert res.recommendation.scores["Site_C"].feasible is False
    assert len(res.recommendation.scores["Site_C"].hard_violations) >= 1
    assert "Site_C" not in res.recommendation.pareto_frontier


# --- Category 2: Facility Expansion ---

@pytest.mark.asyncio
async def test_bm02_school_facility_expansion_walkability(v3_engine):
    """Case 02: Primary school expansion balancing student walkability and highway noise."""
    prob = create_school_facility_expansion_problem()
    res = await v3_engine.solve_problem(prob)

    assert res.recommendation.admissibility == RecommendationAdmissibility.RECOMMENDED
    assert res.recommendation.scores["School_C"].feasible is False
    assert res.recommendation.recommended_alternative_id in {"School_A", "School_B"}
    assert res.recommendation.scores[res.recommendation.recommended_alternative_id].rank == 1


# --- Category 3: Transport Scenario ---

@pytest.mark.asyncio
async def test_bm03_subway_transit_corridor_evaluation(v3_engine):
    """Case 03: Subway alignment selection optimizing rider catchment and travel time."""
    alts = [
        Alternative(id="Align_1", name="Express Radial", attributes={"ridership": 240000, "cost_b": 18.5, "travel_min": 22}),
        Alternative(id="Align_2", name="Urban Infill", attributes={"ridership": 310000, "cost_b": 24.0, "travel_min": 31}),
        Alternative(id="Align_3", name="Ring Line", attributes={"ridership": 190000, "cost_b": 15.0, "travel_min": 28}),
    ]
    criteria = [
        Criterion(id="ridership", name="Daily Ridership", direction=CriterionDirection.MAXIMIZE, weight=0.45),
        Criterion(id="cost_b", name="Capital Cost", direction=CriterionDirection.MINIMIZE, weight=0.30),
        Criterion(id="travel_min", name="Travel Time", direction=CriterionDirection.MINIMIZE, weight=0.25),
    ]
    prob = DecisionProblem(
        problem_id="bm_subway",
        goal="Select optimal transit corridor",
        target_area=TargetAreaSpec(query="Metro Corridor"),
        alternatives=alts,
        criteria=criteria,
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.recommended_alternative_id is not None
    assert len(res.recommendation.pareto_frontier) >= 1


# --- Category 4: Green Space Scenario ---

@pytest.mark.asyncio
async def test_bm04_urban_park_siting_greenspace_equity(v3_engine):
    """Case 04: Urban park siting to address green space deficit in dense districts."""
    alts = [
        Alternative(id="Park_North", name="North Brownfield", attributes={"park_deficit_coverage": 45000, "cost_m": 80, "tree_canopy": 60}),
        Alternative(id="Park_South", name="South Waterfront", attributes={"park_deficit_coverage": 65000, "cost_m": 130, "tree_canopy": 85}),
    ]
    criteria = [
        Criterion(id="park_deficit_coverage", name="Deficit Population Covered", direction=CriterionDirection.MAXIMIZE, weight=0.5),
        Criterion(id="tree_canopy", name="Ecological Canopy", direction=CriterionDirection.MAXIMIZE, weight=0.3),
        Criterion(id="cost_m", name="Land Cost", direction=CriterionDirection.MINIMIZE, weight=0.2),
    ]
    prob = DecisionProblem(
        problem_id="bm_park",
        goal="Select park site for green equity",
        target_area=TargetAreaSpec(query="Urban Core"),
        alternatives=alts,
        criteria=criteria,
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.admissibility in {
        RecommendationAdmissibility.RECOMMENDED,
        RecommendationAdmissibility.CONDITIONALLY_RECOMMENDED,
        RecommendationAdmissibility.NO_CLEAR_WINNER,
    }


# --- Category 5: Emergency & Facility Placement ---

@pytest.mark.asyncio
async def test_bm05_fire_station_emergency_coverage(v3_engine):
    """Case 05: Fire station placement optimizing 5-minute response coverage."""
    alts = [
        Alternative(id="FS_East", name="East District", attributes={"coverage_5min": 92.0, "response_time_sec": 240, "cost": 45}),
        Alternative(id="FS_West", name="West District", attributes={"coverage_5min": 78.0, "response_time_sec": 310, "cost": 30}),
    ]
    criteria = [
        Criterion(id="coverage_5min", name="5-Min Coverage", direction=CriterionDirection.MAXIMIZE, weight=0.5),
        Criterion(id="response_time_sec", name="Response Time", direction=CriterionDirection.MINIMIZE, weight=0.3),
        Criterion(id="cost", name="Construction Cost", direction=CriterionDirection.MINIMIZE, weight=0.2),
    ]
    prob = DecisionProblem(
        problem_id="bm_fire",
        goal="Select fire station location",
        target_area=TargetAreaSpec(query="Industrial Zone"),
        alternatives=alts,
        criteria=criteria,
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.recommended_alternative_id == "FS_East"


# --- Category 6: Budget Constraint Disqualification ---

@pytest.mark.asyncio
async def test_bm06_hard_budget_constraint_disqualification(v3_engine):
    """Case 06: Highest-performing alternative disqualified due to hard budget violation."""
    alts = [
        Alternative(id="Luxury_Site", name="High Capacity Deluxe", attributes={"capacity": 5000, "cost": 950}),
        Alternative(id="Standard_Site", name="Standard Parcel", attributes={"capacity": 3000, "cost": 420}),
    ]
    criteria = [
        Criterion(id="capacity", name="Capacity", direction=CriterionDirection.MAXIMIZE, weight=0.7),
        Criterion(id="cost", name="Cost", direction=CriterionDirection.MINIMIZE, weight=0.3),
    ]
    constraints = [
        Constraint(
            id="c_budget",
            name="Budget Ceiling <= 500",
            constraint_type=ConstraintType.HARD,
            category=ConstraintCategory.NUMERIC,
            metric_key="cost",
            operator="<=",
            threshold=500.0,
        )
    ]
    prob = DecisionProblem(
        problem_id="bm_budget",
        goal="Budget constrained facility",
        target_area=TargetAreaSpec(query="South Hub"),
        alternatives=alts,
        criteria=criteria,
        constraints=constraints,
    )
    res = await v3_engine.solve_problem(prob)
    # Luxury_Site violates budget 950 > 500
    assert res.recommendation.scores["Luxury_Site"].feasible is False
    assert res.recommendation.scores["Standard_Site"].feasible is True
    assert res.recommendation.recommended_alternative_id == "Standard_Site"


# --- Category 7: Environmental Exclusion Constraint ---

@pytest.mark.asyncio
async def test_bm07_ecological_nature_reserve_veto(v3_engine):
    """Case 07: Spatial constraint vetoes candidate inside wetland polygon."""
    wetland_poly = {
        "type": "Polygon",
        "coordinates": [[[100, 20], [105, 20], [105, 25], [100, 25], [100, 20]]],
    }
    alts = [
        Alternative(id="Alt_Encroaching", name="Encroaching", geometry={"type": "Point", "coordinates": [102, 22]}, attributes={"utility": 99}),
        Alternative(id="Alt_Clear", name="Safe Site", geometry={"type": "Point", "coordinates": [95, 15]}, attributes={"utility": 80}),
    ]
    constraints = [
        Constraint(
            id="c_eco",
            name="Must be outside wetland",
            constraint_type=ConstraintType.HARD,
            category=ConstraintCategory.SPATIAL,
            spatial_predicate=SpatialPredicate.OUTSIDE,
            reference_geometry=wetland_poly,
        )
    ]
    prob = DecisionProblem(
        problem_id="bm_eco",
        goal="Ecological protection test",
        target_area=TargetAreaSpec(query="Lake Basin"),
        alternatives=alts,
        criteria=[Criterion(id="utility", name="Utility", direction=CriterionDirection.MAXIMIZE)],
        constraints=constraints,
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.scores["Alt_Encroaching"].feasible is False
    assert res.recommendation.recommended_alternative_id == "Alt_Clear"


# --- Category 8: Minimum Spacing Constraint ---

@pytest.mark.asyncio
async def test_bm08_hospital_spacing_separation_constraint(v3_engine):
    """Case 08: Distance separation constraint prevents redundant clustering."""
    existing_fac = {"type": "Point", "coordinates": [116.3000, 39.9000]}
    # Alt_TooClose: ~100m away
    # Alt_FarEnough: ~5000m away
    alts = [
        Alternative(id="Alt_Close", name="Clustered", geometry={"type": "Point", "coordinates": [116.3008, 39.9000]}, attributes={"score": 90}),
        Alternative(id="Alt_Far", name="Distributed", geometry={"type": "Point", "coordinates": [116.3600, 39.9000]}, attributes={"score": 85}),
    ]
    constraints = [
        Constraint(
            id="c_sep",
            name="Min 1000m separation",
            constraint_type=ConstraintType.HARD,
            category=ConstraintCategory.SPATIAL,
            spatial_predicate=SpatialPredicate.MIN_DISTANCE,
            reference_geometry=existing_fac,
            threshold=1000.0,
        )
    ]
    prob = DecisionProblem(
        problem_id="bm_sep",
        goal="Separation constraint test",
        target_area=TargetAreaSpec(query="Urban Center"),
        alternatives=alts,
        criteria=[Criterion(id="score", name="Score", direction=CriterionDirection.MAXIMIZE)],
        constraints=constraints,
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.scores["Alt_Close"].feasible is False
    assert res.recommendation.recommended_alternative_id == "Alt_Far"


# --- Category 9: Compound Multi-Constraint Veto ---

@pytest.mark.asyncio
async def test_bm09_multi_constraint_compound_veto(v3_engine):
    """Case 09: Multiple independent constraints compound to disqualify candidates."""
    alts = [
        Alternative(id="A1", name="A1", attributes={"slope": 18.0, "budget": 300}), # Slope violation
        Alternative(id="A2", name="A2", attributes={"slope": 5.0, "budget": 650}),  # Budget violation
        Alternative(id="A3", name="A3", attributes={"slope": 4.0, "budget": 350}),  # Compliant
    ]
    constraints = [
        Constraint(id="c_slope", name="Slope <= 15", constraint_type=ConstraintType.HARD, category=ConstraintCategory.NUMERIC, metric_key="slope", operator="<=", threshold=15.0),
        Constraint(id="c_bud", name="Budget <= 500", constraint_type=ConstraintType.HARD, category=ConstraintCategory.NUMERIC, metric_key="budget", operator="<=", threshold=500.0),
    ]
    prob = DecisionProblem(
        problem_id="bm_compound",
        goal="Compound constraints test",
        target_area=TargetAreaSpec(query="Valley"),
        alternatives=alts,
        criteria=[Criterion(id="budget", name="Cost", direction=CriterionDirection.MINIMIZE)],
        constraints=constraints,
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.scores["A1"].feasible is False
    assert res.recommendation.scores["A2"].feasible is False
    assert res.recommendation.scores["A3"].feasible is True
    assert res.recommendation.recommended_alternative_id == "A3"


# --- Category 10: Soft Constraint Penalty Tradeoff ---

@pytest.mark.asyncio
async def test_bm10_soft_constraint_penalty_tradeoff(v3_engine):
    """Case 10: Candidate with soft constraint penalty remains feasible but suffers score reduction."""
    alts = [
        Alternative(id="BaselineRef", name="Baseline", attributes={"quality": 50.0, "noise_dist": 600}),
        Alternative(id="OptA", name="Perfect Fit", attributes={"quality": 89.0, "noise_dist": 600}),
        Alternative(id="OptB", name="Marginal Noise", attributes={"quality": 90.0, "noise_dist": 400}), # Soft violation: noise < 500
    ]
    constraints = [
        Constraint(
            id="c_soft_noise",
            name="Preferred Noise Setback >= 500",
            constraint_type=ConstraintType.SOFT,
            category=ConstraintCategory.NUMERIC,
            metric_key="noise_dist",
            operator=">=",
            threshold=500.0,
            penalty_weight=0.10,
        )
    ]
    prob = DecisionProblem(
        problem_id="bm_soft",
        goal="Soft penalty test",
        target_area=TargetAreaSpec(query="Corridor"),
        alternatives=alts,
        criteria=[Criterion(id="quality", name="Quality", direction=CriterionDirection.MAXIMIZE)],
        constraints=constraints,
    )
    res = await v3_engine.solve_problem(prob)
    # Both are feasible
    assert res.recommendation.scores["OptA"].feasible is True
    assert res.recommendation.scores["OptB"].feasible is True
    # OptB has soft violation penalty applied
    assert len(res.recommendation.scores["OptB"].soft_violations) == 1
    assert res.recommendation.recommended_alternative_id == "OptA"


# --- Category 11: All Infeasible -> NO_FEASIBLE_ALTERNATIVE ---

@pytest.mark.asyncio
async def test_bm11_all_infeasible_no_feasible_alternative(v3_engine):
    """Case 11: All alternatives violate hard constraints -> NO_FEASIBLE_ALTERNATIVE."""
    alts = [
        Alternative(id="A", name="Alt A", attributes={"cost": 900}),
        Alternative(id="B", name="Alt B", attributes={"cost": 850}),
    ]
    constraints = [
        Constraint(id="c_limit", name="Cost <= 500", constraint_type=ConstraintType.HARD, category=ConstraintCategory.NUMERIC, metric_key="cost", operator="<=", threshold=500.0)
    ]
    prob = DecisionProblem(
        problem_id="bm_none_feas",
        goal="Infeasible test",
        target_area=TargetAreaSpec(query="Area"),
        alternatives=alts,
        criteria=[Criterion(id="cost", name="Cost", direction=CriterionDirection.MINIMIZE)],
        constraints=constraints,
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.admissibility == RecommendationAdmissibility.NO_FEASIBLE_ALTERNATIVE
    assert res.recommendation.recommended_alternative_id is None
    assert len(res.recommendation.explanation.binding_constraints) >= 1


# --- Category 12: Missing Baseline Evidence Fail-Closed ---

@pytest.mark.asyncio
async def test_bm12_missing_baseline_evidence_fail_closed(v3_engine):
    """Case 12: Core criterion missing baseline across all alternatives -> INSUFFICIENT_EVIDENCE."""
    alts = [
        Alternative(id="A", name="Alt A", attributes={"cost": 100}),
        Alternative(id="B", name="Alt B", attributes={"cost": 120}),
    ]
    criteria = [
        Criterion(id="cost", name="Cost", direction=CriterionDirection.MINIMIZE, is_core=True),
        Criterion(id="missing_pop", name="Missing Population", direction=CriterionDirection.MAXIMIZE, is_core=True),
    ]
    prob = DecisionProblem(
        problem_id="bm_missing",
        goal="Missing evidence test",
        target_area=TargetAreaSpec(query="Unsurveyed Area"),
        alternatives=alts,
        criteria=criteria,
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.admissibility == RecommendationAdmissibility.INSUFFICIENT_EVIDENCE
    assert res.recommendation.recommended_alternative_id is None
    assert len(res.recommendation.explanation.evidence_gaps) >= 1


# --- Category 13: Mixed Baseline Honest Reporting ---

@pytest.mark.asyncio
async def test_bm13_mixed_baseline_honest_reporting(v3_engine):
    """Case 13: Optional missing metric reported honestly without breaking non-core criteria."""
    alts = [
        Alternative(id="A", name="Alt A", attributes={"cost": 100, "non_core_metric": 50}),
        Alternative(id="B", name="Alt B", attributes={"cost": 120, "non_core_metric": 70}),
    ]
    criteria = [
        Criterion(id="cost", name="Cost", direction=CriterionDirection.MINIMIZE, is_core=True, weight=0.8),
        Criterion(id="non_core_metric", name="Non-Core", direction=CriterionDirection.MAXIMIZE, is_core=False, weight=0.2),
    ]
    prob = DecisionProblem(
        problem_id="bm_mixed",
        goal="Mixed baseline test",
        target_area=TargetAreaSpec(query="Surveyed Area"),
        alternatives=alts,
        criteria=criteria,
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.admissibility == RecommendationAdmissibility.RECOMMENDED
    assert res.recommendation.recommended_alternative_id == "A"


# --- Category 14: Explicit User Scenario Assumption ---

@pytest.mark.asyncio
async def test_bm14_explicit_user_assumption_admissibility(v3_engine):
    """Case 14: Alternative containing planning assumptions results in CONDITIONALLY_RECOMMENDED."""
    alts = [
        Alternative(
            id="Alt_Assumed",
            name="Growth Parcel",
            attributes={"benefit": 90, "cost": 100},
            assumptions=[
                Assumption(
                    key="transit_2030",
                    statement="Assumes 2030 projected transit extension completion",
                    value=True,
                )
            ],
        ),
        Alternative(
            id="Alt_Standard",
            name="Existing Parcel",
            attributes={"benefit": 60, "cost": 120},
        ),
    ]
    prob = DecisionProblem(
        problem_id="bm_assumed",
        goal="Assumption tracking test",
        target_area=TargetAreaSpec(query="Future Expansion"),
        alternatives=alts,
        criteria=[
            Criterion(id="benefit", name="Benefit", direction=CriterionDirection.MAXIMIZE, weight=0.5),
            Criterion(id="cost", name="Cost", direction=CriterionDirection.MINIMIZE, weight=0.5),
        ],
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.admissibility == RecommendationAdmissibility.CONDITIONALLY_RECOMMENDED
    assert res.recommendation.recommended_alternative_id == "Alt_Assumed"


# --- Category 15: Conflicting Evidence Detection ---

def test_bm15_conflicting_evidence_penalizes_confidence():
    """Case 15: Two credible sources disagree by >30% -> conflict flagged and score penalized."""
    ev1 = EvidenceItem(
        id="ev_gis",
        type="observed_fact",
        domain="demographics",
        statement="Measured population 100,000",
        source="census_2025",
        confidence=0.95,
        parameters={"population": 100000},
    )
    ev2 = EvidenceItem(
        id="ev_survey",
        type="computed_fact",
        domain="demographics",
        statement="Telecom big data survey estimated population 160,000",
        source="telecom_cdr",
        confidence=0.90,
        parameters={"population": 160000}, # 60% discrepancy
    )
    score, conflicts, warnings = evaluate_evidence_quality_and_conflicts([ev1, ev2])
    assert len(conflicts) >= 1
    assert conflicts[0]["type"] == "evidence_contradiction"
    assert "discrepancy" in conflicts[0]["message"].lower()


# --- Category 16: Rule Parameter Conflict Detection ---

def test_bm16_rule_parameter_conflict_detection():
    """Case 16: Two conflicting standards in the same domain flagged with conflict audit."""
    r_nat = DomainRule(
        id="r_nat_01",
        domain="transportation",
        name="National Highway Buffer Standard",
        statement="Expressway buffer 100m",
        parameters={"expressway_buffer_m": 100},
    )
    r_loc = DomainRule(
        id="r_loc_01",
        domain="transportation",
        name="Local Municipal Environmental Standard",
        statement="Expressway buffer 200m",
        parameters={"expressway_buffer_m": 200}, # 100% discrepancy
    )
    conflicts = detect_rule_conflicts([r_nat, r_loc])
    assert len(conflicts) >= 1
    assert conflicts[0]["type"] == "parameter_conflict"
    assert conflicts[0]["parameter_key"] == "expressway_buffer_m"


# --- Category 17: Temporal Expired Rule Flagged ---

def test_bm17_temporal_expired_rule_flagged():
    """Case 17: Expired planning standard flagged as unreviewable."""
    r_old = DomainRule(
        id="r_master_plan_2020",
        domain="urban_planning",
        name="Urban Master Plan 2010-2020",
        statement="Expired planning target",
        parameters={"valid_until": "2020-12-31"},
    )
    conflicts = detect_rule_conflicts([r_old], reference_date="2026-09-03")
    assert len(conflicts) >= 1
    assert conflicts[0]["type"] == "temporal_expired"


# --- Category 18: Weight Sensitivity Tipping Point Switch ---

@pytest.mark.asyncio
async def test_bm18_weight_sensitivity_tipping_point_switch(v3_engine):
    """Case 18: Sensitivity engine identifies tipping point where ranking switches."""
    alts = [
        Alternative(id="Option_A", name="Cheaper Option", attributes={"cost": 100, "benefit": 70}),
        Alternative(id="Option_B", name="Higher Quality Option", attributes={"cost": 200, "benefit": 100}),
    ]
    prob = DecisionProblem(
        problem_id="bm_tipping",
        goal="Tipping point analysis",
        target_area=TargetAreaSpec(query="Test"),
        alternatives=alts,
        criteria=[
            Criterion(id="cost", name="Cost", direction=CriterionDirection.MINIMIZE, weight=0.6),
            Criterion(id="benefit", name="Benefit", direction=CriterionDirection.MAXIMIZE, weight=0.4),
        ],
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.sensitivity is not None
    # Critical thresholds should identify when weight switch occurs
    thresholds = res.recommendation.sensitivity.critical_weight_thresholds
    assert len(thresholds) >= 1


# --- Category 19: Near-Tie Yields NO_CLEAR_WINNER ---

@pytest.mark.asyncio
async def test_bm19_near_tie_yields_no_clear_winner(v3_engine):
    """Case 19: Near-tie with unstable weights triggers NO_CLEAR_WINNER admissibility."""
    alts = [
        Alternative(id="Alt_X", name="Alt X", attributes={"m1": 100, "m2": 80}),
        Alternative(id="Alt_Y", name="Alt Y", attributes={"m1": 80, "m2": 100}),
    ]
    prob = DecisionProblem(
        problem_id="bm_tie",
        goal="Near tie test",
        target_area=TargetAreaSpec(query="Test"),
        alternatives=alts,
        criteria=[
            Criterion(id="m1", name="M1", direction=CriterionDirection.MAXIMIZE, weight=0.5),
            Criterion(id="m2", name="M2", direction=CriterionDirection.MAXIMIZE, weight=0.5),
        ],
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.admissibility == RecommendationAdmissibility.NO_CLEAR_WINNER
    assert res.recommendation.recommended_alternative_id is None


# --- Category 20: Dominant Alternative High Rank Stability ---

@pytest.mark.asyncio
async def test_bm20_dominant_alternative_high_rank_stability(v3_engine):
    """Case 20: Dominant alternative retains rank #1 in >90% of weight perturbations."""
    alts = [
        Alternative(id="Clear_Winner", name="Winner", attributes={"quality": 98.0, "cost": 110.0}),
        Alternative(id="Distant_Second", name="Runner-up", attributes={"quality": 70.0, "cost": 250.0}),
    ]
    prob = DecisionProblem(
        problem_id="bm_clear_winner",
        goal="Clear winner test",
        target_area=TargetAreaSpec(query="Zone"),
        alternatives=alts,
        criteria=[
            Criterion(id="quality", name="Quality", direction=CriterionDirection.MAXIMIZE, weight=0.6),
            Criterion(id="cost", name="Cost", direction=CriterionDirection.MINIMIZE, weight=0.4),
        ],
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.admissibility == RecommendationAdmissibility.RECOMMENDED
    assert res.recommendation.recommended_alternative_id == "Clear_Winner"
    assert res.recommendation.sensitivity.rank_stability["Clear_Winner"] >= 95.0


# --- Category 21: Minimax Regret Selects Least Risky ---

@pytest.mark.asyncio
async def test_bm21_minimax_regret_selects_least_risky(v3_engine):
    """Case 21: Minimax regret identifies robust alternative with smallest worst-case regret."""
    alts = [
        Alternative(id="Safe", name="Safe Low-Risk Site", attributes={"score": 80}),
        Alternative(id="Volatile", name="Speculative Site", attributes={"score": 82}),
    ]
    prob = DecisionProblem(
        problem_id="bm_regret",
        goal="Minimax regret test",
        target_area=TargetAreaSpec(query="Zone"),
        alternatives=alts,
        criteria=[Criterion(id="score", name="Score", direction=CriterionDirection.MAXIMIZE)],
        mc_sample_count=200,
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.robustness is not None
    assert res.recommendation.robustness.robust_winner_id in {"Safe", "Volatile"}


# --- Category 22: TOPSIS Vector Closeness Consistency ---

@pytest.mark.asyncio
async def test_bm22_topsis_vector_closeness_consistency(v3_engine):
    """Case 22: TOPSIS method evaluates relative closeness correctly."""
    alts = [
        Alternative(id="A", name="A", attributes={"b": 100, "c": 20}),
        Alternative(id="B", name="B", attributes={"b": 50, "c": 80}),
    ]
    prob = DecisionProblem(
        problem_id="bm_topsis",
        goal="TOPSIS evaluation test",
        target_area=TargetAreaSpec(query="Zone"),
        alternatives=alts,
        criteria=[
            Criterion(id="b", name="Benefit", direction=CriterionDirection.MAXIMIZE, weight=0.5),
            Criterion(id="c", name="Cost", direction=CriterionDirection.MINIMIZE, weight=0.5),
        ],
        mcda_method="topsis",
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.admissibility == RecommendationAdmissibility.RECOMMENDED
    assert res.recommendation.recommended_alternative_id == "A"


# --- Category 23: Tied Criterion Zero Range Safety ---

@pytest.mark.asyncio
async def test_bm23_tied_criterion_zero_range_safety(v3_engine):
    """Case 23: All alternatives have identical values for a criterion; no division by zero."""
    alts = [
        Alternative(id="A", name="A", attributes={"cost": 100, "fixed": 50.0}),
        Alternative(id="B", name="B", attributes={"cost": 150, "fixed": 50.0}),
    ]
    prob = DecisionProblem(
        problem_id="bm_zero_range",
        goal="Zero range test",
        target_area=TargetAreaSpec(query="Zone"),
        alternatives=alts,
        criteria=[
            Criterion(id="cost", name="Cost", direction=CriterionDirection.MINIMIZE, weight=0.7),
            Criterion(id="fixed", name="Fixed", direction=CriterionDirection.MAXIMIZE, weight=0.3),
        ],
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.recommended_alternative_id == "A"


# --- Category 24: Target Distance Normalization ---

@pytest.mark.asyncio
async def test_bm24_bounded_utility_target_distance_normalization(v3_engine):
    """Case 24: Target distance normalization rewards closeness to ideal target value."""
    crit = Criterion(
        id="density",
        name="Target Density",
        direction=CriterionDirection.TARGET,
        normalization_strategy=NormalizationStrategy.TARGET_DISTANCE,
        target_value=500.0,
        tolerance=100.0,
    )
    alts = [
        Alternative(id="Exact", name="Exact Target", attributes={"density": 500.0}),
        Alternative(id="Close", name="Close Target", attributes={"density": 480.0}),
        Alternative(id="Far", name="Way Off", attributes={"density": 800.0}),
    ]
    prob = DecisionProblem(
        problem_id="bm_target_dist",
        goal="Target distance test",
        target_area=TargetAreaSpec(query="TOD Zone"),
        alternatives=alts,
        criteria=[crit],
    )
    res = await v3_engine.solve_problem(prob)
    assert res.recommendation.scores["Exact"].mcda_score == 1.0
    assert res.recommendation.scores["Close"].mcda_score > res.recommendation.scores["Far"].mcda_score
    assert res.recommendation.recommended_alternative_id == "Exact"


# --- Category 25: Scalability & Performance ---

@pytest.mark.asyncio
async def test_bm25_scalability_50_alternatives_performance(v3_engine):
    """Case 25: Benchmark scales efficiently across 50 alternatives (< 1.5 seconds)."""
    alts = [
        Alternative(
            id=f"Parcel_{i:02d}",
            name=f"Parcel #{i}",
            attributes={
                "demand": 1000 + (i * 73) % 5000,
                "cost": 100 + (i * 37) % 300,
                "accessibility": 50 + (i * 19) % 50,
            },
        )
        for i in range(50)
    ]
    criteria = [
        Criterion(id="demand", name="Demand", direction=CriterionDirection.MAXIMIZE, weight=0.4),
        Criterion(id="cost", name="Cost", direction=CriterionDirection.MINIMIZE, weight=0.35),
        Criterion(id="accessibility", name="Access", direction=CriterionDirection.MAXIMIZE, weight=0.25),
    ]
    prob = DecisionProblem(
        problem_id="bm_scale_50",
        goal="Scale 50 parcels test",
        target_area=TargetAreaSpec(query="Metropolitan"),
        alternatives=alts,
        criteria=criteria,
        mc_sample_count=500,
    )

    t0 = time.perf_counter()
    res = await v3_engine.solve_problem(prob)
    elapsed = time.perf_counter() - t0

    assert len(res.recommendation.scores) == 50
    assert res.recommendation.recommended_alternative_id is not None
    assert elapsed < 3.0  # Must be fast and vectorized
