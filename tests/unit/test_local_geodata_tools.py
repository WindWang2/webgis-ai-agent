"""本地地理数据工具契约：行政区 SHP 查询 + OSM 主题查询 + 小型 ETL 端到端。

fixture 全部在 tmp_path 内合成（微型 shapefile / GPKG / 甚至 PBF——pyosmium
SimpleWriter 产 3 节点 1 线），不依赖 1.5GB 真实数据；settings 指向 tmp 目录。
"""
import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, box

from app.core.config import settings


# ── fixture 数据合成 ──────────────────────────────────────────────────────


def _write_admin_shp(root, sub, fname, rows, name_col, extra_cols):
    gdf = gpd.GeoDataFrame(
        pd.DataFrame(rows),
        geometry="geometry",
        crs="EPSG:4326",
    )
    target = root / "ChinaAdminDivisonSHP" / sub
    target.mkdir(parents=True, exist_ok=True)
    gdf.to_file(target / fname, driver="ESRI Shapefile", encoding="utf-8")


@pytest.fixture
def admin_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_GEODATA_DIR", str(tmp_path), raising=False)
    from app.tools import local_admin

    local_admin._reset_cache_for_tests()

    _write_admin_shp(
        tmp_path, "2. Province", "province.shp",
        [
            {"pr_name": "四川省", "adcode": "510000",
             "geometry": box(101, 28, 108, 33)},
            {"pr_name": "广东省", "adcode": "440000",
             "geometry": box(112, 21, 117, 26)},
        ],
        "pr_name", ["adcode"],
    )
    _write_admin_shp(
        tmp_path, "3. City", "city.shp",
        [
            {"ct_name": "成都市", "pr_name": "四川省", "adcode": "510100",
             "geometry": box(103.0, 30.5, 104.5, 31.5)},
            {"ct_name": "绵阳市", "pr_name": "四川省", "adcode": "510700",
             "geometry": box(104.5, 31.0, 106.0, 32.5)},
            {"ct_name": "广州市", "pr_name": "广东省", "adcode": "440100",
             "geometry": box(112.8, 22.5, 114.0, 23.9)},
        ],
        "ct_name", ["pr_name", "adcode"],
    )
    _write_admin_shp(
        tmp_path, "4. District", "district.shp",
        [
            {"dt_name": "锦江区", "ct_name": "成都市", "pr_name": "四川省", "adcode": "510104",
             "geometry": box(104.0, 30.6, 104.2, 30.8)},
            {"dt_name": "武侯区", "ct_name": "成都市", "pr_name": "四川省", "adcode": "510107",
             "geometry": box(103.9, 30.5, 104.1, 30.7)},
            {"dt_name": "越秀区", "ct_name": "广州市", "pr_name": "广东省", "adcode": "440104",
             "geometry": box(113.2, 23.1, 113.4, 23.3)},
        ],
        "dt_name", ["ct_name", "pr_name", "adcode"],
    )
    yield tmp_path
    local_admin._reset_cache_for_tests()


@pytest.fixture
def registry(admin_env):
    from app.tools.registry import ToolRegistry
    from app.tools.local_admin import register_local_admin_tools
    from app.tools.local_osm import register_local_osm_tools

    r = ToolRegistry()
    register_local_admin_tools(r)
    register_local_osm_tools(r)
    return r


# ── 行政区查询 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_boundary_by_name(registry, admin_env):
    res = await registry.dispatch(
        "get_local_admin_boundary",
        {"name": "成都市", "level": "city"},
        session_id="s-test",
    )
    assert res["count"] == 1
    assert res["features"][0]["properties"]["ct_name"] == "成都市"
    assert res["type"] == "FeatureCollection"
    assert res["total_bounds"] == [103.0, 30.5, 104.5, 31.5]


@pytest.mark.asyncio
async def test_admin_boundary_by_adcode_exact(registry):
    res = await registry.dispatch(
        "get_local_admin_boundary",
        {"adcode": "510104", "level": "district"},
    )
    assert res["count"] == 1
    assert res["features"][0]["properties"]["dt_name"] == "锦江区"


