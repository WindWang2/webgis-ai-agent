"""字段计算域 API 契约（G19 / WP09）。

有界纪律：响应不携带数据本体 —— describe 样本 ≤5 行、preview before/after
≤10 行、receipt 只有计数/码/公式摘要/错误示例（≤8）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Union

from pydantic import BaseModel, Field


class DescribeRequest(BaseModel):
    session_id: str
    source_ref: str


class ColumnSpec(BaseModel):
    name: str = Field(..., description="新字段名（不得与输入字段/保留字段冲突）")
    expression: Union[str, Dict[str, Any]] = Field(
        ..., description="文本公式或闭合 IR dict；两者编译为同一个 IR")
    declared_unit: str = ""
    description: str = ""


class PlanRequest(BaseModel):
    session_id: str
    source_ref: str
    columns: List[ColumnSpec] = Field(..., min_length=1, max_length=8)
    scope: Dict[str, Any] = Field(default_factory=dict)
    field_units: Dict[str, str] = Field(
        default_factory=dict,
        description="user-declared canonical unit（字段名只是提示，不是单位证据）")
    timezone: str = "UTC"
    date_format: str = ""
    error_policy: str = "null"
    owner: str = ""


class ExecuteRequest(BaseModel):
    expected_fingerprint: str = Field(
        default="", description="已看到预览的 confirmation.params_fingerprint")
    project_id: str = ""
    owner: str = ""


class PlanOut(BaseModel):
    plan_id: str
    compute_fingerprint: str
    state: str
    input_ref: str
    content_hash: str = ""
    data_shape: str = ""
    columns: List[Dict[str, Any]] = Field(default_factory=list)
    error_policy: str = "null"
    timezone: str = "UTC"


class PlanResponse(BaseModel):
    plan: PlanOut
    data_shape: str
    column_order: List[str] = Field(default_factory=list)
    scope_summary: Dict[str, Any] = Field(default_factory=dict)
    geometry: Dict[str, Any] = Field(default_factory=dict)
    estimated_ops: int = 0
    warnings: List[str] = Field(default_factory=list)


class PlanListResponse(BaseModel):
    plans: List[PlanOut] = Field(default_factory=list)


class DiscardResponse(BaseModel):
    plan_id: str
    state: str


# 复杂嵌套响应保持 dict（service 已保证有界；不在此重复截断）。
DescribeResponse = Dict[str, Any]
PreviewResponse = Dict[str, Any]
ReceiptResponse = Dict[str, Any]
ResultResponse = Dict[str, Any]
CatalogResponse = Dict[str, Any]
ProjectResultsResponse = Dict[str, Any]

__all__ = [
    "DescribeRequest", "ColumnSpec", "PlanRequest", "ExecuteRequest",
    "PlanOut", "PlanResponse", "PlanListResponse", "DiscardResponse",
    "DescribeResponse", "PreviewResponse", "ReceiptResponse",
    "ResultResponse", "CatalogResponse", "ProjectResultsResponse",
]
