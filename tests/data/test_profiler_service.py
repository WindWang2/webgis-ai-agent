"""Dataset Profiler 服务层测试（会话 ref / 缓存 / 修订绑定）。"""
import pytest

from app.services.data_profile.profiler import (
    get_dataset_profiler,
    reset_dataset_profiler,
)
from app.lib.data.profile import ProfileQuality
from app.services.session_data import session_data_manager


@pytest.fixture()
def profiler():
    reset_dataset_profiler()
    yield get_dataset_profiler()
    reset_dataset_profiler()


async def _store_fc(features):
    return await session_data_manager.store(
        "profiler-tests", {"type": "FeatureCollection", "features": features}, prefix="geojson"
    )


def _feats(n=10):
    return [
        {"geometry": {"type": "Point", "coordinates": [116.0 + i * 0.1, 39.0]},
         "properties": {"v": i, "cat": f"c{i % 2}"}}
        for i in range(n)
    ]


class TestSessionRefProfiling:
    async def test_shallow_is_partial_descriptor_projection(self, profiler):
        ref = await _store_fc(_feats())
        prof = await profiler.profile_session_ref("profiler-tests", ref)
        assert prof is not None
        assert prof.profile_quality is ProfileQuality.PARTIAL
        assert prof.vector.row_count == 10
        assert prof.vector.scanned_rows == 0          # 零扫描
        assert "v" in prof.vector.fields
        assert prof.vector.fields["v"].mean is None   # 描述符无均值，不虚构

    async def test_deep_is_complete(self, profiler):
        ref = await _store_fc(_feats())
        prof = await profiler.profile_session_ref("profiler-tests", ref, deep=True)
        assert prof.profile_quality is ProfileQuality.COMPLETE
        assert prof.vector.scanned_rows == 10
        assert prof.vector.fields["v"].mean is not None

    async def test_cache_binds_revision(self, profiler):
        ref = await _store_fc(_feats())
        deep1 = await profiler.profile_session_ref("profiler-tests", ref, deep=True)
        # overwrite 同 ref，内容变化（revision bump）→ 缓存键变化 → 重新剖析
        await session_data_manager.overwrite(
            "profiler-tests", ref,
            {"type": "FeatureCollection", "features": _feats(3)},
        )
        deep2 = await profiler.profile_session_ref("profiler-tests", ref, deep=True)
        assert deep2.vector.row_count == 3
        assert deep1.vector.row_count == 10

    async def test_missing_ref_returns_none(self, profiler):
        assert await profiler.profile_session_ref("profiler-tests", "ref:geojson-nonexistent") is None

    async def test_non_fc_payload_degrades_honestly(self, profiler):
        ref = await session_data_manager.store(
            "profiler-tests", {"type": "chart_spec", "series": [1, 2, 3]}, prefix="chart"
        )
        prof = await profiler.profile_session_ref("profiler-tests", ref, deep=True)
        assert prof.profile_quality in (ProfileQuality.PARTIAL, ProfileQuality.FAILED)
        assert any("unsupported_payload" in d or "no_field_schema" in d for d in prof.diagnostics)

    async def test_invalidate(self, profiler):
        ref = await _store_fc(_feats())
        await profiler.profile_session_ref("profiler-tests", ref)
        assert profiler.invalidate("profiler-tests", ref) >= 1
        assert profiler.invalidate() >= 0  # 全清不抛


class TestRasterProfiling:
    async def test_rasterio_missing_degrades(self, profiler, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _no_rasterio(name, *a, **kw):
            if name == "rasterio":
                raise ImportError("No module named 'rasterio'")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _no_rasterio)
        prof = await profiler.profile_raster_file("/nonexistent.tif", ref_id="ref:raster/x")
        assert prof.profile_quality is ProfileQuality.FAILED
        assert any("rasterio_unavailable" in d for d in prof.diagnostics)

    async def test_unreadable_file_fails_honestly(self, profiler, tmp_path):
        pytest.importorskip("rasterio")
        bad = tmp_path / "bad.tif"
        bad.write_bytes(b"not a tiff")
        prof = await profiler.profile_raster_file(str(bad), ref_id="ref:raster/bad")
        assert prof.profile_quality is ProfileQuality.FAILED
        assert any("raster_unreadable" in d for d in prof.diagnostics)
