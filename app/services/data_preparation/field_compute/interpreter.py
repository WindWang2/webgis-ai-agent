"""确定性行/批解释器（G19 / WP03–WP05）—— 预览与全量执行**同一核心**。

纪律：
- 严格类型：``bool`` 不是整数，空串不是 0，``null`` 不隐式变值；显式
  ``to_number`` / ``to_integer`` 才转换，失败保留行级原因；
- ``if`` / ``case`` / ``coalesce`` / ``and`` / ``or`` 惰性求值，未选分支
  **不求值**（不会凭空制造除零）；
- 除零/溢出/非有限 → 行级 ``error`` 或按 ``error_policy=abort`` 终止整次；
  不偷偷变零、不裁剪、不丢行；NaN/Infinity 绝不写为合法 JSON 数字；
- 大整数 >2^53-1 不静默舍入：以文本写出并在 receipt 披露
  ``precision_policy=bigint_as_string``；
- 单位传播用编译期 ``static_unit`` 缓存；几何只经 ``geometry.py`` 的 CRS 闸；
- CPU 密集部分为纯同步函数，由 service 层 ``asyncio.to_thread`` offload，
  不长期堵塞事件循环。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.data_preparation.field_compute.contracts import (
    MAX_BATCH_ROWS, MAX_ERROR_SAMPLES, MAX_RESULT_STRING, MAX_ROW_OPS,
    MAX_STRING_INPUT, ROW_ERROR_CODES, SAFE_INTEGER_MAX, ComputeColumn,
    DateTimeValue, ExprNode, Value,
)
from app.services.data_preparation.field_compute.functions import (
    DATETIME_PARTS, FUNCTIONS, UnitInfo, static_unit,
)
from app.services.data_preparation.field_compute.geometry import (
    GeometryBlocked, measure_geometry,
)
from app.services.data_preparation.field_compute.units import (
    UnitConversionError, convert_value,
)


class RowError(Exception):
    """行级计算失败（stable code ∈ ROW_ERROR_CODES）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code if code in ROW_ERROR_CODES else "TYPE_MISMATCH"
        self.detail = detail[:200]
        super().__init__(f"{self.code}: {detail}")


class RowAbort(Exception):
    """error_policy=abort 时整次终止。"""

    def __init__(self, code: str, row_index: int, column: str) -> None:
        self.code, self.row_index, self.column = code, row_index, column
        super().__init__(f"{code} at row {row_index} column {column}")


ROW_EXCEPTIONS = (RowError, GeometryBlocked, UnitConversionError)

#: CRS/几何资格阻断是「列级不可用」，不是数据行算错；即使 error_policy=abort
#: 也只让该列降级，不终止整次，保证不依赖几何的其他列仍可执行。
GEOMETRY_BLOCK_CODES = frozenset({
    "CRS_UNKNOWN", "CRS_NOT_METRIC", "GEOMETRY_NOT_MEASURABLE",
    "GEOMETRY_INVALID",
})


@dataclass(slots=True)
class EvalConfig:
    field_units: Dict[str, str] = dc_field(default_factory=dict)
    column_units: Dict[str, str] = dc_field(default_factory=dict)
    timezone: str = "UTC"
    date_format: str = ""
    error_policy: str = "null"
    geometry_mode: str = ""
    geometry_block_reason: str = ""
    crs: str = ""
    unselected_policy: str = "null"
    max_ops: int = MAX_ROW_OPS


@dataclass(slots=True)
class ColumnStats:
    computed: int = 0
    nulls: int = 0
    errors: int = 0
    samples: List[Dict[str, Any]] = dc_field(default_factory=list)

    def add_error(self, row_index: int, column: str, code: str) -> None:
        self.errors += 1
        if len(self.samples) < MAX_ERROR_SAMPLES:
            self.samples.append({"row": row_index, "column": column, "code": code})


