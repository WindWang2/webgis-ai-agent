"""dasymetric_reallocation 算法单元测试（Wave 6 原生化验收）。

手算可验的合成几何（成都附近 ~0.001° 正方形；to_utm_gdf 按 WGS84
经纬度解析输入后投影到 UTM —— 坐标必须落在合法经纬度域）：
- 源面 = 0.001° × 0.001° 正方形；
- 控制面 = 左右各 0.0005° 宽的竖条（同形同积，权重比 3:1）；
- 权重/守恒/退化/拒绝语义逐条断言。
"""
from __future__ import annotations

import math

import pytest

pytest.importorskip("geopandas")

from app.lib.geo_analysis.dasymetric import dasymetric_reallocation


def _square_fc(x0: float, y0: float, size: float, props: dict | None = None):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": props or {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [x0, y0], [x0 + size, y0],
                        [x0 + size, y0 + size], [x0, y0 + size],
                        [x0, y0],
                    ]],
                },
            }
        ],
    }


LON0, LAT0 = 104.0, 30.6
SIZE = 0.001
HALF = 0.0005


def _half_slabs_fc(weight_field: str = ""):
    """源正方形的左右两半（各 0.0005° 宽竖条，同形同积）。"""
    def slab(x0: float, weight):
        props = {}
        if weight_field:
            props[weight_field] = weight
        return _square_fc(x0, LAT0, HALF, props)["features"][0]

    return {
        "type": "FeatureCollection",
        "features": [slab(LON0, 3), slab(LON0 + HALF, 1)],
    }


SRC = _square_fc(LON0, LAT0, SIZE, {"id": "src-A", "population": 1000.0})


def test_ancillary_weighted_conservation_and_exact_split():
    """权重 3:1 的两半 → 分配值 750/250，总量守恒。"""
    res = dasymetric_reallocation(
        SRC, _half_slabs_fc("weight"), "population", weight_field="weight",
        output_crs=None,
    )
    assert res.success, res.summary
    frags = res.data["features"]
    assert len(frags) == 2
    by_anc = {f["properties"]["ancillary_id"]: f["properties"]["population"] for f in frags}
    values = sorted(by_anc.values(), reverse=True)
    assert math.isclose(values[0], 750.0, rel_tol=1e-5)
    assert math.isclose(values[1], 250.0, rel_tol=1e-5)
    assert math.isclose(sum(values), 1000.0, rel_tol=1e-9)
    assert res.data["metadata"]["mass_conserving"] is True
    assert res.data["metadata"]["method"] == "ancillary_weighted"


def test_area_proportional_fallback_when_no_weight_field():
    """无权重字段 → 纯面积比例（50/50）。"""
    res = dasymetric_reallocation(
        SRC, _half_slabs_fc(), "population", output_crs=None,
    )
    assert res.success, res.summary
    values = [f["properties"]["population"] for f in res.data["features"]]
    assert math.isclose(sum(values), 1000.0, rel_tol=1e-9)
    assert all(math.isclose(v, 500.0, rel_tol=1e-5) for v in values)
    assert res.data["metadata"]["method"] == "area_proportional"


def test_zero_weight_fallback_disclosed():
    """权重全零（覆盖存在）→ 面积比例 + 逐源披露。"""
    zeros = {
        "type": "FeatureCollection",
        "features": [
            _square_fc(LON0, LAT0, HALF, {"weight": 0})["features"][0],
            _square_fc(LON0 + HALF, LAT0, HALF, {"weight": 0})["features"][0],
        ],
    }
    res = dasymetric_reallocation(
        SRC, zeros, "population",
        weight_field="weight", output_crs=None,
    )
    assert res.success
    assert res.data["metadata"]["area_proportional_fallback"] == 1
    values = [f["properties"]["population"] for f in res.data["features"]]
    assert math.isclose(sum(values), 1000.0, rel_tol=1e-9)


def test_uncovered_source_keeps_whole_value():
    """控制层不覆盖源面 → 整面保值（no_ancillary_coverage），不丢值。"""
    far_control = _square_fc(LON0 + 0.01, LAT0, 0.0005, {"weight": 5})
    res = dasymetric_reallocation(SRC, far_control, "population",
                                  weight_field="weight", output_crs=None)
    assert res.success
    assert res.data["metadata"]["uncovered_sources"] == 1
    frags = res.data["features"]
    assert len(frags) == 1
    assert math.isclose(frags[0]["properties"]["population"], 1000.0, rel_tol=1e-9)
    assert frags[0]["properties"]["method"] == "no_ancillary_coverage"


def test_missing_value_field_structurally_rejected():
    res = dasymetric_reallocation(SRC, _half_slabs_fc(), "not_a_field",
                                  output_crs=None)
    assert not res.success
    assert "not_a_field" in (res.summary or "")


def test_negative_value_clamped_and_disclosed():
    # Review R1（GIS F11）：源面必须与控制层同域（合法经纬度）——否则
    # UTM 自动选带漂移、走的是 uncovered 路径而非「钳制 + 切分」。
    src = _square_fc(LON0, LAT0, SIZE, {"id": "src-N", "population": -50.0})
    res = dasymetric_reallocation(src, _half_slabs_fc("weight"), "population",
                                  weight_field="weight", output_crs=None)
    assert res.success
    assert res.data["metadata"]["clamped_negative_values"] == 1
    assert math.isclose(
        sum(f["properties"]["population"] for f in res.data["features"]), 0.0,
        abs_tol=1e-9,
    )


def test_determinism_same_input_same_output():
    a = dasymetric_reallocation(SRC, _half_slabs_fc("weight"), "population",
                                weight_field="weight", output_crs=None)
    b = dasymetric_reallocation(SRC, _half_slabs_fc("weight"), "population",
                                weight_field="weight", output_crs=None)
    assert a.success and b.success
    assert a.data["features"] == b.data["features"]


def test_feature_cap_hard_reject():
    """超上限硬拒绝（先拒绝不 OOM）。"""
    big = {
        "type": "FeatureCollection",
        "features": [_square_fc(LON0 + i * 0.101, LAT0, SIZE,
                                {"id": f"s{i}", "population": 1})["features"][0]
                     for i in range(3)],
    }
    # monkeypatch 上限到 2（不真造 20k 面）
    import app.lib.geo_analysis.dasymetric as mod
    original = mod.DASYMETRIC_MAX_SOURCE_FEATURES
    mod.DASYMETRIC_MAX_SOURCE_FEATURES = 2
    try:
        res = dasymetric_reallocation(big, _half_slabs_fc("weight"), "population",
                                      weight_field="weight", output_crs=None)
    finally:
        mod.DASYMETRIC_MAX_SOURCE_FEATURES = original
    assert not res.success
    assert "exceed cap" in (res.summary or "")
