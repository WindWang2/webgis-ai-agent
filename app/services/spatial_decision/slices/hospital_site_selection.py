"""
Vertical Slice #1: Hospital Site Selection for Spatial Decision Intelligence V3.
Demonstrates end-to-end evidence grounding, spatial exclusion constraint,
MCDA evaluation, sensitivity analysis, and robust decision report.
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


def create_hospital_site_selection_problem() -> DecisionProblem:
    """
    Constructs a realistic Hospital Site Selection decision problem.

    Candidate Sites:
      - Site_A: North District (High population demand, moderate cost, outside protected land)
      - Site_B: South Industrial Buffer (Low cost, moderate accessibility, outside protected land)
      - Site_C: West Mountain Foothills (Excellent road access, high capacity, BUT inside Protected Nature Reserve!)
    """
    target_area = TargetAreaSpec(
        query="Haidian District Healthcare Planning Zone",
        resolved_name="Haidian Healthcare Development Zone",
        source="planning_atlas",
        center=(116.28, 39.95),
        bbox=[116.20, 39.90, 116.36, 40.02],
        confidence=0.98,
    )

    # Protected Ecological Wetland Reserve GeoJSON polygon (West side)
    protected_reserve_geojson = {
        "type": "Polygon",
        "coordinates": [[
            [116.20, 39.96],
            [116.24, 39.96],
            [116.24, 40.01],
            [116.20, 40.01],
            [116.20, 39.96],
        ]],
    }

    # Existing District Hospital (Site for spacing check: 116.32, 39.93)
    existing_hospital_geojson = {
        "type": "Point",
        "coordinates": [116.32, 39.93],
    }

    # Candidate Alternatives
    site_a = Alternative(
        id="Site_A",
        name="North District Community Health Parcel",
        description="Near dense residential cluster, excellent public transit access.",
        geometry={"type": "Point", "coordinates": [116.29, 39.98]},
        attributes={
            "covered_population": 85000,    # people in 15-min catchment
            "travel_time_min": 14.5,        # average emergency response time
            "construction_cost_m": 380.0,   # Million RMB
            "environmental_quality": 82.0,  # Green index
            "slope_deg": 3.2,
        },
    )

    site_b = Alternative(
        id="Site_B",
        name="South Suburb Gateway Parcel",
        description="Lower land acquisition cost, emerging development area.",
        geometry={"type": "Point", "coordinates": [116.30, 39.91]},
        attributes={
            "covered_population": 62000,
            "travel_time_min": 18.0,
            "construction_cost_m": 290.0,
            "environmental_quality": 74.0,
            "slope_deg": 2.1,
        },
    )

    site_c = Alternative(
        id="Site_C",
        name="West Foothill Scenic Parcel",
        description="High potential capacity, but located inside protected wetland reserve.",
        # Coordinates (116.22, 39.98) strictly INSIDE protected_reserve_geojson!
        geometry={"type": "Point", "coordinates": [116.22, 39.98]},
        attributes={
            "covered_population": 92000,
            "travel_time_min": 11.2,
            "construction_cost_m": 420.0,
            "environmental_quality": 95.0,
            "slope_deg": 5.0,
        },
    )

    # Criteria
    criteria = [
        Criterion(
            id="covered_population",
            name="Covered Population Demand (服务覆盖人口)",
            unit="people",
            direction=CriterionDirection.MAXIMIZE,
            weight=0.35,
            weight_source=WeightSource.USER_DECLARED,
            normalization_strategy=NormalizationStrategy.MIN_MAX_BENEFIT,
            is_core=True,
        ),
        Criterion(
            id="travel_time_min",
            name="Emergency Travel Time (急救车达时间)",
            unit="min",
            direction=CriterionDirection.MINIMIZE,
            weight=0.30,
            weight_source=WeightSource.USER_DECLARED,
            normalization_strategy=NormalizationStrategy.MIN_MAX_COST,
            is_core=True,
        ),
        Criterion(
            id="construction_cost_m",
            name="Total Construction Cost (建设投资成本)",
            unit="Million RMB",
            direction=CriterionDirection.MINIMIZE,
            weight=0.20,
            weight_source=WeightSource.USER_DECLARED,
            normalization_strategy=NormalizationStrategy.MIN_MAX_COST,
            is_core=True,
        ),
        Criterion(
            id="environmental_quality",
            name="Environmental Quality Index (生态环境品质)",
            unit="pts",
            direction=CriterionDirection.MAXIMIZE,
            weight=0.15,
            weight_source=WeightSource.USER_DECLARED,
            normalization_strategy=NormalizationStrategy.MIN_MAX_BENEFIT,
            is_core=False,
        ),
    ]

    # Constraints
    constraints = [
        # 1. Hard Spatial Exclusion: Outside Protected Wetland Reserve
        Constraint(
            id="c_outside_protected_reserve",
            name="Outside Ecological Protection Redline (生态保护红线避让)",
            constraint_type=ConstraintType.HARD,
            category=ConstraintCategory.SPATIAL,
            spatial_predicate=SpatialPredicate.OUTSIDE,
            reference_geometry=protected_reserve_geojson,
            description="Candidate medical facility must not encroach upon ecological protection zones.",
        ),
        # 2. Hard Numeric Constraint: Capital Budget Ceiling <= 500 Million RMB
        Constraint(
            id="c_budget_limit",
            name="Maximum Capital Budget Limit (预算上限约束)",
            constraint_type=ConstraintType.HARD,
            category=ConstraintCategory.NUMERIC,
            metric_key="construction_cost_m",
            operator="<=",
            threshold=500.0,
            description="Construction budget cannot exceed fiscal allocation of 500M RMB.",
        ),
        # 3. Soft Spacing Constraint: Distance to Existing Hospital >= 2000m (to avoid redundancy)
        Constraint(
            id="c_spacing_existing",
            name="Minimum Distance to Existing Hospital (与既有综合医院合理间距)",
            constraint_type=ConstraintType.SOFT,
            category=ConstraintCategory.SPATIAL,
            spatial_predicate=SpatialPredicate.MIN_DISTANCE,
            reference_geometry=existing_hospital_geojson,
            threshold=2000.0,
            penalty_weight=0.05,
            description="Prefer at least 2000m separation from existing general hospitals.",
        ),
    ]

    return DecisionProblem(
        problem_id="prob_hospital_site_v3",
        goal="Select optimal location for new district general hospital (新建区级综合医院选址评估)",
        target_area=target_area,
        alternatives=[site_a, site_b, site_c],
        criteria=criteria,
        constraints=constraints,
        baseline_context=BaselineEvidenceContext(
            status=BaselineTruthState.OBSERVED,
            source_refs=["ref:haidian_pop_2026", "ref:haidian_eco_redline"],
        ),
        mcda_method="wsm",
        random_seed=42,
        mc_sample_count=1000,
    )
