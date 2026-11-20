"""G24 选择集句柄 HTTP 响应契约（ADR-0138 response_model 基线）。

逐字段镜像 ``app.services.selection_handles`` 回执的运行时形状。路由以
Response 直接构造（自定义 nosniff 头），response_model 仅声明 OpenAPI
契约 + 挂模门禁，不改变运行时序列化。所有模型 ``extra="allow"``：未知
字段原样透传，保证挂模前后响应体逐字节一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SelectionHandleResponse(BaseModel):
    """创建/读取句柄共用（count 超精确上限时如实为 None）。"""

    model_config = ConfigDict(extra="allow")

    handle: str
    count: Optional[int] = None
    count_exact: bool = False
    revision: Optional[int] = None
    scope: Dict[str, Any] = Field(default_factory=dict)
    missing_ids: Optional[List[str]] = None
    ttl_seconds: int = 0


class SelectionFeaturesPageResponse(BaseModel):
    """句柄全集稳定序分页（page_features 语义复用）。"""

    model_config = ConfigDict(extra="allow")

    type: str = "FeatureCollection"
    features: List[Dict[str, Any]] = Field(default_factory=list)
    pagination: Dict[str, Any] = Field(default_factory=dict)
    selection: Dict[str, Any] = Field(default_factory=dict)
    revision: Optional[int] = None
    reason_codes: List[str] = Field(default_factory=list)


class SelectionMaterializeResponse(BaseModel):
    """句柄物化为会话 ref（materialize_selection 形状）。"""

    model_config = ConfigDict(extra="allow")

    ref_id: str
    feature_count: int = 0
    coarse_bbox: bool = False
    revision: Optional[int] = None


class SelectionReleaseResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    released: bool
