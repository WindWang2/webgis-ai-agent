"""G3D01：PMTiles→XYZ 服务路由 —— 鉴权/缓存/ETag/typed 负例。

自托管底图路径的真实服务面：注册 pmtiles 数据源 → catalog item →
``/data-fabric/catalog/{id}/pmtiles-tiles/{z}/{x}/{y}``。与 MVT 瓦片
路由（test_deep_review_api_data_tiles）同纪律：鉴权先于缓存（API-01）、
fingerprint 版本化 ETag、空瓦片 204、错误 typed 不吞。
"""
from __future__ import annotations

import struct
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import data_fabric as df_routes
from app.core.database import Base, Engine, SessionLocal
from app.models.data_fabric import CatalogItemModel, DataSourceModel
from app.models.db_model import User
from app.services.data_fabric.adapters import pmtiles_adapter as pmtiles_mod
from app.services.data_fabric.adapters.pmtiles_adapter import (
    PMTilesAdapter,
    _encode_varint,
    zxy_to_tile_id,
)
from app.services.data_fabric.tile_cache import TILE_CACHE as df_tile_cache
from app.services.data_fabric.tile_cache import TileCacheEntry

_ITEM_ID = "cat_g3d01_pmtiles"
_SOURCE_ID = "ds_g3d01_pmtiles"
_POSTGIS_ITEM = "cat_g3d01_postgis"
_POSTGIS_SOURCE = "ds_g3d01_postgis"
_OWNER_ID = "g3d01-pmtiles-owner"

#: 合成 PNG 瓦片（魔数真实 —— 服务层 content-type 嗅探依赖）。
_PNG_TILE = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def _varint(n: int) -> bytes:
    return _encode_varint(n)


def _build_archive(tiles: dict[tuple[int, int, int], bytes]) -> bytes:
    """构造最小合法 PMTiles v3 归档（root 直连 entries、无压缩）。

    复用 spec 级 header 布局（tests/unit/test_file_adapters_v2 的字节语义）
    + adapter `_parse_directory` 的 delta 编码（ids 增量 / raw_offsets+1 绝对）。
    """
    entries = sorted(
        (zxy_to_tile_id(z, x, y), blob) for (z, x, y), blob in tiles.items()
    )
    tile_data = b"".join(blob for _, blob in entries)
    offsets = []
    cur = 0
    for _, blob in entries:
        offsets.append(cur)
        cur += len(blob)

    n = len(entries)
    dir_buf = bytearray()
    dir_buf += _varint(n)
    prev_id = 0
    for i, (tid, _) in enumerate(entries):
        delta = tid if i == 0 else tid - prev_id
        dir_buf += _varint(delta)
        prev_id = tid
    for _ in range(n):
        dir_buf += _varint(1)  # run_length=1
    for _, blob in entries:
        dir_buf += _varint(len(blob))
    for off in offsets:
        dir_buf += _varint(off + 1)  # 非 0 = 绝对偏移（0 保留给连续）

    header = bytearray(127)
    header[0:7] = b"PMTiles"
    header[7] = 3
    struct.pack_into("<Q", header, 8, 127)             # root_dir offset
    struct.pack_into("<Q", header, 16, len(dir_buf))   # root_dir length
    struct.pack_into("<Q", header, 24, 127 + len(dir_buf))  # metadata offset
    struct.pack_into("<Q", header, 32, 0)              # metadata length
    struct.pack_into("<Q", header, 40, 0)              # leaf offset
    struct.pack_into("<Q", header, 48, 0)              # leaf length
    struct.pack_into("<Q", header, 56, 127 + len(dir_buf))  # tile_data offset
    struct.pack_into("<Q", header, 64, len(tile_data))
    struct.pack_into("<Q", header, 72, n)              # num_addressed
    struct.pack_into("<Q", header, 80, n)              # num_entries
    struct.pack_into("<Q", header, 88, n)              # num_contents
    header[96] = 1   # clustered
    header[97] = 1   # internal_compression: none
    header[98] = 1   # tile_compression: none
    header[99] = 2   # tile_type: PNG
    header[100] = 0  # min_zoom
    header[101] = 14  # max_zoom
    struct.pack_into("<2i", header, 102, int(-120.0 * 1e7), int(-20.0 * 1e7))
    struct.pack_into("<2i", header, 110, int(60.0 * 1e7), int(70.0 * 1e7))
    header[118] = 2
    struct.pack_into("<2i", header, 119, int(-10.5 * 1e7), int(40.25 * 1e7))
    return bytes(header) + bytes(dir_buf) + tile_data


