"""PostGIS V2 reference adapter tests (ADR-0094 Wave D).

覆盖：AST where 参数化编译、投影下推（无 SELECT *）、稳定排序、
聚合/GROUP BY 下推、STATISTICS 零几何、cursor 分页、确定性采样子句、
statement_timeout、MVT SQL、字段白名单、注入面回归。
"""
import json

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter
from app.services.data_fabric.errors import InvalidQueryError
from app.services.data_fabric.query.capabilities import default_capabilities


class _RoutingCursor:
    """按 SQL 模式应答的假游标（meta/catalog/query/count/agg/mvt）。"""

    def __init__(self, executed, *, srid=4326, pk="id", count=1234, rows=None, agg_rows=None):
        self._executed = executed
        self._srid = srid
        self._pk = pk
        self._count = count
        self._rows = rows if rows is not None else []
        self._agg_rows = agg_rows
        self.description: list = []
        self._result = None

    def execute(self, sql, params=()):
        self._executed.append((sql, params))
        sql_l = sql.lower() if isinstance(sql, str) else ""
        self.description = []
        self._result = None
        if "information_schema.columns" in sql_l:
            self.description = [("name",), ("type",)]
            self._result = [("id", "integer"), ("name", "text"), ("district", "text"),
                            ("students", "integer"), ("geom", "geometry")]
        elif "f_geometry_column, srid, type" in sql_l:
            self._result = ("geom", self._srid, "POINT")
        elif "pg_index" in sql_l:
            self._result = [(self._pk,)]
        elif "pg_indexes" in sql_l:
            self._result = ("CREATE INDEX ... USING GIST (geom)",)
        elif "estimatedextent" in sql_l:
            self._result = (100.0, 30.0, 105.0, 32.0)
        elif "st_asmvt" in sql_l:
            self._result = (b"\x1a\x02tile",)
        elif "count(*)" in sql_l and "group by" not in sql_l:
            self._result = (self._count,)
        elif "group by" in sql_l or "count(" in sql_l or "sum(" in sql_l or "avg(" in sql_l:
            self.description = [("district",), ("count",)]
            self._result = self._agg_rows if self._agg_rows is not None else [("金牛区", 12), ("武侯区", 8)]
        elif "set local statement_timeout" in sql_l:
            self._result = None
        else:
            # 从 SELECT 列表动态推导 description（模拟真实驱动）
            select_part = sql.split(" FROM ")[0].replace("SELECT", "", 1)
            # 括号深度感知切分（ST_AsGeoJSON("geom", 7) 内的逗号不是列分隔）
            frags, depth, buf = [], 0, []
            for ch in select_part:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if ch == "," and depth == 0:
                    frags.append("".join(buf)); buf = []
                else:
                    buf.append(ch)
            if buf:
                frags.append("".join(buf))
            cols = []
            for frag in frags:
                frag = frag.strip()
                if not frag:
                    continue
                alias = None
                if " AS " in frag.upper():
                    idx_as = frag.upper().index(" AS ")
                    alias = frag[idx_as + 4:].strip().strip('"')
                    frag = frag[:idx_as].strip()
                name = alias if alias else frag.strip('"')
                cols.append((name,))
            self.description = cols or [("id",)]
            limit = params[-1] if params and isinstance(params[-1], int) else None
            rows = self._rows
            if ") > (" in sql:
                # 模拟 keyset 排除谓词：首列 > cursor 值（本测试无 WHERE 参数）
                cursor_val = params[0]
                rows = [r for r in rows if r[0] > cursor_val]
            self._result = rows[:limit] if limit is not None else rows

    def fetchone(self):
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def fetchall(self):
        if isinstance(self._result, list):
            return self._result
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


