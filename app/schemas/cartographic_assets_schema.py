"""G40 制图符号资产工作台 HTTP 响应契约（ADR-0138 response_model 基线）。

逐字段镜像 ``app.services.cartographic_assets`` 目录/版本/绑定回执的运行时
形状；资产文档本体由 ``app.lib.cartography.symbol_asset`` 领域契约拥有，
本模型按其字段平铺透传。所有模型 ``extra="allow"``：未知字段原样透传，
保证挂模前后响应体逐字节一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CartographicAssetView(BaseModel):
    """资产行视图（路由 _asset_view 形状；创建/详情回执内嵌）。"""

    model_config = ConfigDict(extra="allow")

    asset_id: str
    name: str = ""
    kind: str = ""
    visibility: str = "private"
    project_id: Optional[str] = None
    status: str = ""
    latest_revision: int = 0
    current_revision: int = 0
    builtin: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    revision: Optional[int] = None
    content_digest: Optional[str] = None
    version_status: Optional[str] = None


class CartographicAssetListResponse(BaseModel):
    """目录页（builtin 内联规范形 + DB 资产；两类条目异构，如实 Dict）。"""

    model_config = ConfigDict(extra="allow")

    success: bool = True
    assets: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 0


class CartographicAssetCreateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool = True
    asset: CartographicAssetView


class CartographicAssetDetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool = True
    asset: CartographicAssetView
    versions: List[Dict[str, Any]] = Field(default_factory=list)


class CartographicAssetContentResponse(BaseModel):
    """版本规范形（SymbolAssetDocument 字段平铺 + meta；fetchDocByRef 消费）。"""

    model_config = ConfigDict(extra="allow")

    success: bool = True
    meta: Dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 0
    kind: str = ""
    name: str = ""
    viewbox: Optional[List[float]] = None
    primitives: List[Dict[str, Any]] = Field(default_factory=list)
    pattern: Optional[Dict[str, Any]] = None
    default_params: Dict[str, Any] = Field(default_factory=dict)
    source: Dict[str, str] = Field(default_factory=dict)
    content_digest: str = ""


class CartographicAssetVersionResponse(BaseModel):
    """新版本 / 发布 / 恢复共用（revision 恒在；digest/status 按操作可选）。"""

    model_config = ConfigDict(extra="allow")

    success: bool = True
    revision: int
    content_digest: Optional[str] = None
    status: Optional[str] = None


class CartographicAssetRetireResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool = True
    status: str


class CartographicAssetDeleteVersionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    deleted: bool
    asset_id: str
    revision: int
    reference_scan: Dict[str, Any] = Field(default_factory=dict)


class SymbolBindingInspectResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool = True
    layer_id: str
    current_binding: Optional[Dict[str, Any]] = None
    eligibility: Dict[str, Any] = Field(default_factory=dict)
    revision: int = 0
    builtin_assets: List[Dict[str, Any]] = Field(default_factory=list)


class SymbolBindingReceipt(BaseModel):
    """preview / apply / unbind 共用回执（binding._receipt 形状）。

    asset/params/binding_fingerprint/category_binding 仅带候选时出现；
    revision/mapspec_fingerprint/can_undo/mutations 仅提交时回填；
    restored_paint 仅 unbind。
    """

    model_config = ConfigDict(extra="allow")

    success: bool = True
    layer_id: str
    committed: bool
    asset: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None
    binding_fingerprint: Optional[str] = None
    category_binding: Optional[Dict[str, Any]] = None
    unbound: Optional[bool] = None
    restored_paint: Optional[bool] = None
    preview: Optional[Dict[str, Any]] = None
    revision: Optional[int] = None
    mapspec_fingerprint: Optional[str] = None
    can_undo: Optional[bool] = None
    mutations: Optional[int] = None
