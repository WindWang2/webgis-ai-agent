"""Structural GeoJSON contracts shared by analysis and service adapters.

These are static contracts, not validators or serializers: foreign members and
legacy ``crs`` members continue to round-trip at the existing IO boundaries.
Coordinates accept both JSON lists and Shapely's tuple projection. Property
names belong to the dataset, so only that leaf surface is an open JSON map.
Use concrete geometry types when the producer guarantees a geometry kind.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypeAlias

from pydantic import JsonValue
from typing_extensions import NotRequired, TypedDict

Position: TypeAlias = Sequence[float]


class _GeometryMembers(TypedDict):
    bbox: NotRequired[Sequence[float]]


class PointGeometry(_GeometryMembers):
    type: Literal["Point"]
    coordinates: Position


class MultiPointGeometry(_GeometryMembers):
    type: Literal["MultiPoint"]
    coordinates: Sequence[Position]


class LineStringGeometry(_GeometryMembers):
    type: Literal["LineString"]
    coordinates: Sequence[Position]


class MultiLineStringGeometry(_GeometryMembers):
    type: Literal["MultiLineString"]
    coordinates: Sequence[Sequence[Position]]


class PolygonGeometry(_GeometryMembers):
    type: Literal["Polygon"]
    coordinates: Sequence[Sequence[Position]]


class MultiPolygonGeometry(_GeometryMembers):
    type: Literal["MultiPolygon"]
    coordinates: Sequence[Sequence[Sequence[Position]]]


class GeometryCollection(_GeometryMembers):
    type: Literal["GeometryCollection"]
    geometries: Sequence[GeoJSONGeometry]


GeoJSONGeometry: TypeAlias = (
    PointGeometry | MultiPointGeometry | LineStringGeometry
    | MultiLineStringGeometry | PolygonGeometry | MultiPolygonGeometry
    | GeometryCollection
)


class CRSProperties(TypedDict):
    name: str


class NamedCRS(TypedDict):
    type: Literal["name"]
    properties: CRSProperties


class GeoJSONFeature(TypedDict):
    type: Literal["Feature"]
    geometry: GeoJSONGeometry | None
    properties: dict[str, JsonValue] | None
    id: NotRequired[str | int | float]
    bbox: NotRequired[Sequence[float]]


class GeoJSONFeatureCollection(TypedDict):
    type: Literal["FeatureCollection"]
    features: list[GeoJSONFeature]
    bbox: NotRequired[Sequence[float]]
    crs: NotRequired[NamedCRS | str]
