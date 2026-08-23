"""GIS 算法专项回归（G-1..G-9 / #865-#873）。

- G-1: 高德 POI bbox 路径 fid 均匀采样 + 截断披露透传
- G-2: 行政边界 GCJ-02 crs 成员声明
- G-3: 聚类值维与坐标同量级（默认档值维真实参与）
- G-4: OSM 标准标签映射（小学→amenity=school + 学段窄化；shop/railway 键）
- G-5: KDE 自动带宽 kNN 尺度钳制
- G-6: Gi*/LISA 多重比较披露（BH-FDR + expected_false_positives）
- G-7: query_osm_poi count==limit 截断披露
- G-9: h3_binning stat_method 降级披露
"""
from __future__ import annotations


import pytest


# ─── G-1（#865）: bbox 路径均匀采样 ───────────────────────────────────────

@pytest.fixture
def gd_env(tmp_path, monkeypatch):
    """双区县 × N 点的合成 gd_poi 库（xlsx zip → ingest → GPKG）。"""
    import pandas as pd

    from app.core.config import settings
    monkeypatch.setattr(settings, "LOCAL_GEODATA_DIR", str(tmp_path), raising=False)

    poi_dir = tmp_path / "POI"
    poi_dir.mkdir(parents=True, exist_ok=True)

    def _gcj(lng, lat):
        return f"{lng},{lat}"

    def _row(pid, name, lng, lat, adcode, adname):
        return {"id": pid, "name": name, "type": "科教文化服务;小学",
                "address": "地址", "location": _gcj(lng, lat),
                "typecode": "141101", "pcode": "510000", "pname": "四川省",
                "citycode": "028", "cityname": "成都市", "adcode": adcode,
                "adname": adname, "tel": ""}

    rows = []
    # 两个"区县"各 12 所小学，入库顺序 = 区县A 全部在前（fid 顺序偏斜源）
    for i in range(12):
        rows.append(_row(f"A{i}", f"小学A{i}", 104.00 + i * 0.004, 30.60, "510104", "锦江区"))
    for i in range(12):
        rows.append(_row(f"B{i}", f"小学B{i}", 104.50 + i * 0.004, 30.70, "510181", "都江堰市"))

    df = pd.DataFrame(rows)
    out = poi_dir / "gd_510000_poi.zip"
    import zipfile
    with zipfile.ZipFile(out, "w") as zf:
        with zf.open("gd_510000_poi/gd_510000_poi.xlsx", "w") as fh:
            with pd.ExcelWriter(fh, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="查询编码", index=False)
    from app.services import local_poi
    result = local_poi.ingest_gd_poi()
    assert not result.get("error"), result
    yield


def test_g1_bbox_query_samples_across_districts(gd_env):
    """bbox 命中 24 条、limit=8 时：覆盖两个区县（旧实现 8/8 全来自 fid 最小区县）。"""
    from app.services.local_poi import query_gd_poi

    fc = query_gd_poi([103.9, 30.5, 104.7, 30.8], subtype="小学", limit=8)
    assert not fc.get("error"), fc
    assert fc["count"] == 8
    adcodes = {f["properties"].get("adcode") for f in fc["features"]}
    assert len(adcodes) >= 2, f"均匀采样必须覆盖多个区县，实际 {adcodes}"
    assert fc.get("total_matched") == 24
    assert fc.get("truncated") is True
    assert "均匀采样" in fc.get("note", "")


def test_g1_disclosure_survives_local_chain(gd_env, monkeypatch):
    """截断披露（total_matched/truncated）经 _local_poi_chain → try_local_osm_poi 透传。"""
    import app.services.local_first as lf
    from app.services.local_first import try_local_osm_poi
    from app.core.config import settings as _settings

    # 单元隔离：开关显式打开；行政边界库不在本 fixture 内，bbox 直接给定
    monkeypatch.setattr(_settings, "LOCAL_QUERY_FIRST", True, raising=False)
    monkeypatch.setattr(lf, "admin_bbox_wgs84", lambda area: (103.9, 30.5, 104.7, 30.8))

    result = try_local_osm_poi("成都市", "小学", limit=6)
    assert result is not None
    assert result["count"] == 6
    assert result.get("total_matched") == 24
    assert result.get("truncated") is True


# ─── G-2（#866）: 边界 crs 成员 ───────────────────────────────────────────

