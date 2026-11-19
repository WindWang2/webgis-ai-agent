"""#2232 回归：未识别 CRS 字符串抛 CRSError（RuntimeError 子类而非
ValueError），曾穿透 ``except ValueError`` 防御打崩调用方。

断言四类调用点都按契约降级/typed 拒绝，绝不把 CRSError 泄漏为 500。
"""
import pytest

from app.services.data_catalog.catalog import CatalogEntry, CatalogFilter
from app.services.data_fabric.coverage_read import (
    CoverageReadError,
    RC_AXIS_NOT_IDENTIFIABLE,
)
from app.services.data_profile.distribution import spatial_distribution
from app.services.data_profile.incremental import update_state

_BOGUS_CRS = "NOT_A_CRS_AT_ALL"


def _entry(**kw) -> CatalogEntry:
    base = dict(entry_id="e1", scope="session")
    base.update(kw)
    return CatalogEntry(**base)


class TestCatalogFilterCrs:
    def test_invalid_filter_crs_returns_no_match(self):
        """DataCatalog.search 路径：filter.crs 无法解析 → 该条目不匹配，
        不得抛 CRSError。"""
        f = CatalogFilter(crs=_BOGUS_CRS)
        assert f.matches(_entry(crs="EPSG:4326")) is False

    def test_invalid_entry_crs_with_bbox_returns_no_match(self):
        """bbox 过滤需把 entry.extent 转到 4326；entry.crs 无法解析 →
        不匹配，不得抛 CRSError。"""
        f = CatalogFilter(bbox=[100.0, 20.0, 101.0, 21.0])
        assert f.matches(_entry(crs=_BOGUS_CRS, extent=[100.0, 20.0, 100.5, 20.5])) is False

    def test_invalid_entry_crs_with_filter_crs_returns_no_match(self):
        f = CatalogFilter(crs="EPSG:4326")
        assert f.matches(_entry(crs=_BOGUS_CRS)) is False


class TestSpatialDistribution:
    def test_invalid_crs_reports_unavailable(self):
        """build_unified_profile(spatial_distribution) 路径：未知 CRS →
        available=False / reason=invalid_crs，不得抛 CRSError。"""
        feats = [{"geometry": {"type": "Point", "coordinates": [104.0, 30.0]}}]
        out = spatial_distribution(feats, crs=_BOGUS_CRS)
        assert out == {"available": False, "reason": "invalid_crs"}


class TestIncrementalUpdateState:
    def test_invalid_crs_degrades_distribution_without_raise(self):
        """incremental.update_state 路径：未知 CRS → 分布增量退化为空，
        其余统计照常，不得抛 CRSError。"""
        from app.services.data_profile.incremental import new_state

        state = new_state()
        feats = [
            {
                "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
                "properties": {"v": 1.0},
            }
        ]
        out = update_state(state, feats, crs=_BOGUS_CRS)
        assert out["count"] == 1
        assert out["geom_types"].get("Point") == 1
        assert out["fields"]["v"]["n"] == 1


class TestCoverageReadSubset:
    @pytest.fixture
    def cov_setup(self, patched_safe_sessions, ogc_fake_server, tmp_path, monkeypatch):
        from app.core.config import settings
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_coverages_adapter import OGCCoveragesAdapter

        monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
        url = ogc_fake_server.add_ogc_coverages()
        patched_safe_sessions(ogc_fake_server)
        return OGCCoveragesAdapter(
            ConnectionProfile(source_type="ogc_api_coverages", endpoint_url=url, name="cov_2232")
        )

    async def test_unresolvable_grid_crs_is_typed_rejection(self, cov_setup, monkeypatch):
        """read_coverage_subset 路径：domainset 声明的 grid_crs 无法解析 →
        CoverageReadError(axis_not_identifiable)，不得抛 CRSError。"""
        orig_describe = cov_setup.describe

        def _patched(dataset_id):
            desc = orig_describe(dataset_id)
            meta = dict(desc.metadata)
            meta["grid_crs"] = "EPSG:999999999"  # pyproj → CRSError
            from types import SimpleNamespace

            return SimpleNamespace(metadata=meta)

        monkeypatch.setattr(cov_setup, "describe", _patched)
        from app.services.data_fabric.coverage_read import read_coverage_subset

        with pytest.raises(CoverageReadError) as ei:
            await read_coverage_subset(
                cov_setup, "dem", session_id="cov-read-sess-2232",
                bbox=[100.0, 20.0, 100.04, 20.04],
            )
        assert ei.value.reason_code == RC_AXIS_NOT_IDENTIFIABLE
