"""
Network Analyst V2 Domain Models & Value Objects.
Defines NetworkDataset, Node, Edge, Junction, Cost, Impedance, TravelProfile,
Barrier, Facility, Demand, Route, ServiceArea, ODPair, AccessibilityResult,
and NetworkAnalysisResult.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field, ConfigDict


class Cost(BaseModel):
    """Value object representing travel cost metrics."""
    model_config = ConfigDict(extra="ignore")

    length_m: float = Field(default=0.0, description="Length in meters")
    travel_time_s: float = Field(default=0.0, description="Travel time in seconds")
    monetary_cost: float = Field(default=0.0, description="Toll / monetary cost")
    custom_cost: float = Field(default=0.0, description="User-defined custom weight")


class Impedance(BaseModel):
    """Impedance model defining edge cost calculations and penalties."""
    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="length_m", description="Primary impedance field: length_m, travel_time_s, custom")
    unit: str = Field(default="meters", description="meters, seconds, minutes, kilometers")
    turn_penalty_s: float = Field(default=0.0, description="Turn penalty in seconds")
    barrier_multiplier: float = Field(default=1.0, description="Impedance multiplier for slowed segments")
    directional_strictness: bool = Field(default=True, description="Respect directionality / one-way flags")


class TravelProfile(BaseModel):
    """Travel mode profile defining default speeds, impedance fields, and access rules."""
    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="driving", description="Mode name: walking, driving, cycling, transit, heavy_truck")
    speed_kmh: float = Field(default=40.0, description="Default speed in km/h")
    impedance_field: str = Field(default="travel_time_s", description="Field used for cost: length_m, travel_time_s, custom")
    allowed_highway_types: Optional[List[str]] = Field(default=None, description="Allowed OSM highway or road types")
    one_way_strict: bool = Field(default=True, description="Enforce one-way directions")
    turn_penalty_s: float = Field(default=0.0, description="Penalty for turns in seconds")
    max_slope_pct: Optional[float] = Field(default=None, description="Max grade percentage for walking/cycling")


class Node(BaseModel):
    """Network node location."""
    model_config = ConfigDict(extra="ignore")

    id: Union[int, str]
    x: float = Field(..., description="Longitude / Easting")
    y: float = Field(..., description="Latitude / Northing")
    elevation: Optional[float] = Field(default=None)
    properties: Dict[str, Any] = Field(default_factory=dict)


class Junction(Node):
    """Network topological junction node with signal delays or turn restrictions."""
    junction_type: str = Field(default="standard", description="standard, traffic_signals, roundabout, intersection")
    signal_delay_s: float = Field(default=0.0, description="Average delay at junction in seconds")


class Edge(BaseModel):
    """Network directed or undirected edge."""
    model_config = ConfigDict(extra="ignore")

    id: Union[int, str]
    u: Union[int, str] = Field(..., description="Start node ID")
    v: Union[int, str] = Field(..., description="End node ID")
    length_m: float = Field(..., description="Geodesic / projected length in meters")
    one_way: bool = Field(default=False, description="Is edge one-way from u to v?")
    speed_kmh: Optional[float] = Field(default=None)
    travel_time_s: Optional[float] = Field(default=None)
    highway_type: str = Field(default="unclassified")
    geometry: Optional[Dict[str, Any]] = Field(default=None, description="LineString GeoJSON geometry")
    properties: Dict[str, Any] = Field(default_factory=dict)


class NetworkDataset(BaseModel):
    """In-memory or referenced network graph dataset representation."""
    model_config = ConfigDict(extra="ignore")

    dataset_id: str = Field(..., description="Unique dataset identifier or ref_id")
    crs: str = Field(default="EPSG:4326", description="Spatial Reference System")
    node_count: int = Field(default=0)
    edge_count: int = Field(default=0)
    bounding_box: List[float] = Field(default_factory=list, description="[west, south, east, north]")
    nodes: List[Node] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)
    available_profiles: List[str] = Field(default_factory=lambda: ["walking", "cycling", "driving"])
    is_projected: bool = Field(default=False, description="Whether dataset is in projected meter CRS")


class Barrier(BaseModel):
    """Point or polygon barrier blocking or penalizing network edge traversals."""
    model_config = ConfigDict(extra="ignore")

    barrier_id: str
    barrier_type: str = Field(default="point", description="point, line, polygon")
    geometry: Dict[str, Any] = Field(..., description="GeoJSON geometry")
    impedance_factor: float = Field(default=float("inf"), description="Multiplier or inf for full block")


class PointSnappingResult(BaseModel):
    """Snapping location of a facility or demand point onto network graph."""
    model_config = ConfigDict(extra="ignore")

    original_point: Tuple[float, float] = Field(..., description="(lng, lat)")
    snapped_point: Tuple[float, float] = Field(..., description="(lng, lat)")
    nearest_node_id: Union[int, str]
    nearest_edge_id: Optional[Union[int, str]] = None
    fraction_along_edge: float = Field(default=0.0, description="Position along edge [0.0, 1.0]")
    distance_to_network_m: float = Field(..., description="Perpendicular distance to network in meters")
    confidence: float = Field(default=1.0, description="Snapping confidence score in [0.0, 1.0]")
    correction_hint: Optional[str] = Field(default=None)


class Facility(BaseModel):
    """Facility feature location."""
    model_config = ConfigDict(extra="ignore")

    facility_id: str
    name: str = ""
    geometry: Dict[str, Any] = Field(..., description="Point GeoJSON")
    capacity: float = Field(default=1.0)
    snapping: Optional[PointSnappingResult] = None


class DemandPoint(BaseModel):
    """Demand point feature location."""
    model_config = ConfigDict(extra="ignore")

    demand_id: str
    weight: float = Field(default=1.0, description="Population or demand weight")
    geometry: Dict[str, Any] = Field(..., description="Point or Polygon GeoJSON")
    snapping: Optional[PointSnappingResult] = None


# Alias Demand to DemandPoint for domain model completeness
Demand = DemandPoint


class Route(BaseModel):
    """Network shortest path route result."""
    model_config = ConfigDict(extra="ignore")

    route_id: str
    origin_id: str
    destination_id: str
    profile_name: str
    total_distance_m: float
    total_time_s: float
    total_cost: float
    geometry: Dict[str, Any] = Field(..., description="LineString GeoJSON of route")
    path_node_ids: List[Union[int, str]] = Field(default_factory=list)
    path_edge_ids: List[Union[int, str]] = Field(default_factory=list)
    directions: List[Dict[str, Any]] = Field(default_factory=list)


class ODPair(BaseModel):
    """Origin-Destination Pair cost record."""
    model_config = ConfigDict(extra="ignore")

    origin_id: str
    destination_id: str
    distance_m: float
    travel_time_s: float
    reachable: bool = True


class ServiceAreaBreak(BaseModel):
    """Service area cutoff break definition."""
    model_config = ConfigDict(extra="ignore")

    break_value: float = Field(..., description="Cutoff value e.g. 5.0, 10.0, 15.0")
    break_unit: str = Field(default="minutes", description="minutes or meters")
    geometry: Dict[str, Any] = Field(..., description="Isochrone Polygon GeoJSON")
    reachable_network_geometry: Optional[Dict[str, Any]] = Field(default=None, description="MultiLineString GeoJSON of reachable edges")
    reachable_edge_count: int = 0


class ServiceArea(BaseModel):
    """Complete service area analysis output for a facility."""
    model_config = ConfigDict(extra="ignore")

    facility_id: str
    mode: str
    breaks: List[ServiceAreaBreak] = Field(default_factory=list)
    overall_geometry: Optional[Dict[str, Any]] = Field(default=None, description="Combined Service Area MultiPolygon GeoJSON")


class AccessibilityResult(BaseModel):
    """Spatial accessibility analysis result (2SFCA, E2SFCA, Gravity)."""
    model_config = ConfigDict(extra="ignore")

    analysis_id: str
    mode: str
    cutoff_minutes: float
    total_demand: float
    served_demand: float
    unserved_demand: float
    coverage_percentage: float
    average_travel_time_min: float
    per_zone_metrics: List[Dict[str, Any]] = Field(default_factory=list)
    accessibility_layer_geojson: Dict[str, Any] = Field(default_factory=dict)


class NetworkAnalysisResult(BaseModel):
    """Unified network analysis output wrapper."""
    model_config = ConfigDict(extra="ignore")

    analysis_type: str = Field(..., description="shortest_path, od_matrix, closest_facility, service_area, accessibility, location_allocation, optimize_route")
    status: str = Field(default="success")
    summary: Dict[str, Any] = Field(default_factory=dict)
    routes: List[Route] = Field(default_factory=list)
    od_matrix: List[ODPair] = Field(default_factory=list)
    service_areas: List[ServiceArea] = Field(default_factory=list)
    service_area_breaks: List[ServiceAreaBreak] = Field(default_factory=list)
    accessibility: Optional[AccessibilityResult] = None
    allocated_facilities: List[Dict[str, Any]] = Field(default_factory=list)
    result_geojson: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

