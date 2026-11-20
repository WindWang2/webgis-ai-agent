"""G21 OD flow product — public contracts (pure domain, no I/O, no stores).

Only WGS84 geographic coordinates are accepted; projected meters are never
silently consumed as lng/lat. Cost-like semantics (time / distance /
od_matrix) are rejected as flow weights.
"""
from __future__ import annotations

from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

#: Hard caps — 500k input rows, 5000 output edges / nodes (bounded product).
MAX_INPUT_ROWS = 500_000
MAX_OUTPUT_EDGES = 5_000
MAX_OUTPUT_NODES = 5_000

SUPPORTED_CRS = {"epsg:4326", "wgs84", "crs84", "ogc:crs84"}

#: Field names that signal cost semantics (time/distance/matrix), not flow.
COST_LIKE_WEIGHT_FIELDS = {
    "time", "times", "duration", "minutes", "hours", "seconds",
    "distance", "dist", "dist_km", "distance_km", "km", "kilometers",
    "meters", "metres", "cost", "travel_time", "traveltime",
    "travel_time_min", "length", "length_m", "od_matrix", "od_cost",
    "cost_matrix",
}

SCHEMA_VERSION = "od_flow_product.v1"
FAMILY_ID = "od_flow"
SUPPORTED_MODES = ["directed", "undirected-total", "net"]


class FlowProductError(ValueError):
    """Bounded, explicit domain rejection (bad CRS, cost semantics, ID
    coordinate conflicts, empty/unresolvable input, over-cap input)."""


class FlowProductConfig(BaseModel):
    """Declarative configuration for one OD flow product build."""

    od_table_ref: str = ""
    origin_id_field: str = "origin_id"
    origin_lng_field: str = "origin_lng"
    origin_lat_field: str = "origin_lat"
    destination_id_field: str = "destination_id"
    destination_lng_field: str = "destination_lng"
    destination_lat_field: str = "destination_lat"
    weight_field: str = "weight"
    weight_unit: str = ""
    value_semantics: Literal["auto", "flow", "cost"] = "auto"

    node_table_ref: Optional[str] = None
    node_id_field: str = "node_id"
    node_lng_field: str = "node_lng"
    node_lat_field: str = "node_lat"

    source_crs: str = "EPSG:4326"

    time_field: Optional[str] = None
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    timezone: str = "UTC"

    category_field: Optional[str] = None
    category_values: Optional[List[str]] = None

    dimension_fields: List[str] = Field(default_factory=list)

    direction_mode: Literal["directed", "undirected-total", "net"] = "directed"
    missing_reverse: Literal["unknown", "zero"] = "unknown"
    net_reference: Literal["node_id_asc"] = "node_id_asc"

    geometry: Literal["straight", "curve"] = "straight"
    show_arrows: bool = True
    top_n: int = 500
    min_weight: float = 0.0
    normalize: bool = True
    curvature: float = 0.2
    arrow_spacing_px: int = 40
    arrow_size_px: int = 10
    width_min_px: float = 1.0
    width_max_px: float = 8.0
    weight_transform: Literal["linear", "sqrt", "log1p"] = "linear"
    weight_domain: Optional[Tuple[float, float]] = None
    palette: Optional[List[str]] = None
    show_nodes: bool = True
    node_totals_scope: Literal["filtered", "thresholded"] = "filtered"
    product_id: Optional[str] = None

    @field_validator("top_n")
    @classmethod
    def _top_n_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("top_n 必须为正整数")
        return v

    @field_validator("min_weight")
    @classmethod
    def _min_weight_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("min_weight 不能为负")
        return v

    @field_validator("curvature")
    @classmethod
    def _curvature_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("curvature 不能为负")
        return v

    @field_validator("dimension_fields")
    @classmethod
    def _dims_no_reserved(cls, v: List[str]) -> List[str]:
        reserved = {"weight", "origin_id", "destination_id"}
        bad = [f for f in v if f in reserved]
        if bad:
            raise ValueError(f"dimension_fields 包含保留字段: {bad}")
        return v

    @model_validator(mode="after")
    def _domain_ordered(self) -> "FlowProductConfig":
        if self.weight_domain is not None:
            lo, hi = self.weight_domain
            if not (hi > lo):
                raise ValueError("weight_domain 必须满足 max > min")
        if self.width_max_px < self.width_min_px:
            raise ValueError("width_max_px 必须 >= width_min_px")
        if self.arrow_spacing_px <= 0 or self.arrow_size_px <= 0:
            raise ValueError("arrow_spacing_px / arrow_size_px 必须为正")
        if self.category_values is not None and not self.category_field:
            raise ValueError("category_values 需要 category_field")
        return self
