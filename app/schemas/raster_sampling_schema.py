"""G39 raster_sampling API 请求/响应模型（路由面；工具面共用 service）。"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SourceSpecIn(BaseModel):
    ref_id: str = Field(..., min_length=1, max_length=128,
                        json_schema_extra={"ref_cursor": True})
    label: str = ""
    bands: List[int] = Field(default_factory=lambda: [1])
    band_labels: Dict[str, str] = Field(default_factory=dict)
    band_units: Dict[str, str] = Field(default_factory=dict)
    #: {band: "categorical"|"continuous"}（用户显式声明；优先于元数据）
    band_semantics: Dict[str, str] = Field(default_factory=dict)


class SelectionIn(BaseModel):
    ref_id: str = Field(..., min_length=1, max_length=128)
    handle: str = Field(..., min_length=1, max_length=128)


class PlanCreateIn(BaseModel):
    name: str = ""
    mode: str = Field(..., pattern="^(source_points|line_stations)$")
    points_ref: str = ""
    selection: Optional[SelectionIn] = None
    lines_ref: str = ""
    line_feature_id: str = ""
    station_crs: str = "EPSG:4326"
    distance_mode: str = "geodesic"
    station_step: Optional[float] = None
    include_vertices: bool = True
    method: str = "source_pixel"
    nodata_policy: str = "strict"
    sources: List[SourceSpecIn] = Field(min_length=1)
    project_id: str = ""


class PlanReviseIn(BaseModel):
    expected_revision: int = Field(..., ge=1)
    name: str = ""
    method: str = ""
    nodata_policy: str = ""
    station_step: Optional[float] = None
    include_vertices: Optional[bool] = None
    distance_mode: str = ""
    sources: Optional[List[SourceSpecIn]] = None


class PreviewIn(BaseModel):
    expected_revision: Optional[int] = None
    limit: int = Field(64, ge=1, le=64)


class ExecuteIn(BaseModel):
    expected_revision: Optional[int] = None
    background: bool = False
    project_id: str = ""
