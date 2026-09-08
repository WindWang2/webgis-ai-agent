"""Arrow-lane 算子（Wave 5 / ADR-0101 D8 续）：filter / project / aggregate /
bbox 预筛选，作用于 ``pyarrow`` Table / RecordBatch。

设计红线（与 vector_carrier 同纪律）：

- **可选依赖**：pyarrow 未声明为仓库依赖 —— 每个公共入口先探测
  ``vector_carrier.arrow_available()``，不可用时抛 typed
  ``VectorCarrierUnavailable``（诚实降级，绝不假装）；
- **同源语义**：过滤表达式从 data_fabric 的**同一个**类型化谓词 AST
  （``query.predicates.predicate_from_dict``）构建；三值逻辑与 dict lane 的
  ``evaluate_predicate`` 对齐（Kleene and/or；NULL → unknown → 行被 WHERE
  排除 —— ``Table.filter`` 缺省丢弃 mask 为 null 的行）；聚合委托
  ``query.accumulators.AggregateDriver``（与两个 dict 驱动共用唯一真相）；
- **绝不静默错结果**：无法在 Arrow 语义下**等价**执行的谓词/操作抛 typed
  ``ArrowPredicateUnsupported``，调用方必须回落 dict lane —— 而不是得到
  看似成功的错误结果。
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Sequence

from app.services.data_fabric import vector_carrier
from app.services.data_fabric.errors import DataFabricError
from app.services.data_fabric.query.accumulators import AggregateDriver


class ArrowPredicateUnsupported(DataFabricError):
    """谓词/操作无法在 Arrow 语义下等价执行（调用方应回落 dict lane）。"""

    code = "ARROW_PREDICATE_UNSUPPORTED"


def _require_pa_ops() -> Any:
    """探测 + 惰性导入（pyarrow 缺失 → typed unavailable，带回落建议）。"""
    if not vector_carrier.arrow_available():
        from app.services.data_fabric.vector_carrier import VectorCarrierUnavailable

        raise VectorCarrierUnavailable(
            "arrow_ops require the optional 'pyarrow' dependency; "
            "fall back to the dict-lane streaming seams or install pyarrow",
        )
    import pyarrow as pa
    import pyarrow.compute as pc

    return pa, pc


# ── 谓词 AST → pa.compute 表达式 ────────────────────────────────────────────


def _unsupported(msg: str) -> ArrowPredicateUnsupported:
    return ArrowPredicateUnsupported(msg)


def _column_type(schema: Any, name: str) -> Any:
    if schema is None:
        return None
    get_field = getattr(schema, "field", None)
    if callable(get_field):
        try:
            return get_field(name).type
        except (KeyError, ValueError):
            raise _unsupported(f"field '{name}' not present in the Arrow schema")
    if hasattr(schema, "get"):  # {name: DataType} mapping
        return schema.get(name)
    return None


def _scalar_for(value: Any, col_type: Any) -> Any:
    """把谓词标量绑定到列类型（镜像 dict lane 的类型宽松度；不齐 → unsupported）。

    dict lane（evaluate_predicate）遇"数值可互转"做数值比较、否则字符串
    比较。列类型在 Arrow 是已知的，因此等价规则是：
    数值列 ← 数值 / 可解析数字字符串；字符串列 ← 字符串；bool 列 ← bool。
    其余（bool 参与数值比较、数值标量对字符串列等 dict lane 的逐行回退
    分支）在 Arrow 下无法等价表达 → typed unsupported（回落 dict lane）。
    特别地，非整数值对整型列拒绝绑定 —— ``pa.scalar`` 会**静默截断**，
    那会改变 eq/ne 的比较结果（绝不静默错结果）。
    """
    import pyarrow as pa

    if value is None:
        return pa.scalar(None)
    if col_type is None:
        return pa.scalar(value)
    if pa.types.is_boolean(col_type):
        if isinstance(value, bool):
            return pa.scalar(value, col_type)
        raise _unsupported(f"bool column cannot be compared with {type(value).__name__}")
    if pa.types.is_integer(col_type):
        if isinstance(value, bool):
            raise _unsupported("bool scalar is excluded from numeric comparison (dict-lane parity)")
        if isinstance(value, int):
            return pa.scalar(value, col_type)
        if isinstance(value, float):
            if value.is_integer():
                return pa.scalar(int(value), col_type)
            raise _unsupported("non-integral scalar against integer column (silent truncation)")
        if isinstance(value, str):
            try:
                return pa.scalar(int(value), col_type)
            except ValueError:
                raise _unsupported(f"string scalar {value!r} not parseable for integer column")
        raise _unsupported(f"integer column cannot be compared with {type(value).__name__}")
    if pa.types.is_floating(col_type) or pa.types.is_decimal(col_type):
        if isinstance(value, bool):
            raise _unsupported("bool scalar is excluded from numeric comparison (dict-lane parity)")
        if isinstance(value, (int, float)):
            return pa.scalar(value, col_type)
        if isinstance(value, str):
            try:
                num = float(value)
            except ValueError:
                raise _unsupported(f"string scalar {value!r} not parseable for numeric column")
            return pa.scalar(num, col_type)
        raise _unsupported(f"numeric column cannot be compared with {type(value).__name__}")
    if pa.types.is_string(col_type) or pa.types.is_large_string(col_type):
        if isinstance(value, str):
            return pa.scalar(value, col_type)
        raise _unsupported(
            "numeric scalar against string column requires dict-lane per-row coercion")
    try:
        return pa.scalar(value, col_type)
    except Exception as exc:
        raise _unsupported(f"scalar not bindable to column type {col_type}: {exc}") from exc


def _like_kernel(pc: Any) -> Any:
    kernel = getattr(pc, "match_like", None) or getattr(pc, "match_like_pattern", None)
    if kernel is None:
        raise _unsupported("pyarrow build lacks a LIKE kernel")
    return kernel


def build_predicate_expression(node: Any, schema: Any = None) -> Any:
    """类型化谓词 AST（或 dict）→ ``pa.compute`` Expression。

    支持：eq / ne / gt / ge / lt / le / in / like / is_null 叶子与
    and / or / not 组合（Kleene 三值逻辑 —— 与 SQL WHERE 及 dict lane 的
    ``evaluate_predicate`` 一致）。其余形状（between / not_in / 空间 /
    时间等）抛 :class:`ArrowPredicateUnsupported` —— 调用方回落 dict lane，
    绝不静默错结果。
    """
    pa, pc = _require_pa_ops()
    if isinstance(node, dict):
        from app.services.data_fabric.query.predicates import predicate_from_dict

        node = predicate_from_dict(node)

    op = node.op
    if op in ("and", "or"):
        args = [build_predicate_expression(a, schema) for a in node.args]
        combine = pc.and_kleene if op == "and" else pc.or_kleene
        expr = args[0]
        for a in args[1:]:
            expr = combine(expr, a)
        return expr
    if op == "not":
        # invert(null) = null → unknown 行仍被 WHERE 排除（3VL NOT 语义）。
        return pc.invert(build_predicate_expression(node.arg, schema))
    if op == "is_null":
        col = pc.field(node.field)
        mask = pc.is_null(col)
        return pc.invert(mask) if node.negated else mask

    if op in ("eq", "ne", "gt", "ge", "lt", "le"):
        col_type = _column_type(schema, node.field)
        scalar = _scalar_for(node.value, col_type)
        col = pc.field(node.field)
        # 比较遇 NULL → null（unknown）→ filter 丢弃，与 dict lane 一致。
        if op == "eq":
            return col == scalar
        if op == "ne":
            return col != scalar
        if op == "gt":
            return col > scalar
        if op == "ge":
            return col >= scalar
        if op == "lt":
            return col < scalar
        return col <= scalar
    if op == "in":
        col_type = _column_type(schema, node.field)
        members: List[Any] = []
        none_member = False
        for m in node.values:
            if m is None:
                # dict lane：None 成员对非 null 值永不命中；对 null 值整行
                # 已是 unknown → 用 is_valid 显式排除（绝不让 is_in 的
                # null 成员命中语义混进来）。
                none_member = True
                continue
            members.append(_scalar_for(m, col_type))
        if not members:
            raise _unsupported("'in' predicate has no comparable members")
        try:
            value_set = pa.array([s.as_py() for s in members], type=col_type)
        except Exception:
            # 成员无法组成与列同型的数组（混合类型等）→ dict lane 逐行
            # 匹配才有等价语义。
            raise _unsupported("'in' value_set cannot be typed against the column")
        mask = pc.is_in(pc.field(node.field), value_set=value_set)
        if none_member:
            mask = pc.and_kleene(mask, pc.is_valid(pc.field(node.field)))
        return mask
    if op == "like":
        col_type = _column_type(schema, node.field)
        if col_type is not None and not (
            pa.types.is_string(col_type) or pa.types.is_large_string(col_type)
        ):
            raise _unsupported(
                f"'like' requires a string column (got {col_type}); "
                "dict lane coerces values per-row")
        return _like_kernel(pc)(pc.field(node.field), node.pattern)
    raise _unsupported(f"predicate op '{op}' has no Arrow-lane equivalent")


# ── 公共算子 ────────────────────────────────────────────────────────────────


def arrow_filter(table: Any, predicate: Any) -> Any:
    """Arrow lane 属性过滤（同一类型化谓词 AST；三值逻辑保真）。

    ``predicate`` 为 dict 或已解析的 AST 节点。无法等价执行的形状抛
    :class:`ArrowPredicateUnsupported`。输入 Table 或 RecordBatch，
    返回同型过滤结果（mask 为 null 的行被丢弃 —— SQL WHERE 语义）。
    """
    pa, _pc = _require_pa_ops()
    expr = build_predicate_expression(predicate, getattr(table, "schema", None))
    return table.filter(expr)


def arrow_project(table: Any, fields: Sequence[str]) -> Any:
    """Arrow lane 列投影（fields 顺序；缺失列按 dict lane 契约忽略；
    geometry 列由调用方按需包含 —— 列选择不做隐式魔法）。"""
    pa, _pc = _require_pa_ops()
    present = set(table.schema.names)
    return table.select([f for f in fields if f in present])


def _iter_record_batches(source: Any) -> Iterator[Any]:
    """Table → batches；RecordBatch/批迭代器原样（逐批，绝不整表物化）。"""
    pa, _pc = _require_pa_ops()
    if isinstance(source, pa.Table):
        yield from source.to_batches()
        return
    if hasattr(source, "schema") and hasattr(source, "num_rows") and not hasattr(source, "__next__"):
        # 单个 RecordBatch 形状
        yield source
        return
    yield from source


def _needed_columns(driver: "AggregateDriver") -> List[str]:
    names: List[str] = list(driver.group_by)
    for req in driver.requests:
        if req.field is not None and req.field not in names:
            names.append(req.field)
    return names


def arrow_aggregate_batches(
    source: Any,
    aggregations: Sequence[Any],
    group_by: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Arrow lane 聚合：逐批只取**需要的列**（to_pylist 仅限这些列，绝不
    整行物化），值语义委托统一累加器 ``AggregateDriver`` —— 与
    ``compute_aggregates`` / ``stream_aggregate`` 一个真相。

    结果行形状与 ``compute_aggregates`` 一致（含空输入无分组聚合的一行
    哨兵）—— 本算子是 fast lane 里它的替身。
    """
    pa, _pc = _require_pa_ops()
    driver = AggregateDriver(aggregations, group_by, emit_empty_global_row=True)
    needed = _needed_columns(driver)
    for batch in _iter_record_batches(source):
        if batch.num_rows == 0:
            continue
        cols: Dict[str, List[Any]] = {}
        for n in needed:
            if n in batch.schema.names:
                cols[n] = batch.column(n).to_pylist()
        missing = {n: None for n in needed if n not in cols}
        for i in range(batch.num_rows):
            driver.update({n: col_list[i] for n, col_list in cols.items()} | missing)
    return driver.finalize_rows(style="compute")


