"""G33 专题表达工作台 HTTP 响应契约（ADR-0138 response_model 基线）。

逐字段镜像 ``app.services.thematic_authoring`` inspect/preview/commit 回执的
运行时形状；自由子文档（MapSpec 层/图例规格/分布回执）以 Dict[str, Any]
透传，不重复固化各自领域契约。所有模型 ``extra="allow"``：未知字段原样
透传，保证挂模前后响应体逐字节一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ThematicInspectResponse(BaseModel):
    """inspect_layer 回执（当前表达 + 同版本分布 + 字段清单 + 共享组资格）。"""

    model_config = ConfigDict(extra="allow")

    layer_id: str
    layer_type: Optional[str] = None
    source: Dict[str, Any] = Field(default_factory=dict)
    current: Dict[str, Any] = Field(default_factory=dict)
    fields: List[Dict[str, Any]] = Field(default_factory=list)
    distribution: Dict[str, Any] = Field(default_factory=dict)
    revision: int = 0
    shared_expression_scope: Optional[Dict[str, Any]] = None
    notes: Optional[List[str]] = None


class ThematicExpressionReceipt(BaseModel):
    """preview / commit 共用回执（builder.CandidateExpression.receipt +
    service._receipt 形状）。

    revision / mapspec_fingerprint / can_undo / mutations 仅 commit 回填；
    size_samples 仅带尺寸通道的计划出现。
    """

    model_config = ConfigDict(extra="allow")

    legend_spec: Dict[str, Any] = Field(default_factory=dict)
    paint: Optional[Dict[str, Any]] = None
    size_paint: Optional[Dict[str, Any]] = None
    class_table: List[Dict[str, Any]] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)
    requested_k: Optional[int] = None
    actual_k: Optional[int] = None
    layer_id: Optional[str] = None
    field: str
    mode: str
    committed: bool
    source_anchor: Dict[str, Any] = Field(default_factory=dict)
    current_fingerprint: Optional[str] = None
    candidate_fingerprint: Optional[str] = None
    stats_basis: Optional[Dict[str, Any]] = None
    distribution: Dict[str, Any] = Field(default_factory=dict)
    plan_digest: str
    revision: Optional[int] = None
    mapspec_fingerprint: Optional[str] = None
    can_undo: Optional[bool] = None
    mutations: Optional[int] = None
    size_samples: Optional[List[Dict[str, Any]]] = None
