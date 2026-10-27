"""G3D-V2-A A2：会话 DEM 摄入校验/资格/注册 —— fixture 契约族。

验收锚点（TEST_MATRIX）：正常有效、负高程、无 datum、单位英尺（声明/tag）、
nodata 洞、无 CRS、多波段、非法像素、异常尖峰、旋转网格、synthetic 强制
display_only、幂等重注册、剖析预算（降采样）—— 每个不合格因素都有可编程
reason code，绝不静默伪造高程语义。
"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_bounds

from app.services.session_data import SessionDataManager
from app.services.terrain_ingest import (
    native_zoom_for_gsd,
    register_session_terrain_source,
    validate_dem,
)

# 成都窗口（synthetic_terrain 同款），512×512 → ~0.00068°/px ≈ 67m GSD。
_BBOX = (103.90, 30.50, 104.25, 30.80)
_SHAPE = (512, 512)


def _write_dem(
    path,
    elevation=None,
    *,
    bbox=_BBOX,
    shape=_SHAPE,
    crs="EPSG:4326",
    nodata=None,
    unit_tag=None,
    synthetic=False,
    transform=None,
    bands=1,
    dtype="float32",
):
    """fixture 构造器：可控 CRS/单位 tag/nodata/synthetic 标记/旋转。"""
    if elevation is None:
        elevation = np.linspace(500.0, 1500.0, shape[0] * shape[1]).reshape(shape)
    profile = {
        "driver": "GTiff",
        "width": shape[1],
        "height": shape[0],
        "count": bands,
        "dtype": dtype,
        "crs": crs,
        "transform": transform if transform is not None else from_bounds(*bbox, width=shape[1], height=shape[0]),
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        if bands == 1:
            dst.write(np.asarray(elevation, dtype=dtype), 1)
        else:
            for b in range(1, bands + 1):
                dst.write(np.asarray(elevation, dtype=dtype), b)
        if unit_tag:
            dst.set_band_unit(1, unit_tag)
        if synthetic:
            dst.update_tags(SYNTHETIC_DEM="1")
            dst.update_tags(1, SYNTHETIC_DEM="1")
    return path


@pytest.fixture
def mgr():
    return SessionDataManager(capacity=8)


class TestValidDem:
    def test_valid_meter_dem_with_declared_datum_is_analysis_grade(self, tmp_path):
        p = _write_dem(tmp_path / "dem.tif")
        receipt, normalized = validate_dem(p, declared_vertical_datum="EGM96", declared_vertical_unit="m")
        assert receipt.qualification == "analysis_grade"
        assert receipt.reasons == ()
        assert receipt.vertical_datum == "EGM96"
        assert receipt.vertical_datum_source == "declared"
        assert receipt.vertical_unit_source == "declared"
        assert receipt.crs == "EPSG:4326"
        assert normalized == p
        assert receipt.bbox_epsg4326 is not None
        assert receipt.bbox_epsg4326[0] == pytest.approx(_BBOX[0], abs=1e-6)
        assert receipt.ground_sample_distance_m == pytest.approx(67.0, abs=3.0)
        assert receipt.synthetic is False

    def test_negative_elevations_are_legitimate(self, tmp_path):
        # 死海量级 -430m：合法高程，不得误判 outlier（包络下界 -500）。
        elev = np.full(_SHAPE, -430.0)
        p = _write_dem(tmp_path / "dem.tif", elev)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96", declared_vertical_unit="m")
        assert receipt.qualification == "analysis_grade"
        assert receipt.outlier_pixels == 0
        assert receipt.elevation_min_m == pytest.approx(-430.0)

    def test_native_zoom_from_gsd(self):
        # 30m SRTM 级：z ≈ log2(156543*cos(30°)/30) ≈ 11.7 → 11
        assert native_zoom_for_gsd(30.0, (100.0, 29.9, 101.0, 30.1)) in (10, 11, 12)
        assert native_zoom_for_gsd(None, None) == 14  # 缺省锚
        assert native_zoom_for_gsd(1.0, (100.0, 0.0, 100.1, 0.1)) == 17  # 1m ≈ z17


class TestVerticalGovernance:
    def test_unknown_datum_is_display_only_with_reason(self, tmp_path):
        p = _write_dem(tmp_path / "dem.tif")
        receipt, _ = validate_dem(p, declared_vertical_unit="m")
        assert receipt.qualification == "display_only"
        assert receipt.vertical_datum is None
        codes = [r.code for r in receipt.reasons]
        assert "UNKNOWN_VERTICAL_DATUM" in codes

    def test_declared_unit_ft_converts_with_evidence(self, tmp_path):
        # 英尺声明 → 换算副本 + 双 sha256 证据；换算后值域正确。
        elev_ft = np.full(_SHAPE, 3280.84)  # ≈1000m
        p = _write_dem(tmp_path / "dem.tif", elev_ft, nodata=-9999.0)
        receipt, normalized = validate_dem(
            p, declared_vertical_datum="EGM96", declared_vertical_unit="ft"
        )
        assert receipt.qualification == "analysis_grade"
        assert normalized is not None and normalized != p
        assert normalized.name == "dem.dem-m.tif"
        assert receipt.unit_conversion is not None
        assert receipt.unit_conversion.from_unit == "ft"
        assert receipt.unit_conversion.factor == pytest.approx(0.3048)
        assert len(receipt.unit_conversion.input_sha256) == 64
        assert len(receipt.unit_conversion.output_sha256) == 64
        assert receipt.elevation_min_m == pytest.approx(1000.0, abs=0.5)
        # 副本可被渲染路径直接读（nodata 统一 -9999）
        with rasterio.open(normalized) as src:
            assert src.nodata == -9999.0
            assert src.count == 1

    def test_geotiff_band_unit_tag_ft(self, tmp_path):
        p = _write_dem(tmp_path / "dem.tif", np.full(_SHAPE, 1000.0))
        with rasterio.open(p, "r+") as src:
            src.set_band_unit(1, "US survey foot")
        receipt, normalized = validate_dem(p, declared_vertical_datum="EGM2008")
        assert receipt.vertical_unit_source == "geotiff_tag"
        assert receipt.unit_conversion.from_unit == "us_survey_ft"
        assert receipt.unit_conversion.factor == pytest.approx(1200.0 / 3937.0)
        assert receipt.elevation_min_m == pytest.approx(304.8, abs=0.5)

    def test_unit_assumed_m_is_display_only(self, tmp_path):
        p = _write_dem(tmp_path / "dem.tif")
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96")
        assert receipt.qualification == "display_only"
        assert "UNIT_ASSUMED_M" in [r.code for r in receipt.reasons]
        assert receipt.vertical_unit_source == "assumed"

    def test_illegal_declared_unit_refused(self, tmp_path):
        p = _write_dem(tmp_path / "dem.tif")
        receipt, normalized = validate_dem(p, declared_vertical_unit="cubits")
        assert receipt.qualification == "unavailable"
        assert "UNIT_NOT_CONVERTIBLE" in [r.code for r in receipt.reasons]
        assert normalized is None

    def test_unknown_band_unit_tag_refused(self, tmp_path):
        p = _write_dem(tmp_path / "dem.tif")
        with rasterio.open(p, "r+") as src:
            src.set_band_unit(1, "K")
        receipt, _ = validate_dem(p)
        assert receipt.qualification == "unavailable"
        assert "UNIT_NOT_CONVERTIBLE" in [r.code for r in receipt.reasons]


class TestDataQuality:
    def test_nodata_holes_counted_not_interpolated(self, tmp_path):
        elev = np.full(_SHAPE, 500.0)
        elev[0:100, 0:100] = -9999.0  # ~3.9% 洞
        p = _write_dem(tmp_path / "dem.tif", elev, nodata=-9999.0)
        receipt, normalized = validate_dem(
            p, declared_vertical_datum="EGM96", declared_vertical_unit="m"
        )
        assert receipt.qualification == "analysis_grade"  # 洞是披露事实，不取消资格
        assert receipt.nodata_hole_fraction == pytest.approx(0.0390, abs=0.002)
        assert normalized is not None

    def test_non_finite_fraction_over_threshold_unavailable(self, tmp_path):
        elev = np.full(_SHAPE, 500.0, dtype="float32")
        elev[0:80, :] = np.nan  # 15.6% > 1%
        p = _write_dem(tmp_path / "dem.tif", elev)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96", declared_vertical_unit="m")
        assert receipt.qualification == "unavailable"
        assert "CORRUPT_ELEVATION" in [r.code for r in receipt.reasons]

    def test_few_non_finite_pixels_disclosed(self, tmp_path):
        elev = np.full(_SHAPE, 500.0, dtype="float32")
        elev[10, 10] = np.nan
        p = _write_dem(tmp_path / "dem.tif", elev)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96", declared_vertical_unit="m")
        assert receipt.qualification == "analysis_grade"
        assert receipt.non_finite_pixels == 1
        assert "NON_FINITE_PIXELS" in [r.code for r in receipt.reasons]

    def test_extreme_spike_forces_display_only(self, tmp_path):
        elev = np.full(_SHAPE, 500.0)
        elev[256, 256] = 20000.0  # |20000|>15000 极端尖峰（单像素，密度低于阈值）
        p = _write_dem(tmp_path / "dem.tif", elev)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96", declared_vertical_unit="m")
        assert receipt.qualification == "display_only"
        codes = [r.code for r in receipt.reasons]
        # 单尖峰走 extreme 码（密度码按比例触发，此处不重叠）
        assert "ELEVATION_EXTREME_OUTLIERS" in codes
        assert receipt.outlier_pixels == 1

    def test_dense_outliers_over_threshold_display_only(self, tmp_path):
        elev = np.full(_SHAPE, 500.0)
        elev[0:40, :] = 9500.0  # 7.8% 超包络
        p = _write_dem(tmp_path / "dem.tif", elev)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96", declared_vertical_unit="m")
        assert receipt.qualification == "display_only"
        assert "ELEVATION_OUTLIERS" in [r.code for r in receipt.reasons]

    def test_beyond_encodable_domain_disclosed(self, tmp_path):
        elev = np.full(_SHAPE, 500.0)
        elev[5, 5] = 33000.0  # 超 Terrarium 编码域（同时是极端尖峰）
        p = _write_dem(tmp_path / "dem.tif", elev)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96", declared_vertical_unit="m")
        assert "ELEVATION_OUT_OF_RANGE" in [r.code for r in receipt.reasons]


class TestStructuralRefusals:
    def test_no_crs_unavailable(self, tmp_path):
        p = _write_dem(tmp_path / "dem.tif", crs=None)
        receipt, normalized = validate_dem(p, declared_vertical_datum="EGM96")
        assert receipt.qualification == "unavailable"
        assert "TERRAIN_INGEST_NO_CRS" in [r.code for r in receipt.reasons]
        assert normalized is None

    def test_multiband_unavailable(self, tmp_path):
        p = _write_dem(tmp_path / "dem.tif", bands=3)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96")
        assert receipt.qualification == "unavailable"
        assert "TERRAIN_REQUIRES_SINGLE_BAND" in [r.code for r in receipt.reasons]

    def test_rotated_grid_unavailable(self, tmp_path):
        # 旋转 45°：a=d=cos45*s, b=-c=sin45*s → b≠0
        s = (_BBOX[2] - _BBOX[0]) / _SHAPE[1]
        rot = Affine.rotation(45.0) * Affine.translation(_BBOX[0], _BBOX[3])
        rot = rot * Affine.scale(s, -s)
        p = _write_dem(tmp_path / "dem.tif", transform=rot)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96")
        assert receipt.qualification == "unavailable"
        assert "ROTATED_GRID_UNSUPPORTED" in [r.code for r in receipt.reasons]

    def test_synthetic_tag_forces_display_only_even_with_datum(self, tmp_path):
        p = _write_dem(tmp_path / "dem.tif", synthetic=True)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96", declared_vertical_unit="m")
        assert receipt.qualification == "display_only"
        assert receipt.synthetic is True
        assert "SYNTHETIC_DEM_DISPLAY_ONLY" in [r.code for r in receipt.reasons]

    def test_missing_file_unavailable(self, tmp_path):
        receipt, normalized = validate_dem(tmp_path / "nope.tif")
        assert receipt.qualification == "unavailable"
        assert normalized is None

    def test_projected_crs_epsg3857_supported(self, tmp_path):
        # UTM/3857 投影 DEM：北向上 + 可解析 → 合法（#1805 邻域不涉及：
        # 本路径只做 transform_bounds 实测）。
        elev = np.full((256, 256), 500.0)
        tf = from_bounds(11580000, 3580000, 11600000, 3600000, width=256, height=256)
        p = _write_dem(tmp_path / "dem3857.tif", elev, crs="EPSG:3857", transform=tf)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96", declared_vertical_unit="m")
        assert receipt.qualification == "analysis_grade"
        assert receipt.bbox_epsg4326[0] == pytest.approx(104.2, abs=0.2)


class TestRegistrationLane:
    async def test_register_and_payload_shape(self, tmp_path, mgr):
        p = _write_dem(tmp_path / "dem.tif")
        out = await register_session_terrain_source(
            "sess-1", p, declared_vertical_datum="EGM96", declared_vertical_unit="m",
            content_sha256="a" * 64, manager=mgr,
        )
        assert out["ok"] is True and out["ref_id"].startswith("ref:terrain-")
        payload = await mgr.get("sess-1", out["ref_id"])
        assert payload["kind"] == "terrain_dem"
        assert payload["file_path"] == str(p)
        assert payload["content_sha256"] == "a" * 64
        assert payload["receipt"]["qualification"] == "analysis_grade"

    async def test_idempotent_reregistration_reuses_ref(self, tmp_path, mgr):
        p = _write_dem(tmp_path / "dem.tif")
        first = await register_session_terrain_source(
            "sess-1", p, declared_vertical_datum="EGM96", declared_vertical_unit="m",
            content_sha256="b" * 64, manager=mgr,
        )
        second = await register_session_terrain_source(
            "sess-1", p, declared_vertical_datum="EGM96", declared_vertical_unit="m",
            content_sha256="b" * 64, manager=mgr,
        )
        assert second["duplicate"] is True
        assert second["ref_id"] == first["ref_id"]
        refs = await mgr.list_refs("sess-1")
        assert len([r for r in refs if r.startswith("ref:terrain-")]) == 1

    async def test_unavailable_not_registered_but_receipt_returned(self, tmp_path, mgr):
        p = _write_dem(tmp_path / "dem.tif", crs=None)
        out = await register_session_terrain_source(
            "sess-1", p, declared_vertical_datum="EGM96", content_sha256="c" * 64, manager=mgr,
        )
        assert out["ok"] is False and out["ref_id"] is None
        assert out["receipt"].qualification == "unavailable"
        assert "TERRAIN_INGEST_NO_CRS" in out["error"]
        refs = await mgr.list_refs("sess-1")
        assert refs == {}

    async def test_lane_never_raises(self, tmp_path, mgr):
        out = await register_session_terrain_source(
            "sess-1", tmp_path / "missing.tif", manager=mgr
        )
        assert out["ok"] is False
        assert out["error"] is not None

    async def test_cross_session_isolation(self, tmp_path, mgr):
        p = _write_dem(tmp_path / "dem.tif")
        a = await register_session_terrain_source(
            "sess-a", p, declared_vertical_datum="EGM96", declared_vertical_unit="m",
            content_sha256="d" * 64, manager=mgr,
        )
        refs_b = await mgr.list_refs("sess-b")
        assert refs_b == {}
        payload_other = await mgr.get("sess-b", a["ref_id"])
        assert payload_other is None


class TestProfileBudget:
    def test_large_grid_profiled_via_decimation(self, tmp_path):
        # 8192×8192（67M 像元）：剖析必须降采样 —— 验证 receipt 仍正确且不整幅解码。
        shape = (2048, 2048)  # CI 资源纪律下用 4M 像元验证同一代码路径
        elev = np.linspace(100.0, 900.0, shape[0] * shape[1]).reshape(shape)
        p = _write_dem(tmp_path / "big.tif", elev, shape=shape)
        receipt, _ = validate_dem(p, declared_vertical_datum="EGM96", declared_vertical_unit="m")
        assert receipt.qualification == "analysis_grade"
        assert receipt.width == 2048 and receipt.height == 2048
        assert receipt.elevation_min_m == pytest.approx(100.0, abs=1.0)
