"""字段计算域 —— 闭合契约、类型化 IR、预算与版本锚（G19 / WP02）。

设计红线（与 ``mapspec_expr.py`` 的 fail-open 显示层求值**严格区分**）：

- **表达式不是代码权限**：只有 ``lit/field/unary/binary/call/case`` 六类节点，
  算子与函数是 closed 词表；没有 import / 属性反射 / 任意调用 / 动态代码 /
  文件 / 网络 / 系统时钟 / 随机数。解析器只产出 dict，绝不使用 eval/exec；
- **fail-closed**：未知节点、未知函数、超预算、保留字段、字段冲突、循环列
  依赖、前向引用、不可靠 selection 一律在**计算前**拒绝，带稳定原因码与位置；
- **可重放**：``canonical()`` 产出排序 JSON，重排等价表达式不破坏指纹；
  计划指纹绑定输入内容/schema/scope/IR/函数集版本/单位/时区/错误策略；
- **诚实状态**：``unknown / not_evaluated / blocked / partial`` 不冒充成功；
  单位默认 undeclared，只有 user-declared 才提供科学资格。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 契约版本（全部参与 compute_fingerprint；改动必须升版本）。
IR_VERSION = "g19-ir-1"
FUNCTION_SET_VERSION = "g19-fc-1.0"
RECEIPT_VERSION = 1

# ── 资源预算（编译期 + 执行期双重强制）────────────────────────────────────
MAX_EXPR_NODES = 64
MAX_EXPR_DEPTH = 12
MAX_LITERAL_STRING = 512
MAX_RESULT_STRING = 512
MAX_STRING_INPUT = 2048
MAX_FIELD_NAME_LEN = 128
MAX_CALL_ARGS = 4
MAX_CASE_BRANCHES = 8
MAX_COLUMNS = 8
MAX_ROW_OPS = 256
MAX_ROWS = 50_000
MAX_BATCH_ROWS = 2_000
MAX_PREVIEW_ROWS = 50
MAX_ERROR_SAMPLES = 8
SAFE_INTEGER_MAX = 2 ** 53 - 1

#: GeoJSON / 运行时保留字段：计算列不得写入（防破坏载荷结构）。
RESERVED_FIELD_NAMES = frozenset({
    "type", "geometry", "properties", "features", "bbox", "crs",
    "id", "__proto__", "constructor", "prototype",
})

# ── 词表 ────────────────────────────────────────────────────────────────────
NODE_KINDS = frozenset({"lit", "field", "unary", "binary", "call", "case"})
VALUE_TYPES = frozenset({"number", "string", "boolean", "datetime", "null", "error"})
LITERAL_DTYPES = frozenset({"number", "string", "boolean", "null"})
UNARY_OPS = frozenset({"neg", "not"})
BINARY_OPS = frozenset({
    "add", "sub", "mul", "div", "mod",
    "eq", "ne", "lt", "le", "gt", "ge", "and", "or",
})
SCOPE_MODES = frozenset({"all_rows", "selection", "predicate"})
UNSELECTED_POLICIES = frozenset({"null", "absent"})
ERROR_POLICIES = frozenset({"abort", "null"})
PREDICATE_OPS = frozenset({
    "eq", "ne", "lt", "le", "gt", "ge", "is_null", "not_null", "in",
})
PLAN_STATES = frozenset({"compiled", "previewed", "executed", "cancelled", "rejected"})

#: 行级错误码（closed；每一行最多一个，计数 + 有限示例进 receipt）。
ROW_ERROR_CODES = frozenset({
    "DIVISION_BY_ZERO", "NON_FINITE", "NUMERIC_OVERFLOW", "TYPE_MISMATCH",
    "EMPTY_STRING_NOT_ZERO", "CONVERSION_FAILED", "INVALID_DATE",
    "UNSAFE_INTEGER", "STRING_TOO_LONG", "NULL_OPERAND", "UNKNOWN_FIELD",
    "GEOMETRY_NOT_MEASURABLE", "GEOMETRY_INVALID", "CRS_UNKNOWN",
    "CRS_NOT_METRIC", "UNIT_INCOMPATIBLE", "UNIT_UNDECLARED",
    "BRANCH_ERROR", "RESOURCE_LIMIT",
})

#: 编译期错误码（计算前拒绝）。
COMPILE_ERROR_CODES = frozenset({
    "UNKNOWN_NODE", "UNKNOWN_FUNCTION", "UNKNOWN_OPERATOR", "UNKNOWN_FIELD",
    "UNKNOWN_UNIT", "UNKNOWN_TIMEZONE", "BUDGET_EXCEEDED", "RESERVED_FIELD",
    "FIELD_EXISTS", "DUPLICATE_COLUMN", "CYCLIC_DEPENDENCY", "FORWARD_REFERENCE",
    "TYPE_MISMATCH", "UNIT_INCOMPATIBLE", "SELECTION_UNRELIABLE",
    "SELECTION_STALE", "CRS_UNKNOWN", "AMBIGUOUS_DATETIME", "INVALID_ARITY",
    "UNSAFE_INTEGER", "INVALID_LITERAL", "INVALID_PREDICATE", "EMPTY_PLAN",
})

#: 执行期错误码（typed，绝不裸抛）。
EXEC_ERROR_CODES = frozenset({
    "PLAN_NOT_FOUND", "PLAN_REJECTED", "STALE_PLAN", "SNAPSHOT_CHANGED",
    "INPUT_REF_MISSING", "PAYLOAD_UNSUPPORTED", "ROW_LIMIT_EXCEEDED",
    "STORE_FAILED", "WRITE_FAILED", "CANCELLED", "ABORTED_BY_ROW_ERROR",
    "PERSISTENCE_FAILED", "OWNER_MISMATCH",
})


class CompileError(Exception):
    """计算前拒绝（带稳定原因码、位置与修复建议）。"""

    def __init__(self, code: str, detail: str = "", *, position: int = -1,
                 hint: str = "") -> None:
        self.code = code if code in COMPILE_ERROR_CODES else "UNKNOWN_NODE"
        self.detail = str(detail)[:240]
        self.position = int(position)
        self.hint = str(hint)[:240]
        super().__init__(f"{self.code}: {self.detail}")

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "detail": self.detail,
                "position": self.position, "hint": self.hint}


class ExecError(Exception):
    """执行期 typed 失败。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code if code in EXEC_ERROR_CODES else "STORE_FAILED"
        self.detail = str(detail)[:240]
        super().__init__(f"{self.code}: {self.detail}")

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "detail": self.detail}


