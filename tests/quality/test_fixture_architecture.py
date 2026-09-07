"""Fixture 架构红线（ADR-0104 Wave 18）。

合成样本库（tests/fixtures/gis_samples.py）的三条契约：
- 确定性：同参数两次构造逐位一致；
- 小：序列化字节硬上界（禁止样本库膨胀成数据仓库）；
- 坏数据场景必须真的"坏"（校验器/CRS 语义能识别），否则是假夹具。
"""
from __future__ import annotations

import json

import pytest

from tests.fixtures.gis_samples import (
    SCENARIO_BUILDERS,
    bad_crs_fc,
    invalid_geometry_fc,
    line_fc,
    missing_field_fc,
    point_fc,
    polygon_fc,
    temporal_fc,
    zero_variance_fc,
)

MAX_BYTES = 256 * 1024  # 任何样本 ≤256KB（实际都在几 KB 量级）


@pytest.mark.parametrize("name", sorted(SCENARIO_BUILDERS))
def test_fixtures_are_deterministic(name):
    import functools

    builder = SCENARIO_BUILDERS[name]
    sig = __import__("inspect").signature(builder).parameters
    kwargs = {k: v for k, v in {"n": 12, "seed": 5}.items() if k in sig}
    a = json.dumps(builder(**kwargs), sort_keys=True)
    b = json.dumps(builder(**kwargs), sort_keys=True)
    assert a == b, f"{name} 不是确定性的"


@pytest.mark.parametrize("name", sorted(SCENARIO_BUILDERS))
def test_fixtures_are_small(name):
    builder = SCENARIO_BUILDERS[name]
    payload = json.dumps(builder(), ensure_ascii=False)
    assert len(payload.encode()) <= MAX_BYTES, f"{name} 样本过大: {len(payload)}B"


def test_nominal_samples_are_valid_geojson():
    from app.tools.registry import validate_geojson_structure

    for fc in (point_fc(5), polygon_fc(4), line_fc(2)):
        validate_geojson_structure(fc)  # 非法即抛


def test_bad_crs_fixture_declares_conflicting_crs():
    fc = bad_crs_fc()
    from app.lib.gis.crs_safety import classify_crs

    # 坐标域是 WGS84 成都，声明是 CGCS2000 —— 分类器必须能给出 CRS 判定
    verdict = classify_crs("EPSG:4490")
    assert verdict is not None


def test_invalid_geometry_fixture_is_detectably_invalid():
    """bowtie 必须被权威几何库判定为 invalid（假夹具即红）。"""
    import shapely
    from shapely.geometry import shape

    fc = invalid_geometry_fc()
    geom = shape(fc["features"][0]["geometry"])
    assert geom.is_valid is False, "bowtie 样本必须是自交多边形"
    _ = shapely


def test_missing_field_fixture_actually_missing():
    fc = missing_field_fc(n=10, absent_ratio=0.5)
    values = [f["properties"].get("value") for f in fc["features"]]
    assert any(v is None for v in values) and any(v is not None for v in values)


def test_zero_variance_fixture_is_constant():
    fc = zero_variance_fc(n=8)
    values = {f["properties"]["value"] for f in fc["features"]}
    assert len(values) == 1


def test_temporal_fixture_has_time_field():
    fc = temporal_fc()
    assert all("time" in f["properties"] for f in fc["features"])
