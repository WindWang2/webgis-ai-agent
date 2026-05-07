"""Core data models for the spatial explorer engine"""
from datetime import datetime
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field


class DataSourceQualityScore(BaseModel):
    """五维数据源质量评分模型"""
    temporal_score: float = Field(ge=0.0, le=1.0)
    thematic_score: float = Field(ge=0.0, le=1.0)
    spatial_score: float = Field(ge=0.0, le=1.0)
    field_score: float = Field(ge=0.0, le=1.0)
    precision_score: float = Field(ge=0.0, le=1.0)
    overall: float = Field(ge=0.0, le=1.0)
    details: dict = Field(default_factory=dict)


class ExplorerPerceptionEvent(BaseModel):
    """Explorer 感知事件协议"""
    stage: Literal["discover", "fetch", "parse", "geocode", "validate"]
    task_id: str
    status: Literal["started", "progress", "decision_point", "completed", "failed"]
    context: dict = Field(default_factory=dict)
    available_actions: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    requires_intervention: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class DataPackage(BaseModel):
    """统一数据包契约"""
    source_layer: Literal[
        "L1_upload", "L1_session", "L2_rag",
        "L3_api", "L3_spatial", "L4_gov", "L4_web", "L4_social"
    ]
    source_name: str
    source_url: str = ""
    quality: DataSourceQualityScore
    geojson: Optional[dict] = None
    features_count: int = 0
    temporal_range: Optional[tuple[datetime, datetime]] = None
    spatial_bbox: Optional[str] = None
    available_fields: list[str] = Field(default_factory=list)
    is_fusion_result: bool = False
    fusion_sources: list[str] = Field(default_factory=list)
    has_conflicts: bool = False
    conflict_fields: list[str] = Field(default_factory=list)


class SearchContext(BaseModel):
    """搜索上下文"""
    query: str
    expected_data_type: str = "poi_list"
    map_bbox: Optional[str] = None
    source_hint: list[str] = Field(default_factory=list)
    auto_threshold: float = 0.7


class FieldInfo(BaseModel):
    """字段信息"""
    name: str
    sample_values: list[Any] = Field(default_factory=list)
    nullable_ratio: float = 0.0


class RawContent(BaseModel):
    """原始内容"""
    data: bytes
    content_type: str = "text/csv"
    encoding: str = "utf-8"


class StructuredData(BaseModel):
    """结构化数据"""
    rows: list[dict]
    fields: list[FieldInfo]
