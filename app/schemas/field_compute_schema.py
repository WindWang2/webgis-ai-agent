"""字段计算域 API 契约（G19 / WP09）。

有界纪律：响应不携带数据本体 —— describe 样本 ≤5 行、preview before/after
≤10 行、receipt 只有计数/码/公式摘要/错误示例（≤8）。

**命名纪律（独立评审 P1）**：所有模型必须带 ``FieldCompute`` 前缀。若与
``data_preparation_schema`` 的 ``PlanOut``/``PlanRequest`` 等短名重名，
FastAPI 会对 OpenAPI 组件做全局消歧（``app__schemas__...__PlanOut``），
改写既有 7 个数据准备端点的 ``$ref`` —— 那是共享契约破坏，不是 additive。
"""
from __future__ import annotations

from typing import Any, Dict, List, Union

from pydantic import BaseModel, Field


class FieldComputeDescribeRequest(BaseModel):
    session_id: str
    source_ref: str


class FieldComputeColumnSpec(BaseModel):
    name: str = Field(..., description="新字段名（不得与输入字段/保留字段冲突）")
    expression: Union[str, Dict[str, Any]] = Field(
        ..., description="文本公式或闭合 IR dict；两者编译为同一个 IR")
    declared_unit: str = ""
    description: str = ""


class FieldComputePlanRequest(BaseModel):
    session_id: str
    source_ref: str
    columns: List[FieldComputeColumnSpec] = Field(..., min_length=1, max_length=8)
    scope: Dict[str, Any] = Field(default_factory=dict)
    field_units: Dict[str, str] = Field(
        default_factory=dict,
        description="user-declared canonical unit（字段名只是提示，不是单位证据）")
    timezone: str = "UTC"
    date_format: str = ""
    error_policy: str = "null"
    #: 仅作为兼容性字段保留；owner 一律由服务端认证上下文注入，不接受客户端覆盖。
    owner: str = ""


class FieldComputeExecuteRequest(BaseModel):
    expected_fingerprint: str = Field(
        default="", description="已看到预览的 confirmation.params_fingerprint")
    project_id: str = ""
    owner: str = ""
    # G23：True = 后台 durable job（立即返回句柄）；False = 同步收据（BASE）
    background: bool = False


class FieldComputePlanOut(BaseModel):
    plan_id: str
    compute_fingerprint: str
    state: str
    input_ref: str
    owner: str = ""
    content_hash: str = ""
    data_shape: str = ""
    columns: List[Dict[str, Any]] = Field(default_factory=list)
    error_policy: str = "null"
    timezone: str = "UTC"


class FieldComputePlanResponse(BaseModel):
    plan: FieldComputePlanOut
    data_shape: str
    column_order: List[str] = Field(default_factory=list)
    scope_summary: Dict[str, Any] = Field(default_factory=dict)
    geometry: Dict[str, Any] = Field(default_factory=dict)
    estimated_ops: int = 0
    warnings: List[str] = Field(default_factory=list)


class FieldComputePlanListResponse(BaseModel):
    plans: List[FieldComputePlanOut] = Field(default_factory=list)


class FieldComputeDiscardResponse(BaseModel):
    plan_id: str
    state: str


class FieldComputeCancelResponse(BaseModel):
    plan_id: str
    cancelled: bool


# 复杂嵌套响应保持 dict（service 已保证有界；不在此重复截断）。
FieldComputeDescribeResponse = Dict[str, Any]
FieldComputePreviewResponse = Dict[str, Any]
FieldComputeReceiptResponse = Dict[str, Any]
FieldComputeResultResponse = Dict[str, Any]
FieldComputeCatalogResponse = Dict[str, Any]
FieldComputeProjectResultsResponse = Dict[str, Any]

__all__ = [
    "FieldComputeDescribeRequest", "FieldComputeColumnSpec",
    "FieldComputePlanRequest", "FieldComputeExecuteRequest",
    "FieldComputePlanOut", "FieldComputePlanResponse",
    "FieldComputePlanListResponse", "FieldComputeDiscardResponse",
    "FieldComputeCancelResponse",
    "FieldComputeDescribeResponse", "FieldComputePreviewResponse",
    "FieldComputeReceiptResponse", "FieldComputeResultResponse",
    "FieldComputeCatalogResponse", "FieldComputeProjectResultsResponse",
]
