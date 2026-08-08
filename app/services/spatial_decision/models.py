"""
Spatial Decision Intelligence V2 Domain Models & Value Objects.
Defines unified SpatialDecisionResult, Scenario, Baseline, Evidence, Rule, Metric, and Comparison schemas.
"""
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from pydantic import BaseModel, Field


class TargetAreaSpec(BaseModel):
    """Target area resolution specification."""
    query: str = Field(..., description="Original target area string or query")
    geometry_type: Literal["Point", "Polygon", "MultiPolygon", "BBOX", "Unknown"] = Field(
        default="Unknown", description="Resolved geometry type"
    )
    center: Optional[Tuple[float, float]] = Field(
        default=None, description="Center coordinates [lng, lat]"
    )
    geometry: Optional[Dict[str, Any]] = Field(
        default=None, description="Resolved GeoJSON Geometry object"
    )
    bbox: Optional[List[float]] = Field(
        default=None, description="Bounding box [west, south, east, north]"
    )
    resolved_name: str = Field(..., description="Canonical resolved name or geocoded address")
    source: str = Field(..., description="Resolution source: geocode, session_ref, geojson, bbox")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Target resolution confidence")
    correction_hint: Optional[str] = Field(default=None, description="Correction hint if area unresolvable")


class EvidenceItem(BaseModel):
    """Evidence backing spatial reasoning or simulation delta."""
    id: str = Field(..., description="Evidence ID")
    type: Literal["observed_fact", "computed_fact", "retrieved_rule", "assumption", "inference"] = Field(
        ..., description="Evidence classification"
    )
    domain: str = Field(..., description="Domain category: urban_planning, site_selection, transportation, environment, etc.")
    statement: str = Field(..., description="Factual statement or rule text")
    source: str = Field(..., description="Evidence source, document title, or algorithm")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Reliability score 0.0-1.0")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Rule or fact parameters")


class MetricRange(BaseModel):
    """Metric value range representing spatial uncertainty."""
    min_val: float = Field(..., description="Minimum bound")
    expected_val: float = Field(..., description="Expected/mean bound")
    max_val: float = Field(..., description="Maximum bound")


class MetricDeltaV2(BaseModel):
    """Quantitative metric evaluated across baseline and simulated states."""
    metric_key: str = Field(..., description="Metric key identifier")
    metric_name: str = Field(..., description="Human-readable metric name")
    baseline: float = Field(..., description="Baseline value")
    simulated: float = Field(..., description="Simulated expected value")
    delta_abs: float = Field(..., description="Absolute change (simulated - baseline)")
    delta_pct: float = Field(..., description="Percentage change")
    range: Optional[MetricRange] = Field(default=None, description="Uncertainty range bounds")
    unit: str = Field(default="", description="Metric unit, e.g., RMB/m2, %, min")
    missing_baseline: bool = Field(default=False, description="Whether real baseline was missing")
    evidence_gap_note: Optional[str] = Field(default=None, description="Explanation of evidence gap if baseline missing")


class DomainRule(BaseModel):
    """Domain Rule specification."""
    id: str = Field(..., description="Unique rule ID")
    domain: str = Field(..., description="Domain category")
    name: str = Field(..., description="Rule name")
    statement: str = Field(..., description="Human-readable rule statement")
    applicability_conditions: Dict[str, Any] = Field(default_factory=dict, description="Rule trigger conditions")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Rule impact ranges or radii")
    source: str = Field(default="domain_rule_pack", description="Rule source")
    version: str = Field(default="v2.0", description="Rule version")
    confidence: float = Field(default=0.9, ge=0.0, le=1.0, description="Rule confidence")
    jurisdiction: Optional[str] = Field(default=None, description="Jurisdiction or valid area")


class SpatialImpactZone(BaseModel):
    """Spatial impact zone geometry and metrics."""
    zone_type: Literal["direct", "indirect", "general"] = Field(..., description="Zone classification")
    radius_m: Optional[float] = Field(default=None, description="Zone radius in meters")
    area_km2: float = Field(default=0.0, description="Zone calculated surface area in km2")
    impact_level: Literal["low", "medium", "high"] = Field(default="low", description="Zone impact intensity")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Metric deltas assigned to zone")


class ScenarioSpec(BaseModel):
    """Scenario input definition."""
    scenario_id: str = Field(..., description="Unique scenario ID")
    scenario_type: str = Field(..., description="Scenario type: subway, school, hospital, park, population_growth, traffic_restriction, custom")
    name: str = Field(..., description="Scenario display name")
    description: str = Field(..., description="Original scenario text description")
    target_area: TargetAreaSpec = Field(..., description="Resolved target area")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Scenario execution parameters")
    baseline_data_ref: str = Field(default="", description="Baseline data reference ID")


class SpatialDecisionResult(BaseModel):
    """Unified Spatial Decision Result schema."""
    type: Literal["spatial_decision_result"] = Field(default="spatial_decision_result", description="Schema type tag")
    decision_id: str = Field(..., description="Unique decision run ID")
    scenario: ScenarioSpec = Field(..., description="Scenario specification")
    target_area: TargetAreaSpec = Field(..., description="Resolved target area")
    baseline_evidence_refs: List[str] = Field(default_factory=list, description="Baseline data ref IDs used")
    assumptions: List[str] = Field(default_factory=list, description="Explicit assumptions adopted")
    evidence_chain: List[EvidenceItem] = Field(default_factory=list, description="Audit evidence chain")
    rules_applied: List[DomainRule] = Field(default_factory=list, description="Applied domain rules")
    metrics: Dict[str, MetricDeltaV2] = Field(default_factory=dict, description="Evaluated metric deltas")
    spatial_impacts: List[SpatialImpactZone] = Field(default_factory=list, description="Spatial impact zones")
    simulation_geojson: Dict[str, Any] = Field(..., description="Simulation FeatureCollection GeoJSON")
    simulation_ref_id: str = Field(..., description="Registered SessionStore cursor ref ID")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Overall decision confidence 0.0-1.0")
    uncertainty_description: str = Field(..., description="Detailed uncertainty analysis")
    recommendations: List[str] = Field(default_factory=list, description="Actionable recommendations")
    provenance: Dict[str, Any] = Field(default_factory=dict, description="Lineage metadata")


class ScenarioComparisonResult(BaseModel):
    """Multi-scenario comparison result schema."""
    type: Literal["scenario_comparison_result"] = Field(default="scenario_comparison_result", description="Schema type tag")
    comparison_id: str = Field(..., description="Unique comparison run ID")
    scenarios: List[SpatialDecisionResult] = Field(..., description="Evaluated scenario results")
    metric_matrix: Dict[str, Dict[str, float]] = Field(..., description="Metric key -> {scenario_id: simulated_value}")
    affected_area_comparison: Dict[str, float] = Field(..., description="Scenario ID -> total affected area km2")
    trade_offs: List[str] = Field(default_factory=list, description="Trade-off analysis notes")
    pareto_optimal_scenarios: List[str] = Field(default_factory=list, description="IDs of Pareto non-dominated scenarios")
    recommended_scenario_id: str = Field(..., description="ID of recommended scenario")
    recommendation_rationale: str = Field(..., description="Evidence-grounded explanation for recommendation")
    comparison_geojson: Dict[str, Any] = Field(..., description="Merged multi-scenario FeatureCollection GeoJSON")
    comparison_ref_id: str = Field(..., description="Registered SessionStore ref ID for comparison map")
