"""ads-v1 fixture-infra round-trip tests (DS0, ADR-0170).

Proves the A11 offline fixture layer serves *minimal true responses* for the
five protocols: each canned payload must round-trip through the REAL adapter
(probe → list_datasets → query/describe). Also proves the offline guard
actually blocks dials while the fixtures keep working under it.
"""
from __future__ import annotations

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from tests.data.offline_guard import NetworkBlockedError, offline_socket_guard
from tests.data.fabric_fixtures import (
    FakeSourceServer,
    _feature,
)


@pytest.fixture
def server_and_sessions(fake_source_server, patched_safe_sessions):
    fake_source_server.add_ogc_api(features=[_feature("1"), _feature("2")])
    fake_source_server.add_wfs(features=[_feature("3")])
    fake_source_server.add_stac()
    fake_source_server.add_arcgis(features=[_feature("4")])
    patched_safe_sessions(fake_source_server)
    return fake_source_server


def _profile(source_type: str, endpoint_url: str) -> ConnectionProfile:
    return ConnectionProfile(source_type=source_type, endpoint_url=endpoint_url, name=f"ads_{source_type}")


# ── HTTP protocols (4) ───────────────────────────────────────────────────────


def test_ogc_api_roundtrip(server_and_sessions):
    from app.services.data_fabric.adapters.ogc_api_adapter import OGCAPIAdapter

    server = server_and_sessions
    url = server.base_url + "/ogc"
    adapter = OGCAPIAdapter(_profile("ogc_api", url))
    assert adapter.probe() is True
    datasets = adapter.list_datasets()
    assert any(d.get("id") == "lake_depth" for d in datasets)
    result = adapter.query("lake_depth", QuerySpec(limit=10))
    assert len(result.features) == 2
    assert result.features[0]["properties"]["name"] == "feat-1"


def test_wfs_roundtrip(server_and_sessions):
    from app.services.data_fabric.adapters.wfs_adapter import WFSAdapter

    server = server_and_sessions
    adapter = WFSAdapter(_profile("wfs", server.base_url + "/wfs"))
    assert adapter.probe() is True
    datasets = adapter.list_datasets()
    assert any("parcels" in str(d.get("id", "")) for d in datasets)
    result = adapter.query("ads:parcels", QuerySpec(limit=5))
    assert len(result.features) >= 1
    assert result.features[0]["properties"]["name"] == "feat-3"


def test_stac_roundtrip(server_and_sessions):
    from app.services.data_fabric.adapters.stac_adapter import STACAdapter

    server = server_and_sessions
    adapter = STACAdapter(_profile("stac", server.base_url + "/stac"))
    assert adapter.probe() is True
    result = adapter.query("scene-001", QuerySpec(limit=5))
    assert len(result.features) >= 1


def test_arcgis_roundtrip(server_and_sessions):
    from app.services.data_fabric.adapters.arcgis_adapter import ArcGISAdapter

    server = server_and_sessions
    url = server.base_url + "/arcgis/rest/services/ads/FeatureServer"
    adapter = ArcGISAdapter(_profile("arcgis", url))
    assert adapter.probe() is True
    result = adapter.query("0", QuerySpec(limit=5))
    assert len(result.features) >= 1
    assert result.features[0]["geometry"]["type"] == "Point"


# ── PostGIS (DB-API pool seam) ───────────────────────────────────────────────


def test_postgis_probe_offline(monkeypatch):
    from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter

    server = FakeSourceServer()
    dsn = server.add_postgis(monkeypatch, canned=[("SELECT 1", [(1,)])])
    adapter = PostGISAdapter(_profile("postgis", dsn))
    assert adapter.probe() is True


# ── Offline guard really blocks, fixtures still work ────────────────────────


def test_offline_guard_blocks_dial_but_not_fixtures(server_and_sessions):
    # fixtures keep working under the guard (no socket involved)
    with offline_socket_guard():
        from app.services.data_fabric.adapters.ogc_api_adapter import OGCAPIAdapter

        server = server_and_sessions
        adapter = OGCAPIAdapter(_profile("ogc_api", server.base_url + "/ogc"))
        assert adapter.list_datasets()  # served by the fake adapter, zero dials

        # a raw dial attempt to a public internet address must raise the typed
        # blocker, not reach the wire. Assert at the raw socket level: a
        # requests-level dial may legitimately terminate at an internal proxy
        # (which the guard allows), so the veto point must be tested directly.
        import socket as _socket

        with pytest.raises(NetworkBlockedError):
            _socket.create_connection(("1.1.1.1", 443), timeout=1)
        with pytest.raises(NetworkBlockedError):
            _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM).connect(("8.8.8.8", 53))


def test_fake_server_never_serves_outside_declared_routes(server_and_sessions):
    from tests.fixtures.data_fabric.fake_server import session_with_fake

    session = session_with_fake(tuple(server_and_sessions.routes))
    # a path under NO declared route prefix → typed 404 from the fake adapter
    resp = session.get(f"{server_and_sessions.base_url}/nowhere-at-all")
    assert resp.status_code == 404