@pytest.mark.asyncio
async def test_admin_boundary_validation_errors(registry):
    res = await registry.dispatch(
        "get_local_admin_boundary", {"name": "x", "level": "continent"})
    assert "不支持的级别" in res["error"]
    res = await registry.dispatch("get_local_admin_boundary", {"level": "city"})
    assert "至少提供一个" in res["error"]
    res = await registry.dispatch(
        "get_local_admin_boundary", {"name": "纽约市", "level": "city"})
    assert "未找到" in res["error"]


@pytest.mark.asyncio
async def test_admin_boundary_to_wgs84_shifts_coords(registry):
    native = await registry.dispatch(
        "get_local_admin_boundary", {"name": "成都市", "level": "city"})
    shifted = await registry.dispatch(
        "get_local_admin_boundary", {"name": "成都市", "level": "city", "to_wgs84": True})
    n0 = native["features"][0]["geometry"]["coordinates"]
    s0 = shifted["features"][0]["geometry"]["coordinates"]

    def first_point(coords):
        while isinstance(coords[0], list):
            coords = coords[0]
        return coords

    assert first_point(n0) != first_point(s0), "GCJ→WGS 转换应产生坐标偏移"
    # GCJ-02 偏移量级 ~100-700m（<0.01°）
    dx = abs(first_point(n0)[0] - first_point(s0)[0])
    assert 0.00001 < dx < 0.01


@pytest.mark.asyncio
async def test_admin_children_by_city(registry):
    res = await registry.dispatch(
        "get_local_child_districts",
        {"parent_name": "成都市", "parent_level": "city"},
    )
    assert res["count"] == 2
    names = {f["properties"]["dt_name"] for f in res["features"]}
    assert names == {"锦江区", "武侯区"}


@pytest.mark.asyncio
async def test_admin_data_missing_honest_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_GEODATA_DIR", str(tmp_path), raising=False)
    from app.tools import local_admin

    local_admin._reset_cache_for_tests()
    try:
        from app.tools.registry import ToolRegistry
        from app.tools.local_admin import register_local_admin_tools

        r = ToolRegistry()
        register_local_admin_tools(r)
        res = await r.dispatch(
            "get_local_admin_boundary", {"name": "成都市", "level": "city"})
        assert "不可用" in res["error"]
        assert "correction_hint" in res
    finally:
        local_admin._reset_cache_for_tests()


