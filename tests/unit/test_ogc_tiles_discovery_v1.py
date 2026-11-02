"""GOAL-02 M1/M7：OGC API - Tiles 发现 adapter 离线往返（fixture 真响应）。

三态能力 + 版本化 descriptor + typed 拒绝面。全部离线（FakeSourceServer
挂在 requests seam 上，逐跳 SSRF 仍生效）。
"""
import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters import OGCTilesAdapter
from app.services.data_fabric.adapters.ogc_tiles_adapter import DESCRIPTOR_VERSION
from app.services.data_fabric.errors import (
    QueryUnsupportedError,
    SourceUnreachableError,
    UnsupportedSourceError,
)
from tests.data.ogc_service_fixtures import (
    CRS_3857_URI,
    MEDIA_MVT,
    TMS_CRS84_URI,
    TMS_WEBMERCATOR_URI,
)

from app.services.data_fabric.registry import AdapterRegistry
from app.services.data_fabric.registry import _build_registry
from app.services.data_fabric.source_registry import KNOWN_PROTOCOLS


def _adapter(patched_safe_sessions, server, installed) -> OGCTilesAdapter:
    patched_safe_sessions(server)
    url = installed
    return OGCTilesAdapter(
        ConnectionProfile(source_type="ogc_api_tiles", endpoint_url=url, name="tiles_test")
    )


def test_registry_and_protocols_wired():
    reg: AdapterRegistry = _build_registry()
    for name in ("ogc_api_tiles", "ogc_tiles"):
        spec = reg.resolve(name)
        assert spec.canonical == "ogc_api_tiles"
        assert spec.is_raster_tile
        assert spec.adapter_cls is OGCTilesAdapter
    assert {"ogc_api_tiles", "ogc_api_coverages"} <= KNOWN_PROTOCOLS
    # 未注册变体仍拒绝（append-only 契约不软化）
    with pytest.raises(UnsupportedSourceError):
        reg.resolve("ogc_api_tiles_v2")


def test_probe_health_and_list(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_tiles(collection_ids=("admin",))
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    assert adapter.probe() is True
    health = adapter.health()
    assert health.status == "healthy"
    assert health.details["declares_tiles"] is True

    datasets = adapter.list_datasets()
    assert [d["id"] for d in datasets] == ["admin:WebMercatorQuad"]
    assert datasets[0]["item_type"] == "tile"
    assert datasets[0]["source_type"] == "ogc_api_tiles"


def test_describe_supported_webmercator_vector(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_tiles(collection_ids=("admin",))
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)

    desc = adapter.describe("admin:WebMercatorQuad")
    meta = desc.metadata
    assert meta["descriptor_version"] == DESCRIPTOR_VERSION
    assert meta["capability"]["state"] == "supported"
    assert meta["capability"]["reasons"] == []
    assert meta["display_ready"] is True
    assert desc.geometry_type == "tile_vector"
    assert desc.srs == CRS_3857_URI
    assert meta["tile_matrix_set_uri"] == TMS_WEBMERCATOR_URI
    assert meta["minzoom"] == 0 and meta["maxzoom"] == 5
    assert meta["media_types_declared"] == [MEDIA_MVT]
    assert meta["media_kind"] == "vector"
    # 瓦片模板保留 RFC 6570 变量（代理在 M2 做代入）
    raw_tpl = meta["tile_url_template_raw"]
    assert raw_tpl.endswith("/tiles/WebMercatorQuad/{tileMatrix}/{tileRow}/{tileCol}")
    # 出目录 URL 一律 redact
    assert "@" not in meta["tile_url_template"]
    assert desc.bbox is not None and len(desc.bbox) == 4
    # preview 是 descriptor 级（不取瓦片字节）
    pv = adapter.preview("admin:WebMercatorQuad")
    assert pv["schema"]["descriptor_version"] == DESCRIPTOR_VERSION
    assert pv["properties"]["display_ready"] is True


def test_describe_embedded_tms(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_tiles(embed_tms=True)
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    desc = adapter.describe("admin:WebMercatorQuad")
    assert desc.metadata["tile_matrix_set_declared"] is True
    assert desc.metadata["display_ready"] is True


def test_describe_raster_media_kind(patched_safe_sessions, ogc_fake_server):
    from tests.data.ogc_service_fixtures import MEDIA_PNG

    url = ogc_fake_server.add_ogc_tiles(media_type=MEDIA_PNG)
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    desc = adapter.describe("admin:WebMercatorQuad")
    assert desc.geometry_type == "tile_raster"
    assert desc.metadata["media_kind"] == "raster"
    assert desc.metadata["display_ready"] is True


def test_three_state_matrix(patched_safe_sessions, ogc_fake_server):
    """三态资格：非 WebMercatorQuad → unsupported；声明缺失 → unknown。"""
    cases = [
        # (kwargs, expected_state, expected_reason_code)
        ({"tms_uri": TMS_CRS84_URI}, "unsupported", "tms_not_displayable"),
        ({"crs": None}, "unknown", "crs_not_declared"),
        ({"with_tile_links": False}, "unknown", "no_tile_links"),
        ({"media_type": "image/gif"}, "unsupported", "media_type_unsupported"),
    ]
    for kwargs, state, code in cases:
        url = ogc_fake_server.add_ogc_tiles(collection_ids=(f"admin-{len(code)}",), prefix=f"/ogc-{code}", **kwargs)
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        cid = list(kwargs and [f"admin-{len(code)}"] or ["admin"])[0]
        desc = adapter.describe(f"{cid}:WebMercatorQuad")
        cap = desc.metadata["capability"]
        assert cap["state"] == state, f"{kwargs}: {cap}"
        assert any(r["code"] == code for r in cap["reasons"]), f"{kwargs}: {cap['reasons']}"
        assert desc.metadata["display_ready"] is (state == "supported")


def test_conformance_absent_still_builds_from_tileset(patched_safe_sessions, ogc_fake_server):
    """conformance 是辅助信号：tileset 文档自身声明才是资格依据。"""
    url = ogc_fake_server.add_ogc_tiles(include_tiles_conformance=False)
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    assert adapter.health().details["declares_tiles"] is False
    desc = adapter.describe("admin:WebMercatorQuad")
    assert desc.metadata["capability"]["state"] == "supported"
    assert desc.metadata["capabilities_conformance_declared"] is False


def test_describe_remote_failure_is_typed(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_tiles(status_overrides={"/tiles/WebMercatorQuad": 500})
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    with pytest.raises(SourceUnreachableError):
        adapter.describe("admin:WebMercatorQuad")


def test_query_is_typed_unsupported(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_tiles()
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    with pytest.raises(QueryUnsupportedError):
        adapter.query("admin:WebMercatorQuad", QuerySpec(limit=10))


def test_sync_registers_display_descriptor(patched_safe_sessions, ogc_fake_server):
    url = ogc_fake_server.add_ogc_tiles(collection_ids=("admin",))
    adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
    res = adapter.sync(owner="sess-tiles")
    assert res["status"] == "synced"
    assert res["count"] == 1
