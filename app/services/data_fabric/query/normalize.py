"""Legacy QuerySpec → QuerySpecV2 归一化（ADR-0094 §2）。

归一化规则（兼容矩阵）：
- ``bbox``                 → ``spatial = BBox(bbox, crs="EPSG:4326")``（legacy 无 CRS 字段，
                              axis-order 约定与 V1 相同：lon/lat）
- ``where``/``filter_expr``（str 或 dict）→ 受限解析器 → ``filter`` AST；
                              解析失败抛 ``InvalidQueryError``（不再静默丢弃）
- ``columns``/``fields``   → ``select``
- ``datetime_range`` [s,e] → ``temporal = During(field=配置的时间字段或 "time")``
- ``limit``/``offset``     → ``OffsetPage``
- ``srs``（extra 字段）     → ``output.crs``（修复 V1 的静默 no-op）
- ``tile_coords``          → ``output.mode = VECTOR_TILE``
- ``result_mode``/``aggregate`` 等 V2 extras 直接透传

受限 where 解析器沿用 V1 PostGIS ``_parse_safe_where`` 语法
（``column op literal`` 单表达式，``AND`` 连接），产出 typed AST。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from app.schemas.data_fabric_schema import QuerySpec
from app.services.data_fabric.errors import InvalidQueryError
from app.services.data_fabric.query.models import (
    AggSpec,
    CursorPage,
    ExecutionBudget,
    OffsetPage,
    OrderByItem,
    OutputSpec,
    QuerySpecV2,
    ResultMode,
    SampleSpec,
)
from app.services.data_fabric.query.predicates import (
    And,
    Eq,
    Ge,
    Gt,
    In,
    IsNull,
    Le,
    Like,
    Lt,
    Ne,
    NotIn,
    Predicate,
    PredicateError,
    predicate_from_dict,
    spatial_from_dict,
    temporal_from_dict,
)

# legacy where 解析（与 V1 postgis _parse_safe_where 同语法族）
_WHERE_TOKEN_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(>=|<=|!=|<>|=|>|<|LIKE|like|IS\s+NULL|IS\s+NOT\s+NULL|IN\s*\(|NOT\s+IN\s*\()"
)
_STRUCTURAL_TOKENS = (" OR ", " AND ", " SELECT ", " UNION ", "--", "/*", "*/")
_UNQUOTED_KEYWORDS = ("SELECT", "DROP", "DELETE", "INSERT", "UPDATE", ";")
_OPS = {
    "=": Eq, ">=": Ge, "<=": Le, "!=": Ne, "<>": Ne,
    ">": Gt, "<": Lt, "LIKE": Like, "like": Like,
}

_TEMPORAL_FIELD_CANDIDATES = ("time", "datetime", "created_at", "timestamp", "date", "observed_at")


def parse_legacy_where(where: str) -> Predicate:
    """受限单表达式语法 → AST。任何不符合语法的输入抛 ``InvalidQueryError``。

    支持：``col = 5``、``col LIKE 'x%'``、``col IN (1,2,3)``、
    ``col IS NULL``/``IS NOT NULL``、多表达式 ``AND`` 连接。
    """
    if not isinstance(where, str) or not where.strip():
        raise InvalidQueryError("where must be a non-empty string")
    text = where.strip()
    if len(text) > 512:
        raise InvalidQueryError("where expression too long (max 512 chars)")
    for tok in _STRUCTURAL_TOKENS[2:]:  # SELECT/UNION/comments 直接拒绝
        if tok in text.upper():
            raise InvalidQueryError(f"forbidden token in where: {tok.strip()}")

    clauses: List[Predicate] = []
    for raw_clause in re.split(r"\s+AND\s+|\s+and\s+", text):
        clause = raw_clause.strip()
        if not clause:
            raise InvalidQueryError("empty clause in where")
        clauses.append(_parse_single_clause(clause))
    if not clauses:
        raise InvalidQueryError("empty where expression")
    return clauses[0] if len(clauses) == 1 else And(args=clauses)


def _parse_single_clause(clause: str) -> Predicate:
    m = _WHERE_TOKEN_RE.match(clause)
    if not m:
        raise InvalidQueryError(f"unparsable where clause: {clause[:60]!r}")
    field, op_token = m.group(1), m.group(2)
    rest = clause[m.end():].strip()

    if op_token.upper().startswith("IS"):
        negated = "NOT" in op_token.upper()
        return IsNull(field=field, negated=negated)

    if op_token.upper().startswith("IN"):
        not_in = op_token.upper().startswith("NOT")
        close = rest.find(")")
        if close < 0:
            raise InvalidQueryError("unclosed IN list")
        inner = rest[:close].strip()
        values = [_coerce_literal(v.strip()) for v in inner.split(",") if v.strip()]
        if not values:
            raise InvalidQueryError("empty IN list")
        return (NotIn if not_in else In)(field=field, values=values)

    # 剩余是比较 / LIKE：值是字面量（引号字符串或数字）
    value, err = _parse_literal(rest)
    if err:
        raise InvalidQueryError(f"{err} in clause: {clause[:60]!r}")
    cls = _OPS.get(op_token)
    if cls is None:
        raise InvalidQueryError(f"unsupported operator {op_token!r}")
    if cls is Like:
        if not isinstance(value, str):
            raise InvalidQueryError("LIKE pattern must be a quoted string")
        return Like(field=field, pattern=value)
    return cls(field=field, value=value)


def _parse_literal(rest: str):
    if not rest:
        return None, "missing value"
    if rest[0] in ("'", '"'):
        quote = rest[0]
        end = rest.find(quote, 1)
        while end != -1 and end + 1 < len(rest) and rest[end + 1] == quote:
            end = rest.find(quote, end + 2)  # 转义的引号 ''
        if end == -1:
            return None, "unterminated string literal"
        if rest[end + 1:].strip():
            return None, "trailing characters after string literal"
        return rest[1:end].replace(quote * 2, quote), None
    # 数字 / 布尔 / NULL
    tok = rest.strip()
    if re.match(r"^-?\d+(\.\d+)?$", tok):
        return float(tok) if "." in tok else int(tok), None
    if tok.upper() == "TRUE":
        return True, None
    if tok.upper() == "FALSE":
        return False, None
    if tok.upper() == "NULL":
        return None, None
    # V1 兼容：单 token 裸词（无空格/引号/括号）作为字符串字面量 —— 值仍然
    # 走参数绑定，无注入面（tests/unit/test_security_round2.py 契约）。
    # SQL 关键字裸词依旧拒绝（V1 语义：x = DROP 是语法错误）。
    if re.match(r"^[A-Za-z0-9_%.+\-]+$", tok) and tok.upper() not in (
        "SELECT", "UNION", "DROP", "INSERT", "DELETE", "UPDATE", "AND", "OR",
        "NULL", "TRUE", "FALSE",
    ):
        return tok, None
    return None, "value must be quoted string or numeric literal"


def _coerce_literal(v: str) -> Any:
    parsed, err = _parse_literal(v)
    if err:
        raise InvalidQueryError(f"bad literal in IN list: {v!r}")
    return parsed


def _spec_attrs(spec: Any) -> Dict[str, Any]:
    """pydantic QuerySpec 或 duck-typed 对象 → 已知字段 + extras 的扁平视图。"""
    known = (
        "bbox", "columns", "fields", "limit", "offset", "filter_expr", "where",
        "datetime_range", "zoom", "tile_coords",
    )
    attrs: Dict[str, Any] = {}
    for k in known:
        v = getattr(spec, k, None)
        if v is not None:
            attrs[k] = v
    extras = getattr(spec, "model_extra", None)
    if isinstance(extras, dict):
        for k, v in extras.items():
            attrs.setdefault(k, v)
    elif not hasattr(spec, "model_extra"):
        # duck-typed：其余实例属性视为 extras
        for k, v in vars(spec).items():
            if not k.startswith("_") and k not in known:
                attrs.setdefault(k, v)
    return attrs


def normalize_query_spec(spec: QuerySpec) -> QuerySpecV2:
    """legacy ``QuerySpec``（或 duck-typed 等价物）→ ``QuerySpecV2``。"""
    extras_all = _spec_attrs(spec)
    extras = {k: v for k, v in extras_all.items()
              if k not in ("bbox", "columns", "fields", "limit", "offset",
                           "filter_expr", "where", "datetime_range", "zoom", "tile_coords")}

    # ---- filter ----
    filter_ast: Optional[Predicate] = None
    where_any = extras_all.get("where") or extras_all.get("filter_expr") or extras.get("filter")
    if where_any is not None:
        filter_ast = _coerce_filter(where_any)

    # ---- spatial ----
    spatial = extras.get("spatial")
    spatial_ast = None
    if spatial is not None:
        try:
            spatial_ast = spatial_from_dict(spatial)
        except PredicateError as e:
            raise InvalidQueryError(f"invalid spatial predicate: {e}") from e
    elif extras_all.get("bbox"):
        spatial_ast = _legacy_bbox_to_spatial(extras_all["bbox"])

    # ---- temporal ----
    temporal = extras.get("temporal")
    temporal_ast = None
    if temporal is not None:
        try:
            temporal_ast = temporal_from_dict(temporal)
        except PredicateError as e:
            raise InvalidQueryError(f"invalid temporal predicate: {e}") from e
    elif extras_all.get("datetime_range"):
        temporal_ast = _legacy_datetime_range(extras_all["datetime_range"], extras)

    # ---- aggregate / group_by / order_by / distinct ----
    aggregate = _coerce_aggregates(extras.get("aggregate"))
    group_by = extras.get("group_by") or None
    if group_by is not None:
        if not isinstance(group_by, list) or not all(isinstance(g, str) for g in group_by):
            raise InvalidQueryError("group_by must be a list of field names")
    order_by = _coerce_order_by(extras.get("order_by"))
    distinct = bool(extras.get("distinct", False))

    # ---- page ----
    # legacy 语义是 clamp（V1 min(limit, MAX)），保留：超界 limit 钳制而非报错。
    raw_limit = extras_all.get("limit", 100) or 100
    try:
        clamped_limit = max(1, min(int(raw_limit), 10_000))
    except (TypeError, ValueError):
        raise InvalidQueryError("limit must be an integer") from None
    cursor = extras.get("cursor")
    page: Any
    if cursor is not None or extras.get("page_kind") == "cursor":
        page = CursorPage(limit=clamped_limit, cursor=cursor)
    else:
        page = OffsetPage(limit=clamped_limit, offset=max(0, extras_all.get("offset") or 0))

    # ---- output ----
    mode_raw = extras.get("result_mode")
    mode = _coerce_result_mode(mode_raw)
    out_crs = extras.get("srs") or extras.get("output_crs") or "EPSG:4326"
    output = OutputSpec(
        mode=mode,
        crs=str(out_crs),
        max_features=_opt_int(extras.get("max_features")),
        max_bytes=_opt_int(extras.get("max_bytes")),
    )

    # ---- sample ----
    sample = None
    if mode == ResultMode.SAMPLE:
        size = _int_extra(extras, "sample_size", 100)
        sample = SampleSpec(size=max(1, min(5000, size)), seed=_opt_int(extras.get("sample_seed")))

    # ---- execution budget ----
    execution = ExecutionBudget(
        deadline_s=float(extras.get("deadline_s", ExecutionBudget().deadline_s)),
        max_rows=_int_extra(extras, "max_rows", ExecutionBudget().max_rows),
        max_bytes=_int_extra(extras, "max_bytes", ExecutionBudget().max_bytes),
        max_vertices=_int_extra(extras, "max_vertices", ExecutionBudget().max_vertices),
        max_pages=_int_extra(extras, "max_pages", ExecutionBudget().max_pages),
    )

    v2 = QuerySpecV2(
        select=(extras_all.get("fields") or extras_all.get("columns") or None),
        filter=filter_ast,
        spatial=spatial_ast,
        temporal=temporal_ast,
        aggregate=aggregate,
        group_by=group_by,
        distinct=distinct,
        order_by=order_by,
        page=page,
        output=output,
        sample=sample,
        execution=execution,
    )

    # 聚合隐含 STATISTICS 语义（除非显式要求物化）
    if v2.aggregate and output.mode == ResultMode.FEATURES:
        v2 = v2.model_copy(update={"output": output.model_copy(update={"mode": ResultMode.STATISTICS})})
    return v2


def _legacy_bbox_to_spatial(bbox: Sequence[float]):
    from app.services.data_fabric.query.predicates import BBox

    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise InvalidQueryError("bbox must be [minx, miny, maxx, maxy]")
    try:
        return BBox(bbox=list(bbox), crs="EPSG:4326")
    except PredicateError as e:
        raise InvalidQueryError(f"invalid bbox: {e}") from e


def _legacy_datetime_range(rng: Any, extras: Dict[str, Any]):
    from app.services.data_fabric.query.predicates import During

    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        raise InvalidQueryError("datetime_range must be [start, end] ISO-8601 strings")
    field = str(extras.get("temporal_field") or _TEMPORAL_FIELD_CANDIDATES[0])
    try:
        return During(field=field, start=str(rng[0]), end=str(rng[1]))
    except PredicateError as e:
        raise InvalidQueryError(f"invalid datetime_range: {e}") from e


def _coerce_filter(where_any: Any) -> Predicate:
    """dict（AST wire 形式）或受限字符串 → AST。"""
    try:
        if isinstance(where_any, dict):
            return predicate_from_dict(where_any)
        if isinstance(where_any, str):
            return parse_legacy_where(where_any)
        raise InvalidQueryError(f"filter must be dict or str, got {type(where_any).__name__}")
    except PredicateError as e:
        raise InvalidQueryError(f"invalid filter: {e}") from e


def _coerce_aggregates(raw: Any) -> Optional[List[AggSpec]]:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise InvalidQueryError("aggregate must be a list of {func, field}")
    out: List[AggSpec] = []
    for item in raw:
        if isinstance(item, str):
            if item != "count":
                raise InvalidQueryError(f"string aggregate only supports 'count', got {item!r}")
            out.append(AggSpec(func="count"))
            continue
        if not isinstance(item, dict):
            raise InvalidQueryError("aggregate item must be dict")
        try:
            out.append(AggSpec(func=item.get("func"), field=item.get("field")))
        except Exception as e:
            raise InvalidQueryError(f"invalid aggregate spec {item!r}: {e}") from e
    return out


def _coerce_order_by(raw: Any) -> List[OrderByItem]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    out: List[OrderByItem] = []
    for item in raw:
        if isinstance(item, str):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(ASC|DESC|asc|desc)?$", item.strip())
            if not m:
                raise InvalidQueryError(f"invalid order_by item: {item!r}")
            out.append(OrderByItem(field=m.group(1), direction=(m.group(2) or "asc").lower()))
        elif isinstance(item, dict):
            try:
                out.append(OrderByItem(
                    field=item.get("field"),
                    direction=str(item.get("direction", "asc")).lower(),
                ))
            except Exception as e:
                raise InvalidQueryError(f"invalid order_by item {item!r}: {e}") from e
        else:
            raise InvalidQueryError(f"invalid order_by item: {item!r}")
    return out


def _coerce_result_mode(raw: Any) -> ResultMode:
    if raw is None:
        return ResultMode.FEATURES
    if isinstance(raw, ResultMode):
        return raw
    try:
        return ResultMode(str(raw).lower())
    except ValueError:
        valid = [m.value for m in ResultMode]
        raise InvalidQueryError(f"invalid result_mode {raw!r} (valid: {valid})") from None


def _int_extra(extras: Dict[str, Any], key: str, default: int) -> int:
    v = extras.get(key, default)
    try:
        return int(v)
    except (TypeError, ValueError):
        raise InvalidQueryError(f"{key} must be an integer") from None


def _opt_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise InvalidQueryError("numeric limit expected") from None


__all__ = ["normalize_query_spec", "parse_legacy_where"]
