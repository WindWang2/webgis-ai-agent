"""GOAL-02 M3：Coverage 受控 subset 读取 —— 真值对照 + 预算/资格拒绝面。

真值来自 fixture 常量（coverage_values()：value=10+row*nx+col，[0][0]=nodata）
—— 服务端与断言共享同一函数，不存在"测试伪造通过"。
"""
import pytest

from app.core.config import settings
from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.adapters.ogc_coverages_adapter import OGCCoveragesAdapter
from app.services.data_fabric.coverage_read import (
    CoverageReadError,
    read_coverage_subset,
    RC_AXIS_NOT_IDENTIFIABLE,
    RC_BUDGET_EXCEEDED,
    RC_CAPABILITY_NOT_DECLARED,
)
from tests.data.ogc_service_fixtures import (
    COVERAGE_AXIS_X_VALUES,
    COVERAGE_AXIS_Y_VALUES,
    COVERAGE_NODATA,
    coverage_values,
)

_SESSION = "cov-read-sess-01"


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    yield tmp_path


def _adapter(patched_safe_sessions, server, url) -> OGCCoveragesAdapter:
    patched_safe_sessions(server)
    return OGCCoveragesAdapter(
        ConnectionProfile(source_type="ogc_api_coverages", endpoint_url=url, name="cov_read")
    )


