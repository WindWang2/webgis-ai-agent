"""首批函数契约与静态类型/单位推导（G19 / WP02、WP05）。

``FUNCTIONS`` 是**唯一**函数词表：解析器校验、解释器分派、UI 帮助、Pi 工具
的能力描述、文档全部由它驱动（契约驱动文档，不手写第二份清单）。

纪律：
- ``arg_types`` 用 ``VALUE_TYPES`` 的值或 ``any``；``any`` 表示输入类型在
  运行时按实际值严格判定（**绝不**把 bool/空串偷偷提升为 number）；
- ``lazy=True`` 的函数由解释器短路求值，未选分支**不求值**（不制造除零）；
- ``geometry=True`` 的函数必须经 CRS 资格闸，且输出单位固定；
- ``unit_rule`` 是编译期单位传播的唯一依据；未知组合保留 ``undeclared``，
  不编造科学资格。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.lib.gis.measurement import CANONICAL_UNITS, UnitDimension

from app.services.data_preparation.field_compute.contracts import (
    ExprNode, expr_fingerprint,
)


@dataclass(frozen=True, slots=True)
class FunctionSpec:
    """单函数契约（输入/输出类型、null 行为、单位规则、资源上限）。"""

    name: str
    family: str
    arg_types: Tuple[str, ...]
    result_type: str                 # "number"/"string"/"boolean"/"datetime"/"branch"/"same_as_first"
    null_behavior: str
    unit_rule: str                   # preserve | none | fixed:<unit> | convert | branch
    resource: str
    min_args: int = 1
    max_args: int = 1
    lazy: bool = False
    geometry: bool = False
    requires_datetime_context: bool = False
    help_zh: str = ""


def _spec(*args: Any, **kwargs: Any) -> FunctionSpec:
    return FunctionSpec(*args, **kwargs)


#: 首批函数清单（FUNCTION_SET_VERSION = g19-fc-1.0）。非目标：UDF / 正则 /
#: 窗口函数 / SQL 方言 / 任意脚本。新增函数必须同步升版本并补边界测试。
FUNCTIONS: Dict[str, FunctionSpec] = {
    s.name: s for s in (
        # ── 数值 ──────────────────────────────────────────────────────────
        _spec("abs", "number", ("number",), "number",
              "null→null；非数值→TYPE_MISMATCH", "preserve", "O(1)",
              help_zh="绝对值"),
        _spec("round", "number", ("number", "number"), "number",
              "null→null；digits 必须 0–9 的整数", "preserve", "O(1)",
              min_args=1, max_args=2, help_zh="四舍五入（可指定小数位）"),
        _spec("floor", "number", ("number",), "number",
              "null→null", "preserve", "O(1)", help_zh="向下取整"),
        _spec("ceil", "number", ("number",), "number",
              "null→null", "preserve", "O(1)", help_zh="向上取整"),
        _spec("min", "number", ("number", "number"), "number",
              "null→null；两侧须同量纲", "preserve", "O(1)",
              min_args=2, max_args=2, help_zh="较小值"),
        _spec("max", "number", ("number", "number"), "number",
              "null→null；两侧须同量纲", "preserve", "O(1)",
              min_args=2, max_args=2, help_zh="较大值"),
        _spec("sqrt", "number", ("number",), "number",
              "null→null；负数→NON_FINITE", "none", "O(1)",
              help_zh="平方根（负数产生行级错误）"),
        # ── 条件（惰性）────────────────────────────────────────────────────
        _spec("if", "condition", ("boolean", "any", "any"), "branch",
              "未选分支不求值", "branch", "O(1)", min_args=3, max_args=3,
              lazy=True, help_zh="条件分支（只求值被选中的分支）"),
        _spec("coalesce", "condition", ("any", "any"), "same_as_first",
              "返回第一个非 null 且非 error 的值", "branch", "O(n args)",
              min_args=2, max_args=4, lazy=True,
              help_zh="取第一个非空值（显式处理 null，不隐式造 0）"),
        _spec("is_null", "condition", ("any",), "boolean",
              "null→true；error→false", "none", "O(1)",
              help_zh="判断是否为 null"),
        # ── 文本 ──────────────────────────────────────────────────────────
        _spec("trim", "text", ("string",), "string",
              "null→null；输入 ≤2048 字符", "none", "O(len)",
              help_zh="去除首尾空白"),
        _spec("upper", "text", ("string",), "string",
              "null→null；Unicode 安全", "none", "O(len)",
              help_zh="转大写"),
        _spec("lower", "text", ("string",), "string",
              "null→null；Unicode 安全", "none", "O(len)",
              help_zh="转小写"),
        _spec("concat", "text", ("string", "string"), "string",
              "null→null（需显式 coalesce）；结果 ≤512 字符", "none", "O(len)",
              min_args=2, max_args=4, help_zh="拼接文本"),
        _spec("substring", "text", ("string", "number", "number"), "string",
              "null→null；负数/越界按空串；结果 ≤512 字符", "none", "O(len)",
              min_args=2, max_args=3, help_zh="取子串（起始位置从 0 开始）"),
        _spec("str_length", "text", ("string",), "number",
              "null→null；按 Unicode 码点计数（non-BMP 计 1）", "none", "O(len)",
              help_zh="文本长度（Unicode 码点）"),
        # ── 代码 / 显式转换 ────────────────────────────────────────────────
        _spec("pad_left", "code", ("string", "number", "string"), "string",
              "null→null；前导零与代码精度保留", "none", "O(len)",
              min_args=2, max_args=3, help_zh="左侧补齐（行政区代码前导零）"),
        _spec("to_string", "conversion", ("any",), "string",
              "null→null；丢失单位并披露", "none", "O(1)",
              help_zh="显式转文本（大整数保精度）"),
        _spec("to_number", "conversion", ("string",), "number",
              "空串→EMPTY_STRING_NOT_ZERO（绝不自动变 0）；bool→TYPE_MISMATCH",
              "none", "O(1)", help_zh="严格转数值"),
        _spec("to_integer", "conversion", ("string",), "number",
              "空串/小数→CONVERSION_FAILED；大整数保 Python int 精度",
              "none", "O(1)", help_zh="严格转整数（代码类文本用）"),
        # ── 日期时间（声明时区 + 格式；不读系统时钟）────────────────────────
        _spec("parse_datetime", "datetime", ("string", "string", "string"),
              "datetime",
              "空/含糊格式→行级 INVALID_DATE；无时区→编译期 AMBIGUOUS_DATETIME",
              "none", "O(1)", min_args=1, max_args=3,
              requires_datetime_context=True,
              help_zh="按声明格式与时区解析时间（1 参时用计划级 date_format/timezone）"),
        _spec("datetime_part", "datetime", ("datetime", "string"), "number",
              "null→null；part 必须是编译期字面量", "none", "O(1)",
              min_args=2, max_args=2,
              help_zh="提取年/月/日/时/分/星期（按声明时区）"),
        _spec("date_diff_days", "datetime", ("datetime", "datetime"), "number",
              "null→null；按 UTC epoch 差", "none", "O(1)",
              min_args=2, max_args=2, help_zh="两时间相差天数"),
        # ── 单位 ──────────────────────────────────────────────────────────
        _spec("convert_unit", "unit", ("number", "string", "string"), "number",
              "null→null；参数必须是编译期字面量且同量纲", "convert", "O(1)",
              min_args=3, max_args=3,
              help_zh="显式单位换算（平方米↔平方千米、percent↔fraction 等）"),
        # ── 几何（CRS 门控；输出单位固定）──────────────────────────────────
        _spec("area_km2", "geometry", (), "number",
              "无/非法几何→GEOMETRY_NOT_MEASURABLE；CRS 不合格→CRS_UNKNOWN/CRS_NOT_METRIC",
              "fixed:square_kilometers", "O(vertices)", min_args=0, max_args=0,
              geometry=True, help_zh="要素面积（km²，CRS 安全）"),
        _spec("area_m2", "geometry", (), "number",
              "同上", "fixed:square_meters", "O(vertices)", min_args=0, max_args=0,
              geometry=True, help_zh="要素面积（m²，CRS 安全）"),
        _spec("length_km", "geometry", (), "number",
              "仅线/多线；其余→GEOMETRY_NOT_MEASURABLE",
              "fixed:kilometers", "O(vertices)", min_args=0, max_args=0,
              geometry=True, help_zh="要素长度（km，CRS 安全）"),
        _spec("length_m", "geometry", (), "number",
              "同上", "fixed:meters", "O(vertices)", min_args=0, max_args=0,
              geometry=True, help_zh="要素长度（m，CRS 安全）"),
    )
}


#: 闭合作算子 → 输出类型（解析/编译期共用）。
BINARY_RESULT_TYPE: Dict[str, str] = {
    "add": "number", "sub": "number", "mul": "number", "div": "number",
    "mod": "number", "eq": "boolean", "ne": "boolean", "lt": "boolean",
    "le": "boolean", "gt": "boolean", "ge": "boolean",
    "and": "boolean", "or": "boolean",
}

DATETIME_PARTS = frozenset({"year", "month", "day", "hour", "minute", "weekday", "date"})


def function_help() -> list[dict[str, Any]]:
    """函数帮助（UI / Pi / 文档共用；契约驱动，有界且无样本值）。"""
    return [
        {"name": s.name, "family": s.family, "arg_types": list(s.arg_types),
         "result_type": s.result_type, "null_behavior": s.null_behavior,
         "unit_rule": s.unit_rule, "resource": s.resource,
         "min_args": s.min_args, "max_args": s.max_args,
         "help_zh": s.help_zh}
        for s in FUNCTIONS.values()
    ]


def dependencies(node: ExprNode) -> Tuple[str, ...]:
    """IR → 依赖字段名（排序去重；含计算列引用，用于拓扑排序）。"""
    out: set[str] = set()
    _collect(node, out)
    return tuple(sorted(out))


def _collect(node: ExprNode, out: set[str]) -> None:
    if node.kind == "field":
        out.add(node.name)
        return
    if node.kind == "unary" and node.arg is not None:
        _collect(node.arg, out)
    elif node.kind == "binary":
        if node.left is not None:
            _collect(node.left, out)
        if node.right is not None:
            _collect(node.right, out)
    elif node.kind == "call":
        for a in node.args:
            _collect(a, out)
    elif node.kind == "case":
        for cond, val in node.branches:
            _collect(cond, out)
            _collect(val, out)
        if node.default is not None:
            _collect(node.default, out)


def node_count(node: ExprNode) -> int:
    if node.kind in ("lit", "field"):
        return 1
    if node.kind == "unary":
        return 1 + (node_count(node.arg) if node.arg else 0)
    if node.kind == "binary":
        return 1 + (node_count(node.left) if node.left else 0) \
            + (node_count(node.right) if node.right else 0)
    if node.kind == "call":
        return 1 + sum(node_count(a) for a in node.args)
    total = 1
    for cond, val in node.branches:
        total += node_count(cond) + node_count(val)
    return total + (node_count(node.default) if node.default else 0)


def node_depth(node: ExprNode) -> int:
    if node.kind in ("lit", "field"):
        return 1
    if node.kind == "unary":
        return 1 + (node_depth(node.arg) if node.arg else 0)
    if node.kind == "binary":
        return 1 + max(node_depth(node.left) if node.left else 0,
                       node_depth(node.right) if node.right else 0)
    if node.kind == "call":
        return 1 + max((node_depth(a) for a in node.args), default=0)
    depths = [node_depth(node.default) if node.default else 0]
    for cond, val in node.branches:
        depths.extend((node_depth(cond), node_depth(val)))
    return 1 + max(depths, default=0)


def uses_geometry(node: ExprNode) -> bool:
    """IR 是否依赖行几何（供 CRS 资格闸与版本锚识别）。"""
    return _child_uses_geometry(node)


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


def _child_uses_geometry(node: ExprNode) -> bool:
    spec = FUNCTIONS.get(node.name) if node.kind == "call" else None
    if spec is not None and spec.geometry:
        return True
    return any(_child_uses_geometry(c) for c in _children(node))


# ── 静态类型推导（编译期；运行时仍按实际值严格校验）────────────────────────

def static_type(node: ExprNode, field_types: Optional[Dict[str, str]] = None) -> str:
    """IR → 静态类型（``any`` = 数据形态未知，运行时判定，不静默提升）。"""
    types = field_types or {}
    if node.kind == "lit":
        return "null" if node.dtype == "null" else node.dtype
    if node.kind == "field":
        return types.get(node.name, "any")
    if node.kind == "unary":
        return "number" if node.op == "neg" else "boolean"
    if node.kind == "binary":
        return BINARY_RESULT_TYPE.get(node.op, "any")
    if node.kind == "call":
        spec = FUNCTIONS.get(node.name)
        if spec is None:
            return "any"
        if spec.result_type == "branch":
            if node.name == "if":
                return static_type(node.args[1], types) if len(node.args) > 2 else "any"
            return "any"
        if spec.result_type == "same_as_first":
            return static_type(node.args[0], types) if node.args else "any"
        return spec.result_type
    # case：分支与 default 类型一致才确定，否则 any
    branch_types = [static_type(v, types) for _, v in node.branches]
    if node.default is not None:
        branch_types.append(static_type(node.default, types))
    uniq = {t for t in branch_types if t != "null"}
    return next(iter(uniq)) if len(uniq) == 1 else "any"


# ── 单位投影（复用 measurement.CANONICAL_UNITS；字段名只作提示）────────────

@dataclass(frozen=True, slots=True)
class UnitInfo:
    unit: str = ""
    dimension: str = ""
    status: str = "undeclared"        # declared | derived | undeclared
    measurement_kind: str = ""
    derived_from: Tuple[Tuple[str, str], ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {"unit": self.unit, "dimension": self.dimension,
                "status": self.status,
                "measurement_kind": self.measurement_kind,
                "derived_from": [list(x) for x in self.derived_from]}


def dimension_of(unit: str) -> str:
    entry = CANONICAL_UNITS.get(unit)
    return entry.dimension.value if entry is not None else ""


def unit_scale(unit: str) -> Optional[float]:
    entry = CANONICAL_UNITS.get(unit)
    return float(entry.scale_to_reference) if entry is not None else None


def _expr_source(node: ExprNode) -> str:
    if node.kind == "field":
        return node.name
    if node.kind == "lit":
        return f"literal:{node.value}"
    return f"expr:{expr_fingerprint(node)[:8]}"


def _combine_units(left: UnitInfo, right: UnitInfo, op: str) -> UnitInfo:
    """乘除形成派生量：保留分子/分母来源；无声明单位时保留 undeclared。"""
    derived = (("numerator", _source_of(left) or "unknown"),
               ("denominator", _source_of(right) or "unknown"))
    if not (left.dimension and right.dimension and left.status != "undeclared"
            and right.status != "undeclared"):
        return UnitInfo(status="undeclared", derived_from=derived)
    if op == "mul":
        dim = f"{left.dimension}*{right.dimension}"
        return UnitInfo(unit=f"({left.unit})*({right.unit})", dimension=dim,
                        status="derived", derived_from=derived)
    dim = f"{left.dimension}/{right.dimension}"
    kind = ""
    if left.dimension in (UnitDimension.POPULATION.value, UnitDimension.COUNT.value) \
            and right.dimension == UnitDimension.AREA.value:
        kind = "density"
    elif left.dimension == right.dimension:
        kind = "ratio"
    return UnitInfo(unit=f"({left.unit})_per_({right.unit})", dimension=dim,
                    status="derived", measurement_kind=kind,
                    derived_from=derived)


def _source_of(info: UnitInfo) -> str:
    for role, src in info.derived_from:
        if role == "source":
            return src
    return ""


def static_unit(node: ExprNode, field_units: Optional[Dict[str, str]] = None,
                *, declared_column_units: Optional[Dict[str, str]] = None) -> UnitInfo:
    """IR → 单位投影。``field_units`` 是 **user-declared**（metadata 猜测不进这里）。"""
    declared = field_units or {}
    col_units = declared_column_units or {}
    if node.kind == "field":
        unit = declared.get(node.name, "")
        if not unit:
            # 已计算列的显式声明单位（列依赖）也可作为声明来源
            unit = col_units.get(node.name, "")
        if not unit:
            return UnitInfo(status="undeclared",
                            derived_from=(("source", node.name),))
        return UnitInfo(unit=unit, dimension=dimension_of(unit),
                        status="declared",
                        derived_from=(("source", node.name),))
    if node.kind == "lit":
        return UnitInfo(status="undeclared") if node.dtype == "number" else UnitInfo()
    if node.kind == "unary":
        return static_unit(node.arg, declared, declared_column_units=col_units) \
            if node.arg is not None else UnitInfo()
    if node.kind == "binary":
        left = static_unit(node.left, declared, declared_column_units=col_units) \
            if node.left else UnitInfo()
        right = static_unit(node.right, declared, declared_column_units=col_units) \
            if node.right else UnitInfo()
        if node.op in ("add", "sub"):
            if left.dimension and right.dimension and left.dimension != right.dimension:
                return UnitInfo(status="incompatible",
                                derived_from=(("left", _source_of(left) or "unknown"),
                                              ("right", _source_of(right) or "unknown")))
            if left.status == "declared" and right.status == "declared":
                return UnitInfo(unit=left.unit, dimension=left.dimension,
                                status="declared",
                                derived_from=(("source", _expr_source(node)),))
            return UnitInfo(status="undeclared")
        if node.op in ("mul", "div"):
            return _combine_units(left, right, node.op)
        return UnitInfo()            # 比较/逻辑：无量纲
    if node.kind == "call":
        spec = FUNCTIONS.get(node.name)
        if spec is None:
            return UnitInfo()
        if spec.unit_rule.startswith("fixed:"):
            unit = spec.unit_rule.split(":", 1)[1]
            return UnitInfo(unit=unit, dimension=dimension_of(unit),
                            status="declared",
                            derived_from=(("source", "geometry"),))
        if spec.unit_rule == "preserve":
            return static_unit(node.args[0], declared,
                               declared_column_units=col_units) if node.args else UnitInfo()
        if spec.unit_rule == "convert":
            if len(node.args) >= 3 and node.args[2].kind == "lit":
                to_unit = str(node.args[2].value)
                return UnitInfo(unit=to_unit, dimension=dimension_of(to_unit),
                                status="declared" if to_unit in CANONICAL_UNITS else "undeclared",
                                derived_from=(("source", "convert_unit"),))
            return UnitInfo(status="undeclared")
        if spec.unit_rule == "branch":
            infos = [static_unit(a, declared, declared_column_units=col_units)
                     for a in node.args[1:]] or [UnitInfo()]
            dims = {i.dimension for i in infos if i.dimension}
            if len(dims) > 1:
                return UnitInfo(status="incompatible")
            if all(i.status == "declared" and i.dimension for i in infos if i.dimension):
                first = next((i for i in infos if i.dimension), None)
                if first is not None:
                    return UnitInfo(unit=first.unit, dimension=first.dimension,
                                    status="declared",
                                    derived_from=(("source", "branch"),))
            return UnitInfo(status="undeclared")
        return UnitInfo()
    # case
    infos = [static_unit(v, declared, declared_column_units=col_units)
             for _, v in node.branches]
    if node.default is not None:
        infos.append(static_unit(node.default, declared,
                                 declared_column_units=col_units))
    dims = {i.dimension for i in infos if i.dimension}
    if len(dims) > 1:
        return UnitInfo(status="incompatible")
    if infos and all(i.status == "declared" and i.dimension for i in infos):
        return UnitInfo(unit=infos[0].unit, dimension=infos[0].dimension,
                        status="declared", derived_from=(("source", "case"),))
    return UnitInfo(status="undeclared")


__all__ = [
    "FunctionSpec", "FUNCTIONS", "BINARY_RESULT_TYPE", "DATETIME_PARTS",
    "function_help", "dependencies", "node_count", "node_depth",
    "uses_geometry", "static_type", "static_unit", "UnitInfo",
    "dimension_of", "unit_scale",
]