# ── bbox 预筛选 ─────────────────────────────────────────────────────────────


def _table_geo_meta(table: Any) -> Dict[str, Any]:
    raw = (table.schema.metadata or {}).get(b"geo")
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001 - 外来元数据：按无元数据处理
        return {}


def covering_bbox_columns(geo_meta: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """geo 元数据里的逐行 bbox covering 列声明（GeoParquet 1.1 形状）。"""
    for col in (geo_meta.get("columns") or {}).values():
        cov = (col.get("covering") or {}).get("bbox") if isinstance(col, dict) else None
        if isinstance(cov, dict) and all(
            isinstance(cov.get(k), str) for k in ("xmin", "ymin", "xmax", "ymax")
        ):
            return {k: cov[k] for k in ("xmin", "ymin", "xmax", "ymax")}
    return None


def _check_bbox_4(bbox: Sequence[float]) -> List[float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("bbox must be [minx, miny, maxx, maxy]")
    out = []
    for c in bbox:
        if not isinstance(c, (int, float)) or isinstance(c, bool):
            raise ValueError("bbox coordinates must be numeric")
        out.append(float(c))
    return out


def bbox_filter_expression(covering: Dict[str, str], bbox: Sequence[float]) -> Any:
    """逐行 bbox covering 列 → pa.compute 包围盒相交表达式。

    语义与 dict lane 的 ``_row_passes`` 精确对齐：not (xmax < minx or
    xmin > maxx or ymax < miny or ymin > maxy)；covering 列为 null（几何
    缺失）→ mask null → 行被丢弃（与 dict lane "无几何行不保留" 一致）。
    """
    pa, pc = _require_pa_ops()
    minx, miny, maxx, maxy = _check_bbox_4(bbox)
    xmin, ymin, xmax, ymax = (pc.field(covering[k]) for k in ("xmin", "ymin", "xmax", "ymax"))
    disjoint = (xmax < minx) | (xmin > maxx) | (ymax < miny) | (ymin > maxy)
    return pc.invert(disjoint)


def arrow_bbox_filter(table: Any, bbox: Sequence[float]) -> Any:
    """Arrow lane bbox 预筛选（解码几何自由）。

    - schema 带**逐行 bbox covering 列**（GeoParquet 1.1 covering）→
      pa.compute 包围盒相交过滤（null covering = 缺几何 → 丢弃）；
    - 否则若 geo 元数据带**表级 bbox**：与查询框不相交 → 诚实空表；
    - 相交但无逐行列 → 无法不解析几何而逐行筛选 → typed
      :class:`ArrowPredicateUnsupported`（诚实回落 dict lane），绝不假装过滤。
    """
    pa, pc = _require_pa_ops()
    _check_bbox_4(bbox)
    geo_meta = _table_geo_meta(table)
    covering = covering_bbox_columns(geo_meta)
    if covering is not None:
        names = set(table.schema.names)
        if all(covering[k] in names for k in ("xmin", "ymin", "xmax", "ymax")):
            return table.filter(bbox_filter_expression(covering, bbox))
    for col in (geo_meta.get("columns") or {}).values():
        table_bbox = col.get("bbox") if isinstance(col, dict) else None
        if isinstance(table_bbox, (list, tuple)) and len(table_bbox) == 4:
            b0, b1, b2, b3 = (float(c) for c in table_bbox)
            minx, miny, maxx, maxy = _check_bbox_4(bbox)
            if b2 < minx or b0 > maxx or b3 < miny or b1 > maxy:
                return table.slice(0, 0)  # 表级不相交 → 确定空结果（诚实零行）
            break
    raise ArrowPredicateUnsupported(
        "row-level bbox filtering requires per-row bbox covering columns; "
        "arrow_bbox_filter does not parse geometry — fall back to the dict lane",
    )


__all__ = [
    "ArrowPredicateUnsupported",
    "build_predicate_expression",
    "arrow_filter",
    "arrow_project",
    "arrow_aggregate_batches",
    "arrow_bbox_filter",
    "bbox_filter_expression",
]