@pytest.fixture
def cov_setup(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_coverages()
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    return adapter


async def _read(adapter, **kw):
    return await read_coverage_subset(
        adapter, "dem", session_id=_SESSION, **kw
    )


class TestHappyPathTruth:
    async def test_full_subset_roundtrip_truth(self, cov_setup):
        """subset=x(100:100.04),y(20:20.04) → COG ref 像元值/transform/nodata
        与 fixture 真值一致。"""
        result = await _read(
            cov_setup,
            subsets={"x": (100.0, 100.04), "y": (20.0, 20.04)},
        )
        assert result["status"] == "success"
        assert result["ref"].startswith("ref:fabric-cog/")
        assert result["crs"] == "OGC:CRS84"
        assert result["band_names"] == ["elevation"]
        assert result["nodata"] == COVERAGE_NODATA

        import rasterio

        with rasterio.open(result["path"]) as ds:
            arr = ds.read(1)
            truth = coverage_values()  # north-up 翻转前：truth[row][col]
            # fixture lat 升序（20→20.04），COG north-up 需降序 → 行翻转。
            expected = truth[::-1]
            assert arr.shape == (3, 3)
            for r in range(3):
                for c in range(3):
                    if expected[r][c] == COVERAGE_NODATA:
                        assert arr[r, c] == COVERAGE_NODATA
                    else:
                        assert arr[r, c] == pytest.approx(expected[r][c], abs=1e-6), (r, c)
            # transform：像元中心 → 角点（半像元偏移），分辨率 = 轴步长。
            xres = (COVERAGE_AXIS_X_VALUES[-1] - COVERAGE_AXIS_X_VALUES[0]) / 2
            yres = (COVERAGE_AXIS_Y_VALUES[-1] - COVERAGE_AXIS_Y_VALUES[0]) / 2
            assert ds.transform.a == pytest.approx(xres)
            assert ds.transform.e == pytest.approx(-yres)  # north-up 负 y
            assert ds.transform.c == pytest.approx(COVERAGE_AXIS_X_VALUES[0] - xres / 2)
            # north-up 顶行对应 lat 最大值 20.04：其顶边 = 20.04 + yres/2
            assert ds.transform.f == pytest.approx(COVERAGE_AXIS_Y_VALUES[-1] + yres / 2)
            assert ds.nodata == COVERAGE_NODATA

    async def test_bbox_maps_to_declared_axes(self, cov_setup):
        """bbox [100,20,100.04,20.04] 映射到声明轴 x/y（封闭名表）。"""
        result = await _read(cov_setup, bbox=[100.0, 20.0, 100.04, 20.04])
        assert result["status"] == "success"
        assert result["grid_shape"] == [1, 3, 3]

    async def test_partial_subset_narrows_grid(self, cov_setup):
        """窄 subset（x 只取后两列）→ 网格 2 列，值仍与真值子集一致。"""
        result = await _read(cov_setup, subsets={"x": (100.02, 100.04)})
        assert result["status"] == "success"
        import rasterio

        with rasterio.open(result["path"]) as ds:
            assert ds.width == 2 and ds.height == 3
            truth = coverage_values()
            expected = [row[1:] for row in truth[::-1]]
            arr = ds.read(1)
            for r in range(3):
                for c in range(2):
                    assert arr[r, c] == pytest.approx(expected[r][c], abs=1e-6)

    async def test_provenance_and_fingerprint(self, cov_setup):
        result = await _read(cov_setup, subsets={"x": (100.0, 100.04)})
        prov = result["provenance"]
        assert prov["source_type"] == "ogc_api_coverages"
        assert prov["dataset_id"] == "dem"
        assert prov["format"] == "application/prs.coverage+json"
        assert "subset" in prov["source_url"] or "/coverage" in prov["source_url"]
        assert result["fingerprint"].startswith("sha256:")
        assert result["content_sha256"] == result["fingerprint"].split(":", 1)[1]
        # 同一资产重读 → 新 ref，但 CAS 内容指纹一致（幂等基础）
        result2 = await _read(cov_setup, subsets={"x": (100.0, 100.04)})
        assert result2["content_sha256"] == result["content_sha256"]


class TestRefConsumption:
    async def test_ref_feeds_raster_statistics(self, cov_setup):
        """ref 产物可被栅格工具消费：有效像元统计与真值一致（G01 numeric
        raster 资格的实证）。"""
        result = await _read(cov_setup, subsets={"x": (100.0, 100.04), "y": (20.0, 20.04)})
        import numpy as np
        import rasterio

        with rasterio.open(result["path"]) as ds:
            arr = ds.read(1)
        valid = arr[arr != COVERAGE_NODATA]
        truth_vals = [v for row in coverage_values() for v in row if v != COVERAGE_NODATA]
        assert valid.size == len(truth_vals)
        assert float(np.mean(valid)) == pytest.approx(sum(truth_vals) / len(truth_vals), abs=1e-6)
        assert float(valid.max()) == pytest.approx(max(truth_vals))


class TestRefusalFaces:
    async def test_not_ready_refused(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_coverages(with_coverage_link=False, prefix="/cov-nr")
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        with pytest.raises(CoverageReadError) as ei:
            await _read(adapter, subsets={"x": (100.0, 100.04)})
        assert ei.value.reason_code == RC_CAPABILITY_NOT_DECLARED

    async def test_unknown_axis_refused(self, cov_setup):
        with pytest.raises(CoverageReadError) as ei:
            await _read(cov_setup, subsets={"Long": (100.0, 100.04)})
        assert ei.value.reason_code == RC_AXIS_NOT_IDENTIFIABLE
        assert "declared_axes" in ei.value.details

    async def test_budget_exceeded_with_hint(self, cov_setup):
        """整幅网格超像元帽 → typed 拒绝 + 声明网格规模（不静默降采样）。"""
        with pytest.raises(CoverageReadError) as ei:
            await _read(cov_setup, max_cells=4)  # 3x3x1=9 > 4
        assert ei.value.reason_code == RC_BUDGET_EXCEEDED
        assert ei.value.details["declared_grid"] == {"x": 3, "y": 3}

    async def test_remote_5xx_typed(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_coverages(
            status_overrides={"/coverage": 503}, prefix="/cov-503"
        )
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        with pytest.raises(CoverageReadError) as ei:
            await _read(adapter, subsets={"x": (100.0, 100.04)})
        # R1 P1-1/P1-9：5xx = unreachable（≠字节帽），typed reason code 可断言
        assert ei.value.reason_code == "unreachable"
