"""
Quantitative 3D Extrusion Cartographic Model for WebGIS AI Agent.
Implements the height visual channel, mathematical normalization,
MapLibre expression generation, outlier protection, and camera recommendations.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Literal, Optional, Union

import numpy as np
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

HeightTransformType = Literal["linear", "sqrt", "log1p", "uniform"]


class ExtrusionHeightSpec(BaseModel):
    """Specification for the 3D extrusion height visual channel."""
    height_field: str = Field(..., description="Numeric attribute field used for extrusion height")
    height_unit: str = Field(default="m", description="Measurement unit (e.g. 'm', 'people', 'RMB')")
    transform: HeightTransformType = Field(default="linear", description="Mathematical mapping transform")
    scale_factor: float = Field(default=1.0, description="Multiplier for visual height scaling")
    min_visual_height_m: float = Field(default=10.0, ge=0.0, description="Minimum visual height in meters")
    max_visual_height_m: float = Field(default=5000.0, ge=10.0, description="Maximum visual height in meters")
    clamp_negative: bool = Field(default=True, description="Clamp negative values to 0 height")
    base_field: Optional[str] = Field(default=None, description="Optional attribute field for base elevation")
    base_value: float = Field(default=0.0, description="Default base elevation in meters")


class ExtrusionModelContract(BaseModel):
    """Full contract for 3D extrusion cartographic representation."""
    height_spec: ExtrusionHeightSpec
    color_field: Optional[str] = Field(default=None, description="Thematic color field (if different from height)")
    opacity: float = Field(default=0.85, ge=0.0, le=1.0)
    recommended_pitch: float = Field(default=45.0, ge=0.0, le=85.0)
    recommended_bearing: float = Field(default=-15.0, ge=-180.0, le=180.0)
    data_summary: Dict[str, Any] = Field(default_factory=dict)
    height_legend: Optional[Dict[str, Any]] = Field(default=None)


def analyze_height_field_distribution(
    values: List[Any],
    clamp_negative: bool = True,
) -> Dict[str, Any]:
    """
    Extracts valid numeric distribution statistics from raw attribute values.
    Filters out None, NaN, inf, and non-numeric items.
    """
    valid_nums: List[float] = []
    missing_count = 0
    negative_count = 0

    for v in values:
        if v is None:
            missing_count += 1
            continue
        try:
            num = float(v)
            if math.isnan(num) or math.isinf(num):
                missing_count += 1
                continue
            if num < 0:
                negative_count += 1
                if clamp_negative:
                    num = 0.0
            valid_nums.append(num)
        except (ValueError, TypeError):
            missing_count += 1

    if not valid_nums:
        return {
            "valid": False,
            "count": 0,
            "missing_count": missing_count,
            "negative_count": negative_count,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "std": 0.0,
            "p05": 0.0,
            "p25": 0.0,
            "p50": 0.0,
            "p75": 0.0,
            "p95": 0.0,
            "is_all_zero": False,
            "has_extreme_outlier": False,
        }

    arr = np.array(valid_nums, dtype=float)
    min_v = float(np.min(arr))
    max_v = float(np.max(arr))
    mean_v = float(np.mean(arr))
    std_v = float(np.std(arr)) if len(arr) > 1 else 0.0
    p05 = float(np.percentile(arr, 5))
    p25 = float(np.percentile(arr, 25))
    p50 = float(np.percentile(arr, 50))
    p75 = float(np.percentile(arr, 75))
    p95 = float(np.percentile(arr, 95))

    # Outlier detection: if max > 10 * p50 (for N >= 4)
    has_extreme_outlier = bool(max_v > 10.0 * max(p50, 1e-6) and len(arr) >= 4)

    return {
        "valid": True,
        "count": len(valid_nums),
        "missing_count": missing_count,
        "negative_count": negative_count,
        "min": min_v,
        "max": max_v,
        "mean": round(mean_v, 2),
        "std": round(std_v, 2),
        "p05": round(p05, 2),
        "p25": round(p25, 2),
        "p50": round(p50, 2),
        "p75": round(p75, 2),
        "p95": round(p95, 2),
        "is_all_zero": bool(max_v == 0.0 and min_v == 0.0),
        "has_extreme_outlier": has_extreme_outlier,
    }


def build_extrusion_height_expression(
    spec: ExtrusionHeightSpec,
    stats: Dict[str, Any],
) -> Any:
    """
    Builds a deterministic MapLibre GL expression for fill-extrusion-height.
    Supports linear, sqrt, and log1p transforms with multi-stop interpolation.
    """
    field = spec.height_field
    if not stats.get("valid") or stats.get("is_all_zero"):
        return spec.min_visual_height_m

    min_v = float(stats.get("min", 0.0))
    max_v = float(stats.get("max", 1.0))
    min_h = spec.min_visual_height_m * spec.scale_factor
    max_h = spec.max_visual_height_m * spec.scale_factor

    if math.isclose(min_v, max_v, abs_tol=1e-7):
        return min_h

    # For uniform extrusion
    if spec.transform == "uniform":
        return min_h

    # Determine stops based on transform and distribution characteristics.
    # Use 5 quantiles to ensure smooth non-linear interpolation.
    quantiles = [0.0, 0.25, 0.50, 0.75, 1.0]
    stops: List[List[Union[float, int]]] = []

    has_outlier = bool(stats.get("has_extreme_outlier", False))
    transform = spec.transform

    if transform == "log1p":
        # Sample non-linear steps logarithmically in domain space so transformed
        # height stops distribute visual height fairly across the dataset instead
        # of squashing normal values when extreme outliers exist.
        span = max(max_v - min_v, 0.0)
        for q in quantiles:
            domain_v = min_v + ((1.0 + span) ** q - 1.0)
            vis_h = round(min_h + q * (max_h - min_h), 1)
            stops.append([round(domain_v, 2), vis_h])
    elif transform == "sqrt":
        # Sqrt distribution spacing in domain space
        span = max(max_v - min_v, 0.0)
        for q in quantiles:
            domain_v = min_v + (q ** 2) * span
            vis_h = round(min_h + q * (max_h - min_h), 1)
            stops.append([round(domain_v, 2), vis_h])
    elif has_outlier:
        # Linear transform with extreme outliers: sample domain stops at distribution quantiles
        p25 = float(stats.get("p25", min_v + 0.25 * (stats.get("p50", (min_v + max_v) / 2.0) - min_v)))
        p50 = float(stats.get("p50", (min_v + max_v) / 2.0))
        p75 = float(stats.get("p75", p50 + 0.5 * (max_v - p50)))
        domain_points = [min_v, p25, p50, p75, max_v]
        for q, domain_v in zip(quantiles, domain_points):
            vis_h = round(min_h + q * (max_h - min_h), 1)
            stops.append([round(domain_v, 2), vis_h])
    else:
        # Standard linear stepping across domain
        for q in quantiles:
            domain_v = min_v + q * (max_v - min_v)
            vis_h = round(min_h + q * (max_h - min_h), 1)
            stops.append([round(domain_v, 2), vis_h])

    # Remove non-increasing domain stops (MapLibre requires strictly increasing domain stops)
    unique_stops: List[List[Union[float, int]]] = []
    last_domain = None
    for s in stops:
        d_val = s[0]
        if last_domain is None or d_val > last_domain:
            unique_stops.append(s)
            last_domain = d_val

    if len(unique_stops) < 2:
        if max_v > min_v:
            unique_stops = [[round(min_v, 2), min_h], [round(max_v, 2), max_h]]
        else:
            return min_h

    # MapLibre interpolate expression:
    # ["interpolate", ["linear"], ["coalesce", ["get", field], min_v], stop0_v, stop0_h, ...]
    expr: List[Any] = ["interpolate", ["linear"], ["coalesce", ["get", field], min_v]]
    for s in unique_stops:
        expr.append(s[0])
        expr.append(s[1])

    return expr


def build_extrusion_base_expression(spec: ExtrusionHeightSpec) -> Any:
    """Builds MapLibre expression for fill-extrusion-base."""
    if spec.base_field:
        return ["coalesce", ["get", spec.base_field], spec.base_value]
    return spec.base_value
