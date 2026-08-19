"""中国境内查询必须先走本地 SHP/GPKG，未命中才允许出网。"""
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, box

from app.core.config import settings


def _write_admin_shp(root, sub, fname, rows):
    gdf = gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs="EPSG:4326")
    target = root / "ChinaAdminDivisonSHP" / sub
    target.mkdir(parents=True, exist_ok=True)
    gdf.to_file(target / fname, driver="ESRI Shapefile", encoding="utf-8")


@pytest.fixture
def local_first_on(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_QUERY_FIRST", True, raising=False)


@pytest.fixture
def geodata_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_GEODATA_DIR", str(tmp_path), raising=False)
    from app.tools import local_admin

    local_admin._reset_cache_for_tests()
    _write_admin_shp(
        tmp_path, "3. City", "city.shp",
        [
            {"ct_name": "成都市", "pr_name": "四川省", "adcode": "510100",
             "geometry": box(103.0, 30.5, 104.5, 31.5)},
        ],
    )
    _write_admin_shp(
        tmp_path, "4. District", "district.shp",
        [
            {"dt_name": "锦江区", "ct_name": "成都市", "pr_name": "四川省", "adcode": "510104",
             "geometry": box(104.0, 30.6, 104.2, 30.8)},
            {"dt_name": "武侯区", "ct_name": "成都市", "pr_name": "四川省", "adcode": "510107",
             "geometry": box(103.9, 30.5, 104.1, 30.7)},
        ],
    )
    gpkg = tmp_path / "osm_gpkg"
    gpkg.mkdir()
    pois = gpd.GeoDataFrame(
        {
            "osm_id": [1, 2],
            "name": ["春熙路餐厅", "四川大学"],
            "category": ["restaurant", "university"],
            "tags": ['{"amenity": "restaurant"}', '{"amenity": "university"}'],
            "geometry": [Point(104.08, 30.65), Point(104.07, 30.66)],
        },
        crs="EPSG:4326",
    )
    pois.to_file(gpkg / "pois.gpkg", layer="pois", driver="GPKG")
    roads = gpd.GeoDataFrame(
        {
            "osm_id": [101],
            "name": ["人民南路"],
            "category": ["primary"],
            "tags": ['{"highway": "primary"}'],
            "geometry": [LineString([(104.06, 30.64), (104.09, 30.66)])],
        },
        crs="EPSG:4326",
    )
    roads.to_file(gpkg / "roads.gpkg", layer="roads", driver="GPKG")
    yield tmp_path
    local_admin._reset_cache_for_tests()


def test_disabled_flag_skips_local(geodata_env):
    from app.services.local_first import try_local_admin_division

    assert try_local_admin_division("成都市") is None


def test_admin_hit_and_overseas_miss(geodata_env, local_first_on):
    from app.services.local_first import try_local_admin_division

    hit = try_local_admin_division("成都市")
    assert hit is not None
    assert hit["source"] == "local_admin"
    assert hit["count"] == 1
    assert hit["total_bounds"][0] == 103.0

    children = try_local_admin_division("成都市", child_level=1)
    assert children is not None
    assert children["count"] == 2
    assert children["source"] == "local_admin"

    assert try_local_admin_division("纽约市") is None
    assert try_local_admin_division("成都市", child_level=2) is None
    assert try_local_admin_division("锦江区", child_level=1) is None


@pytest.mark.asyncio
async def test_query_osm_poi_stays_local(geodata_env, local_first_on):
    from app.services.local_first import try_local_osm_poi
    from app.tools.osm import register_osm_tools
    from app.tools.registry import ToolRegistry

    hit = try_local_osm_poi("成都市", "restaurant", limit=50)
    assert hit is not None
    assert hit["source"] == "local_osm"
    assert hit["type"] == "poi_query"
    assert hit["count"] >= 1

    registry = ToolRegistry()
    register_osm_tools(registry)

    with patch("app.tools.osm.tracked_provider_get", side_effect=AssertionError("must not leave local")):
        result = await registry.dispatch(
            "query_osm_poi", {"area": "成都市", "category": "restaurant"},
        )
        roads = await registry.dispatch(
            "query_osm_roads", {"area": "成都市", "road_type": "primary"},
        )
    assert result["source"] == "local_osm"
    assert result["count"] >= 1
    assert roads["source"] == "local_osm"
    assert roads["count"] >= 1


@pytest.mark.asyncio
async def test_search_poi_and_admin_tools_stay_local(geodata_env, local_first_on):
    from app.tools.chinese_maps import register_chinese_map_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_chinese_map_tools(registry)

    admin = await registry.dispatch("get_admin_division", {"keywords": "成都市"})
    assert admin["source"] == "local_admin"
    assert admin["count"] == 1

    children = await registry.dispatch("get_child_districts", {"keywords": "成都市"})
    assert children["source"] == "local_admin"
    assert children["count"] == 2

    poi = await registry.dispatch(
        "search_poi", {"keyword": "餐厅", "city": "成都市"},
    )
    assert poi["source"] == "local_osm"
    assert poi["count"] >= 1

    universities = await registry.dispatch(
        "search_poi", {"keyword": "高等院校", "city": "成都市"},
    )
    assert universities["source"] == "local_osm"
    assert universities["count"] >= 1
    names = {f["properties"]["name"] for f in universities["features"]}
    assert "四川大学" in names

    with patch(
        "app.tools.chinese_maps.http.tracked_provider_get",
        side_effect=AssertionError("must not leave local"),
    ):
        poly = await registry.dispatch(
            "search_poi_polygon",
            {"polygon": [103.0, 30.5, 104.5, 31.5], "keyword": "大学", "limit": 20},
        )
        around = await registry.dispatch(
            "search_poi_around",
            {"center": [104.08, 30.65], "radius_m": 5000, "keyword": "餐厅"},
        )
    assert poly["source"] == "local_osm"
    assert poly["count"] >= 1
    assert around["source"] == "local_osm"
    assert around["count"] >= 1


@pytest.mark.asyncio
async def test_query_local_osm_accepts_amenity_alias(geodata_env, local_first_on):
    from app.tools.local_osm import register_local_osm_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_local_osm_tools(registry)
    res = await registry.dispatch(
        "query_local_osm",
        {
            "theme": "pois",
            "bbox": [104.05, 30.60, 104.12, 30.70],
            "amenity": "university",
            "limit": "50",
        },
    )
    assert res["count"] >= 1
    assert res["features"][0]["properties"]["name"] == "四川大学"


def test_university_synonym_does_not_fall_through_to_name_like(geodata_env, local_first_on):
    from app.services.local_first import resolve_poi_filters, try_local_search_poi

    tags, name_like = resolve_poi_filters("高等院校")
    assert "amenity=university" in tags
    assert name_like is None

    # 本地全空 = 没有对应资料：返回 None 放行出网（本地优先、在线兜底）
    miss = try_local_search_poi("地铁站", "成都市", limit=10)
    assert miss is None


# ── gd_poi → OSM → 在线 三级链 ───────────────────────────────────────────


def _write_gd_poi_fixture(root):
    """微型 gd_pois.gpkg（与 gd-poi-ingest 产物同 schema）。"""
    import pyogrio

    gd = gpd.GeoDataFrame(
        {
            "poi_id": ["G1", "G2"],
            "name": ["双流区实验小学", "春熙路书店"],
            "category": ["科教文化服务", "购物服务"],
            "subtype": ["学校;小学", "书店"],
            "typecode": ["141203", "060101"],
            "adcode": ["510116", "510104"],
            "adname": ["双流区", "锦江区"],
            "cityname": ["成都市", "成都市"],
            "pname": ["四川省", "四川省"],
            "address": ["", ""],
            "tel": ["", ""],
        },
        geometry=[Point(104.0, 30.6), Point(104.08, 30.66)],
        crs="EPSG:4326",
    )
    out = root / "gd_pois"
    out.mkdir(exist_ok=True)
    pyogrio.write_dataframe(gd, out / "gd_pois.gpkg", layer="pois", driver="GPKG")


def test_chain_prefers_gd_poi_when_hit(geodata_env, local_first_on):
    _write_gd_poi_fixture(geodata_env)
    from app.services.local_first import try_local_search_poi

    hit = try_local_search_poi("小学", "成都市", limit=10)
    assert hit is not None
    assert hit["source"] == "local_gd_poi"
    assert hit["count"] == 1
    assert hit["features"][0]["properties"]["name"] == "双流区实验小学"

    # gd 分类查空时兜底 name_like（口语词/店名）
    hit = try_local_search_poi("春熙路书店", "成都市", limit=10)
    assert hit is not None and hit["source"] == "local_gd_poi"


def test_chain_falls_back_to_osm_when_gd_misses(geodata_env, local_first_on):
    """gd 库存在但查不到（如大学）→ 降级 OSM（四川大学）。"""
    _write_gd_poi_fixture(geodata_env)
    from app.services.local_first import try_local_search_poi

    hit = try_local_search_poi("高等院校", "成都市", limit=10)
    assert hit is not None
    assert hit["source"] == "local_osm"
    names = {f["properties"]["name"] for f in hit["features"]}
    assert "四川大学" in names
