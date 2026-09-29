"""ValidatedSpatialInput / 共享前置验证上下文（H06 #1548）单元测试。

覆盖：typed 成功上下文（metric frame / 尺寸披露 / 质量旗标）、不可解析
输入的 InvalidGeometry、数值字段提取的历史逐位语义（缺字段 / 全非法值 →
MissingRequiredField 且 detail 与历史文案逐字一致；NaN/±inf 丢弃）、
保守字节估算的方向性。
"""
import pytest

from app.lib.gis.scientific_errors import (
    InvalidGeometry,
    MissingRequiredField,
)
from app.lib.geo_analysis.context import (
    ValidatedSpatialInput,
    estimate_frame_bytes,
    extract_numeric_frame,
    validate_spatial_input,
)


def _points_fc(pts, field="val"):
    feats = []
    for item in pts:
        xy, v = item[0], item[1]
        props = {field: v} if field is not None else {}
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [xy[0], xy[1]]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": feats}


def _valid_fc():
    return _points_fc([
        ((116.39, 39.90), 1.0),
        ((116.40, 39.91), 2.0),
        ((116.41, 39.92), 3.0),
        ((116.42, 39.93), 4.0),
    ])


# --------------------------------------------------------------------------- #
# validate_spatial_input
# --------------------------------------------------------------------------- #
def test_validate_success_metric_frame_and_disclosure():
    vsi = validate_spatial_input(_valid_fc(), purpose="moran")
    assert isinstance(vsi, ValidatedSpatialInput)
    assert vsi.feature_count == 4
    assert vsi.metric_crs.startswith("EPSG:")
    assert vsi.source_crs == "EPSG:4326"
    # metric frame：坐标量级应为米（远大于度）
    minx, miny, maxx, maxy = vsi.bbox
    assert maxx - minx > 100
    assert maxy - miny > 100
    assert vsi.quality["gcj02_normalized"] is False
    assert vsi.quality["source_crs"] == "EPSG:4326"
    assert vsi.estimated_bytes > 0


def test_validate_unparseable_raises_typed():
    with pytest.raises(InvalidGeometry):
        validate_spatial_input({"type": "FeatureCollection", "features": []})
    with pytest.raises(InvalidGeometry):
        validate_spatial_input(None)


def test_validate_identity_projected_source_disclosed():
    fc = _points_fc([((430000.0, 4420000.0), 1.0),
                     ((431000.0, 4421000.0), 2.0),
                     ((432000.0, 4422000.0), 3.0)])
    fc["crs"] = {"type": "name", "properties": {"name": "EPSG:32650"}}
    vsi = validate_spatial_input(fc)
    # 已投影输入 target == source（identity，诚实披露，不谎报 auto-UTM）
    assert vsi.quality["target_crs"] == "EPSG:32650"


# --------------------------------------------------------------------------- #
# extract_numeric_frame —— 历史 _filter_numeric_gdf 语义
# --------------------------------------------------------------------------- #
def test_extract_missing_field_typed_with_legacy_detail():
    vsi = validate_spatial_input(_valid_fc())
    with pytest.raises(MissingRequiredField) as ei:
        extract_numeric_frame(vsi.gdf, "nope")
    assert str(ei.value) == "Field 'nope' missing or non-numeric"


def test_extract_filters_nan_and_inf_aligned():
    fc = _points_fc([
        ((116.39, 39.90), 1.0),
        ((116.40, 39.91), float("nan")),
        ((116.41, 39.92), float("inf")),
        ((116.42, 39.93), 4.0),
    ])
    vsi = validate_spatial_input(fc)
    gdf2, values = extract_numeric_frame(vsi.gdf, "val")
    assert values.tolist() == [1.0, 4.0]
    assert len(gdf2) == 2  # 行对齐（权重矩阵构建依赖此不变量）


def test_extract_string_field_coerced_and_all_invalid_typed():
    fc = _points_fc([
        ((116.39, 39.90), "1.5"),
        ((116.40, 39.91), "oops"),
        ((116.41, 39.92), "3.5"),
    ])
    vsi = validate_spatial_input(fc)
    _, values = extract_numeric_frame(vsi.gdf, "val")
    assert values.tolist() == [1.5, 3.5]

    fc_bad = _points_fc([
        ((116.39, 39.90), "x"),
        ((116.40, 39.91), "y"),
    ])
    vsi_bad = validate_spatial_input(fc_bad)
    with pytest.raises(MissingRequiredField) as ei:
        extract_numeric_frame(vsi_bad.gdf, "val")
    # detail 与历史文案逐字一致（API compat）
    assert str(ei.value) == "Field 'val' missing or non-numeric"
    # guidance 区分科学成因（全非法 vs 缺字段）
    assert "non-numeric/NaN/inf" in (ei.value.correction_hint or "")


# --------------------------------------------------------------------------- #
# estimate_frame_bytes —— 保守偏高方向
# --------------------------------------------------------------------------- #
def test_estimate_bytes_positive_and_grows_with_rows():
    small = validate_spatial_input(_valid_fc())
    big_pts = [((116.0 + i * 1e-4, 39.0), float(i)) for i in range(500)]
    big = validate_spatial_input(_points_fc(big_pts))
    b_small = estimate_frame_bytes(small.gdf)
    b_big = estimate_frame_bytes(big.gdf)
    assert b_small > 0
    assert b_big > b_small * 50  # 4 → 500 行（125×），估算至少同数量级增长


def test_estimate_bytes_zero_for_empty():
    import geopandas as gpd
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    assert estimate_frame_bytes(empty) == 0


# --------------------------------------------------------------------------- #
# inline 准入（H06 DoD3）：超限在投影之前 typed 拒绝，引导 durable
# --------------------------------------------------------------------------- #
def test_oversized_inline_rejected_before_projection():
    from app.lib.gis.scientific_errors import ResourceScaleMismatch
    from app.lib.geo_analysis.context import INLINE_MAX_FEATURES

    # 只造 features 骨架（ admission 预检在解析/投影之前 —— 不应为拒绝
    # 付出 20 万要素的投影成本）
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature"}] * (INLINE_MAX_FEATURES + 1)}
    with pytest.raises(ResourceScaleMismatch) as ei:
        validate_spatial_input(fc, purpose="moran")
    assert str(ei.value.estimated) == f"{INLINE_MAX_FEATURES + 1} features"
    assert "durable_job" in (ei.value.correction_hint or "")


def test_at_ceiling_inputs_still_validate():
    from app.lib.geo_analysis.context import INLINE_MAX_FEATURES
    # 恰在上限内（小集合 + 合法要素）正常通过：100k 合成基准的前提
    assert INLINE_MAX_FEATURES >= 100_000
    vsi = validate_spatial_input(_valid_fc())
    assert vsi.feature_count == 4