@dataclass(slots=True)
class BatchResult:
    """一批行的结果（新属性 dict；原件深拷贝由 service 负责）。"""

    rows: List[Dict[str, Any]] = dc_field(default_factory=list)
    stats: Dict[str, ColumnStats] = dc_field(default_factory=dict)
    precision_policy: str = ""
    geometry_evidence: Dict[str, Any] = dc_field(default_factory=dict)
    #: 无任何列级错误且至少一列产出非 null 值的行数（收据 computed_rows 真值）。
    computed_rows: int = 0


@dataclass(slots=True)
class _RowState:
    props: Dict[str, Any]
    geometry: Optional[Dict[str, Any]]
    config: EvalConfig
    unit_cache: Dict[int, UnitInfo]
    ops: int = 0


# ── 值转换 ─────────────────────────────────────────────────────────────────

def from_python(raw: Any) -> Value:
    """JSON/GeoJSON 属性值 → 严格 typed Value（绝不隐式提升）。"""
    if raw is None:
        return Value.null()
    if isinstance(raw, bool):
        return Value.boolean(raw)
    if isinstance(raw, int):
        return Value.number(raw)
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise RowError("NON_FINITE", "NaN/Infinity 不是合法 JSON 数字")
        return Value.number(raw)
    if isinstance(raw, str):
        if len(raw) > MAX_STRING_INPUT:
            raise RowError("STRING_TOO_LONG", f"输入字符串 > {MAX_STRING_INPUT}")
        return Value.string(raw)
    raise RowError("TYPE_MISMATCH", f"不支持的属性值类型 {type(raw).__name__}")


def json_value(v: Value) -> Tuple[Any, str]:
    """Value → (可安全写入 JSON 的值, precision 标记)。"""
    if v.type in ("null", "error"):
        return None, ""
    if v.type == "number":
        x = v.value
        if isinstance(x, bool):  # pragma: no cover — Value.number 已排除
            raise RowError("TYPE_MISMATCH", "bool 不是 number")
        if isinstance(x, int):
            if abs(x) > SAFE_INTEGER_MAX:
                return str(x), "bigint_as_string"
            return x, ""
        if isinstance(x, float):
            if not math.isfinite(x):
                raise RowError("NON_FINITE", "结果非有限")
            return x, ""
        raise RowError("TYPE_MISMATCH", "number 值类型异常")
    if v.type == "boolean":
        return bool(v.value), ""
    if v.type == "string":
        s = str(v.value)
        if len(s) > MAX_RESULT_STRING:
            raise RowError("STRING_TOO_LONG", f"结果字符串 > {MAX_RESULT_STRING}")
        return s, v.precision
    if v.type == "datetime" and isinstance(v.value, DateTimeValue):
        return v.value.iso, ""
    raise RowError("TYPE_MISMATCH", f"无法序列化的值类型 {v.type}")


# ── 时区 / 日期（不读机器当前时钟）─────────────────────────────────────────

_TZ_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")
_TZ_IANA_RE = re.compile(r"^[A-Za-z_]+/[A-Za-z0-9_+\-]+$")


def is_supported_timezone(tz: str) -> bool:
    """时区必须显式且可解析；IANA 名需本机 zoneinfo 可用（否则拒，不猜本地时区）。"""
    t = str(tz or "").strip()
    if t in ("UTC", "Z"):
        return True
    if _TZ_OFFSET_RE.match(t):
        return True
    if _TZ_IANA_RE.match(t):
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(t)  # noqa: B018 — 可用性探测
            return True
        except Exception:  # noqa: BLE001
            return False
    return False


def _tz_object(tz: str):
    t = str(tz or "UTC").strip()
    if t in ("UTC", "Z"):
        return timezone.utc
    m = _TZ_OFFSET_RE.match(t)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
        return timezone(sign * delta)
    from zoneinfo import ZoneInfo
    return ZoneInfo(t)


def parse_datetime_value(s: str, fmt: str, tz: str) -> DateTimeValue:
    if not isinstance(s, str) or not s.strip():
        raise RowError("INVALID_DATE", "空时间字符串")
    if not fmt or not is_supported_timezone(tz):
        raise RowError("INVALID_DATE", "格式/时区未声明或不可用")
    try:
        # 显式时区在下一行通过 replace(tzinfo=...) 绑定，绝不依赖机器本地时区。
        naive = datetime.strptime(s, fmt)  # noqa: DTZ007
    except (ValueError, TypeError) as exc:
        raise RowError("INVALID_DATE", f"按 {fmt!r} 解析失败：{exc}"[:160]) from exc
    aware = naive.replace(tzinfo=_tz_object(tz))
    return DateTimeValue(iso=aware.isoformat(), timezone=tz,
                         epoch_s=aware.timestamp(), source_format=fmt)


