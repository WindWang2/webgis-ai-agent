"""Wire records for network inputs and bounded solver evidence.

The graph itself can carry arbitrary application edge attributes, so its
attribute maps remain at the NetworkX boundary. These records describe the
closed records exchanged by our solvers without changing Pydantic validation.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

import networkx as nx
from typing_extensions import TypedDict

from app.contracts.geojson import (
    GeoJSONFeature, GeoJSONFeatureCollection, GeoJSONGeometry, Position,
)
from app.services.network.models import NetworkDataset

NetworkInput: TypeAlias = (
    GeoJSONFeatureCollection | GeoJSONFeature | NetworkDataset | list[GeoJSONFeature]
)


class DemandInput(TypedDict, total=False):
    id: str | int
    demand_id: str
    weight: float
    geometry: GeoJSONGeometry
    coordinates: Position


class FacilityInput(TypedDict, total=False):
    id: str | int
    facility_id: str
    name: str
    capacity: float
    geometry: GeoJSONGeometry
    coordinates: Position


class SnapPointEvidence(TypedDict):
    distance_m: float
    confidence: float


class ShortestPathSummary(TypedDict):
    snap_evidence: dict[str, SnapPointEvidence | bool | int]
    reachable: bool


class ODSummary(TypedDict):
    pair_count: int
    unreachable_pair_count: int
    unreachable_ratio: float
    snap_evidence: dict[str, SnapPointEvidence | bool | int]


class ServiceAreaSummary(TypedDict):
    facility_count: int
    unreachable_facility_count: int
    unreachable_facility_ids: list[str]
    snap_evidence: dict[str, SnapPointEvidence | bool | int]


class AccessibilitySummary(TypedDict):
    method: str
    decay_zones: int
    catchment_radius_min: float
    demand_total: float
    supply_total: float
    demand_point_count: int
    facility_count: int


class RouteDirection(TypedDict):
    step: int
    action: str
    street_name: str
    distance_m: float
    time_s: float
    instruction: str


class ODPairInfo(TypedDict):
    path: list[str]
    cost: float
    distance_m: float
    time_s: float
    reachable: bool


class OriginCosts(TypedDict, total=False):
    dists: dict[str, float]
    paths: dict[str, list[str]]
    dist_m: dict[str, float]
    time_s: dict[str, float]


class ODComputation(TypedDict):
    origin_nodes: list[tuple[str, str]]
    dest_nodes: list[tuple[str, str]]
    graph_view: nx.DiGraph
    weight_func: Callable[..., float]
    results: dict[str, OriginCosts]


class ODPaths(TypedDict):
    origin_nodes: list[tuple[str, str]]
    origin_labels: list[str]
    dest_nodes: list[tuple[str, str]]
    dest_labels: list[str]
    graph_view: nx.DiGraph
    weight_func: Callable[..., float] | None
    pairs: dict[tuple[str, str], ODPairInfo]
