"""Scale / Geometry display semantics 测试（ADR-0204 S5）。

验收矩阵对应：A10 几何不兼容/高密度/标注/栅格分辨率提示。
"""
from app.lib.gis.dataset_profile import DatasetProfile, RasterProfile
from app.lib.gis.scale_semantics import display_hints


def _profile(geom_types, feature_count=None, raster=None, crs=""):
    return DatasetProfile(
        source="synthetic",
        feature_count=feature_count,
        geometry_types=geom_types,
        raster=raster,
        crs=crs,
    )


def _codes(hints):
    return [h["code"] for h in hints.get("hints") or []]


def test_high_density_points_hint():
    p = _profile(["Point"], feature_count=8000)
    out = display_hints(p, feature_count=8000)
    assert "HIGH_DENSITY_POINTS" in _codes(out)
    assert out["geometry_family"] == "point"


def test_low_density_points_no_hint():
    p = _profile(["Point"], feature_count=120)
    assert "HIGH_DENSITY_POINTS" not in _codes(display_hints(p))


def test_polygon_label_density_hint():
    p = _profile(["Polygon"], feature_count=1200)
    assert "POLYGON_LABEL_DENSITY" in _codes(display_hints(p))


def test_multipart_centroid_semantics_hint():
    """multipart → 质心不代表要素整体的提示（centroid-is-not-feature）。"""
    p = _profile(["MultiPolygon"], feature_count=30)
    out = display_hints(p)
    assert "MULTIPART_GEOMETRY" in _codes(out)
    assert "质心" in out["hints"][0]["detail"]


def test_mixed_geometry_family_hint():
    p = _profile(["Point", "Polygon"], feature_count=50)
    assert "MIXED_GEOMETRY_FAMILY" in _codes(display_hints(p))


def test_raster_resolution_hint():
    p = _profile(
        [],
        raster=RasterProfile(
            width=8000, height=8000, band_count=1, pixel_size=30.0,
            dtype="float32",
        ),
        crs="EPSG:4326",
    )
    out = display_hints(p)
    codes = _codes(out)
    assert "RASTER_RESOLUTION" in codes
    # 6400 万像元超阈值 → 金字塔/降采样提示并入同一 detail。
    raster_hint = next(h for h in out["hints"] if h["code"] == "RASTER_RESOLUTION")
    assert "金字塔" in raster_hint["detail"]


def test_empty_profile_is_safe():
    assert display_hints(None) == {"hints": [], "geometry_family": "unknown"}
    out = display_hints(_profile([], feature_count=None))
    assert out["hints"] == []
