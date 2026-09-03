"""
Spatial Decision Intelligence V3 Domain Models & Value Objects.
Defines explicit DecisionProblem, Alternative, Criterion, Constraint, Normalization,
MCDA, Uncertainty, Sensitivity, Robustness, and RecommendationAdmissibility schemas.
"""
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from app.services.spatial_decision.models import (
    TargetAreaSpec,
    EvidenceItem,
    MetricDeltaV2,
    DomainRule,
    SpatialImpactZone,
)


class BaselineTruthState(str, Enum):
    """Factual status of baseline data."""
    OBSERVED = "observed"      # Empirically measured facts from real GIS/POI/spatial data
    DERIVED = "derived"        # Computed deterministically from observed facts via model
    ASSUMED = "assumed"        # Explicitly declared scenario hypothesis (never marked observed)
    MISSING = "missing"        # Missing evidence (fail-closed, no synthetic fabrication)


class CriterionDirection(str, Enum):
    """Optimization direction for a criterion."""
    MAXIMIZE = "maximize"      # Benefit criterion (higher is better)
    MINIMIZE = "minimize"      # Cost criterion (lower is better)
    TARGET = "target"          # Distance to ideal target value
    RANGE = "range"            # Acceptable numeric interval
    UNKNOWN = "unknown"        # Direction unstated, requires explicit metadata


class NormalizationStrategy(str, Enum):
    """Strategy for dimensionless criterion normalization."""
    MIN_MAX_BENEFIT = "min_max_benefit"
    MIN_MAX_COST = "min_max_cost"
    TARGET_DISTANCE = "target_distance"
    BOUNDED_UTILITY = "bounded_utility"
    VECTOR_NORM = "vector_norm"


class WeightSource(str, Enum):
    """Provenance source of criterion weight."""
    USER_DECLARED = "user_declared"
    POLICY_DEFINED = "policy_defined"
    RULE_PACK = "rule_pack"
    EQUAL_DEFAULT = "equal_default"


class MissingPolicy(str, Enum):
    """Policy when an alternative lacks data for a criterion."""
    FAIL_CLOSED = "fail_closed"           # Mark result as INSUFFICIENT_EVIDENCE
    PENALIZE = "penalize"                 # Assign worst possible normalized score (0.0)
    IGNORE = "ignore"                     # Exclude criterion from this alternative
    REQUIRE_ASSUMPTION = "require_assumption"  # Only allow if explicitly assumed


class ConstraintType(str, Enum):
    """Enforcement severity of constraint."""
    HARD = "hard"              # Violation disqualifies alternative (feasible = False)
    SOFT = "soft"              # Violation applies penalty and warning (feasible = True)


class ConstraintCategory(str, Enum):
    """Domain category of constraint."""
    NUMERIC = "numeric"        # Value bounds (<=, >=, in_range)
    SPATIAL = "spatial"        # Geometry-based topological/distance constraints
    CATEGORICAL = "categorical"# Set membership (in, not_in)
    LOGICAL = "logical"        # Boolean/composite conditions


class SpatialPredicate(str, Enum):
    """Spatial relationship predicate for spatial constraints."""
    WITHIN = "within"                    # Must be completely inside target boundary
    OUTSIDE = "outside"                  # Must be completely outside exclusion zone (e.g. protected area)
    INTERSECTS = "intersects"            # Must touch or overlap
    DISJOINT = "disjoint"                # Must not touch or overlap
    MIN_DISTANCE = "min_distance"        # Distance to reference features must be >= threshold
    MAX_DISTANCE = "max_distance"        # Distance to reference features must be <= threshold
    BUFFER_EXCLUSION = "buffer_exclusion"# Must not intersect buffer of reference features
    SERVICE_COVERAGE = "service_coverage"# Population or demand covered within catchment >= threshold
    OVERLAP_RATIO = "overlap_ratio"      # Overlap area ratio with existing services <= threshold


class DistributionType(str, Enum):
    """Probability distribution type for uncertainty propagation."""
    FIXED = "fixed"
    INTERVAL = "interval"      # Uniform U(min, max)
    TRIANGULAR = "triangular"  # Triangular(min, mode, max)
    NORMAL = "normal"          # Truncated Normal(mean, std, min, max)
    EMPIRICAL = "empirical"    # Discrete empirical samples


class ParetoStatus(str, Enum):
    """Multi-objective Pareto optimality status."""
    NON_DOMINATED = "non_dominated"  # On Pareto frontier (not dominated by any alternative)
    DOMINATED = "dominated"          # Strictly worse in at least one, no better in any


