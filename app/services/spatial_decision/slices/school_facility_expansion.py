"""
Vertical Slice #2: School Facility Expansion Decision for Spatial Decision Intelligence V3.
Demonstrates generalization across educational infrastructure, walking accessibility,
noise corridor setback constraints, and budget optimization.
"""
from app.services.spatial_decision.models_v3 import (
    Alternative,
    BaselineEvidenceContext,
    BaselineTruthState,
    Constraint,
    ConstraintCategory,
    ConstraintType,
    Criterion,
    CriterionDirection,
    DecisionProblem,
    NormalizationStrategy,
    SpatialPredicate,
    TargetAreaSpec,
    WeightSource,
)


def create_school_facility_expansion_problem() -> DecisionProblem:
    """
    Constructs a School Facility Expansion decision problem.

    Candidate Sites:
      - School_A: Urban Core Infill (High walkability, high cost, safe distance from highway)
      - School_B: Eastern Expansion Zone (Balanced cost, high capacity, safe distance)
      - School_C: Expressway Corridor Parcel (Low cost, high capacity, BUT within 100m of Expressway Noise Corridor!)
    """
    target_area = TargetAreaSpec(
        query="Chaoyang East New Town Education Hub",
        resolved_name="Chaoyang East Educational Catchment",
        source="planning_atlas",
        center=(116.48, 39.92),
        bbox=[116.42, 39.88, 116.54, 39.96],
        confidence=0.97,
    )

    # Expressway Noise Exclusion Corridor (LineString buffer or corridor polygon)
    expressway_noise_corridor = {
        "type": "Polygon",
        "coordinates": [[
            [116.49, 39.88],
            [116.51, 39.88],
            [116.51, 39.96],
            [116.49, 39.96],
            [116.49, 39.88],
        ]],
    }

    # Candidate Alternatives
    school_a = Alternative(
        id="School_A",
        name="Central Neighborhood Infill Parcel",
        description="High pedestrian density, compact campus design.",
        geometry={"type": "Point", "coordinates": [116.45, 39.92]},
        attributes={
            "student_capacity": 1200,          # enrolled students
            "walkability_10min_pct": 88.0,     # % students within 10-min walk
            "land_acquisition_cost_m": 125.0,  # Million RMB
            "traffic_safety_score": 90.0,      # Safety index
        },
    )

    school_b = Alternative(
        id="School_B",
        name="Eastern Suburb Green Campus",
        description="Ample sports facilities, moderate walking distance.",
        geometry={"type": "Point", "coordinates": [116.47, 39.94]},
        attributes={
            "student_capacity": 1800,
            "walkability_10min_pct": 72.0,
            "land_acquisition_cost_m": 85.0,
            "traffic_safety_score": 82.0,
        },
    )

    school_c = Alternative(
        id="School_C",
        name="Expressway Interchange Adjacent Parcel",
        description="Lowest land price, large area, but intersects heavy arterial noise corridor.",
        # Coordinates [116.50, 39.92] strictly INSIDE expressway_noise_corridor!
        geometry={"type": "Point", "coordinates": [116.50, 39.92]},
        attributes={
            "student_capacity": 2000,
            "walkability_10min_pct": 65.0,
            "land_acquisition_cost_m": 60.0,
            "traffic_safety_score": 50.0,
        },
    )

    criteria = [
        Criterion(
            id="student_capacity",
            name="Enrolled Student Capacity (规划学位规模)",
            unit="students",
            direction=CriterionDirection.MAXIMIZE,
            weight=0.30,
            weight_source=WeightSource.USER_DECLARED,
            normalization_strategy=NormalizationStrategy.MIN_MAX_BENEFIT,
            is_core=True,
        ),
        Criterion(
            id="walkability_10min_pct",
            name="10-Minute Walking Reachability (10分钟步达覆盖率)",
            unit="%",
            direction=CriterionDirection.MAXIMIZE,
            weight=0.35,
            weight_source=WeightSource.USER_DECLARED,
            normalization_strategy=NormalizationStrategy.MIN_MAX_BENEFIT,
            is_core=True,
        ),
        Criterion(
            id="land_acquisition_cost_m",
            name="Land Acquisition Cost (土地征迁投资成本)",
            unit="Million RMB",
            direction=CriterionDirection.MINIMIZE,
            weight=0.20,
            weight_source=WeightSource.USER_DECLARED,
            normalization_strategy=NormalizationStrategy.MIN_MAX_COST,
            is_core=True,
        ),
        Criterion(
            id="traffic_safety_score",
            name="Pedestrian Traffic Safety Index (上下学校门交通安全指数)",
            unit="pts",
            direction=CriterionDirection.MAXIMIZE,
            weight=0.15,
            weight_source=WeightSource.USER_DECLARED,
            normalization_strategy=NormalizationStrategy.MIN_MAX_BENEFIT,
            is_core=False,
        ),
    ]

    constraints = [
        # Hard Spatial Constraint: Outside Heavy Noise Corridor
        Constraint(
            id="c_outside_noise_corridor",
            name="Outside Expressway Noise Zone (高速公路主干道噪声隔离避让)",
            constraint_type=ConstraintType.HARD,
            category=ConstraintCategory.SPATIAL,
            spatial_predicate=SpatialPredicate.OUTSIDE,
            reference_geometry=expressway_noise_corridor,
            description="Educational campuses must be isolated from primary expressway noise corridors.",
        ),
        # Hard Numeric Constraint: Budget Ceiling <= 140 Million RMB
        Constraint(
            id="c_school_budget_limit",
            name="School Land Budget Ceiling (学校用地预算上限)",
            constraint_type=ConstraintType.HARD,
            category=ConstraintCategory.NUMERIC,
            metric_key="land_acquisition_cost_m",
            operator="<=",
            threshold=140.0,
            description="Land acquisition budget cannot exceed 140M RMB.",
        ),
    ]

    return DecisionProblem(
        problem_id="prob_school_expansion_v3",
        goal="Select optimal site for primary school facility expansion (中小学改扩建选址多准则决策)",
        target_area=target_area,
        alternatives=[school_a, school_b, school_c],
        criteria=criteria,
        constraints=constraints,
        baseline_context=BaselineEvidenceContext(
            status=BaselineTruthState.OBSERVED,
            source_refs=["ref:chaoyang_school_age_pop", "ref:noise_corridor_survey"],
        ),
        mcda_method="wsm",
        random_seed=42,
        mc_sample_count=1000,
    )