def datetime_part(dt: DateTimeValue, part: str, tz: str) -> Any:
    local = datetime.fromtimestamp(dt.epoch_s, tz=_tz_object(tz))
    if part == "year":
        return local.year
    if part == "month":
        return local.month
    if part == "day":
        return local.day
    if part == "hour":
        return local.hour
    if part == "minute":
        return local.minute
    if part == "weekday":
        return local.isoweekday()      # 1=Mon … 7=Sun
    if part == "date":
        return local.year * 10000 + local.month * 100 + local.day
    raise RowError("INVALID_DATE", f"unknown datetime part {part!r}")


# ── 求值核心 ───────────────────────────────────────────────────────────────

def _unit_of(node: ExprNode, st: _RowState) -> UnitInfo:
    cached = st.unit_cache.get(id(node))
    if cached is None:
        cached = static_unit(node, st.config.field_units,
                             declared_column_units=st.config.column_units)
        st.unit_cache[id(node)] = cached
    return cached


def _num(v: Value) -> Any:
    """严格取数值；null 交由调用方处理（不隐式变 0）。"""
    if v.is_null:
        return None
    if not v.is_number:
        raise RowError("TYPE_MISMATCH", f"{v.type} 不能当数值（bool/空串不自动转换）")
    return v.value


def _children(node: ExprNode) -> Tuple[ExprNode, ...]:
    if node.kind == "unary" and node.arg is not None:
        return (node.arg,)
    if node.kind == "binary":
        return tuple(x for x in (node.left, node.right) if x is not None)
    if node.kind == "call":
        return node.args
    if node.kind == "case":
        out = [x for pair in node.branches for x in pair]
        if node.default is not None:
            out.append(node.default)
        return tuple(out)
    return ()


def build_unit_cache(columns: Sequence[ComputeColumn], config: EvalConfig
                     ) -> Dict[int, UnitInfo]:
    """编译期单位投影一次缓存（避免每行重复推导）。"""
    cache: Dict[int, UnitInfo] = {}

    def walk(node: ExprNode) -> None:
        cache[id(node)] = static_unit(node, config.field_units,
                                      declared_column_units=config.column_units)
        for c in _children(node):
            walk(c)

    for col in columns:
        walk(col.expression)
    return cache


def eval_node(node: ExprNode, st: _RowState) -> Value:
    st.ops += 1
    if st.ops > st.config.max_ops:
        raise RowError("RESOURCE_LIMIT", f"单行操作数 > {st.config.max_ops}")
    kind = node.kind
    if kind == "lit":
        if node.dtype == "null":
            return Value.null()
        if node.dtype == "number":
            return Value.number(node.value)
        if node.dtype == "string":
            return Value.string(str(node.value))
        return Value.boolean(bool(node.value))
    if kind == "field":
        if node.name not in st.props:
            return Value.null()          # 行级缺字段 = null（不丢行）
        raw = st.props[node.name]
        if isinstance(raw, Value):       # 前序计算列
            return raw
        return from_python(raw)
    if kind == "unary":
        arg = eval_node(node.arg, st)
        if node.op == "neg":
            if arg.is_null:
                return Value.null()
            x = _num(arg)
            u = _unit_of(node, st)
            return Value.number(-x, unit=u.unit, unit_status=u.status,
                                derived_from=u.derived_from)
        if arg.is_null:
            return Value.boolean(False)
        if arg.type != "boolean":
            raise RowError("TYPE_MISMATCH", "not 只接受布尔")
        return Value.boolean(not arg.value)
    if kind == "binary":
        return _eval_binary(node, st)
    if kind == "call":
        return _eval_call(node, st)
    # case
    for cond, val in node.branches:
        c = eval_node(cond, st)
        if c.type == "boolean" and c.value is True:
            return eval_node(val, st)
    return eval_node(node.default, st)