def _app() -> FastAPI:
    application = FastAPI()
    application.include_router(df_routes.router, prefix="/api/v1")
    return application


def _client(user_id: str) -> TestClient:
    application = _app()
    application.dependency_overrides[df_routes.get_current_user] = (
        lambda: {"user_id": user_id}
    )
    return TestClient(application)


@pytest.fixture()
def pmtiles_archive(tmp_path):
    path = tmp_path / "basemap.pmtiles"
    path.write_bytes(_build_archive({(0, 0, 0): _PNG_TILE, (1, 0, 0): _PNG_TILE}))
    return path


@pytest.fixture(autouse=True)
def _seed_catalog(pmtiles_archive, tmp_path, monkeypatch):
    monkeypatch.setattr(
        pmtiles_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)]
    )
    monkeypatch.setattr(
        pmtiles_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024
    )
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        if db.get(User, _OWNER_ID) is None:
            db.add(User(
                id=_OWNER_ID,
                username="g3d01-pmtiles-owner",
                email="g3d01-pmtiles-owner@example.com",
                password_hash="scrypt$16384$8$1$00$00",
                role="editor",
                is_active=True,
                token_version=0,
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
        for item_id, source_id, source_type, endpoint in (
            (_ITEM_ID, _SOURCE_ID, "pmtiles", str(pmtiles_archive)),
            (_POSTGIS_ITEM, _POSTGIS_SOURCE, "postgis", "postgresql://example/db"),
        ):
            db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).delete()
            db.query(DataSourceModel).filter(DataSourceModel.id == source_id).delete()
            db.add(DataSourceModel(
                id=source_id, org_id=None, owner_id=_OWNER_ID,
                name=f"src {source_id}", source_type=source_type,
                endpoint_url=endpoint,
                connection_profile={}, capabilities_json=[],
            ))
            db.add(CatalogItemModel(
                id=item_id, source_id=source_id, name=f"layer {item_id}",
                title=item_id, geometry_type="Polygon", feature_type="raster_tile",
                crs="EPSG:3857", bbox_json=None, tags_json=[],
                descriptor_json={}, meta_profile_json={},
                fingerprint="fp-g3d01", availability="available",
            ))
        db.commit()
    df_tile_cache.invalidate_item(f"pmtiles:{_ITEM_ID}")
    yield
    df_tile_cache.invalidate_item(f"pmtiles:{_ITEM_ID}")


def _url(item_id: str, z: int, x: int, y: int) -> str:
    return f"/api/v1/data-fabric/catalog/{item_id}/pmtiles-tiles/{z}/{x}/{y}"


class TestPmtilesTileRoute:
    def test_serves_tile_bytes_with_content_type_and_etag(self):
        client = _client(_OWNER_ID)
        r = client.get(_url(_ITEM_ID, 0, 0, 0))
        assert r.status_code == 200, r.text
        assert r.content == _PNG_TILE
        assert r.headers["content-type"].startswith("image/png")
        assert r.headers["etag"]
        assert r.headers["x-tile-cache"] == "miss"

    def test_second_request_hits_cache_and_304_revalidates(self):
        client = _client(_OWNER_ID)
        r1 = client.get(_url(_ITEM_ID, 0, 0, 0))
        assert r1.status_code == 200
        r2 = client.get(_url(_ITEM_ID, 0, 0, 0))
        assert r2.status_code == 200
        assert r2.headers["x-tile-cache"] == "hit"
        assert r2.headers["etag"] == r1.headers["etag"]
        r304 = client.get(
            _url(_ITEM_ID, 0, 0, 0), headers={"If-None-Match": r1.headers["etag"]}
        )
        assert r304.status_code == 304

    def test_missing_tile_in_archive_is_204(self):
        client = _client(_OWNER_ID)
        r = client.get(_url(_ITEM_ID, 5, 3, 2))  # 归档中不存在
        assert r.status_code == 204

    def test_unknown_item_404(self):
        client = _client(_OWNER_ID)
        r = client.get(_url("cat_does_not_exist", 0, 0, 0))
        assert r.status_code == 404

    def test_cross_tenant_blocked_even_after_cache_hit(self):
        """API-01：鉴权先于缓存 —— 跨租户调用不得借缓存命中拿字节。"""
        owner = _client(_OWNER_ID)
        assert owner.get(_url(_ITEM_ID, 1, 0, 0)).status_code == 200
        stranger = _client("someone-else")
        r = stranger.get(_url(_ITEM_ID, 1, 0, 0))
        assert r.status_code == 404

    def test_non_pmtiles_source_is_typed_422(self):
        client = _client(_OWNER_ID)
        r = client.get(_url(_POSTGIS_ITEM, 0, 0, 0))
        assert r.status_code == 422

    def test_invalid_coordinates_400(self):
        client = _client(_OWNER_ID)
        assert client.get(_url(_ITEM_ID, 23, 0, 0)).status_code == 400
        assert client.get(_url(_ITEM_ID, 1, 2, 0)).status_code == 400  # x >= 2^z

    def test_fingerprint_change_switches_cache_key(self):
        client = _client(_OWNER_ID)
        r1 = client.get(_url(_ITEM_ID, 0, 0, 0))
        assert r1.headers["x-tile-cache"] == "miss"
        with SessionLocal() as db:
            row = db.get(CatalogItemModel, _ITEM_ID)
            setattr(row, "fingerprint", "fp-g3d01-v2")
            db.commit()
        r2 = client.get(_url(_ITEM_ID, 0, 0, 0))
        assert r2.status_code == 200
        assert r2.headers["x-tile-cache"] == "miss", "fingerprint 变化后不得复用旧瓦片"
        assert r2.headers["x-dataset-fingerprint"] == "fp-g3d01-v2"[:16]


class TestPmtilesServiceUnits:
    def test_sniff_content_type_magic_bytes(self):
        from app.services.data_fabric.pmtiles_tile_service import sniff_content_type

        assert sniff_content_type(b"\x89PNG\r\n\x1a\n....") == "image/png"
        assert sniff_content_type(b"\xff\xd8\xff\xe0") == "image/jpeg"
        assert sniff_content_type(b"RIFF\x00\x00\x00\x00WEBPVP8") == "image/webp"
        assert sniff_content_type(b"\x1f\x8b\x08\x00") == "application/vnd.mapbox-vector-tile"
        assert sniff_content_type(b"\x00\x01\x02") == "application/octet-stream"

    def test_content_type_survives_cache_hit_via_side_record_or_sniff(self):
        """旁路记录被挤掉后，魔数嗅探兜底（PNG 仍正确标型）。"""
        import asyncio

        from app.services.data_fabric.pmtiles_tile_service import PmtilesTileService
        from app.services.data_fabric.tile_cache import DfTileCache

        class _FakeAdapter:
            def read_tile_bytes(self, z, x, y):
                return (_PNG_TILE, "image/png")

        svc = PmtilesTileService(cache=DfTileCache())
        svc._resolve_governed_adapter = lambda ds: _FakeAdapter()  # noqa: SLF001

        class _Item:
            id = "it"
            fingerprint = "fp"

        class _Ds:
            org_id = None
            owner_id = "o"

        r1 = asyncio.run(svc.serve_pmtiles_tile(_Item(), _Ds(), 0, 0, 0))
        assert r1.content_type == "image/png" and r1.cache_state == "miss"
        svc._content_types.clear()  # noqa: SLF001 —— 模拟旁路记录驱逐漂移
        r2 = asyncio.run(svc.serve_pmtiles_tile(_Item(), _Ds(), 0, 0, 0))
        assert r2.cache_state == "hit"
        assert r2.content_type == "image/png", "嗅探兜底必须保持正确 content-type"

    def test_manager_invalidation_covers_pmtiles_prefix(self):
        from app.services.data_fabric.manager import _invalidate_catalog_tiles

        df_tile_cache.put(
            ("pmtiles:some-item", "org:x|owner:y", "fp", 0, 0, 0),
            TileCacheEntry(gz=b"b", fingerprint="fp"),
        )
        df_tile_cache.put(
            ("some-item", "org:x|owner:y", "fp", 0, 0, 0),
            TileCacheEntry(gz=b"m", fingerprint="fp"),
        )
        _invalidate_catalog_tiles("some-item")
        s = df_tile_cache.stats()
        # 两种前缀都该被清掉（同 item 变更 = 全部表示缓存失效）
        assert s["entries"] == 0

    def test_adapter_reads_archive_fixture_directly(self, pmtiles_archive):
        from app.services.data_fabric.registry import ConnectionProfile

        adapter = PMTilesAdapter(ConnectionProfile(
            source_type="pmtiles", endpoint_url=str(pmtiles_archive)
        ))
        assert adapter.read_tile_bytes(0, 0, 0) == (_PNG_TILE, "image/png")
        assert adapter.read_tile_bytes(9, 9, 9) is None
