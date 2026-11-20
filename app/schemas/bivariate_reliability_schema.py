"""G41 双变量/可靠性作者工作台 HTTP 响应契约（ADR-0138 response_model 基线）。

逐字段镜像 ``app.services.bivariate_reliability_authoring`` inspect/preview/
commit 回执的运行时形状；自由子文档（有界图例投影/统计依据/派生信息）以
Dict[str, Any] 透传。所有模型 ``extra="allow"``：未知字段原样透传，保证
挂模前后响应体逐字节一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BivariateInspectResponse(BaseModel):
    """inspect_layer 回执（强版本源锚 + 字段画像 + 当前表达/证据 + 共享组）。"""

    model_config = ConfigDict(extra="allow")

    layer_id: str
    layer_type: Optional[str] = None
    source: Dict[str, Any] = Field(default_factory=dict)
    source_anchor: Dict[str, Any] = Field(default_factory=dict)
    current: Dict[str, Any] = Field(default_factory=dict)
    fields: List[Dict[str, Any]] = Field(default_factory=list)
    fields_truncated: bool = False
    supported: Dict[str, Any] = Field(default_factory=dict)
    measure_evidence_contract: Dict[str, Any] = Field(default_factory=dict)
    revision: int = 0
    shared_expression_scope: Optional[Dict[str, Any]] = None
    algorithm_version: str = ""
    notes: Optional[List[str]] = None


class BivariateExpressionReceipt(BaseModel):
    """preview / commit 回执（_bounded_receipt + revert 变体形状）。

    revert 变体以 restores 代替 legend/summary/derived；layer_id /
    revision / mapspec_fingerprint 等按运行时如实可选。
    """

    model_config = ConfigDict(extra="allow")

    success: bool = True
    layer_id: Optional[str] = None
    mode: str
    committed: bool
    preview_only: Optional[bool] = None
    algorithm_version: Optional[str] = None
    source_anchor: Optional[Dict[str, Any]] = None
    plan_digest: Optional[str] = None
    candidate_digest: Optional[str] = None
    current_fingerprint: Optional[str] = None
    candidate_fingerprint: Optional[str] = None
    legend: Optional[Dict[str, Any]] = None
    stats_basis: Optional[Dict[str, Any]] = None
    summary: Optional[Dict[str, Any]] = None
    disclosures: List[str] = Field(default_factory=list)
    derived: Optional[Dict[str, Any]] = None
    restores: Optional[Dict[str, Any]] = None
    revision: Optional[int] = None
    mapspec_fingerprint: Optional[str] = None
    can_undo: Optional[bool] = None
    mutations: Optional[int] = None
    export: Optional[Dict[str, Any]] = None