def _eval_binary(node: ExprNode, st: _RowState) -> Value:
    op = node.op
    if op in ("and", "or"):
        left = eval_node(node.left, st)
        if left.is_error:
            return left
        if op == "and" and left.type == "boolean" and left.value is False:
            return Value.boolean(False)
        if op == "or" and left.type == "boolean" and left.value is True:
            return Value.boolean(True)
        right = eval_node(node.right, st)
        if right.is_error:
            return right
        if op == "and" and right.type == "boolean" and right.value is False:
            return Value.boolean(False)
        if op == "or" and right.type == "boolean" and right.value is True:
            return Value.boolean(True)
        if left.is_null or right.is_null:
            return Value.null()
        if left.type != "boolean" or right.type != "boolean":
            raise RowError("TYPE_MISMATCH", "逻辑算子只接受布尔")
        return Value.boolean(left.value and right.value if op == "and"
                             else left.value or right.value)

    left = eval_node(node.left, st)
    right = eval_node(node.right, st)
    if left.is_error:
        return left
    if right.is_error:
        return right
    if op in ("eq", "ne", "lt", "le", "gt", "ge"):
        return _compare(op, left, right)
    if left.is_null or right.is_null:
        return Value.null()
    x, y = _num(left), _num(right)
    info = _unit_of(node, st)
    if op in ("add", "sub"):
        if left.unit and right.unit and left.unit != right.unit:
            raise RowError("UNIT_INCOMPATIBLE",
                           "加减两侧单位不同 —— 请先用 convert_unit() 显式换算")
    if op == "add":
        r = x + y
    elif op == "sub":
        r = x - y
    elif op == "mul":
        r = x * y
    elif op == "div":
        if y == 0:
            raise RowError("DIVISION_BY_ZERO", "分母为 0")
        r = x / y
    elif op == "mod":
        if y == 0:
            raise RowError("DIVISION_BY_ZERO", "取模分母为 0")
        r = x % y
    else:  # pragma: no cover — 词表已收口
        raise RowError("TYPE_MISMATCH", f"unknown op {op!r}")
    if isinstance(r, float) and not math.isfinite(r):
        raise RowError("NON_FINITE", "非有限结果（溢出/除零）")
    if isinstance(r, int) and abs(r) > 10 ** 300:
        raise RowError("NUMERIC_OVERFLOW", "整数结果过大")
    return Value.number(r, unit=info.unit, unit_status=info.status,
                        derived_from=info.derived_from)


def _compare(op: str, a: Value, b: Value) -> Value:
    if a.is_null or b.is_null:
        return Value.null()
    if a.type != b.type:
        raise RowError("TYPE_MISMATCH", f"比较两侧类型不同：{a.type} vs {b.type}")
    if a.type == "datetime":
        x, y = a.value.epoch_s, b.value.epoch_s
    elif a.type == "boolean":
        x, y = bool(a.value), bool(b.value)
    elif a.type == "number":
        x, y = a.value, b.value
    else:
        x, y = str(a.value), str(b.value)
    if op == "eq":
        return Value.boolean(x == y)
    if op == "ne":
        return Value.boolean(x != y)
    if op == "lt":
        return Value.boolean(x < y)
    if op == "le":
        return Value.boolean(x <= y)
    if op == "gt":
        return Value.boolean(x > y)
    return Value.boolean(x >= y)


