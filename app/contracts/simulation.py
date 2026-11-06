"""Wire records for spatial scenario comparison and temporal map delivery."""
from __future__ import annotations

from typing import Literal, TypedDict

from app.contracts.geojson import GeoJSONFeature


class FeatureChange(TypedDict):
    before: GeoJSONFeature
    after: GeoJSONFeature


class GeometrySummary(TypedDict):
    added: int
    removed: int
    modified: int
    unchanged: int
    added_area_m2: float
    removed_area_m2: float
    added_length_m: float
    removed_length_m: float


class CoverageDiff(TypedDict):
    gained_area_m2: float
    lost_area_m2: float
    baseline_coverage_population: float | None
    branch_coverage_population: float | None
    gained_population: float | None
    lost_population: float | None


class TemporalSource(TypedDict):
    type: Literal["geojson"]
    dataPath: str | None


class TemporalLayer(TypedDict):
    id: str
    source: str
    type: Literal["fill", "line"]
    visible: bool


class VisibilityOverride(TypedDict):
    visible: bool


class TemporalFrame(TypedDict):
    id: str
    title: str
    layerOverrides: dict[str, VisibilityOverride]


class TemporalLayout(TypedDict):
    frames: list[TemporalFrame]


class TemporalMapSpec(TypedDict):
    version: Literal["1.2"]
    sources: dict[str, TemporalSource]
    layers: list[TemporalLayer]
    layout: TemporalLayout