class RecommendationAdmissibility(str, Enum):
    """Admissibility state for decision recommendation."""
    RECOMMENDED = "recommended"                                 # Clear robust winner
    CONDITIONALLY_RECOMMENDED = "conditionally_recommended"     # Winner has soft violations or key assumptions
    NO_CLEAR_WINNER = "no_clear_winner"                         # Ranking flips under minor weight shifts
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"             # Essential criteria lack empirical baseline
    NO_FEASIBLE_ALTERNATIVE = "no_feasible_alternative"         # All alternatives fail hard constraints


# --- Domain Core Structures ---

class Criterion(BaseModel):
    """Explicit decision criterion."""
    id: str = Field(..., description="Unique criterion ID (e.g., 'accessibility', 'cost')")
    name: str = Field(..., description="Human-readable criterion name")
    unit: str = Field(default="", description="Physical measurement unit (e.g., 'min', 'RMB', 'people')")
    direction: CriterionDirection = Field(
        default=CriterionDirection.MAXIMIZE,
        description="Optimization direction: maximize, minimize, target, range, or unknown",
    )
    weight: float = Field(default=1.0, ge=0.0, description="Raw preference weight")
    weight_source: WeightSource = Field(default=WeightSource.EQUAL_DEFAULT, description="Weight origin")
    weight_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence in weight specification")
    normalization_strategy: NormalizationStrategy = Field(
        default=NormalizationStrategy.MIN_MAX_BENEFIT,
        description="Normalization function strategy",
    )
    target_value: Optional[float] = Field(default=None, description="Ideal target value for TARGET direction")
    range_bounds: Optional[Tuple[float, float]] = Field(default=None, description="(min, max) for RANGE direction")
    missing_policy: MissingPolicy = Field(
        default=MissingPolicy.FAIL_CLOSED,
        description="Policy when metric data is absent",
    )
    is_core: bool = Field(
        default=True,
        description="If True, absence of baseline for this criterion blocks recommendation",
    )
    description: Optional[str] = Field(default=None, description="Criterion explanation or standard reference")


class Constraint(BaseModel):
    """Explicit decision constraint."""
    id: str = Field(..., description="Unique constraint ID")
    name: str = Field(..., description="Constraint display name")
    constraint_type: ConstraintType = Field(default=ConstraintType.HARD, description="Hard (veto) or Soft (penalty)")
    category: ConstraintCategory = Field(default=ConstraintCategory.NUMERIC, description="Constraint domain category")
    spatial_predicate: Optional[SpatialPredicate] = Field(default=None, description="Spatial relation if spatial")
    metric_key: Optional[str] = Field(default=None, description="Target metric key if numeric/categorical")
    operator: Optional[str] = Field(default=None, description="Operator: '<=', '>=', '<', '>', '==', '!=', 'in', 'outside'")
    threshold: Any = Field(default=None, description="Threshold value or boundary geometry")
    reference_geometry: Optional[Dict[str, Any]] = Field(
        default=None, description="GeoJSON geometry or FeatureCollection for spatial tests"
    )
    buffer_distance_m: Optional[float] = Field(default=None, ge=0.0, description="Buffer distance in meters")
    penalty_weight: float = Field(default=1.0, ge=0.0, description="Penalty multiplier for soft constraint violation")
    description: str = Field(default="", description="Detailed constraint requirement")


class ConstraintEvaluation(BaseModel):
    """Result of evaluating one constraint on one alternative."""
    constraint_id: str = Field(..., description="Constraint evaluated")
    alternative_id: str = Field(..., description="Alternative evaluated")
    passed: bool = Field(..., description="True if constraint is satisfied, False if violated")
    observed_value: Any = Field(default=None, description="Observed or derived quantity")
    threshold: Any = Field(default=None, description="Constraint threshold")
    margin: Optional[float] = Field(default=None, description="Distance to threshold boundary (>0 means safe)")
    penalty: float = Field(default=0.0, ge=0.0, description="Penalty deducted from soft violation")
    evidence_statement: str = Field(default="", description="Factual explanation of pass/fail outcome")


class Assumption(BaseModel):
    """Explicitly declared scenario assumption."""
    key: str = Field(..., description="Assumption identifier")
    statement: str = Field(..., description="Human-readable statement of assumed condition")
    value: Any = Field(..., description="Assumed value or parameter")
    source: str = Field(default="user_declared", description="Origin: 'user_declared', 'planning_hypothesis'")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0, description="Belief confidence")
    range_or_distribution: Optional[Dict[str, Any]] = Field(default=None, description="Stochastic uncertainty spec")


class Alternative(BaseModel):
    """Candidate spatial alternative or scenario option."""
    id: str = Field(..., description="Stable unique identifier for alternative (e.g., 'site_a', 'route_1')")
    name: str = Field(..., description="Display name")
    description: str = Field(default="", description="Alternative description")
    geometry: Optional[Dict[str, Any]] = Field(default=None, description="GeoJSON Point, Polygon, or LineString")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Pre-computed attributes (cost, area, etc.)")
    assumptions: List[Assumption] = Field(default_factory=list, description="Explicit assumptions for this alternative")


