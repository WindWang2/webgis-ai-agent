"""G47 Dorling cartogram 作者工作台 HTTP 响应契约（ADR-0138 response_model 基线）。

逐字段镜像 ``app.services.cartogram_authoring`` inspect/plan/run/apply/job/
layout 回执的运行时形状；导出端点（``layouts/{ref}/export``）返回真实
SVG/PNG/PDF 字节（Response 直接构造），按 EXCLUSIONS 最小豁免。所有模型
``extra="allow"``：未知字段原样透传，保证挂模前后响应体逐字节一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CartogramInspectResponse(BaseModel):
    """作者检查回执（资格 + 字段画像 + 当前绑定 + 数量契约）。"""

    model_config = ConfigDict(extra="allow")

    layer_id: str
    layer_type: Optional[str] = None
    source: Dict[str, Any] = Field(default_factory=dict)
    source_anchor: Dict[str, Any] = Field(default_factory=dict)
    geometry_types: List[str] = Field(default_factory=list)
    fields: List[Dict[str, Any]] = Field(default_factory=list)
    fields_truncated: bool = False
    current: Optional[Dict[str, Any]] = None
    supported: Dict[str, Any] = Field(default_factory=dict)
    contract: Dict[str, Any] = Field(default_factory=dict)
    revision: Optional[int] = None


class CartogramPlanReceipt(BaseModel):
    """plan 回执（_plan_receipt 形状）；run 追加 job 键。"""

    model_config = ConfigDict(extra="allow")

    success: bool = True
    layer_id: Optional[str] = None
    mode: str
    algorithm: Dict[str, Any] = Field(default_factory=dict)
    source_anchor: Dict[str, Any] = Field(default_factory=dict)
    plan_digest: str = ""
    candidate_digest: str = ""
    quantity: Dict[str, Any] = Field(default_factory=dict)
    units_for_layout: Dict[str, Any] = Field(default_factory=dict)
    geometry: Dict[str, Any] = Field(default_factory=dict)
    area_scale: Dict[str, Any] = Field(default_factory=dict)
    examples: List[Dict[str, Any]] = Field(default_factory=list)
    solver: Dict[str, Any] = Field(default_factory=dict)
    quality_gate: Dict[str, Any] = Field(default_factory=dict)
    budgets: Dict[str, Any] = Field(default_factory=dict)
    disclosures: List[str] = Field(default_factory=list)
    revision: Optional[int] = None
    # start_layout 追加键
    job_id: Optional[int] = None
    status: Optional[str] = None
    idempotent_reuse: Optional[bool] = None
    poll: Optional[str] = None
    note: Optional[str] = None


class CartogramApplyReceipt(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool = True
    layer_id: str
    layout_ref: str
    committed: bool
    duplicate: bool = False
    publication_ready: bool = False
    diagnostic: bool = False
    stale_source: bool = False
    revision: int = 0
    mapspec_fingerprint: Optional[str] = None
    can_undo: bool = False
    mutations: int = 0
    disclosures: List[str] = Field(default_factory=list)
    coordinate_space: Optional[str] = None


class CartogramJobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_id: int
    task_type: Optional[str] = None
    status: str
    progress: Optional[Any] = None
    message: Optional[str] = None
    result_ref: Optional[str] = None
    result_summary: Optional[Dict[str, Any]] = None
    error: Optional[Any] = None
    attempt: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CartogramLayoutListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str
    items: List[Dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False


class CartogramLayoutDetailResponse(BaseModel):
    """布局详情：full=True 时为完整布局文档，否则为 bounded_receipt 投影。"""

    model_config = ConfigDict(extra="allow")

    layout_ref: str
    kind: Optional[str] = None
    schema_version: Optional[int] = None
    algorithm: Optional[Dict[str, Any]] = None
    coordinate_space: Optional[str] = None
    units: Optional[Any] = None
    is_geographic: Optional[bool] = None
    status: Optional[str] = None
    publication_ready: Optional[bool] = None
    source: Optional[Dict[str, Any]] = None
    plan_digest: Optional[str] = None
    candidate_digest: Optional[str] = None
    quantity: Optional[Dict[str, Any]] = None
    area_scale: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    legend: Optional[Dict[str, Any]] = None
    adjacency: Optional[Dict[str, Any]] = None
    circle_count: Optional[int] = None
    circles_sample: Optional[List[Dict[str, Any]]] = None
    zero_count: Optional[int] = None
    missing_count: Optional[int] = None
    excluded_count: Optional[int] = None
    reference_outline_count: Optional[int] = None
    disclosures: List[str] = Field(default_factory=list)