# ── 运行时值（轻量、不可变；预览与全量共用）──────────────────────────────

@dataclass(frozen=True, slots=True)
class DateTimeValue:
    """确定性时间点：ISO-8601（带偏移）+ 声明时区 + UTC epoch 秒 + 来源格式。"""

    iso: str
    timezone: str
    epoch_s: float
    source_format: str = ""


@dataclass(frozen=True, slots=True)
class Value:
    """带类型、单位与错误原因的行级值。

    ``type`` ∈ VALUE_TYPES；``error`` 类型的 ``error_code`` ∈ ROW_ERROR_CODES。
    布尔**绝不**视作整数（``is_int`` 显式排除 bool）；空串**绝不**自动变 0。
    """

    type: str
    value: Any = None
    unit: str = ""                 # canonical unit 名（"" = unknown）
    unit_status: str = "undeclared"  # declared | derived | undeclared
    derived_from: Tuple[Tuple[str, str], ...] = ()  # [(role, source), ...]
    error_code: str = ""
    precision: str = ""           # "" | bigint_as_string

    @staticmethod
    def null() -> "Value":
        return Value("null")

    @staticmethod
    def error(code: str) -> "Value":
        return Value("error", error_code=code if code in ROW_ERROR_CODES else "TYPE_MISMATCH")

    @staticmethod
    def number(v: Any, *, unit: str = "", unit_status: str = "undeclared",
               derived_from: Tuple[Tuple[str, str], ...] = ()) -> "Value":
        return Value("number", v, unit=unit, unit_status=unit_status,
                     derived_from=derived_from)

    @staticmethod
    def string(v: str, *, precision: str = "") -> "Value":
        return Value("string", v, precision=precision)

    @staticmethod
    def boolean(v: bool) -> "Value":
        return Value("boolean", bool(v))

    @staticmethod
    def datetime(v: DateTimeValue) -> "Value":
        return Value("datetime", v)

    @property
    def is_null(self) -> bool:
        return self.type == "null"

    @property
    def is_error(self) -> bool:
        return self.type == "error"

    @property
    def is_number(self) -> bool:
        return self.type == "number" and isinstance(self.value, (int, float)) \
            and not isinstance(self.value, bool)