class BaselineEvidenceContext(BaseModel):
    """Baseline evidence status and backing data."""
    status: BaselineTruthState = Field(default=BaselineTruthState.OBSERVED, description="Truth status of baseline")
    metrics: Dict[str, MetricDeltaV2] = Field(default_factory=dict, description="Observed/derived baseline metrics")
    evidence_items: List[EvidenceItem] = Field(default_factory=list, description="Underlying evidence items")
    source_refs: List[str] = Field(default_factory=list, description="SessionStore or catalog dataset references")
    derivation_notes: Optional[str] = Field(default=None, description="Derivation description if status is DERIVED")


class UncertainParameter(BaseModel):
    """Stochastic parameter with probability distribution."""
    param_id: str = Field(..., description="Parameter identifier")
    name: str = Field(..., description="Parameter name")
    distribution: DistributionType = Field(default=DistributionType.INTERVAL, description="Distribution type")
    params: Dict[str, float] = Field(
        default_factory=dict,
        description="Distribution parameters (e.g., {'min': 10, 'mode': 15, 'max': 25} or {'mean': 50, 'std': 5})",
    )


class OutcomeDistribution(BaseModel):
    """Quantile distribution of simulated outcome under uncertainty."""
    metric_key: str = Field(..., description="Metric key")
    mean: float = Field(..., description="Expected mean")
    median: float = Field(..., description="Median p50")
    std: float = Field(..., description="Standard deviation")
    p05: float = Field(..., description="5th percentile (conservative lower bound)")
    p25: float = Field(..., description="25th percentile")
    p75: float = Field(..., description="75th percentile")
    p95: float = Field(..., description="95th percentile (optimistic/worst upper bound)")
    prob_constraint_met: Optional[float] = Field(default=None, description="Probability of satisfying constraint")


class DecisionScore(BaseModel):
    """Detailed score and evaluation for a single alternative."""
    alternative_id: str = Field(..., description="Alternative evaluated")
    feasible: bool = Field(..., description="False if ANY hard constraint is violated")
    hard_violations: List[ConstraintEvaluation] = Field(default_factory=list, description="Hard violations causing infeasibility")
    soft_violations: List[ConstraintEvaluation] = Field(default_factory=list, description="Soft violations with penalties")
    raw_metrics: Dict[str, Optional[float]] = Field(default_factory=dict, description="Raw metric values")
    normalized_scores: Dict[str, float] = Field(default_factory=dict, description="Normalized criterion scores in [0.0, 1.0]")
    mcda_score: float = Field(default=0.0, description="Overall MCDA composite score (WSM or TOPSIS closeness)")
    mcda_method: str = Field(default="wsm", description="MCDA algorithm used ('wsm', 'topsis')")
    rank: int = Field(default=0, description="Rank among feasible alternatives (1 = best; 0 = infeasible)")
    pareto_status: ParetoStatus = Field(default=ParetoStatus.NON_DOMINATED, description="Pareto non-dominance status")
    dominates: List[str] = Field(default_factory=list, description="IDs of alternatives dominated by this one")
    dominated_by: List[str] = Field(default_factory=list, description="IDs of alternatives that dominate this one")
    outcome_distributions: Dict[str, OutcomeDistribution] = Field(default_factory=dict, description="Uncertainty distributions")


class SensitivityResult(BaseModel):
    """Results from criterion weight and parameter perturbation."""
    rank_stability: Dict[str, float] = Field(
        default_factory=dict,
        description="Alternative ID -> percentage of perturbations where alternative retains rank #1",
    )
    top_rank_probabilities: Dict[str, float] = Field(
        default_factory=dict,
        description="Alternative ID -> probability of being top-ranked across weight simplex",
    )
    critical_weight_thresholds: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Identified weight tipping points where rank order switches",
    )
    tradeoff_drivers: List[str] = Field(
        default_factory=list,
        description="Pairs of criteria driving ranking instability",
    )
    summary: str = Field(default="", description="Human-readable sensitivity summary")


class RobustnessResult(BaseModel):
    """Multi-dimensional decision robustness analysis."""
    alternative_regrets: Dict[str, float] = Field(
        default_factory=dict,
        description="Alternative ID -> Minimax Regret score (lower is better)",
    )
    prob_top_ranked: Dict[str, float] = Field(
        default_factory=dict,
        description="Alternative ID -> Empirical probability of being best across uncertain parameters",
    )
    prob_feasible: Dict[str, float] = Field(
        default_factory=dict,
        description="Alternative ID -> Probability of satisfying all hard constraints under uncertainty",
    )
    worst_case_normalized: Dict[str, float] = Field(
        default_factory=dict,
        description="Alternative ID -> Worst-case normalized performance across scenarios",
    )
    robust_winner_id: Optional[str] = Field(default=None, description="Alternative that maximizes robustness")
    summary: str = Field(default="", description="Robustness analysis summary")