@pytest.mark.heavy
def test_g2_admin_boundary_declares_gcj02(monkeypatch):
    """to_wgs84=False 的边界输出必须带 crs="gcj02"（下游归一管道可感知）。"""
    import geopandas as gpd
    from shapely.geometry import Polygon
    from app.tools import local_admin

    gdf = gpd.GeoDataFrame(
        {"name": ["测试市"], "adcode": ["510100"]},
        geometry=[Polygon([(104.0, 30.6), (104.5, 30.6), (104.5, 30.9), (104.0, 30.9)])],
        crs="EPSG:4326",
    )
    monkeypatch.setattr(local_admin, "_load_level", lambda level: gdf)
    fc = local_admin.query_admin_boundary("city", name="测试市", to_wgs84=False)
    assert fc.get("crs") == "gcj02", "GCJ-02 输出必须声明 crs 成员（机器可读）"
    fc_wgs = local_admin.query_admin_boundary("city", name="测试市", to_wgs84=True)
    assert "crs" not in fc_wgs or fc_wgs.get("crs") != "gcj02"


# ─── G-3（#867）: 聚类值维同量级 ──────────────────────────────────────────

@pytest.mark.heavy
def test_g3_default_value_weight_participates():
    """同位置不同值的两组点，默认 value_weight=1.0 下聚类标签必须可区分。

    旧实现值维 σ=1（≈1 米）与 UTM 坐标（城市尺度 σ≈8-20km）拼接，
    值维贡献 <0.1%——标签与不传 value_field 几乎相同（值感知失效）。
    """
    from app.lib.geo_analysis.statistics import cluster_narrated

    feats = []
    # 两个空间簇（相距 ~3km），每簇 4 点；A 簇值 100，B 簇值 1000
    for i in range(4):
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [104.0 + i * 1e-4, 30.6]},
                      "properties": {"v": 100.0}})
    for i in range(4):
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [104.04 + i * 1e-4, 30.6]},
                      "properties": {"v": 1000.0}})
    res = cluster_narrated({"type": "FeatureCollection", "features": feats},
                           eps=1500, min_samples=2, value_field="v", value_weight=1.0)
    assert res.success, res.summary
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("value_dim_effective_scale_m"), "信封必须披露值维实际尺度"
    # 值维尺度应与空间 σ 同量级（米级），而非 1 米
    assert data["value_dim_effective_scale_m"] > 100


# ─── G-4（#868）: OSM 标准标签 ────────────────────────────────────────────

def test_g4_standard_tags_in_shared_map():
    from app.lib.osm_category_map import CHINESE_CATEGORY_TAGS

    assert CHINESE_CATEGORY_TAGS["小学"] == ("amenity", "school")
    assert CHINESE_CATEGORY_TAGS["中学"] == ("amenity", "school")
    assert CHINESE_CATEGORY_TAGS["超市"] == ("shop", "supermarket")
    assert CHINESE_CATEGORY_TAGS["商场"] == ("shop", "mall")
    assert CHINESE_CATEGORY_TAGS["地铁站"] == ("railway", "station")
    # 旧的非文档化标签不再出现
    values = {v for _, v in CHINESE_CATEGORY_TAGS.values()}
    assert "primary_school" not in values
    assert "secondary_school" not in values


def test_g4_local_first_derives_from_shared_map():
    from app.services.local_first import _CATEGORY_TO_TAG

    assert _CATEGORY_TO_TAG["小学"] == "amenity=school"
    assert _CATEGORY_TO_TAG["超市"] == "shop=supermarket"
    assert _CATEGORY_TO_TAG["地铁站"] == "railway=station"


@pytest.mark.asyncio
async def test_g4_overpass_primary_query_narrows_school_stage(monkeypatch):
    """小学查询的 Overpass primary 查询带 school~primary|elementary 窄化。"""
    from app.tools.registry import ToolRegistry
    from app.tools.osm import register_osm_tools

    captured = {}

    async def _fake_overpass(query, limit=50):
        captured["query"] = query
        captured["limit"] = limit
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
             "properties": {"name": "x"}}
        ]}

    async def _fake_geocode(*a, **k):
        return (104.0, 30.6, 104.2, 30.8)

    import app.tools.osm as osm_mod
    import app.services.local_first as lf_mod
    monkeypatch.setattr(osm_mod, "_query_overpass", _fake_overpass)
    monkeypatch.setattr(osm_mod, "_geocode_bbox", _fake_geocode)
    monkeypatch.setattr(lf_mod, "try_local_osm_poi", lambda *a, **k: None)

    reg = ToolRegistry()
    register_osm_tools(reg)
    res = await reg.dispatch("query_osm_poi", {"area": "测试区", "category": "小学", "limit": 10}, session_id=None)
    assert not res.get("error"), res
    assert 'amenity"="school"' in captured["query"].replace("['", '"').replace("']", '"') or "school" in captured["query"]
    assert "primary" in captured["query"], "学段窄化必须出现在 primary 查询里"