# ── 类型化 IR（单 model + 自引用；canonical 只保留该 kind 相关字段）────────

class ExprNode(BaseModel):
    """闭合 AST 节点。字段全集固定，按 ``kind`` 由 ``validate_expr`` 收口。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["lit", "field", "unary", "binary", "call", "case"]
    dtype: str = ""                    # lit: LITERAL_DTYPES
    value: Any = None                  # lit: int/float/str/bool/None
    name: str = ""                     # field: 字段名；call: 函数名
    op: str = ""                       # unary/binary: 算子码
    arg: Optional["ExprNode"] = None   # unary
    left: Optional["ExprNode"] = None  # binary
    right: Optional["ExprNode"] = None  # binary
    args: Tuple["ExprNode", ...] = ()  # call
    branches: Tuple[Tuple["ExprNode", "ExprNode"], ...] = ()  # case: (cond, value)
    default: Optional["ExprNode"] = None  # case

    def canonical(self) -> Dict[str, Any]:
        """规范化表示（重排等价表达式不破坏哈希；只有语义字段进指纹）。"""
        if self.kind == "lit":
            return {"kind": "lit", "dtype": self.dtype, "value": self.value}
        if self.kind == "field":
            return {"kind": "field", "name": self.name}
        if self.kind == "unary":
            return {"kind": "unary", "op": self.op, "arg": self.arg.canonical()}
        if self.kind == "binary":
            # add/mul/and/or/eq/ne 是可交换算子：排序操作数使重排等价。
            out = [self.left.canonical(), self.right.canonical()]
            if self.op in ("add", "mul", "and", "or", "eq", "ne"):
                out.sort(key=lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True))
            return {"kind": "binary", "op": self.op, "left": out[0], "right": out[1]}
        if self.kind == "call":
            return {"kind": "call", "name": self.name,
                    "args": [a.canonical() for a in self.args]}
        return {"kind": "case",
                "branches": [[c.canonical(), v.canonical()] for c, v in self.branches],
                "default": self.default.canonical()}


ExprNode.model_rebuild()


def _canonical_blob(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def stable_fingerprint(data: Any) -> str:
    """任意 canonical 结构 → sha256 前 16 位（与准备域 plan_hash 同量级）。"""
    return hashlib.sha256(_canonical_blob(data).encode("utf-8")).hexdigest()[:16]


def expr_fingerprint(node: ExprNode) -> str:
    return stable_fingerprint(node.canonical())


# ── 列 / 范围 / 谓词 / 版本锚 / 计划 / 收据 ────────────────────────────────

class ComputeColumn(BaseModel):
    """一条计算列（目标字段名 + 闭合 IR + 可选输出单位声明）。"""

    model_config = ConfigDict(extra="forbid")
    name: str
    expression: ExprNode
    declared_unit: str = ""      # 输出单位（user-declared；仅披露，不推断）
    description: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        name = str(v or "").strip()
        if not name:
            raise ValueError("column name is required")
        if len(name) > MAX_FIELD_NAME_LEN:
            raise ValueError(f"column name exceeds {MAX_FIELD_NAME_LEN}")
        if name in RESERVED_FIELD_NAMES or name.startswith("__"):
            raise ValueError(f"reserved column name: {name!r}")
        return name

    def canonical(self) -> Dict[str, Any]:
        return {"name": self.name,
                "expression": self.expression.canonical(),
                "declared_unit": self.declared_unit}


class PredicateSpec(BaseModel):
    """受限谓词（closed op 子集；不是 SQL，不接受任意表达式）。"""

    model_config = ConfigDict(extra="forbid")
    field: str
    op: str
    value: Any = None
    values: Tuple[Any, ...] = ()

    @field_validator("op")
    @classmethod
    def _op(cls, v: str) -> str:
        if v not in PREDICATE_OPS:
            raise ValueError(f"unregistered predicate op: {v!r}")
        return v

    def canonical(self) -> Dict[str, Any]:
        return {"field": self.field, "op": self.op,
                "value": self.value,
                "values": list(self.values)}


class ScopeSpec(BaseModel):
    """作用范围。selection 必须完整、有稳定 ID 与 revision；截断一律拒绝。"""

    model_config = ConfigDict(extra="forbid")
    mode: str = "all_rows"
    selection_ref: str = ""
    selection_revision: str = ""
    selection_complete: bool = False
    selection_total: int = 0
    id_field: str = ""
    predicate: Optional[PredicateSpec] = None
    unselected_policy: str = "null"
    subset_export: bool = False

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in SCOPE_MODES:
            raise ValueError(f"unregistered scope mode: {v!r}")
        return v

    @field_validator("unselected_policy")
    @classmethod
    def _policy(cls, v: str) -> str:
        if v not in UNSELECTED_POLICIES:
            raise ValueError(f"unregistered unselected policy: {v!r}")
        return v

    def canonical(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "selection_ref": self.selection_ref,
            "selection_revision": self.selection_revision,
            "selection_complete": self.selection_complete,
            "selection_total": self.selection_total,
            "id_field": self.id_field,
            "predicate": self.predicate.canonical() if self.predicate else None,
            "unselected_policy": self.unselected_policy,
            "subset_export": self.subset_export,
        }


class InputAnchor(BaseModel):
    """输入内容/schema/CRS 版本锚（不含时间戳 —— 保证指纹可重放）。"""

    model_config = ConfigDict(extra="forbid")
    input_ref: str
    content_hash: str = ""
    schema_fingerprint: str = ""
    descriptor_fingerprint: str = ""
    row_count: int = 0
    crs: str = ""
    crs_source: str = ""
    available_fields: Tuple[str, ...] = ()

    def canonical(self) -> Dict[str, Any]:
        return {
            "input_ref": self.input_ref,
            "content_hash": self.content_hash,
            "schema_fingerprint": self.schema_fingerprint,
            "descriptor_fingerprint": self.descriptor_fingerprint,
            "row_count": self.row_count,
            "crs": self.crs,
            "crs_source": self.crs_source,
            "available_fields": sorted(self.available_fields),
        }


class ComputePlan(BaseModel):
    """可预览、可重放、幂等的字段计算计划。"""

    model_config = ConfigDict(extra="forbid")
    plan_id: str
    ir_version: str = IR_VERSION
    function_set_version: str = FUNCTION_SET_VERSION
    session_id: str = ""
    owner: str = ""
    input_ref: str = ""
    anchor: InputAnchor
    scope: ScopeSpec = Field(default_factory=ScopeSpec)
    columns: Tuple[ComputeColumn, ...] = ()
    field_units: Dict[str, str] = Field(default_factory=dict)  # user-declared
    timezone: str = "UTC"
    date_format: str = ""
    error_policy: str = "null"
    compute_fingerprint: str = ""
    state: str = "compiled"
    created_at: float = 0.0

    @field_validator("error_policy")
    @classmethod
    def _err(cls, v: str) -> str:
        if v not in ERROR_POLICIES:
            raise ValueError(f"unregistered error policy: {v!r}")
        return v

    @field_validator("state")
    @classmethod
    def _state(cls, v: str) -> str:
        if v not in PLAN_STATES:
            raise ValueError(f"unregistered plan state: {v!r}")
        return v

    def fingerprint_source(self) -> Dict[str, Any]:
        """指纹输入（**不含** plan_id / state / created_at / owner）。"""
        return {
            "ir_version": self.ir_version,
            "function_set_version": self.function_set_version,
            "session_id": self.session_id,
            "input_ref": self.input_ref,
            "anchor": self.anchor.canonical(),
            "scope": self.scope.canonical(),
            "columns": [c.canonical() for c in self.columns],
            "field_units": dict(sorted(self.field_units.items())),
            "timezone": self.timezone,
            "date_format": self.date_format,
            "error_policy": self.error_policy,
        }

    def compute_hash(self) -> str:
        return stable_fingerprint(self.fingerprint_source())

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id[:64],
            "ir_version": self.ir_version,
            "function_set_version": self.function_set_version,
            "session_id": self.session_id[:128],
            "owner": self.owner[:128],
            "input_ref": self.input_ref[:200],
            "anchor": self.anchor.model_dump(mode="json"),
            "scope": self.scope.model_dump(mode="json"),
            "columns": [{"name": c.name,
                         "expression": c.expression.canonical(),
                         "declared_unit": c.declared_unit,
                         "description": c.description[:200]}
                        for c in self.columns],
            "field_units": self.field_units,
            "timezone": self.timezone,
            "date_format": self.date_format,
            "error_policy": self.error_policy,
            "compute_fingerprint": self.compute_fingerprint,
            "state": self.state,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ComputePlan"]:
        if not isinstance(data, dict):
            return None
        try:
            return cls.model_validate({
                "plan_id": data.get("plan_id"),
                "ir_version": data.get("ir_version") or IR_VERSION,
                "function_set_version": data.get("function_set_version") or FUNCTION_SET_VERSION,
                "session_id": data.get("session_id") or "",
                "owner": data.get("owner") or "",
                "input_ref": data.get("input_ref") or "",
                "anchor": data.get("anchor") or {},
                "scope": data.get("scope") or {},
                "columns": [{"name": c.get("name"),
                             "expression": c.get("expression"),
                             "declared_unit": c.get("declared_unit") or "",
                             "description": c.get("description") or ""}
                            for c in (data.get("columns") or [])],
                "field_units": data.get("field_units") or {},
                "timezone": data.get("timezone") or "UTC",
                "date_format": data.get("date_format") or "",
                "error_policy": data.get("error_policy") or "null",
                "compute_fingerprint": data.get("compute_fingerprint") or "",
                "state": data.get("state") or "compiled",
                "created_at": float(data.get("created_at") or 0.0),
            })
        except Exception:  # noqa: BLE001 — 坏记录跳过（append-only 容忍）
            return None


class ColumnResult(BaseModel):
    """单列结果对账（有界；错误示例有限）。"""

    model_config = ConfigDict(extra="forbid")
    name: str
    dtype: str = ""
    unit: str = ""
    unit_status: str = "undeclared"
    measurement_kind: str = ""
    derived_from: Dict[str, str] = Field(default_factory=dict)
    dependencies: Tuple[str, ...] = ()
    expression: Dict[str, Any] = Field(default_factory=dict)
    computed: int = 0
    nulls: int = 0
    errors: int = 0
    error_samples: Tuple[Dict[str, Any], ...] = ()


class ComputeReceipt(BaseModel):
    """执行收据（幂等键 = compute_fingerprint + input_ref + content_hash）。"""

    model_config = ConfigDict(extra="forbid")
    version: int = RECEIPT_VERSION
    plan_id: str
    compute_fingerprint: str
    input_ref: str
    output_ref: str
    input_content_hash: str = ""
    output_content_hash: str = ""
    row_count: int = 0
    computed_rows: int = 0
    selected_rows: int = 0
    skipped_rows: int = 0
    null_count: int = 0
    error_count: int = 0
    columns: Tuple[ColumnResult, ...] = ()
    scope: Dict[str, Any] = Field(default_factory=dict)
    geometry: Dict[str, Any] = Field(default_factory=dict)
    formula_summary: Tuple[str, ...] = ()
    unit_summary: Dict[str, Any] = Field(default_factory=dict)
    precision_policy: str = ""
    persistence: str = "session_ref"
    descriptor_minted: bool = False
    degraded: Tuple[str, ...] = ()
    #: 发布闸门：False = 草稿（store/登记之前写入，绝不对外可见，也不参与幂等）。
    published: bool = True
    created_at: float = 0.0

    def to_bounded_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "IR_VERSION", "FUNCTION_SET_VERSION", "RECEIPT_VERSION",
    "MAX_EXPR_NODES", "MAX_EXPR_DEPTH", "MAX_LITERAL_STRING",
    "MAX_RESULT_STRING", "MAX_STRING_INPUT", "MAX_FIELD_NAME_LEN",
    "MAX_CALL_ARGS", "MAX_CASE_BRANCHES", "MAX_COLUMNS", "MAX_ROW_OPS",
    "MAX_ROWS", "MAX_BATCH_ROWS", "MAX_PREVIEW_ROWS", "MAX_ERROR_SAMPLES",
    "SAFE_INTEGER_MAX", "RESERVED_FIELD_NAMES", "NODE_KINDS", "VALUE_TYPES",
    "LITERAL_DTYPES", "UNARY_OPS", "BINARY_OPS", "SCOPE_MODES",
    "UNSELECTED_POLICIES", "ERROR_POLICIES", "PREDICATE_OPS", "PLAN_STATES",
    "ROW_ERROR_CODES", "COMPILE_ERROR_CODES", "EXEC_ERROR_CODES",
    "CompileError", "ExecError", "DateTimeValue", "Value", "ExprNode",
    "ComputeColumn", "PredicateSpec", "ScopeSpec", "InputAnchor",
    "ComputePlan", "ColumnResult", "ComputeReceipt",
    "stable_fingerprint", "expr_fingerprint",
]
