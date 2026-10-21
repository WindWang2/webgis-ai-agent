"""Issue #1682：dwithin 的 geom::geography cast 与 geography 表达式索引。

锁定三件事：
1. describe 的 GiST 探测之后追加 ``(geom::geography)`` 表达式索引探测，
   ``_TableMeta.has_geography_index`` 如实反映探测结果（None=探测失败）；
2. ``index_suggestion`` 分型：无几何索引 → ``GIST(geom)``；有几何索引但无
   geography 表达式索引 → ``GIST((geom::geography))`` 并披露 cast 语义
   （普通 GiST 服务不了 dwithin，旧建议是「用不上的索引」）；
3. planner 对「有普通 GiST 但缺 geography 表达式索引」的 4326 dwithin 下推
   发 full-scan 警告（旧行为：``has_geometry_index=True`` 让警告永不触发）。

未覆盖（需要真实 ≥10 万行 PostGIS 实例，CI 无）：建索引前后
``EXPLAIN (ANALYZE, BUFFERS)`` 的 Seq Scan → Index Scan 对比。
"""
from __future__ import annotations

import pytest

from app.schemas.data_fabric_schema import DatasetDescriptor
from app.services.data_fabric.adapters.postgis_adapter import (
    PostGISAdapter,
    reset_postgis_meta_cache,
)
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.models import QuerySpecV2
from app.services.data_fabric.query.planner import plan_query


@pytest.fixture(autouse=True)
def _isolated_meta_cache():
    reset_postgis_meta_cache()
    yield
    reset_postgis_meta_cache()


class _RoutingCursor:
    """按 SQL 模式应答的假游标；区分普通 GiST 与 geography 表达式索引探测。

     geography 探测的 ILIKE 模式是**绑定参数**（``%(geom::geography)%``），
     SQL 文本里没有 ``::geography``——只能按 params 区分两次 pg_indexes 探测。
    """

    def __init__(self, executed, *, srid=4326, geom_index=True, geog_index=False):
        self._executed = executed
        self._srid = srid
        self._geom_index = geom_index
        self._geog_index = geog_index
        self.description: list = []
        self._result = None

    def execute(self, sql, params=()):
        self._executed.append((sql, params))
        sql_l = sql.lower() if isinstance(sql, str) else ""
        self.description = []
        self._result = None
        if "information_schema.columns" in sql_l:
            self.description = [("name",), ("type",)]
            self._result = [("id", "integer"), ("name", "text"), ("geom", "geometry")]
        elif "f_geometry_column, srid, type" in sql_l:
            self._result = ("geom", self._srid, "POINT")
        elif "pg_stats" in sql_l:
            self._result = []
        elif "pg_indexes" in sql_l:
            is_geog = any(isinstance(p, str) and "::geography" in p for p in params)
            hit = self._geog_index if is_geog else self._geom_index
            self._result = [("1",)] if hit else []
        elif "indisprimary" in sql_l:
            self._result = [("id",)]
        elif "reltuples" in sql_l:
            self.description = [("reltuples",)]
            self._result = [(100000,)]
        elif "estimatedextent" in sql_l:
            self._result = (100.0, 30.0, 105.0, 32.0)
        else:
            self._result = None

    def fetchone(self):
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def fetchall(self):
        return self._result if isinstance(self._result, list) else []


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
    return adapter


def _geography_probes(executed):
    return [
        (sql, p) for sql, p in executed
        if "pg_indexes" in sql.lower()
        and any(isinstance(x, str) and "::geography" in x for x in p)
    ]


# ── 探测面 ──────────────────────────────────────────────────────────────────


def test_geography_expression_index_probed_after_gist():
    executed: list = []
    a = _adapter(executed, geom_index=True, geog_index=False)
    desc = a.describe("public.schools")
    probes = _geography_probes(executed)
    assert probes, "geography expression index probe must be issued"
    sql, params = probes[0]
    assert "using gist" in sql.lower()
    assert params[2] == "%(geom::geography)%"
    assert desc.metadata["has_geometry_index"] is True
    assert desc.metadata["has_geography_index"] is False


def test_geography_index_detected_when_present():
    a = _adapter([], geom_index=True, geog_index=True)
    desc = a.describe("public.schools")
    assert desc.metadata["has_geography_index"] is True


# ── 索引建议分型 ────────────────────────────────────────────────────────────


def test_suggestion_is_geography_expression_index_when_plain_gist_exists():
    a = _adapter([], geom_index=True, geog_index=False)
    desc = a.describe("public.schools")
    assert desc.metadata["index_suggestion"] == (
        'CREATE INDEX ON "public"."schools" USING GIST ((geom::geography));'
    )
    note = desc.metadata.get("spatial_index_note", "")
    assert "::geography" in note, "cast semantics must be disclosed"
    assert "cannot serve" in note
    assert "seq scan" in note


def test_suggestion_is_plain_gist_when_no_geometry_index():
    a = _adapter([], geom_index=False, geog_index=False)
    desc = a.describe("public.schools")
    assert desc.metadata["has_geometry_index"] is False
    assert desc.metadata["index_suggestion"] == (
        'CREATE INDEX ON "public"."schools" USING GIST ("geom");'
    )
    assert "spatial_index_note" not in desc.metadata


def test_no_suggestion_when_geography_index_present():
    a = _adapter([], geom_index=True, geog_index=True)
    desc = a.describe("public.schools")
    assert "index_suggestion" not in desc.metadata


# ── planner 告警（查询面披露） ───────────────────────────────────────────────


def _descriptor(meta=None, srs="EPSG:4326"):
    return DatasetDescriptor(
        id="ds-1682",
        source_type="postgis",
        srs=srs,
        fields=[{"name": "geom", "type": "geometry"}],
        metadata=meta or {},
    )


def _dwithin_spec() -> QuerySpecV2:
    return QuerySpecV2(spatial={
        "op": "dwithin",
        "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
        "distance": 100.0,
    })


def test_planner_warns_cast_index_missing():
    plan = plan_query(
        _dwithin_spec(),
        _descriptor({"has_geometry_index": True, "has_geography_index": False}),
        caps=get_capabilities("postgis"),
    )
    assert any("geom::geography" in w for w in plan.warnings)


def test_planner_silent_when_geography_index_present():
    plan = plan_query(
        _dwithin_spec(),
        _descriptor({"has_geometry_index": True, "has_geography_index": True}),
        caps=get_capabilities("postgis"),
    )
    assert not any("geom::geography" in w for w in plan.warnings)


def test_planner_silent_for_projected_srid():
    # 投影列的 dwithin 编译为平面 ST_DWithin(geom, geom)，普通 GiST 可服务。
    plan = plan_query(
        _dwithin_spec(),
        _descriptor(
            {"has_geometry_index": True, "has_geography_index": False},
            srs="EPSG:3857",
        ),
        caps=get_capabilities("postgis"),
    )
    assert not any("geom::geography" in w for w in plan.warnings)