# ─── G-5（#869）: KDE 自动带宽钳制 ────────────────────────────────────────

@pytest.mark.heavy
def test_g5_auto_bandwidth_clamped_for_clustered_data():
    """双簇（间距 3km、簇内 σ≈200m）：自动带宽必须被 kNN 尺度钳制。"""
    import numpy as np
    from app.lib.geo_analysis.density import _fit_kde

    rng = np.random.default_rng(42)
    a = rng.normal(loc=0.0, scale=200.0, size=(2, 60))
    b = rng.normal(loc=3000.0, scale=200.0, size=(2, 60))
    data = np.hstack([a, b])

    kde, bw, clamped = _fit_kde(data, 0)
    # 未钳制的 Scott 带宽 ≈ n^(-1/6) × std ≈ 0.42 × 1700 ≈ 700+m
    scott = float(kde.factor * np.mean(np.std(data, axis=1)))
    if scott > 6 * 100:  # Scott 明显大于 kNN 尺度时必须钳制
        assert clamped is True
        assert bw < scott * 0.8, f"钳制后带宽 {bw:.0f} 应显著小于 Scott {scott:.0f}"


# ─── G-6（#870）: 多重比较披露 ────────────────────────────────────────────

@pytest.mark.heavy
def test_g6_hotspot_fdr_disclosure():
    """随机独立值 + 规则格网：信封必须披露 FDR 计数与期望假阳性。"""
    from app.lib.geo_analysis.statistics import hotspot_narrated

    feats = []
    k = 0
    for i in range(10):
        for j in range(10):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6 + j * 0.01]},
                "properties": {"v": float((i * 7 + j * 13) % 10)},
            })
            k += 1
    res = hotspot_narrated({"type": "FeatureCollection", "features": feats},
                           value_field="v", distance_band=0)
    assert res.success, res.summary
    data = res.data if isinstance(res.data, dict) else {}
    assert "expected_false_positives" in data, "信封必须披露期望假阳性数"
    assert "fdr_hot_spots_count" in data
    assert data["expected_false_positives"] == pytest.approx(0.05 * 100, abs=0.5)
    # 每个要素带 FDR q 值
    f0 = data["features"][0]["properties"]
    assert "q_value_fdr" in f0
    assert 0.0 <= f0["q_value_fdr"] <= 1.0


# ─── G-7（#871）: Overpass 截断披露 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_g7_truncated_flag_when_count_hits_limit(monkeypatch):
    import app.tools.osm as osm_mod
    from app.tools.registry import ToolRegistry
    from app.tools.osm import register_osm_tools

    async def _fake_overpass(query, limit=50):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
             "properties": {"name": f"p{i}"}}
            for i in range(limit)
        ]}

    async def _fake_geocode(*a, **k):
        return (104.0, 30.6, 104.2, 30.8)

    import app.services.local_first as lf_mod
    monkeypatch.setattr(osm_mod, "_query_overpass", _fake_overpass)
    monkeypatch.setattr(osm_mod, "_geocode_bbox", _fake_geocode)
    monkeypatch.setattr(lf_mod, "try_local_osm_poi", lambda *a, **k: None)

    reg = ToolRegistry()
    register_osm_tools(reg)
    res = await reg.dispatch("query_osm_poi", {"area": "测试区", "category": "医院", "limit": 20}, session_id=None)
    assert res.get("count") == 20
    assert res.get("truncated") is True, "count==limit 必须置 truncated"
    assert "limit" in res.get("note", "")


# ─── G-9（#873）: h3 降级披露 ─────────────────────────────────────────────

@pytest.mark.heavy
def test_g9_h3_stat_method_degrade_disclosed():
    from app.lib.geo_analysis.aggregation import h3_binning

    feats = [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
         "properties": {"v": float(i)}}
        for i in range(24)
    ]
    res = h3_binning({"type": "FeatureCollection", "features": feats}, 7,
                     stat_field=None, stat_method="mean")
    assert res.success, res.summary
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("stat_method_effective") == "count", "降级必须显式披露"
    assert "stat_method" in data.get("warning", "") or "降级" in res.summary
