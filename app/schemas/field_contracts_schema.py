"""G44 wire schema —— 合同文档 / 评估收据 / 路由载荷（pydantic 有界校验）。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from app.services.field_contracts.contracts import (
    COMPARE_OPERATORS,
    CONDITION_OPERATORS,
    EMPTY_STRING_POLICIES,
    FIELD_TYPE_NAMES,
    MAX_ENUM_VALUES,
    MAX_FIELD_NAME_LEN,
    MAX_FIELDS_PER_CONTRACT,
    MAX_LABEL_LEN,
    MAX_UNIQUE_FIELDS,
    MAX_CROSS_RULES,
    UNIQUE_NULL_POLICIES,
)

FieldName = str


class FieldRuleIn(BaseModel):
    """单字段声明（严格标量 + 取值域 + 展示元数据）。"""

    field: FieldName = Field(..., min_length=1, max_length=MAX_FIELD_NAME_LEN)
    type: Literal["string", "number", "integer", "boolean", "date", "datetime"]
    required: bool = False
    nullable: bool = False
    #: 空串政策（仅 string；reject=空串违规，allow=放行）—— 显式三态之一
    empty_string: Literal["reject", "allow"] = "reject"
    enum: Optional[List[str]] = Field(None, max_length=MAX_ENUM_VALUES)
    # R1-P2：Union[int, float] —— 纯 float 会在 pydantic lax 模式把
    # 2^53+1 强转 float 舍入，REST 声明的 bigint 边界失真（F08）。
    min: Optional[Union[int, float]] = None
    max: Optional[Union[int, float]] = None
    exclusive_min: bool = False
    exclusive_max: bool = False
    min_length: Optional[int] = Field(None, ge=0, le=10_000)
    max_length: Optional[int] = Field(None, ge=0, le=100_000)
    #: 展示元数据（只影响解释，不参与判定；unit 不做量纲换算）
    label: Optional[str] = Field(None, max_length=MAX_LABEL_LEN)
    description: Optional[str] = Field(None, max_length=512)
    unit: Optional[str] = Field(None, max_length=MAX_LABEL_LEN)
    #: create 表单默认值（展示 + 实际采用结果披露；不静默改已存字段）
    default: Optional[Any] = None
    #: 表单控件提示（structured form 由此渲染；缺省按 type 推导）
    form_control: Optional[Literal["text", "textarea", "select", "date", "datetime-local"]] = None


class ConditionalWhen(BaseModel):
    field: FieldName
    op: Literal["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte"]
    value: Optional[Any] = None
    values: Optional[List[Any]] = Field(None, max_length=MAX_ENUM_VALUES)


class CrossRuleIn(BaseModel):
    """跨字段封闭规则：conditional_required | compare | unique。"""

    rule_id: str = Field(..., min_length=1, max_length=64)
    kind: Literal["conditional_required", "compare", "unique"]
    # conditional_required
    when: Optional[ConditionalWhen] = None
    require: Optional[List[FieldName]] = Field(None, min_length=1, max_length=8)
    # compare
    left: Optional[FieldName] = None
    op: Optional[Literal["lt", "lte", "gt", "gte"]] = None
    right: Optional[FieldName] = None
    # unique
    fields: Optional[List[FieldName]] = Field(None, min_length=1, max_length=MAX_UNIQUE_FIELDS)
    null_policy: Literal["ignore", "participate"] = "ignore"
    note: Optional[str] = Field(None, max_length=256)


class ContractDocument(BaseModel):
    """合同声明正文（版本化不可变内容；canonical digest 的输入）。"""

    version: Literal[1] = 1
    title: str = Field("", max_length=200)
    notes: Optional[str] = Field(None, max_length=1024)
    fields: List[FieldRuleIn] = Field(..., min_length=1, max_length=MAX_FIELDS_PER_CONTRACT)
    cross_rules: List[CrossRuleIn] = Field(default_factory=list, max_length=MAX_CROSS_RULES)


class ContractCreateRequest(BaseModel):
    ref_id: str = Field(..., min_length=4, max_length=128)
    document: ContractDocument


class ContractReviseRequest(BaseModel):
    """草稿修订（revision CAS；active/retired 不可改内容 —— 新草稿走新行）。"""

    expected_revision: int = Field(..., ge=1)
    document: ContractDocument


class AssessmentPage(BaseModel):
    findings: List[Dict[str, Any]]
    next_cursor: Optional[str] = None
    total_violations: int
    filtered_total: int
    truncated_page_note: Optional[str] = None


__all__ = [
    "FieldRuleIn",
    "ConditionalWhen",
    "CrossRuleIn",
    "ContractDocument",
    "ContractCreateRequest",
    "ContractReviseRequest",
    "AssessmentPage",
    "FIELD_TYPE_NAMES",
    "EMPTY_STRING_POLICIES",
    "UNIQUE_NULL_POLICIES",
    "CONDITION_OPERATORS",
    "COMPARE_OPERATORS",
]
