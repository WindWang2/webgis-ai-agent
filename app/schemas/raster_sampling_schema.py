"""G39 raster_sampling API 请求/响应模型（路由面；工具面共用 service）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

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


# ── HTTP 响应契约（ADR-0138 response_model 基线）──────────────────────────
#
# 逐字段镜像 ``app.services.raster_sampling`` 各回执的运行时形状
# （describe / plan CRUD / preview / execute / status / results / cancel）。
# 计划负载与收据等自由子文档以 Dict[str, Any] 透传。所有响应模型
# ``extra="allow"``：未知字段原样透传，保证挂模前后响应体逐字节一致。


class RasterSamplingDescribeResponse(BaseModel):
    """会话已登记栅格源 + 采样预算词表（service.describe_sources 形状）。"""

    model_config = {"extra": "allow"}

    success: bool = True
    session_id: str
    raster_refs: List[Dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    budgets: Dict[str, Any] = Field(default_factory=dict)


class RasterSamplingPlanResponse(BaseModel):
    """create / revise / get 共用（stations/selection_materialized 仅 create）。"""

    model_config = {"extra": "allow"}

    success: bool = True
    plan: Dict[str, Any] = Field(default_factory=dict)
    stations: Optional[Dict[str, Any]] = None
    selection_materialized: Optional[Any] = None


class RasterSamplingPlanListResponse(BaseModel):
    model_config = {"extra": "allow"}

    success: bool = True
    plans: List[Dict[str, Any]] = Field(default_factory=list)


class RasterSamplingPreviewResponse(BaseModel):
    model_config = {"extra": "allow"}

    success: bool = True
    plan_id: str
    revision: int = 0
    preview: bool = True
    station_selection: Dict[str, Any] = Field(default_factory=dict)
    read_metrics: Dict[str, Any] = Field(default_factory=dict)
    cost_estimate: Optional[Any] = None
    coverage_preview: Optional[Any] = None


class RasterSamplingExecuteResponse(BaseModel):
    """内联完成 = execution_id + summary；后台 = job 投影（job_id/status/params）。"""

    model_config = {"extra": "allow"}

    execution_id: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    job_id: Optional[str] = None
    status: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


class RasterSamplingStatusResponse(BaseModel):
    model_config = {"extra": "allow"}

    success: bool = True
    plan_id: str
    revision: int = 0
    job: Optional[Dict[str, Any]] = None
    receipt_count: int = 0


class RasterSamplingResultsResponse(BaseModel):
    model_config = {"extra": "allow"}

    success: bool = True
    execution_id: str
    plan_id: str
    receipt: Dict[str, Any] = Field(default_factory=dict)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    pagination: Dict[str, Any] = Field(default_factory=dict)
    sample_table_ref: Optional[str] = None


class RasterSamplingCancelResponse(BaseModel):
    model_config = {"extra": "allow"}

    success: bool = True
    cancelled: List[Dict[str, Any]] = Field(default_factory=list)
