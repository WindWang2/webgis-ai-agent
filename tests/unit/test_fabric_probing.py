"""Capability Probing V7 测试（ADR-0119 W3）。

- 探测后能力升级（CQL2 / maxRecordCount / STAC filter extension）；
- scoped 缓存：TTL、profile revision 失配、容量上界、stale 披露；
- 探测失败回落静态默认（caps_basis=default），绝不编造；
- rate-limit 头解析原语（Retry-After / X-RateLimit-*）；
- ProbeCost 结构性记账。
"""

import pytest

from app.services.data_fabric.fabric.probing import (
    CapabilityProbeService,
    _rate_limit_from_headers,
)
from app.services.data_fabric.fabric.connection_registry import TenantScope, profile_revision
from app.services.data_fabric.query.capabilities import get_capabilities


class _HeaderBag(dict):
    def get(self, name, default=None):
        return super().get(name, default)


class _FakeOgcAdapter:
    """OGC API adapter 假体：conformance 声明 CQL2-JSON。"""

    source_type = "ogc_api"

    def __init__(self, conformance):
        self._conformance = conformance

    def _get_conformance(self):
        return list(self._conformance)

    def capabilities_v2(self):
        caps = get_capabilities("ogc_api")
        if any("cql2" in c for c in self._conformance):
            caps = caps.model_copy(update={"filter_pushdown": True})
        return caps


class _FakeArcgisAdapter:
    source_type = "arcgis"

    def capabilities_v2(self):
        caps = get_capabilities("arcgis")
        return caps.model_copy(update={"max_page_size": 8_000})


class _BrokenAdapter:
    source_type = "arcgis"

    def capabilities_v2(self):
        raise RuntimeError("probe exploded")


class _FakeStacAdapter:
    source_type = "stac"
    url = "about:blank"  # 非 http；探测原语会失败回落 → default


@pytest.fixture()
def svc():
    return CapabilityProbeService(ttl_s=60.0, max_entries=4)


def _args(profile_id="ds_1", source_type="ogc_api", url="https://example.com/x"):
    from app.schemas.data_fabric_schema import ConnectionProfile

    profile = ConnectionProfile(id=profile_id, source_type=source_type, url=url)
    scope = TenantScope(owner="alice")
    return profile, scope.scope_key(), profile_revision({"id": profile_id}, None)


def test_rate_limit_header_parsing_primitive():
    hints = _rate_limit_from_headers(
        _HeaderBag({"X-RateLimit-Limit": "120", "X-RateLimit-Window": "60"}))
    assert hints is not None
    assert hints.requests_per_window == 120
    assert hints.window_s == 60.0
    retry = _rate_limit_from_headers(_HeaderBag({"Retry-After": "17"}))
    assert retry is not None and retry.retry_after_s == 17.0
    assert _rate_limit_from_headers(_HeaderBag({})) is None  # 诚实 None


def test_ogc_cql2_json_probe_upgrades_filter_encoding(svc):
    adapter = _FakeOgcAdapter(
        ["http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
         "http://www.opengis.net/spec/cql2/1.0/conf/cql2-json"])
    profile, scope_key, rev = _args()
    record = svc.probe(adapter, profile, scope_key, rev)
    assert record.caps_basis == "probed"
    assert record.caps.filter_pushdown is True
    assert record.caps.filter_encoding == "cql2-json"
    assert record.probe_cost.requests >= 1


def test_arcgis_max_page_size_probe(svc):
    profile, scope_key, rev = _args(profile_id="ds_ag", source_type="arcgis")
    record = svc.probe(_FakeArcgisAdapter(), profile, scope_key, rev)
    assert record.caps.max_page_size == 8_000


def test_probe_failure_falls_back_to_default_matrix(svc):
    profile, scope_key, rev = _args(profile_id="ds_bad", source_type="arcgis")
    record = svc.probe(_BrokenAdapter(), profile, scope_key, rev)
    assert record.caps_basis == "default"
    # 与静态默认矩阵一致 —— 绝不编造
    assert record.caps == get_capabilities("arcgis")


def test_cache_ttl_and_revision_mismatch(svc):
    adapter = _FakeOgcAdapter([])
    profile, scope_key, rev = _args()
    r1 = svc.probe(adapter, profile, scope_key, rev)
    r2 = svc.probe(adapter, profile, scope_key, rev)
    assert r1 is r2  # 缓存命中
    # revision 变化（profile 更新）→ 新键 → 重新探测
    r3 = svc.probe(adapter, profile, scope_key, rev + "new")
    assert r3 is not r1
    # get 只读披露
    got = svc.get(profile.id, scope_key, rev)
    assert got is r1


def test_cache_capacity_bounded(svc):
    for i in range(6):
        profile, scope_key, rev = _args(profile_id=f"ds_{i}")
        svc.probe(_FakeOgcAdapter([]), profile, scope_key, rev)
    assert svc.stats()["entries"] <= 4


def test_invalidate_by_profile(svc):
    p1, s1, r1 = _args(profile_id="ds_1")
    p2, s2, r2 = _args(profile_id="ds_2")
    svc.probe(_FakeOgcAdapter([]), p1, s1, r1)
    svc.probe(_FakeOgcAdapter([]), p2, s2, r2)
    assert svc.invalidate("ds_1") == 1
    assert svc.get("ds_1", s1, r1) is None
    assert svc.get("ds_2", s2, r2) is not None
