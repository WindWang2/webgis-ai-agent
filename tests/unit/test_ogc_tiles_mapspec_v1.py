"""GOAL-02 M2：OGC Tiles → MapSpec source 落库与 schema 校验。

- store_data 的 vector passthrough（最小兼容扩展）；
- vector/data_fabric(ogc url) 两种源经 parse_mapspec 权威 schema 往返；
- 既有分类行为不回归（geojson inline / ref / model3d 次序不受影响）。
"""

from app.lib.cartography.mapspec_schema import parse_mapspec
from app.services.mapspec_source import store_data


def _minimal_mapspec(sources: dict, layers: list) -> dict:
    return {
        "version": "1.7",
        "metadata": {"name": "ogc-tiles-test"},
        "sources": sources,
        "layers": layers,
        "view": {"center": [116.4, 39.9], "zoom": 10},
    }


_VECTOR_SRC = {
    "type": "vector",
    "tiles": ["/api/v1/data-fabric/catalog/admin:WebMercatorQuad/ogc-tiles/{z}/{x}/{y}?session_id=sess-12345678"],
    "minzoom": 0,
    "maxzoom": 5,
}

_FABRIC_URL_SRC = {
    "type": "data_fabric",
    "protocol": "ogc_api_tiles",
    "catalog_item_id": "admin:WebMercatorQuad",
    "url": "/api/v1/data-fabric/catalog/admin:WebMercatorQuad/ogc-tiles/{z}/{x}/{y}?session_id=sess-12345678",
}


class TestStoreDataVectorPassthrough:
    def test_vector_dict_passthrough(self):
        entry: dict = {}
        store_data(entry, dict(_VECTOR_SRC))
        assert entry["type"] == "vector"
        assert entry["tiles"] == _VECTOR_SRC["tiles"]
        assert entry["minzoom"] == 0 and entry["maxzoom"] == 5

    def test_vector_without_tiles_not_promoted(self):
        """缺 tiles 的 vector dict 不是瓦片源 —— 不落 vector 分支（诚实）。"""
        entry: dict = {}
        store_data(entry, {"type": "vector"})
        assert entry["type"] != "vector"

    def test_data_fabric_url_source_unchanged(self):
        entry: dict = {}
        store_data(entry, dict(_FABRIC_URL_SRC))
        assert entry["type"] == "data_fabric"
        assert "{z}/{x}/{y}" in entry["url"]

    def test_existing_geojson_inline_not_regressed(self):
        entry: dict = {}
        store_data(entry, {"type": "FeatureCollection", "features": []})
        assert entry["type"] == "geojson"

    def test_existing_ref_source_not_regressed(self):
        entry: dict = {}
        store_data(entry, {"type": "geojson", "ref_id": "ref:data-fabric-abc"})
        assert entry["type"] == "geojson"
        assert entry["ref_id"] == "ref:data-fabric-abc"


class TestMapspecSchemaRoundtrip:
    def test_vector_source_passes_authoritative_schema(self):
        spec = _minimal_mapspec(
            {"admin_tiles": dict(_VECTOR_SRC)},
            [{"id": "admin-tiles", "type": "line", "source": "admin_tiles"}],
        )
        parsed = parse_mapspec(spec)
        assert parsed.valid
        src = parsed.document["sources"]["admin_tiles"]
        assert src["type"] == "vector"
        assert src["tiles"] == _VECTOR_SRC["tiles"]
        assert src["minzoom"] == 0 and src["maxzoom"] == 5

    def test_fabric_url_source_passes_authoritative_schema(self):
        spec = _minimal_mapspec(
            {"admin_raster": dict(_FABRIC_URL_SRC)},
            [{"id": "admin-raster", "type": "raster", "source": "admin_raster"}],
        )
        parsed = parse_mapspec(spec)
        assert parsed.valid
        src = parsed.document["sources"]["admin_raster"]
        assert src["type"] == "data_fabric"
        assert "{z}/{x}/{y}" in src["url"]

    def test_full_bridging_path_describe_to_schema(self, patched_safe_sessions, ogc_fake_server):
        """E2E（fixture → descriptor → build_mapspec_source → store_data →
        parse_mapspec）：M2 的 MapSpec 桥完整可验证。"""
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_tiles_adapter import OGCTilesAdapter
        from app.services.data_fabric.ogc_tiles_proxy import build_mapspec_source

        url = ogc_fake_server.add_ogc_tiles()
        patched_safe_sessions(ogc_fake_server)
        adapter = OGCTilesAdapter(
            ConnectionProfile(source_type="ogc_api_tiles", endpoint_url=url, name="bridge")
        )
        desc = adapter.describe("admin:WebMercatorQuad")
        src_dict = build_mapspec_source(
            desc.metadata, item_id="admin:WebMercatorQuad", session_id="sess-12345678"
        )
        entry: dict = {}
        store_data(entry, src_dict)
        spec = _minimal_mapspec(
            {"admin_tiles": entry},
            [{"id": "admin-tiles", "type": "line", "source": "admin_tiles"}],
        )
        parsed = parse_mapspec(spec)
        assert parsed.valid
        assert parsed.document["sources"]["admin_tiles"]["type"] == "vector"
        assert parsed.document["sources"]["admin_tiles"]["maxzoom"] == 5
