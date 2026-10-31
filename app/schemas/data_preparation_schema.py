"""数据准备域 API 契约模型（M5）。

有界纪律：任何响应不携带行数据本体 —— 样本预览 ≤50 行 × ≤64 字段、
receipt/proposals 全部是计数与码，绝不让一次 inspect 变成数据搬运。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

#: 样本预览预算。
MAX_PREVIEW_ROWS = 50
MAX_PREVIEW_FIELDS = 64


class InspectRequest(BaseModel):
    session_id: str = Field(..., description="归属会话（鉴权与 ref 命名空间）")
    source_ref: str = Field(..., description="会话内数据 ref（如 ref:data-…/ref:prep-…）")
    user_hints: Dict[str, str] = Field(
        default_factory=dict,
        description="可选角色提示 {字段名: 角色码}（来自用户对话；错误提示会被值域/类型反证并披露）")


class RoleCandidateOut(BaseModel):
    role: str
    confidence: float
    reasons: List[str] = Field(default_factory=list)


class FieldProposalOut(BaseModel):
    field: str
    dtype: str = ""
    candidates: List[RoleCandidateOut] = Field(default_factory=list)
    rejected: List[List[str]] = Field(default_factory=list)   # [role, reason_code]
    needs_clarification: bool = False


class FindingOut(BaseModel):
    code: str
    severity: str
    message_zh: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    suggestion: str = "none"
    field: str = ""


class ReceiptOut(BaseModel):
    input_ref: str
    geometry_type: str = ""
    srs: str = ""
    crs_source: str = ""
    axis_order_guessed: str = ""
    coordinate_range: Optional[List[float]] = None
    encoding: str = ""
    time_fields: List[str] = Field(default_factory=list)
    tz_status: str = ""
    pk_field: str = ""
    pk_status: str = ""
    row_count: int = 0
    sample_rows_scanned: int = 0
    findings: List[FindingOut] = Field(default_factory=list)
    blocked: bool = False


class SampleFeatureOut(BaseModel):
    properties: Dict[str, Any] = Field(default_factory=dict)
    geometry_type: str = ""
    first_position: Optional[List[float]] = None


class InspectResponse(BaseModel):
    input_ref: str
    input_fingerprint: str = ""      # dsd-v1:…（无 descriptor 时空串披露）
    receipt: ReceiptOut
    proposals: List[FieldProposalOut] = Field(default_factory=list)
    sample: List[SampleFeatureOut] = Field(default_factory=list)
    sample_truncated: bool = False


class PlanRequest(BaseModel):
    session_id: str
    steps: List[Dict[str, Any]] = Field(..., description="closed-union 步集（type 判别）")
    source_ref: str
    auto_steps: bool = Field(
        default=False,
        description="由服务端按收据证据自动补齐安全步（仅 filter_rows 与经用户 hints 确认的 field_mapping）")


class PlanStepOut(BaseModel):
    type: str
    data_changed: bool = False
    detail: Dict[str, Any] = Field(default_factory=dict)


class PlanOut(BaseModel):
    plan_id: str
    plan_hash: str
    state: str
    input_ref: str
    input_fingerprint: str = ""
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    mutates_data: bool = False


class PlanResponse(BaseModel):
    plan: PlanOut
    warnings: List[str] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    plan_id: str
    sampled_rows: int = 0
    truncated: bool = False
    estimated_rows_after: int = 0
    quality_delta_sample: Dict[str, int] = Field(default_factory=dict)
    steps: List[PlanStepOut] = Field(default_factory=list)
    degraded: List[str] = Field(default_factory=list)


class ReceiptSummaryOut(BaseModel):
    plan_id: str
    plan_hash: str
    input_ref: str
    output_ref: str
    output_fingerprint: str = ""
    row_before: int = 0
    row_after: int = 0
    quality_before: Dict[str, int] = Field(default_factory=dict)
    quality_after: Dict[str, int] = Field(default_factory=dict)
    degraded: List[str] = Field(default_factory=list)
    user_confirmations: List[Dict[str, str]] = Field(default_factory=list)


class ExecuteResponse(BaseModel):
    receipt: ReceiptSummaryOut


class FieldBindingRequest(BaseModel):
    session_id: str
    source_ref: str
    roles: Dict[str, str] = Field(..., description="{字段名: 提案角色码}（用户确认结果）")


class FieldBindingResponse(BaseModel):
    source_ref: str
    descriptor_fingerprint: str = ""
    bound_fields: List[str] = Field(default_factory=list)
    skipped: List[List[str]] = Field(default_factory=list)   # [field, reason]
