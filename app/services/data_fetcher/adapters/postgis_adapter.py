"""PostGIS adapter — 修复 A5 SQL 注入。

原实现把 table / geom_col / properties / filter 全部直接 f-string 拼进 SQL，
任何调用方都能注入。本版做三件事：

1. 标识符白名单：table / geom_col / properties 列必须匹配 [A-Za-z_][A-Za-z0-9_]*；
   多列 properties 用逗号分隔，每列单独校验。Schema-qualified `schema.table`
   也用白名单形式拆开校验。
2. bbox 全部按命名绑定参数 :minx/:miny/:maxx/:maxy 注入。
3. 旧的自由 filter 字符串接口删掉 — 替换为结构化 `filter_eq` (列=值)，
   值走绑定参数，列名也走白名单。需要更复杂条件请扩字段，**禁止再走原 raw 路径**。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable

from sqlalchemy import text

from app.tools._utils import db_session
from .base import DataSourceAdapter


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_ident(name: str, kind: str = "identifier") -> str:
    """白名单校验单个 SQL 标识符。不合规直接抛异常，避免静默注入。"""
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"非法 {kind} 名: {name!r}（只允许字母/数字/下划线，首字符非数字）")
    return name


def _validate_table(name: str) -> str:
    """支持可选的 schema 前缀（schema.table），两段都走白名单。"""
    if "." in name:
        parts = name.split(".")
        if len(parts) != 2:
            raise ValueError(f"非法 table 名: {name!r}")
        return f"{_validate_ident(parts[0], 'schema')}.{_validate_ident(parts[1], 'table')}"
    return _validate_ident(name, "table")


def _validate_properties(properties: str) -> list[str] | None:
    """`'*'` 返回 None；否则按逗号拆分并校验每列。"""
    properties = (properties or "").strip()
    if properties in ("", "*"):
        return None
    cols: list[str] = []
    for raw in properties.split(","):
        col = raw.strip()
        if not col:
            continue
        cols.append(_validate_ident(col, "column"))
    if not cols:
        return None
    return cols


def _bbox_values(bbox: Iterable) -> tuple[float, float, float, float]:
    parts = list(bbox)
    if len(parts) != 4:
        raise ValueError("bbox 必须是 4 元组 [minx, miny, maxx, maxy]")
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


class PostGISAdapter(DataSourceAdapter):
    def query(self, query_params: Dict[str, Any]) -> Any:
        """
        Query PostGIS:
            table             : 必填，受白名单约束
            geometry_column   : 默认 'geom'，受白名单
            properties        : '*' 或 'col1,col2'，每列受白名单
            bbox              : [minx, miny, maxx, maxy] WGS84，自动参数绑定
            filter_eq         : {col: value}，受白名单 + 绑定参数
        """
        table_raw = query_params.get("table")
        if not table_raw:
            raise ValueError("table is required for PostGIS query")

        table = _validate_table(str(table_raw))
        geom_col = _validate_ident(str(query_params.get("geometry_column") or "geom"), "geometry_column")
        properties = _validate_properties(str(query_params.get("properties", "*")))
        bbox = query_params.get("bbox")
        filter_eq = query_params.get("filter_eq") or {}
        if not isinstance(filter_eq, dict):
            raise ValueError("filter_eq 必须是字典 {col: value}")

        # 构造 SELECT 子句：列受白名单 / `*` 走 to_jsonb 移除几何列
        if properties is None:
            props_sql = f"to_jsonb(t) - '{geom_col}'"
        else:
            # 安全：properties 已逐列白名单校验，可直接拼入
            pairs = ", ".join([f"'{c}', t.{c}" for c in properties])
            props_sql = f"json_build_object({pairs})"

        where_clauses: list[str] = ["TRUE"]
        params: dict[str, Any] = {}
        if bbox is not None:
            minx, miny, maxx, maxy = _bbox_values(bbox)
            params.update(minx=minx, miny=miny, maxx=maxx, maxy=maxy)
            where_clauses.append(
                f"ST_Intersects(t.{geom_col}, ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326))"
            )
        for i, (col, val) in enumerate(filter_eq.items()):
            col_ok = _validate_ident(str(col), "filter column")
            key = f"feq_{i}"
            params[key] = val
            where_clauses.append(f"t.{col_ok} = :{key}")

        where_sql = " AND ".join(where_clauses)
        sql = text(
            f"""
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(
                    json_agg(
                        json_build_object(
                            'type', 'Feature',
                            'geometry', ST_AsGeoJSON(t.{geom_col})::json,
                            'properties', {props_sql}
                        )
                    ),
                    '[]'::json
                )
            ) as geojson
            FROM {table} t
            WHERE {where_sql}
            """
        )

        with db_session() as db:
            result = db.execute(sql, params)
            geojson = result.scalar_one_or_none()
        return geojson or {"type": "FeatureCollection", "features": []}
