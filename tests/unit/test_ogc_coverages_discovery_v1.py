"""GOAL-02 M1/M7：OGC API - Coverages 发现 adapter 离线往返（fixture 真响应）。"""
import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters import OGCCoveragesAdapter
from app.services.data_fabric.adapters.ogc_coverages_adapter import DESCRIPTOR_VERSION
from app.services.data_fabric.errors import QueryUnsupportedError
from tests.data.ogc_service_fixtures import COVERAGE_NODATA


def _adapter(patched_safe_sessions, server, installed) -> OGCCoveragesAdapter:
    patched_safe_sessions(server)
    return OGCCoveragesAdapter(
        ConnectionProfile(source_type="ogc_api_coverages", endpoint_url=installed, name="cov_test")
    )


def test_probe_health_list(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_coverages()
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    assert adapter.probe() is True
    health = adapter.health()
    assert health.status == "healthy"
    assert health.details["declares_coverages"] is True
    datasets = adapter.list_datasets()
    assert [d["id"] for d in datasets] == ["dem"]
    assert datasets[0]["item_type"] == "coverage"


def test_describe_supported(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_coverages()
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    desc = adapter.describe("dem")
    meta = desc.metadata
    assert meta["descriptor_version"] == DESCRIPTOR_VERSION
    assert meta["capability"]["state"] == "supported"
    assert meta["subset_read_ready"] is True
    assert meta["has_coverage_endpoint"] is True
    # 轴摘要来自 domainset 声明（不硬编码轴名）
    assert meta["axes"]["x"]["count"] == 3
    assert meta["axes"]["x"]["resolution"] == pytest.approx(0.02)
    assert meta["axes"]["y"]["count"] == 3
    # 波段/nodata 来自 rangetype 声明
    assert meta["bands"][0]["name"] == "elevation"
    assert meta["bands"][0]["nodata"] == COVERAGE_NODATA
    # 格式声明（服务自己声明的 COVJSON）
    assert meta["formats_declared"] == ["application/prs.coverage+json"]
    assert meta["formats_supported"] == ["application/prs.coverage+json"]
    # CRS 不猜：collection 级未声明 → None（以 coverage 响应为准）
    assert desc.srs is None
    assert desc.bbox == [100.0, 20.0, 100.04, 20.04]
    pv = adapter.preview("dem")
    assert pv["schema"]["descriptor_version"] == DESCRIPTOR_VERSION
    assert pv["properties"]["subset_read_ready"] is True


def test_no_coverage_endpoint_unsupported(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_coverages(with_coverage_link=False)
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    # 无 coverage 链接但 itemType=coverage → 仍进入目录，但资格 unsupported
    assert [d["id"] for d in adapter.list_datasets()] == ["dem"]
    desc = adapter.describe("dem")
    assert desc.metadata["capability"]["state"] == "unsupported"
    assert any(
        r["code"] == "no_coverage_endpoint"
        for r in desc.metadata["capability"]["reasons"]
    )


def test_missing_domainset_or_rangetype_unknown(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_coverages(with_domainset=False, prefix="/cov-1")
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    desc = adapter.describe("dem")
    assert desc.metadata["capability"]["state"] == "unknown"
    assert any(r["code"] == "domainset_missing" for r in desc.metadata["capability"]["reasons"])

    url2 = ogc_fake_server.add_ogc_coverages(with_rangetype=False, prefix="/cov-2")
    adapter2 = _adapter(patched_safe_sessions, ogc_fake_server, url2)
    desc2 = adapter2.describe("dem")
    assert desc2.metadata["capability"]["state"] == "unknown"
    assert any(r["code"] == "rangetype_missing" for r in desc2.metadata["capability"]["reasons"])


def test_format_not_declared_or_unsupported(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_coverages(declared_formats=None, prefix="/cov-3")
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    desc = adapter.describe("dem")
    assert desc.metadata["capability"]["state"] == "unknown"
    assert any(
        r["code"] == "coverage_format_not_declared"
        for r in desc.metadata["capability"]["reasons"]
    )

    url2 = ogc_fake_server.add_ogc_coverages(
        declared_formats=("application/gml+xml",), prefix="/cov-4"
    )
    adapter2 = _adapter(patched_safe_sessions, ogc_fake_server, url2)
    desc2 = adapter2.describe("dem")
    assert desc2.metadata["capability"]["state"] == "unsupported"
    assert any(
        r["code"] == "coverage_format_unsupported"
        for r in desc2.metadata["capability"]["reasons"]
    )


def test_query_is_typed_unsupported(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_coverages()
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    with pytest.raises(QueryUnsupportedError):
        adapter.query("dem", QuerySpec(limit=10))
