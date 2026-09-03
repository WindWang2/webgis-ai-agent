"""AST → adapter-specific 编译器（ADR-0094 §3 安全链）。

编译器职责：
- 字段名：白名单校验（来自 DatasetDescriptor schema）+ 双引号引用（PostGIS）。
- 值：一律参数化（PostGIS pyformat ``%s``）或经方言转义（CQL2/ArcGIS 单引号
  doubling；FES XML 实体转义）。
- 几何：GeoJSON → WKT（仅构造 WKT 文本，仍走参数绑定或经转义嵌入）。

禁止任何 ``f"WHERE {value}"`` 式拼接。所有编译函数返回 (fragment, params)。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as _xml_escape

from app.services.data_fabric.query.predicates import (
    And,
    Eq,
    Ne,
    Or,
    PredicateError,
    bbox_crosses_antimeridian,
    split_antimeridian_bbox,
    validate_predicate_fields,
)

SQLFragment = Tuple[str, List[Any]]


# ── GeoJSON → WKT ───────────────────────────────────────────────────────────


def _coords_to_wkt(coords: Any, fmt: Callable[[float], str] = repr) -> str:
    if coords and isinstance(coords[0], (int, float)):
        return " ".join(fmt(float(c)) for c in coords)
    return ", ".join(_coords_to_wkt(c, fmt) for c in coords)


def _fmt_coord(v: float) -> str:
    # 避免 1e+16 科学计数法进入 WKT（PostGIS 接受，但保守起见定点输出）。
    if v != v or v in (float("inf"), float("-inf")):
        raise PredicateError("non-finite coordinate in geometry")
    if abs(v) < 1e16:
        return f"{v:.10f}".rstrip("0").rstrip(".")
    return repr(v)


def geojson_to_wkt(geom: Dict[str, Any]) -> str:
    """GeoJSON geometry → WKT（用于 PostGIS ST_GeomFromText 参数）。"""
    if not isinstance(geom, dict) or "type" not in geom:
        raise PredicateError("geometry must be a GeoJSON object")
    gtype = str(geom["type"])
    coords = geom.get("coordinates")

    def _ring_line(c: Any) -> str:
        return "(" + _coords_to_wkt(c, _fmt_coord) + ")"

    if gtype == "Point":
        return f"POINT {_ring_line(coords)}"
    if gtype == "MultiPoint":
        return "MULTIPOINT (" + ", ".join(_ring_line([c if isinstance(c, list) and not isinstance(c[0], list) else c]) for c in coords) + ")"
    if gtype == "LineString":
        return f"LINESTRING {_ring_line(coords)}"
    if gtype == "MultiLineString":
        return "MULTILINESTRING (" + ", ".join(_ring_line(c) for c in coords) + ")"
    if gtype == "Polygon":
        return "POLYGON (" + ", ".join(_ring_line(r) for r in coords) + ")"
    if gtype == "MultiPolygon":
        polys = []
        for poly in coords:
            polys.append("(" + ", ".join(_ring_line(r) for r in poly) + ")")
        return "MULTIPOLYGON (" + ", ".join(polys) + ")"
    if gtype == "GeometryCollection" and isinstance(geom.get("geometries"), list):
        parts = ", ".join(geojson_to_wkt(g) for g in geom["geometries"])
        return f"GEOMETRYCOLLECTION ({parts})"
    raise PredicateError(f"unsupported GeoJSON type for WKT: {gtype}")


# ── PostGIS SQL 编译器 ───────────────────────────────────────────────────────


def quote_ident(name: str) -> str:
    from app.services.data_fabric.query.predicates import _check_field_name

    _check_field_name(name)
    return f'"{name}"'


def compile_predicate_sql(
    node: Any,
    *,
    allowed_fields: Optional[Sequence[str]] = None,
    geom_field: str = "geom",
) -> SQLFragment:
    """属性谓词 → 参数化 SQL 片段（pyformat 占位符）。"""
    if allowed_fields is not None:
        validate_predicate_fields(node, allowed_fields)
    return _sql_node(node)


def _sql_node(node: Any) -> SQLFragment:
    op = node.op
    if op == "and":
        parts: List[str] = []
        params: List[Any] = []
        for a in node.args:
            s, p = _sql_node(a)
            parts.append(s)
            params.extend(p)
        return "(" + " AND ".join(parts) + ")", params
    if op == "or":
        parts = []
        params = []
        for a in node.args:
            s, p = _sql_node(a)
            parts.append(s)
            params.extend(p)
        return "(" + " OR ".join(parts) + ")", params
    if op == "not":
        s, p = _sql_node(node.arg)
        return f"(NOT {s})", p

    col = quote_ident(node.field)
    if op == "eq":
        if node.value is None:
            return f"{col} IS NULL", []
        return f"{col} = %s", [node.value]
    if op == "ne":
        if node.value is None:
            return f"{col} IS NOT NULL", []
        return f"{col} != %s", [node.value]
    if op == "gt":
        return f"{col} > %s", [node.value]
    if op == "ge":
        return f"{col} >= %s", [node.value]
    if op == "lt":
        return f"{col} < %s", [node.value]
    if op == "le":
        return f"{col} <= %s", [node.value]
    if op == "in":
        return f"{col} = ANY(%s)", [list(node.values)]
    if op == "not_in":
        return f"({col} <> ALL(%s))", [list(node.values)]
    if op == "between":
        return f"{col} BETWEEN %s AND %s", [node.low, node.high]
    if op == "like":
        # %/_ 由调用者作为模式语义提供；转义服务器端通配符不是默认（GIS 前缀
        # 匹配是常见意图）。pattern 本身经参数绑定，无注入面。
        return f"{col} LIKE %s", [node.pattern]
    if op == "is_null":
        return f"{col} IS {'NOT ' if node.negated else ''}NULL", []
    raise PredicateError(f"cannot compile op {op!r} to SQL")


def compile_temporal_sql(node: Any, *, allowed_fields: Optional[Sequence[str]] = None) -> SQLFragment:
    if allowed_fields is not None:
        from app.services.data_fabric.query.predicates import iter_fields
        for f in iter_fields(node):
            if f not in set(allowed_fields):
                raise PredicateError(f"field '{f}' not in dataset schema")
    col = quote_ident(node.field)
    if node.op == "before":
        return f"{col} < %s::timestamptz", [node.value]
    if node.op == "after":
        return f"{col} > %s::timestamptz", [node.value]
    return f"{col} BETWEEN %s::timestamptz AND %s::timestamptz", [node.start, node.end]


def compile_spatial_sql(
    node: Any,
    *,
    geom_field: str = "geom",
    col_srid: int = 4326,
    bbox_crs_srid: int = 4326,
) -> SQLFragment:
    """空间谓词 → 参数化 SQL。

    - BBox：``ST_Intersects(geom, ST_MakeEnvelope(...))``；查询 CRS ≠ 列 CRS
      时 envelope 经 ``ST_Transform``；跨反子午线 bbox 显式 split 为 OR。
    - 几何谓词：``ST_GeomFromText(%s, srid)`` 参数绑定。
    - DWithin：4326 时走 ``geography`` cast（meters，geodesic）；投影 CRS 时
      ``ST_DWithin`` 平面距离（此时 units=meters 按 CRS 单位解释并记录 warning
      于 planner 层）。
    """
    gcol = quote_ident(geom_field)
    op = node.op

    if op == "bbox":
        env_template = "ST_MakeEnvelope(%s, %s, %s, %s, %s)"
        same_crs = col_srid in (0, -1, bbox_crs_srid)

        def _one_envelope(b: Sequence[float]) -> SQLFragment:
            if same_crs:
                return (
                    f"ST_Intersects({gcol}, {env_template})",
                    [b[0], b[1], b[2], b[3], bbox_crs_srid],
                )
            return (
                f"ST_Intersects({gcol}, ST_Transform({env_template}, %s))",
                [b[0], b[1], b[2], b[3], bbox_crs_srid, col_srid],
            )

        if bbox_crosses_antimeridian(node.bbox) and bbox_crs_srid == 4326:
            # 显式 split 为西侧/东侧两个 envelope 的 OR（禁止静默错误结果）。
            frags = [_one_envelope(b) for b in split_antimeridian_bbox(node.bbox)]
            sql = " OR ".join(f[0] for f in frags)
            params: List[Any] = []
            for _, p in frags:
                params.extend(p)
            return f"({sql})", params
        return _one_envelope(node.bbox)

    wkt = geojson_to_wkt(node.geometry)
    geom_expr = f"ST_GeomFromText(%s, {bbox_crs_srid})"
    geom_params: List[Any] = [wkt]

    fn = {
        "intersects": "ST_Intersects",
        "within": "ST_Within",
        "contains": "ST_Contains",
        "touches": "ST_Touches",
        "overlaps": "ST_Overlaps",
    }.get(op)

    if fn is not None:
        if col_srid not in (0, -1, bbox_crs_srid):
            return f"{fn}({gcol}, ST_Transform({geom_expr}, %s))", geom_params + [col_srid]
        return f"{fn}({gcol}, {geom_expr})", geom_params

    if op == "dwithin":
        # geography cast：distance 单位 meters，测地语义（EPSG:4326）。
        return (
            f"ST_DWithin({gcol}::geography, {geom_expr}::geography, %s)",
            geom_params + [node.distance],
        )

    raise PredicateError(f"cannot compile spatial op {op!r} to SQL")


# ── CQL2-text 编译器（OGC API Features filter，filter-lang=cql2-text）─────────


def _cql2_quote(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if any(ch in s for ch in "\r\n\x00"):
        raise PredicateError("control characters not allowed in string value")
    return "'" + s.replace("'", "''") + "'"


def _cql2_number(v: Any) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PredicateError(f"numeric literal expected, got {v!r}")
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        raise PredicateError("non-finite number")
    return repr(v)


def compile_predicate_cql2(node: Any) -> str:
    op = node.op
    if op == "and":
        return "(" + " AND ".join(compile_predicate_cql2(a) for a in node.args) + ")"
    if op == "or":
        return "(" + " OR ".join(compile_predicate_cql2(a) for a in node.args) + ")"
    if op == "not":
        return "(NOT " + compile_predicate_cql2(node.arg) + ")"
    prop = node.field
    if op == "eq":
        return "NULL" if node.value is None else f"{prop} = {_cql2_quote(node.value)}"
    if op == "ne":
        return "NULL" if node.value is None else f"{prop} <> {_cql2_quote(node.value)}"
    if op == "gt":
        return f"{prop} > {_cql2_number(node.value)}"
    if op == "ge":
        return f"{prop} >= {_cql2_number(node.value)}"
    if op == "lt":
        return f"{prop} < {_cql2_number(node.value)}"
    if op == "le":
        return f"{prop} <= {_cql2_number(node.value)}"
    if op == "in":
        return f"{prop} IN ({', '.join(_cql2_quote(v) for v in node.values)})"
    if op == "not_in":
        return f"{prop} NOT IN ({', '.join(_cql2_quote(v) for v in node.values)})"
    if op == "between":
        return f"{prop} BETWEEN {_cql2_number(node.low)} AND {_cql2_number(node.high)}"
    if op == "like":
        return f"{prop} LIKE {_cql2_quote(node.pattern)}"
    if op == "is_null":
        return f"{prop} IS{' NOT' if node.negated else ''} NULL"
    raise PredicateError(f"cannot compile op {op!r} to CQL2")


def compile_bbox_cql2(bbox: Sequence[float]) -> str:
    minx, miny, maxx, maxy = bbox
    return (
        f"INTERSECTS(BBOX(), {_cql2_number(minx)}, {_cql2_number(miny)}, "
        f"{_cql2_number(maxx)}, {_cql2_number(maxy)})"
    )


# ── ArcGIS where 编译器 ─────────────────────────────────────────────────────


def _arcgis_quote(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if any(ch in s for ch in "\r\n\x00"):
        raise PredicateError("control characters not allowed in string value")
    return "'" + s.replace("'", "''") + "'"


def compile_predicate_arcgis(node: Any) -> str:
    op = node.op
    if op == "and":
        return "(" + " AND ".join(compile_predicate_arcgis(a) for a in node.args) + ")"
    if op == "or":
        return "(" + " OR ".join(compile_predicate_arcgis(a) for a in node.args) + ")"
    if op == "not":
        return "(NOT " + compile_predicate_arcgis(node.arg) + ")"
    f = node.field
    if op == "eq":
        return "NULL" if node.value is None else f"{f} = {_arcgis_quote(node.value)}"
    if op == "ne":
        return "NULL" if node.value is None else f"{f} <> {_arcgis_quote(node.value)}"
    if op == "gt":
        return f"{f} > {_arcgis_quote(node.value)}"
    if op == "ge":
        return f"{f} >= {_arcgis_quote(node.value)}"
    if op == "lt":
        return f"{f} < {_arcgis_quote(node.value)}"
    if op == "le":
        return f"{f} <= {_arcgis_quote(node.value)}"
    if op == "in":
        return f"{f} IN ({', '.join(_arcgis_quote(v) for v in node.values)})"
    if op == "not_in":
        return f"{f} NOT IN ({', '.join(_arcgis_quote(v) for v in node.values)})"
    if op == "between":
        return f"{f} BETWEEN {_arcgis_quote(node.low)} AND {_arcgis_quote(node.high)}"
    if op == "like":
        return f"UPPER({f}) LIKE UPPER({_arcgis_quote(node.pattern)})"
    if op == "is_null":
        return f"{f} IS{' NOT' if node.negated else ''} NULL"
    raise PredicateError(f"cannot compile op {op!r} to ArcGIS where")


# ── FES XML 编译器（WFS GetFeature POST filter，模板化 + 实体转义）──────────

_FES_OPS = {
    "eq": "PropertyIsEqualTo",
    "ne": "PropertyIsNotEqualTo",
    "gt": "PropertyIsGreaterThan",
    "ge": "PropertyIsGreaterThanOrEqualTo",
    "lt": "PropertyIsLessThan",
    "le": "PropertyIsLessThanOrEqualTo",
    "like": "PropertyIsLike",
    "between": "PropertyIsBetween",
}


def _fes_value(v: Any) -> str:
    if v is None:
        return ""
    return _xml_escape(str(v), {'"': "&quot;", "'": "&apos;"})


def compile_predicate_fes(
    node: Any,
    *,
    ns: str = "http://www.opengis.net/ogc",
) -> str:
    op = node.op
    if op in ("and", "or"):
        tag = "And" if op == "and" else "Or"
        inner = "".join(compile_predicate_fes(a, ns=ns) for a in node.args)
        return f'<ogc:{tag} xmlns:ogc="{ns}">' + inner + f"</ogc:{tag}>"
    if op == "not":
        return f'<ogc:Not xmlns:ogc="{ns}">' + compile_predicate_fes(node.arg, ns=ns) + "</ogc:Not>"
    if op == "is_null":
        tag = "PropertyIsNull" if not node.negated else "PropertyIsNotNull"
        return f'<ogc:{tag} xmlns:ogc="{ns}"><ogc:PropertyName>{_fes_value(node.field)}</ogc:PropertyName></ogc:{tag}>'
    if op == "in":
        # IN → OR of equality（FES 1.1/2.0 无原生 IN）
        or_node = Or(args=[Eq(field=node.field, value=v) for v in node.values])
        return compile_predicate_fes(or_node, ns=ns)
    if op == "not_in":
        and_node = And(args=[Ne(field=node.field, value=v) for v in node.values])
        return compile_predicate_fes(and_node, ns=ns)
    if op == "like":
        return (
            f'<ogc:PropertyIsLike xmlns:ogc="{ns}" wildCard="%" singleChar="_" escapeChar="!">'
            f"<ogc:PropertyName>{_fes_value(node.field)}</ogc:PropertyName>"
            f"<ogc:Literal>{_fes_value(node.pattern)}</ogc:Literal></ogc:PropertyIsLike>"
        )
    if op == "between":
        return (
            f'<ogc:PropertyIsBetween xmlns:ogc="{ns}">'
            f"<ogc:PropertyName>{_fes_value(node.field)}</ogc:PropertyName>"
            f"<ogc:LowerBoundary><ogc:Literal>{_fes_value(node.low)}</ogc:Literal></ogc:LowerBoundary>"
            f"<ogc:UpperBoundary><ogc:Literal>{_fes_value(node.high)}</ogc:Literal></ogc:UpperBoundary>"
            f"</ogc:PropertyIsBetween>"
        )
    if op in _FES_OPS:
        tag = _FES_OPS[op]
        if op == "like":
            val = node.pattern
        else:
            val = node.value
        return (
            f'<ogc:{tag} xmlns:ogc="{ns}">'
            f"<ogc:PropertyName>{_fes_value(node.field)}</ogc:PropertyName>"
            f"<ogc:Literal>{_fes_value(val)}</ogc:Literal></ogc:{tag}>"
        )
    raise PredicateError(f"cannot compile op {op!r} to FES")


def compile_bbox_fes(bbox: Sequence[float], *, srs_name: str = "urn:ogc:def:crs:OGC:1.3:CRS84") -> str:
    minx, miny, maxx, maxy = bbox
    return (
        '<ogc:BBOX xmlns:ogc="http://www.opengis.net/ogc">'
        '<ogc:PropertyName></ogc:PropertyName>'
        f'<gml:Envelope xmlns:gml="http://www.opengis.net/gml" srsName="{_xml_escape(srs_name)}">'
        f"<gml:lowerCorner>{minx} {miny}</gml:lowerCorner>"
        f"<gml:upperCorner>{maxx} {maxy}</gml:upperCorner>"
        "</gml:Envelope></ogc:BBOX>"
    )


__all__ = [
    "quote_ident",
    "geojson_to_wkt",
    "compile_predicate_sql",
    "compile_temporal_sql",
    "compile_spatial_sql",
    "compile_predicate_cql2",
    "compile_bbox_cql2",
    "compile_predicate_arcgis",
    "compile_predicate_fes",
    "compile_bbox_fes",
]