class _FakeConn:
    def __init__(self, cursor: _RoutingCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def rollback(self):
        pass


def _adapter(executed, **cursor_kwargs):
    adapter = PostGISAdapter.__new__(PostGISAdapter)

    class _ConnCtx:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return _FakeConn(_RoutingCursor(executed, **cursor_kwargs))

        def __exit__(self, *a):
            return None

    adapter._connection_context = _ConnCtx
    adapter._meta_cache = {}
    adapter._caps = default_capabilities("postgis")
    adapter.profile = ConnectionProfile(id="p_v2", source_type="postgis")
    return adapter


# ── 投影 & where ─────────────────────────────────────────────────────────────


def test_projection_pushdown_no_select_star():
    executed: list = []
    # 投影顺序：name, _geojson, _cursor_key(pk 不在投影 → 附加为游标键)
    a = _adapter(executed, rows=[("s1", '{"type":"Point","coordinates":[104,30]}', 1)])
    res = a.query("public.schools", QuerySpec(fields=["name"], where="district = '金牛区'", limit=10))

    main = [sql for sql, _ in executed
            if 'FROM "public"."schools"' in sql and sql.strip().startswith("SELECT")
            and "COUNT" not in sql]
    assert main, "feature query must run"
    sql = main[0]
    assert '"name"' in sql and "*, " not in sql and ", *" not in sql
    assert '"students"' not in sql, "unprojected attribute columns must not be fetched"
    assert "ST_AsGeoJSON" in sql
    # geometry 只出现一次（V1 是 SELECT *, geom 双重传输）
    assert sql.count("ST_AsGeoJSON") == 1
    assert res.features[0]["properties"] == {"name": "s1"}
    assert res.metadata["pushdown_projection"] is True


def test_where_ast_parameterized_nested():
    executed: list = []
    a = _adapter(executed, rows=[])
    spec = QuerySpec(where="district = '金牛区' AND students > 500", limit=5)
    a.query("public.schools", spec)
    main = [(sql, p) for sql, p in executed
            if 'FROM "public"."schools"' in sql and "WHERE" in sql and "COUNT" not in sql]
    assert main
    sql, params = main[0]
    for p in params[:2]:
        assert isinstance(p, (str, int)), "filter values must be bound parameters"
    assert "'金牛区'" not in sql and "500" not in sql.split("WHERE")[1].split("LIMIT")[0].replace("%s", "")
    assert " AND " in sql


@pytest.mark.parametrize("evil", [
    "district = 'x'; DROP TABLE users; --",
    "district = 'x' OR '1'='1",
    "district = (SELECT password FROM users)",
    "district = 'x' UNION SELECT 1",
    "district = 'x' /* comment */",
    "district = 'x\\' OR 1=1 --",
])
def test_injection_battery_rejected(evil):
    a = _adapter([])
    with pytest.raises((InvalidQueryError, ValueError)):
        a.query("public.schools", QuerySpec(where=evil, limit=5))


def test_unknown_field_rejected_with_allowlist_hint():
    a = _adapter([])
    with pytest.raises(InvalidQueryError, match="not in table schema|not in dataset schema"):
        a.query("public.schools", QuerySpec(where="evil_col = 'x'", limit=5))


# ── 排序与分页 ───────────────────────────────────────────────────────────────


def test_stable_order_by_pk_appended():
    executed: list = []
    a = _adapter(executed, rows=[])
    a.query("public.schools", QuerySpec(limit=50))
    main = [sql for sql, _ in executed
            if 'FROM "public"."schools"' in sql and sql.strip().startswith("SELECT") and "COUNT" not in sql]
    assert main and "ORDER BY" in main[0], "implicit PK ordering must be appended for stable pagination"


def test_explicit_order_by_pushed():
    executed: list = []
    a = _adapter(executed, rows=[])
    spec = QuerySpec(limit=10, order_by=[{"field": "students", "direction": "desc"}])
    a.query("public.schools", spec)
    main = [sql for sql, _ in executed
            if 'FROM "public"' in sql and "ORDER BY" in sql and "COUNT" not in sql]
    assert main and '"students" desc' in main[0].lower()


def test_cursor_pagination_keyset():
    executed: list = []
    rows = [(i, f"s{i}", '{"type":"Point","coordinates":[104,30]}') for i in range(1, 11)]
    a = _adapter(executed, rows=rows)
    # 第一页
    r1 = a.query("public.schools", QuerySpec(limit=5, page_kind="cursor"))
    assert r1.has_more is True
    assert r1.next_cursor, "keyset cursor must be returned for next page"
    # 第二页使用 cursor
    r2 = a.query("public.schools", QuerySpec(limit=5, page_kind="cursor", cursor=r1.next_cursor))
    page2_sql = [sql for sql, _ in executed if "> (" in sql]
    assert page2_sql, "cursor page must emit keyset exclusion predicate"
    ids1 = [f["properties"]["id"] for f in r1.features]
    ids2 = [f["properties"]["id"] for f in r2.features]
    assert not (set(ids1) & set(ids2)), "cursor pages must not overlap"


def test_total_matching_first_page_count_query():
    executed: list = []
    a = _adapter(executed, rows=[], count=9876)
    res = a.query("public.schools", QuerySpec(limit=10))
    assert res.total_matching == 9876
    assert res.has_more is True
    runtime_counts = [sql for sql, _ in executed
                      if sql.strip().startswith("SELECT COUNT(*) FROM") and not sql.strip().endswith(";")]
    assert len(runtime_counts) == 1, "filtered count runs once on first page"


# ── 聚合 / 统计 ─────────────────────────────────────────────────────────────


def test_aggregation_group_by_pushdown():
    executed: list = []
    a = _adapter(executed, agg_rows=[("金牛区", 120), ("武侯区", 80)])
    spec = QuerySpec(
        aggregate=[{"func": "count"}],
        group_by=["district"],
        limit=100,
    )
    res = a.query("public.schools", spec)
    agg_sql = [sql for sql, _ in executed if "GROUP BY" in sql]
    assert agg_sql, "aggregation must be pushed to the server"
    assert "ST_AsGeoJSON" not in agg_sql[0], "aggregation must not fetch geometry"
    assert res.result_mode == "statistics"
    assert res.data and res.data[0]["district"] == "金牛区"


def test_count_only_no_geometry():
    executed: list = []
    a = _adapter(executed, agg_rows=[(9876,)])
    spec = QuerySpec(aggregate=[{"func": "count"}], limit=1)
    a.query("public.schools", spec)
    assert not any("ST_AsGeoJSON" in sql for sql, _ in executed if "GROUP BY" in sql or "COUNT" in sql)


def test_statistics_result_mode():
    executed: list = []
    a = _adapter(executed, agg_rows=[("x", 1)])
    spec = QuerySpec(limit=10, result_mode="statistics", aggregate=[{"func": "count"}])
    res = a.query("public.schools", spec)
    assert res.result_mode == "statistics"
    assert res.features == []


# ── 结果模式 ────────────────────────────────────────────────────────────────


def test_descriptor_mode_zero_data_transfer():
    executed: list = []
    a = _adapter(executed)
    spec = QuerySpec(limit=10, result_mode="descriptor")
    res = a.query("public.schools", spec)
    assert res.result_mode == "descriptor"
    assert res.features == []
    feature_sqls = [
        sql for sql, _ in executed
        if 'FROM "public"."schools"' in sql and "COUNT" not in sql
    ]
    assert not feature_sqls, "descriptor mode must not run a feature query"


def test_sample_mode_tablesample_deterministic():
    executed: list = []
    a = _adapter(executed, rows=[(1, "s", None)])
    spec1 = QuerySpec(limit=10, result_mode="sample", sample_size=50)
    spec2 = QuerySpec(limit=10, result_mode="sample", sample_size=50)
    a.query("public.schools", spec1)
    a.query("public.schools", spec2)
    sample_sqls = [sql for sql, _ in executed if "TABLESAMPLE" in sql]
    assert len(sample_sqls) == 2
    assert sample_sqls[0] == sample_sqls[1], "sample clause must be deterministic"
    assert "REPEATABLE" in sample_sqls[0]


# ── 预算 / 超时 ─────────────────────────────────────────────────────────────


def test_statement_timeout_applied_from_budget():
    executed: list = []
    a = _adapter(executed, rows=[])
    a.query("public.schools", QuerySpec(limit=10, deadline_s=12.5))
    timeout_calls = [(sql, p) for sql, p in executed if "statement_timeout" in sql]
    assert timeout_calls, "budget deadline must become statement_timeout"
    assert timeout_calls[0][1] == (12500,), "timeout must be a bound parameter"


def test_budget_exceeded_typed_error():
    from app.services.data_fabric.errors import QueryBudgetExceededError

    a = _adapter([])
    spec = QuerySpec(limit=100, offset=500_000, max_rows=100_000)
    with pytest.raises(QueryBudgetExceededError):
        a.query("public.schools", spec)


# ── MVT ─────────────────────────────────────────────────────────────────────


def test_mvt_tile_sql_parameterized_and_bounded():
    executed: list = []
    a = _adapter(executed)
    tile = a.serve_mvt_tile("public.schools", 10, 512, 300)
    assert tile == b"\x1a\x02tile"
    mvt_calls = [(sql, p) for sql, p in executed if "ST_AsMVT" in sql]
    assert mvt_calls
    sql, params = mvt_calls[0]
    assert "ST_TileEnvelope(%s, %s, %s)" in sql
    assert "LIMIT %s" in sql
    assert params[-1] <= 20000, "tile feature cap must be bounded"
    assert sql.count("ST_TileEnvelope") >= 2, "tile envelope used for && and intersects"


def test_mvt_tile_range_validation():
    a = _adapter([])
    with pytest.raises(InvalidQueryError):
        a.serve_mvt_tile("public.schools", 30, 0, 0)
    with pytest.raises(InvalidQueryError):
        a.serve_mvt_tile("public.schools", 5, 100, 0)  # x 越界（2^5=32）


# ── 同源 server join ────────────────────────────────────────────────────────


def test_server_spatial_join_pushdown():
    executed: list = []
    a = _adapter(executed, agg_rows=[("d1", 50)])
    rows = a.server_spatial_join("public.schools", "public.districts", group_by_polygon_field="name")
    assert rows and rows[0]["count"] == 50
    join_sql = [sql for sql, _ in executed if "ST_Within" in sql]
    assert join_sql and "GROUP BY" in join_sql[0].upper()


# ── 证据 ────────────────────────────────────────────────────────────────────


def test_query_evidence_attached():
    executed: list = []
    a = _adapter(executed, rows=[(1, "s", '{"type":"Point","coordinates":[104,30]}')])
    res = a.query("public.schools", QuerySpec(limit=10))
    ev = res.metadata["query_evidence"]
    assert ev["query_fingerprint"]
    assert ev["dataset_fingerprint"]
    assert ev["pushdowns"] == {
        "bbox": False, "filter": False, "projection": False,
        "aggregation": False, "sort": False,
    }
    assert ev["rows_returned"] == 1
    assert ev["dataset_version"]["revision_strength"] == "strong"
    plan = res.metadata["query_plan"]
    assert plan["pagination_strategy"] == "cursor" or plan["pagination_strategy"] == "offset"


def test_legacy_result_fields_preserved():
    executed: list = []
    a = _adapter(executed, rows=[(1, "s", '{"type":"Point","coordinates":[104,30]}')])
    res = a.query("public.schools", QuerySpec(limit=10))
    assert res.total_count == 1
    assert res.returned_count == 1
    assert res.schema_info["columns"]


def test_describe_reports_index_and_pk():
    executed: list = []
    a = _adapter(executed)
    desc = a.describe("public.schools")
    assert desc.metadata["primary_key"] == "id"
    assert desc.metadata["has_geometry_index"] is True
    assert desc.srs == "EPSG:4326"
    assert desc.feature_count == 1234
    assert desc.bbox == [100.0, 30.0, 105.0, 32.0]