@pytest.mark.asyncio
async def test_admin_level_cached_after_first_read(registry, admin_env, monkeypatch):
    calls = {"n": 0}
    orig = gpd.read_file

    def counting_read(*a, **kw):
        calls["n"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(gpd, "read_file", counting_read)

    await registry.dispatch(
        "get_local_admin_boundary", {"name": "成都市", "level": "city"})
    assert calls["n"] == 1, "首次查询应触发一次磁盘读取"

    for _ in range(3):
        await registry.dispatch(
            "get_local_admin_boundary", {"name": "绵阳市", "level": "city"})
    assert calls["n"] == 1, "同 level 的后续查询必须命中进程内缓存"


# ── OSM 查询 + ETL 端到端（合成 PBF）────────────────────────────────────


@pytest.fixture
def osm_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_GEODATA_DIR", str(tmp_path), raising=False)
    return tmp_path


def _make_synthetic_pbf(path):
    # pyosmium SimpleWriter 鸭子类型：任意带 id/location/tags 属性的对象即可。
    from types import SimpleNamespace

    import osmium

    writer = osmium.SimpleWriter(str(path))
    try:
        for i, (lon, lat, tags) in enumerate([
            (104.08, 30.65, {"amenity": "restaurant", "name": "春熙路餐厅"}),
            (104.09, 30.66, {"shop": "supermarket", "name": "伊藤洋华堂"}),
            (104.10, 30.67, {"name": "无主题节点"}),
            (113.28, 23.13, {"amenity": "hospital", "name": "广州医院"}),
        ], start=1):
            writer.add_node(SimpleNamespace(
                id=i, location=osmium.osm.Location(lon, lat), tags=tags))
        writer.add_way(SimpleNamespace(
            id=101, nodes=[1, 2],
            tags={"highway": "primary", "name": "人民南路"}))
        writer.add_way(SimpleNamespace(
            id=102, nodes=[2, 3],
            tags={"waterway": "river", "name": "府河"}))
    finally:
        writer.close()


@pytest.mark.asyncio
async def test_osm_ingest_end_to_end_and_query(osm_env, tmp_path):
    from app.services.local_osm import (
        catalog,
        ingest_pbf,
        query_osm_features,
        theme_gpkg_path,
    )

    pbf = tmp_path / "mini.osm.pbf"
    _make_synthetic_pbf(pbf)
    result = ingest_pbf(pbf, ["pois", "roads", "waterways"], flush_rows=2)
    assert result["themes"]["pois"]["rows"] == 3
    assert result["themes"]["roads"]["rows"] == 1
    assert result["themes"]["waterways"]["rows"] == 1
    for theme in ("pois", "roads", "waterways"):
        assert theme_gpkg_path(theme).exists()

    cat = catalog()
    available = {row["theme"]: row for row in cat["themes"]}
    assert available["pois"]["available"] is True
    assert available["pois"]["feature_count"] == 3
    assert available["railways"]["available"] is False

    # bbox（成都范围）过滤掉广州节点
    chengdu = await _q("pois", [104.05, 30.60, 104.12, 30.70])
    assert chengdu["count"] == 2
    names = {f["properties"]["name"] for f in chengdu["features"]}
    assert "广州医院" not in names

    # name_like
    named = await _q("pois", [104.05, 30.60, 104.12, 30.70], name_like="春熙")
    assert named["count"] == 1
    assert named["features"][0]["properties"]["category"] == "restaurant"

    # tag 过滤（category 精确）
    cafes = await _q("pois", [104.05, 30.60, 104.12, 30.70], tag="shop=supermarket")
    assert cafes["count"] == 1

    # 主题未导入 → 诚实错误
    missing = query_osm_features("railways", [104, 30, 105, 31])
    assert "尚未导入" in missing["error"]
    assert "correction_hint" in missing

    # 非法 bbox
    bad = query_osm_features("pois", [200, 30, 210, 31])
    assert "不合法" in bad["error"]
    bad2 = query_osm_features("pois", "not-a-bbox")
    assert "bbox" in bad2["error"]

    # 未知主题
    unknown = query_osm_features("buildings", [104, 30, 105, 31])
    assert "未知主题" in unknown["error"]


async def _q(theme, bbox, name_like=None, tag=None, limit=200):
    from app.services.local_osm import query_osm_features

    return query_osm_features(theme, bbox, name_like=name_like, tag=tag, limit=limit)


@pytest.mark.asyncio
async def test_osm_tools_registered_and_dispatchable(osm_env, tmp_path, admin_env):
    from app.services.local_osm import ingest_pbf
    from app.tools.registry import ToolRegistry
    from app.tools.local_osm import register_local_osm_tools

    pbf = tmp_path / "mini2.osm.pbf"
    _make_synthetic_pbf(pbf)
    ingest_pbf(pbf, ["pois"])

    r = ToolRegistry()
    register_local_osm_tools(r)
    res = await r.dispatch(
        "query_local_osm",
        {"theme": "pois", "bbox": [104.05, 30.60, 104.12, 30.70]},
    )
    assert res["count"] >= 1
    cat = await r.dispatch("get_local_osm_catalog", {})
    assert any(row["theme"] == "pois" and row["available"] for row in cat["themes"])


# ── HTTP 路由直调 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_data_routes_direct(admin_env, osm_env, tmp_path):
    from app.api.routes.local_data import (
        get_admin_boundary,
        get_admin_children,
        get_osm_catalog,
    )

    res = await get_admin_boundary(
        level="city", name="成都市", to_wgs84=False, simplified=False, _user={}
    )
    assert res["count"] == 1

    res = await get_admin_children(
        parent_name="成都市", parent_level="city", to_wgs84=False, simplified=False, _user={}
    )
    assert res["count"] == 2

    cat = await get_osm_catalog(_user={})
    assert "themes" in cat
