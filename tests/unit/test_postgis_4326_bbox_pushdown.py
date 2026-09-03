"""Regression test for issue #603 (PostGIS bbox pushdown dead branch) — V2 form.

The old guard ``col_srid not in (-1, 4326)`` excluded the most common case —
EPSG:4326 tables — from bbox pushdown entirely. V2 (ADR-0094) routes bbox
through the typed AST spatial compiler; these tests keep asserting the three
#603 semantics against the compiled SQL:

1. EPSG:4326 tables push ``ST_MakeEnvelope(...4326)`` with NO transform;
2. unknown SRID (0) cannot express an envelope → no spatial predicate, and
   metadata/planner honestly report ``pushdown_bbox=False``;
3. projected tables transform the envelope into the column SRID.
"""
from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
import pytest

from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter
from app.services.data_fabric.query.capabilities import default_capabilities


class _RoutingCursor:
    """Fake cursor answering the V2 adapter's catalog/meta/query SQL by pattern."""

    def __init__(self, srid, executed, rows=None):
        self._srid = srid
        self._executed = executed
        self._rows = rows if rows is not None else []
        self.description: list = []
        self._result = None

    def execute(self, sql, params=()):
        self._executed.append((sql, params))
        self.description = []
        self._result = None
        sql_l = sql.lower() if isinstance(sql, str) else ""
        if "information_schema.columns" in sql_l:
            self.description = [("name",), ("type",)]
            self._result = [("name", "text"), ("geom", "geometry")]
        elif "from geometry_columns" in sql_l and "f_geometry_column, srid, type" in sql_l:
            self._result = ("geom", self._srid, "POINT")
        elif "geometry_columns" in sql_l:
            self._result = ("geom", self._srid)
        elif "pg_index" in sql_l:
            self._result = []
        elif "pg_indexes" in sql_l:
            self._result = None
        elif "count(*)" in sql_l:
            self._result = (123,)
        elif "estimatedextent" in sql_l:
            self._result = None
        elif "st_asmvt" in sql_l:
            self._result = None
        else:
            self.description = [("name",), ("_geojson",)]
            self._result = self._rows

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
    def __init__(self, srid, executed, rows=None):
        self._srid = srid
        self._executed = executed
        self._rows = rows

    def cursor(self):
        return _RoutingCursor(self._srid, self._executed, self._rows)

    def rollback(self):
        pass


def _adapter_with_srid(srid, executed, rows=None):
    """PostGISAdapter whose geometry_columns row reports the given SRID."""
    adapter = PostGISAdapter.__new__(PostGISAdapter)

    class _ConnCtx:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return _FakeConn(srid, executed, rows)

        def __exit__(self, *a):
            return None

    adapter._connection_context = _ConnCtx
    adapter._meta_cache = {}
    adapter._caps = default_capabilities("postgis")
    adapter.profile = ConnectionProfile(id="p_test", source_type="postgis")
    return adapter


def test_4326_table_bbox_pushdown_uses_envelope_without_transform():
    """#603: the most common case — EPSG:4326 — must get a real spatial predicate."""
    executed: list = []
    adapter = _adapter_with_srid(4326, executed, rows=[("road1", '{"type":"Point","coordinates":[116.5,39.5]}')])

    res = adapter.query("public.roads", QuerySpec(bbox=[116.0, 39.0, 117.0, 40.0]))

    env_sql = [sql for sql, _ in executed if "ST_MakeEnvelope" in sql]
    assert env_sql, "4326 table with bbox must push down ST_MakeEnvelope"
    assert "ST_Transform(ST_MakeEnvelope" not in env_sql[0], (
        "4326 needs NO transform — envelope is already WGS84"
    )
    assert any("ST_Intersects" in sql for sql, _ in executed)
    # 语义保留（V2 通过 planner/plan 元数据报告）：spatial pushdown 实际发生
    assert res.metadata["pushdown_bbox"] is True
    assert res.features and res.features[0]["properties"]["name"] == "road1"
    # V2 投影：SELECT 不再是 `SELECT *, geom`（几何只经 _geojson 一次）
    main_sql = [sql for sql, _ in executed if 'FROM "public"."roads"' in sql and sql.strip().startswith("SELECT")]
    assert main_sql, "main feature query must have executed"
    assert not any(
        f'"{c}"' in main_sql[0] and "geom" == c for c in ["geom"]
    ) or 'ST_AsGeoJSON("geom"' in main_sql[0]
    assert "*, " not in main_sql[0] and ", *" not in main_sql[0], (
        "V2 must project explicit columns, not SELECT *"
    )


def test_unknown_srid_table_skips_pushdown_and_reports_honestly():
    """#603: unknown SRID cannot push down — report it honestly."""
    executed: list = []
    adapter = _adapter_with_srid("0", executed, rows=[("r", None)])

    res = adapter.query("public.roads_unknown", QuerySpec(bbox=[116.0, 39.0, 117.0, 40.0]))

    assert not [sql for sql, _ in executed if "ST_MakeEnvelope" in sql], (
        "unknown SRID must not emit an envelope predicate"
    )
    assert res.metadata["pushdown_bbox"] is False


def test_projected_table_pushdown_still_transforms_envelope():
    """#603: projected SRIDs keep transforming the envelope into the column SRID."""
    executed: list = []
    adapter = _adapter_with_srid(3857, executed, rows=[("r", None)])

    res = adapter.query("public.roads_3857", QuerySpec(bbox=[116.0, 39.0, 117.0, 40.0]))

    env_sql = [sql for sql, _ in executed if "ST_MakeEnvelope" in sql]
    assert env_sql
    assert "ST_Transform(ST_MakeEnvelope" in env_sql[0], (
        "projected table must transform the 4326 envelope into the column SRID"
    )
    assert res.metadata["pushdown_bbox"] is True


@pytest.fixture(autouse=True)
def _reset_shared_meta_cache():
    """共享 meta cache 是进程级的 —— 跨用例隔离（假连接复用同一 pool key）。"""
    from app.services.data_fabric.adapters.postgis_adapter import reset_postgis_meta_cache

    reset_postgis_meta_cache()
    yield
    reset_postgis_meta_cache()