class StructuredExplanation(BaseModel):
    """Rigorous, transparent decision explanation object."""
    why_selected: List[str] = Field(default_factory=list, description="Key drivers for selecting winner")
    why_not_selected: Dict[str, List[str]] = Field(default_factory=dict, description="Alternative ID -> disqualification/loss reasons")
    binding_constraints: List[str] = Field(default_factory=list, description="Constraints that eliminated alternatives")
    criterion_contributions: Dict[str, Dict[str, float]] = Field(
        default_factory=dict, description="Alternative ID -> {criterion_id: weighted_contribution}"
    )
    major_tradeoffs: List[str] = Field(default_factory=list, description="Explicit trade-offs between leading candidates")
    sensitivity_summary: str = Field(default="", description="Weight sensitivity evaluation")
    uncertainty_summary: str = Field(default="", description="Stochastic uncertainty notes")
    evidence_gaps: List[str] = Field(default_factory=list, description="Unresolved baseline or factual data gaps")


class DecisionProblem(BaseModel):
    """
    Formal Spatial Decision Problem specification.
    Binds goal, target area, candidate alternatives, criteria, constraints, baseline, and preferences.
    """
    problem_id: str = Field(..., description="Unique problem run ID")
    goal: str = Field(..., description="High-level decision objective (e.g. 'Hospital Site Selection')")
    target_area: TargetAreaSpec = Field(..., description="Geographic scope")
    alternatives: List[Alternative] = Field(..., min_length=1, description="Candidate alternatives to evaluate")
    criteria: List[Criterion] = Field(..., min_length=1, description="Decision criteria")
    constraints: List[Constraint] = Field(default_factory=list, description="Hard & Soft constraints")
    baseline_context: BaselineEvidenceContext = Field(default_factory=BaselineEvidenceContext, description="Baseline context")
    uncertain_parameters: List[UncertainParameter] = Field(default_factory=list, description="Stochastic parameters")
    mcda_method: str = Field(default="wsm", description="Primary MCDA algorithm: 'wsm' or 'topsis'")
    decision_horizon: Optional[str] = Field(default=None, description="Planning horizon (e.g. '2026-2035')")
    random_seed: int = Field(default=42, description="Seed for deterministic reproducibility")
    mc_sample_count: int = Field(default=1000, ge=10, le=10000, description="Monte Carlo sample iterations")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional context tags")


class RecommendationResult(BaseModel):
    """
    Complete recommendation outcome with admissibility, explanation, and provenance.
    """
    admissibility: RecommendationAdmissibility = Field(..., description="Admissibility status")
    recommended_alternative_id: Optional[str] = Field(default=None, description="ID of recommended alternative")
    explanation: StructuredExplanation = Field(..., description="Structured audit-ready explanation")
    scores: Dict[str, DecisionScore] = Field(default_factory=dict, description="Alternative ID -> DecisionScore")
    normalized_matrix: Dict[str, Dict[str, float]] = Field(
        default_factory=dict, description="Criterion ID -> {Alternative ID: normalized_score}"
    )
    raw_matrix: Dict[str, Dict[str, Optional[float]]] = Field(
        default_factory=dict, description="Criterion ID -> {Alternative ID: raw_value}"
    )
    sensitivity: Optional[SensitivityResult] = Field(default=None, description="Sensitivity analysis result")
    robustness: Optional[RobustnessResult] = Field(default=None, description="Robustness analysis result")
    pareto_frontier: List[str] = Field(default_factory=list, description="IDs of non-dominated feasible alternatives")
    provenance: Dict[str, Any] = Field(default_factory=dict, description="Decision provenance metadata")
    decision_fingerprint: str = Field(default="", description="Deterministic SHA256 run fingerprint")


class SpatialDecisionResultV3(BaseModel):
    """
    Unified result object combining DecisionProblem, RecommendationResult, GeoJSON layers, and MapSpec hooks.
    """
    type: str = Field(default="spatial_decision_result_v3", description="Schema type tag")
    problem: DecisionProblem = Field(..., description="Decision problem specification")
    recommendation: RecommendationResult = Field(..., description="Evaluated recommendation result")
    comparison_geojson: Dict[str, Any] = Field(default_factory=dict, description="GeoJSON of alternatives & constraints")
    comparison_ref_id: str = Field(default="", description="SessionStore cursor ref for GeoJSON")
    report_markdown: str = Field(default="", description="Formatted decision report markdown")
    mapspec_applied: bool = Field(default=False, description="Whether layers were applied to MapSpec")
    cartographic_review: Optional[Dict[str, Any]] = Field(default=None, description="Cartographic review status")
    provenance: Dict[str, Any] = Field(default_factory=dict, description="Full lineage metadata")
