"""G20 栅格表达工作台 HTTP 响应契约（ADR-0138 response_model 基线）。

逐字段镜像 ``app.services.raster_authoring`` 各回执的**运行时形状**（不改变
其序列化输出）：稳定标量/列表给精确类型；自由子文档（MapSpec 文档、图例
规格、表达陈述、默认表达请求、投影投影）由各自领域契约拥有，本模型以
``Dict[str, Any]`` 透传，不重复固化。所有模型 ``extra="allow"``：未知字段
原样透传，保证挂模前后响应体逐字节一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.raster_spec import RasterExpressionSpec


class RasterRefItem(BaseModel):
    """已登记 ref 列表条目（工作台选择器；未开文件头验证）。"""

    model_config = ConfigDict(extra="allow")

    ref_id: str
    alias: str = ""
    kind: Optional[str] = None
    verified: bool = False


class RasterRefsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool
    refs: List[RasterRefItem] = Field(default_factory=list)
    truncated: bool = False


class RasterBandMeta(BaseModel):
    """单波段元数据（rasterio 头 + 每波段 tag；source._read_band_meta）。"""

    model_config = ConfigDict(extra="allow")

    index: int
    label: str = ""
    description: str = ""
    dtype: str = ""
    unit: Optional[str] = None
    scale: float = 1.0
    offset: float = 0.0
    scale_declared: bool = False
    offset_declared: bool = False
    role: Optional[str] = None
    vmin: Optional[float] = None
    vmax: Optional[float] = None


class RasterBandStatistics(BaseModel):
    """有界降采样统计（BandStats.as_dict 形状）。"""

    model_config = ConfigDict(extra="allow")

    method: str = "minmax_decimated"
    sample_count: int = 0
    approximate: bool = True
    side: int = 0
    per_band: List[List[float]] = Field(default_factory=list)


class RasterHistogram(BaseModel):
    """有界直方图桶（descriptor.histogram 形状）。"""

    model_config = ConfigDict(extra="allow")

    band: int
    buckets: int
    counts: List[int] = Field(default_factory=list)
    min: Optional[float] = None
    max: Optional[float] = None
    valid_samples: int = 0
    total_samples: int = 0
    approximate: bool = True
    method: str = "histogram_decimated_2048"
    all_nodata: bool = False


class RasterDescriptorCapabilities(BaseModel):
    model_config = ConfigDict(extra="allow")

    numeric_bands: bool = True
    tiles: bool = False
    probe: bool = True
    classified_semantics_requires_declaration: bool = True


class RasterDescriptorResponse(BaseModel):
    """describe_raster 回执（descriptor.describe 形状）。"""

    model_config = ConfigDict(extra="allow")

    success: bool = True
    ref_id: str
    session_id: str
    data_revision: str
    content_fingerprint: str
    crs: Optional[str] = None
    crs_present: bool = False
    bounds_source_crs: Optional[List[float]] = None
    bounds_epsg4326: Optional[List[float]] = None
    width: int = 0
    height: int = 0
    transform: Optional[List[float]] = None
    resolution_x: Optional[float] = None
    resolution_y: Optional[float] = None
    driver: str = ""
    nodata: Optional[float] = None
    has_overviews: bool = False
    band_count: int = 0
    bands: List[RasterBandMeta] = Field(default_factory=list)
    statistics: Optional[RasterBandStatistics] = None
    histograms: List[RasterHistogram] = Field(default_factory=list)
    capabilities: RasterDescriptorCapabilities = Field(
        default_factory=RasterDescriptorCapabilities
    )
    defaults: Dict[str, Any] = Field(default_factory=dict)
    disclosures: List[str] = Field(default_factory=list)


class RasterProbeChannelValue(BaseModel):
    """逐通道源像元值（probe._read_pixel channel_values 形状）。"""

    model_config = ConfigDict(extra="allow")

    band: int
    label: str = ""
    role: Optional[str] = None
    raw: Optional[float] = None
    scaled: Optional[float] = None
    scale: float = 1.0
    offset: float = 0.0
    scale_applied: bool = False
    unit: Optional[str] = None
    unit_status: str = "unknown"
    valid: bool = False
    invalid_reason: Optional[str] = None


class RasterProbePoint(BaseModel):
    """单点检视结果；范围外点无 source_coord/values/valid（如实为 None）。"""

    model_config = ConfigDict(extra="allow")

    id: str
    requested_coord: List[float] = Field(default_factory=list)
    requested_crs: str = "source"
    source_coord: Optional[List[float]] = None
    source_crs: Optional[str] = None
    status: str
    row: Optional[int] = None
    col: Optional[int] = None
    values: Optional[List[RasterProbeChannelValue]] = None
    valid: Optional[bool] = None


class RasterProbeResponse(BaseModel):
    """probe_raster 回执（probe.probe 形状）。"""

    model_config = ConfigDict(extra="allow")

    success: bool = True
    ref_id: str
    session_id: str
    data_revision: str
    method: str = "nearest_source_pixel_window_1x1"
    interpolated: bool = False
    bands: List[int] = Field(default_factory=list)
    truncated: bool = False
    points: List[RasterProbePoint] = Field(default_factory=list)
    classification: str = "undeclared"


class RasterStatisticsProvenance(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str = ""
    method: str = ""
    sample_count: Optional[int] = None
    approximate: bool = False


class RasterExpressionReceipt(BaseModel):
    """preview / commit 共用回执（service._receipt 形状）。

    ``mapspec`` 仅 commit 回灌；``projection`` 仅 preview 附加。
    ``expression`` 直接复用领域契约 ``RasterExpressionSpec``。
    """

    model_config = ConfigDict(extra="allow")

    success: bool = True
    committed: bool
    state: Literal["preview", "committed"]
    layer_id: str
    source_id: str
    ref_id: str
    mapspec_revision: Optional[int] = None
    mapspec_fingerprint: Optional[str] = None
    mapspec: Optional[Dict[str, Any]] = None
    mutations: int = 0
    data_revision: str
    expression_version: str
    style_id: str
    statistics_provenance: RasterStatisticsProvenance = Field(
        default_factory=RasterStatisticsProvenance
    )
    expression: RasterExpressionSpec
    legend: Dict[str, Any] = Field(default_factory=dict)
    expression_statement: Dict[str, Any] = Field(default_factory=dict)
    projection: Optional[Dict[str, Any]] = None


class RasterExportResponse(BaseModel):
    """export_snapshot 交付回执（delivery.persist_snapshot_artifact 形状）。"""

    model_config = ConfigDict(extra="allow")

    success: bool = True
    filename: str
    download_url: str
    media_type: str
    bytes: int
    frozen_revision: int
    frozen_style_ids: List[str] = Field(default_factory=list)
    omitted_layers: List[str] = Field(default_factory=list)
    embedded: bool
    expression_summary: Dict[str, Any] = Field(default_factory=dict)
    limits: List[str] = Field(default_factory=list)
