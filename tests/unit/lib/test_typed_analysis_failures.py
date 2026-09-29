"""Typed analysis failure 契约（H06）——narrated 统计失败面。

锁定升级后的失败形状：错误码取 ScientificError 词表（scientific_code）、
correction_hint 可执行、历史 message 文案逐字保留（API compat）、
ScientificError guidance/ref 有界扩展。
"""
import numpy as np
import pytest

from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    InvalidGeometry,
    MissingRequiredField,
    ResourceScaleMismatch,
    ScientificError,
)
from app.lib.geo_analysis.context import (
    extract_numeric_frame,
    validate_spatial_input,
)
from app.lib.geo_analysis.statistics import (
    calculate_nearest,
    cluster_narrated,
    moran_i_narrated,
)


def _points_fc(pts, field="val"):
    feats = []
    for item in pts:
        xy, v = item[0], item[1]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [xy[0], xy[1]]},
            "properties": {field: v},
        })
    return {"type": "FeatureCollection", "features": feats}


def _clustered_field():
    rng = np.random.default_rng(5)
    pts = []
    for cx, cy, val in [(116.39, 39.90, 100.0), (116.42, 39.92, 100.0),
                        (116.39, 39.95, 1.0), (116.42, 39.95, 1.0)]:
        for _ in range(12):
            pts.append(((cx + rng.normal(0, 3e-4), cy + rng.normal(0, 3e-4)), float(val)))
    return _points_fc(pts)


# --------------------------------------------------------------------------- #
# 失败面：typed code + guidance + message 逐字保留
# --------------------------------------------------------------------------- #
def test_invalid_input_typed_failure():
    res = moran_i_narrated({"type": "FeatureCollection", "features": []}, "val")
    assert res.success is False
    # 历史文案逐字保留
    assert res.summary == "Invalid GeoJSON or no features found"
    assert res.error_type == "INVALID_GEOMETRY"
    assert res.correction_hint


def test_missing_field_typed_failure():
    res = moran_i_narrated(_clustered_field(), "nope")
    assert res.success is False
    assert res.summary == "Field 'nope' missing or non-numeric"
    assert res.error_type == "MISSING_REQUIRED_FIELD"
    assert "nope" in res.correction_hint


def test_insufficient_samples_typed_failure():
    pts = [((116.0, 39.0), 1.0), ((116.1, 39.1), 2.0)]
    res = moran_i_narrated(_points_fc(pts), "val")
    assert res.success is False
    assert res.summary == "At least 3 features required for Moran's I"
    assert res.error_type == "INSUFFICIENT_SAMPLES"


def test_nearest_neighbor_insufficient_typed():
    pts = [((116.0, 39.0), 1.0)]
    res = calculate_nearest(_points_fc(pts))
    assert res.success is False
    assert res.error_type == "INSUFFICIENT_SAMPLES"


def test_cluster_missing_field_typed():
    res = cluster_narrated(_clustered_field(), value_field="nope",
                           method="kmeans", n_clusters=2)
    assert res.success is False
    assert res.error_type == "MISSING_REQUIRED_FIELD"


# --------------------------------------------------------------------------- #
# ScientificError guidance/ref 扩展（additive，有界）
# --------------------------------------------------------------------------- #
def test_scientific_error_guidance_ref_bounded():
    exc = DegenerateData(
        "degenerate",
        correction_hint="hint",
        guidance=[f"step {i} " + "x" * 200 for i in range(6)],
        ref={"algorithm": "stats.morans_i", "extra": "y" * 300},
    )
    d = exc.to_dict()
    assert d["scientific_code"] == "DEGENERATE_DATA"
    assert len(d["guidance"]) == 4          # ≤4 步
    assert all(len(g) <= 160 for g in d["guidance"])
    assert d["ref"]["algorithm"] == "stats.morans_i"
    assert len(d["ref"]["extra"]) <= 220
    # 缺席时不输出（存量消费方零变化）
    plain = InsufficientSamples("few").to_dict()
    assert "guidance" not in plain and "ref" not in plain
    # 仍是 ValueError（dispatch 映射兼容）
    assert isinstance(exc, ValueError)


def test_resource_scale_mismatch_carries_evidence_fields():
    exc = ResourceScaleMismatch(
        "too big", estimated="120k features", limit="100k features",
        guidance=["submit via geocompute durable plan", "narrow the extent"],
        ref={"algorithm": "stats.morans_i"},
    )
    d = exc.to_dict()
    assert d["estimated"] == "120k features"
    assert d["limit"] == "100k features"
    assert d["ref"] == {"algorithm": "stats.morans_i"}


# --------------------------------------------------------------------------- #
# context typed 失败：InvalidGeometry / MissingRequiredField
# --------------------------------------------------------------------------- #
def test_context_typed_failures():
    with pytest.raises(InvalidGeometry):
        validate_spatial_input("not geojson")
    vsi = validate_spatial_input(_clustered_field())
    with pytest.raises(MissingRequiredField):
        extract_numeric_frame(vsi.gdf, "nope")
    # ScientificError 基类统一捕获面
    with pytest.raises(ScientificError):
        validate_spatial_input(None)