def _eval_call(node: ExprNode, st: _RowState) -> Value:
    name = node.name
    spec = FUNCTIONS.get(name)
    if spec is None:  # pragma: no cover — 编译期已拒
        raise RowError("TYPE_MISMATCH", f"unregistered function {name!r}")
    args = node.args

    # ── 惰性族：未选分支不求值 ────────────────────────────────────────
    if name == "if":
        cond = eval_node(args[0], st)
        if cond.is_null:
            return Value.null()
        if cond.type != "boolean":
            raise RowError("TYPE_MISMATCH", "if 条件必须是布尔")
        return eval_node(args[1] if cond.value else args[2], st)
    if name == "coalesce":
        for a in args:
            try:
                v = eval_node(a, st)
            except ROW_EXCEPTIONS:
                continue
            if not v.is_null and not v.is_error:
                return v
        return Value.null()
    if name == "is_null":
        try:
            v = eval_node(args[0], st)
        except ROW_EXCEPTIONS:
            return Value.boolean(False)
        return Value.boolean(v.is_null)

    vals = [eval_node(a, st) for a in args]
    for v in vals:
        if v.is_error:
            return v
    # 各函数自行声明 null 行为（见 FunctionSpec.null_behavior）；此处不做
    # 「任一参数 null 就整行 null」的隐式折叠 —— coalesce / is_null 等需要
    # 区分 null 与 error，语义必须显式。

    # ── 几何 ──────────────────────────────────────────────────────────
    if spec.geometry:
        try:
            value, ev = measure_geometry(
                st.geometry, name, st.config.geometry_mode,
                st.config.geometry_block_reason, st.config.crs)
        except GeometryBlocked as exc:
            raise RowError(exc.code, exc.detail) from exc
        st_unit = _unit_of(node, st)
        return Value.number(value, unit=st_unit.unit, unit_status=st_unit.status,
                            derived_from=st_unit.derived_from)

    # ── 数值 ──────────────────────────────────────────────────────────
    if name == "abs":
        if vals[0].is_null:
            return Value.null()
        x = _num(vals[0])
        return Value.number(abs(x), unit=vals[0].unit,
                            unit_status=vals[0].unit_status)
    if name in ("floor", "ceil"):
        if vals[0].is_null:
            return Value.null()
        x = _num(vals[0])
        return Value.number(math.floor(x) if name == "floor" else math.ceil(x),
                            unit=vals[0].unit, unit_status=vals[0].unit_status)
    if name == "round":
        if vals[0].is_null:
            return Value.null()
        digits = 0
        if len(vals) > 1 and not vals[1].is_null:
            d = _num(vals[1])
            if not float(d).is_integer() or not 0 <= d <= 9:
                raise RowError("CONVERSION_FAILED", "digits 必须是 0–9 的整数")
            digits = int(d)
        x = _num(vals[0])
        q = Decimal(1).scaleb(-digits)
        r = float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))
        return Value.number(r, unit=vals[0].unit,
                            unit_status=vals[0].unit_status)
    if name == "sqrt":
        if vals[0].is_null:
            return Value.null()
        x = _num(vals[0])
        if x < 0:
            raise RowError("NON_FINITE", "负数开平方")
        return Value.number(math.sqrt(x))
    if name in ("min", "max"):
        if vals[0].is_null or vals[1].is_null:
            return Value.null()
        a, b = _num(vals[0]), _num(vals[1])
        if vals[0].unit and vals[1].unit and vals[0].unit != vals[1].unit:
            raise RowError("UNIT_INCOMPATIBLE", "min/max 两侧单位不同，先换算")
        r = min(a, b) if name == "min" else max(a, b)
        return Value.number(r, unit=vals[0].unit,
                            unit_status=vals[0].unit_status)

    # ── 文本 / 代码 ────────────────────────────────────────────────────
    if name in ("trim", "upper", "lower", "str_length"):
        v = vals[0]
        if v.is_null:
            return Value.null()
        if v.type != "string":
            raise RowError("TYPE_MISMATCH", f"{name} 需要文本")
        s = str(v.value)
        if name == "trim":
            return Value.string(s.strip())
        if name == "upper":
            return Value.string(s.upper())
        if name == "lower":
            return Value.string(s.lower())
        return Value.number(len(s))
    if name == "concat":
        if any(v.is_null for v in vals):
            return Value.null()   # 不把 null 当空串；需显式 coalesce
        if any(v.type != "string" for v in vals):
            raise RowError("TYPE_MISMATCH", "concat 只接受文本；数值请用 to_string")
        out = "".join(str(v.value) for v in vals)
        if len(out) > MAX_RESULT_STRING:
            raise RowError("STRING_TOO_LONG", "拼接结果超长")
        return Value.string(out)
    if name == "substring":
        v = vals[0]
        if v.is_null:
            return Value.null()
        if v.type != "string":
            raise RowError("TYPE_MISMATCH", "substring 需要文本")
        start = _num(vals[1])
        if start is None:
            return Value.null()
        length: Optional[int] = None
        if len(vals) > 2 and not vals[2].is_null:
            length = int(_num(vals[2]))
        s = str(v.value)
        i = max(0, int(start))
        out = s[i:] if length is None else s[i:i + max(0, length)]
        if len(out) > MAX_RESULT_STRING:
            raise RowError("STRING_TOO_LONG", "子串超长")
        return Value.string(out)
    if name == "pad_left":
        v = vals[0]
        if v.is_null:
            return Value.null()
        if v.type != "string":
            raise RowError("TYPE_MISMATCH", "pad_left 需要文本（代码类字段按文本处理）")
        width = int(_num(vals[1]) or 0)
        if width < 0 or width > 128:
            raise RowError("RESOURCE_LIMIT", "pad_left width 越界 (0–128)")
        ch = str(vals[2].value)[:1] if len(vals) > 2 and not vals[2].is_null else "0"
        if not ch:
            raise RowError("CONVERSION_FAILED", "填充字符不能为空")
        return Value.string(str(v.value).rjust(width, ch))
    if name == "to_string":
        v = vals[0]
        if v.is_null:
            return Value.null()
        if v.type == "number":
            if isinstance(v.value, int) and abs(v.value) > SAFE_INTEGER_MAX:
                return Value.string(str(v.value), precision="bigint_as_string")
            return Value.string(str(v.value))
        if v.type == "boolean":
            return Value.string("true" if v.value else "false")
        if v.type == "datetime":
            return Value.string(v.value.iso)
        return Value.string(str(v.value))
    if name in ("to_number", "to_integer"):
        v = vals[0]
        if v.is_null:
            return Value.null()
        if v.type != "string":
            raise RowError("TYPE_MISMATCH", f"{name} 只接受文本（bool 不自动转数值）")
        s = str(v.value).strip()
        if s == "":
            raise RowError("EMPTY_STRING_NOT_ZERO", "空串不是 0（显式 coalesce 或清洗源数据）")
        try:
            if name == "to_integer":
                if not re.fullmatch(r"[+-]?\d+", s):
                    raise ValueError("not an integer string")
                return Value.number(int(s, 10))
            out = float(s)
        except (ValueError, TypeError) as exc:
            raise RowError("CONVERSION_FAILED", str(exc)[:120]) from exc
        if not math.isfinite(out):
            raise RowError("CONVERSION_FAILED", "非有限数值")
        return Value.number(out)

    # ── 日期时间 ───────────────────────────────────────────────────────
    if name == "parse_datetime":
        v = vals[0]
        if v.is_null:
            return Value.null()
        if v.type != "string":
            raise RowError("TYPE_MISMATCH", "parse_datetime 需要文本")
        fmt = str(args[1].value) if len(args) == 3 else st.config.date_format
        tz = str(args[2].value) if len(args) == 3 else st.config.timezone
        return Value.datetime(parse_datetime_value(str(v.value), fmt, tz))
    if name == "datetime_part":
        v = vals[0]
        if v.is_null:
            return Value.null()
        if v.type != "datetime":
            raise RowError("TYPE_MISMATCH", "datetime_part 需要 parse_datetime 结果")
        part = str(args[1].value) if args[1].kind == "lit" else str(vals[1].value)
        if part not in DATETIME_PARTS:
            raise RowError("INVALID_DATE", "unknown part")
        # 必须用时间点自带的时区（parse_datetime 的显式 tz 参数），而不是计划级
        # 默认 timezone；否则 UTC 计划 + +08:00 公式跨 UTC 边界会取错月份。
        return Value.number(datetime_part(v.value, part, v.value.timezone))
    if name == "date_diff_days":
        a, b = vals[0], vals[1]
        if a.is_null or b.is_null:
            return Value.null()
        if a.type != "datetime" or b.type != "datetime":
            raise RowError("TYPE_MISMATCH", "date_diff_days 需要两个时间点")
        return Value.number((a.value.epoch_s - b.value.epoch_s) / 86400.0)

    # ── 单位 ──────────────────────────────────────────────────────────
    if name == "convert_unit":
        v = vals[0]
        if v.is_null:
            return Value.null()
        x = _num(v)
        from_unit = str(args[1].value)
        to_unit = str(args[2].value)
        try:
            converted, _ev = convert_value(x, from_unit, to_unit)
        except UnitConversionError as exc:
            raise RowError(exc.code, exc.detail) from exc
        info = _unit_of(node, st)
        return Value.number(converted, unit=info.unit, unit_status=info.status,
                            derived_from=info.derived_from)

    raise RowError("TYPE_MISMATCH", f"unimplemented function {name!r}")  # pragma: no cover


# ── 批执行 ─────────────────────────────────────────────────────────────────

def evaluate_rows(
    rows: Sequence[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]],
    columns: Sequence[ComputeColumn],
    selected: Optional[Sequence[bool]],
    config: EvalConfig,
    *,
    batch_size: int = MAX_BATCH_ROWS,
) -> BatchResult:
    """同一核心解释任意批大小；返回**相同**结果（批大小不变性）。"""
    if batch_size < 1:
        batch_size = 1
    cache = build_unit_cache(columns, config)
    result = BatchResult(
        stats={c.name: ColumnStats() for c in columns},
        geometry_evidence={
            "crs": config.crs,
            "mode": config.geometry_mode,
            "block_reason": config.geometry_block_reason or None,
            "functions": sorted({n for c in columns
                                 for n in _geometry_functions(c.expression)}),
        },
    )
    precision = ""
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        for offset, (props, geom) in enumerate(chunk):
            row_index = start + offset
            is_selected = True if selected is None else bool(selected[row_index])
            # working 保留 typed Value（列间依赖携带单位）；out_row 是可写 JSON。
            working: Dict[str, Any] = dict(props)
            out_row: Dict[str, Any] = dict(props)
            st = _RowState(props=working, geometry=geom, config=config,
                           unit_cache=cache, ops=0)
            if not is_selected:
                for c in columns:
                    if config.unselected_policy == "absent":
                        out_row.pop(c.name, None)
                    else:
                        out_row[c.name] = None
                result.rows.append(out_row)
                continue
            row_has_error = False
            row_has_value = False
            for c in columns:
                stats = result.stats[c.name]
                try:
                    v = eval_node(c.expression, st)
                    out, prec = json_value(v)
                    if prec:
                        precision = prec
                    if v.is_null:
                        stats.nulls += 1
                    else:
                        stats.computed += 1
                        row_has_value = True
                    working[c.name] = v
                    out_row[c.name] = out
                except RowAbort:
                    raise
                except ROW_EXCEPTIONS as exc:
                    code = str(getattr(exc, "code", "TYPE_MISMATCH"))
                    stats.add_error(row_index, c.name, code)
                    row_has_error = True
                    # 几何/CRS 资格阻断是列级不可用，不终止整次（其他列仍可执行）。
                    if (config.error_policy == "abort"
                            and code not in GEOMETRY_BLOCK_CODES):
                        raise RowAbort(code, row_index, c.name) from exc
                    working[c.name] = Value.null()
                    out_row[c.name] = None
            if not row_has_error and row_has_value:
                result.computed_rows += 1
            result.rows.append(out_row)
    result.precision_policy = precision
    return result


def _geometry_functions(node: ExprNode) -> List[str]:
    out: List[str] = []
    if node.kind == "call":
        spec = FUNCTIONS.get(node.name)
        if spec is not None and spec.geometry:
            out.append(node.name)
    for c in _children(node):
        out.extend(_geometry_functions(c))
    return out


__all__ = [
    "RowError", "RowAbort", "ROW_EXCEPTIONS", "EvalConfig", "ColumnStats",
    "BatchResult", "from_python", "json_value", "is_supported_timezone",
    "parse_datetime_value", "datetime_part", "build_unit_cache",
    "eval_node", "evaluate_rows",
]
