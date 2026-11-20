"""WP12：真实 HTTP route 旅程（请求参数 → 返回像素 → 恢复后的 spec → 导出）。"""
from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from rasterio.transform import from_origin

from app.api.routes import layer as layer_routes
from app.api.routes import raster_authoring as routes
from app.core.auth import get_current_user_optional_with_version
from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.mvt import tile_lru_cache
from app.services.raster_authoring.service import _read_mapspec, current_revision
from app.services.raster_tile_service import _RASTER_TILE_CACHE
from tests.unit.raster_authoring.helpers import register_raster_ref, write_tif


@pytest.fixture
def export_dir(tmp_path, monkeypatch):
    import app.services.export_paths as ep

    root = tmp_path / "exports"
    root.mkdir()
    monkeypatch.setattr(ep, "exports_root", lambda: root, raising=False)
    monkeypatch.setattr(ep, "ensure_exports_root", lambda: root, raising=False)
    return root


@pytest.fixture(autouse=True)
def _clear_tile_caches():
    tile_lru_cache._cache.clear()
    _RASTER_TILE_CACHE.clear()
    yield
    tile_lru_cache._cache.clear()
    _RASTER_TILE_CACHE.clear()


@pytest.fixture
def app(monkeypatch, session_id, owner_root, export_dir):
    async def _noop_budget(_sid):
        return None

    monkeypatch.setattr(layer_routes, "_layer_data_budget", _noop_budget)
    application = FastAPI()
    application.dependency_overrides[require_owned_session] = lambda: Conversation(id=session_id)
    application.dependency_overrides[get_current_user_optional_with_version] = (
        lambda: {"user_id": "g20-test-user"}
    )
    application.include_router(routes.router, prefix="/api/v1")
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _world(owner_root):
    import numpy as np

    rows, cols = np.mgrid[0:32, 0:64]
    data = np.stack([(b + 1) * 100 + rows * 2 + cols for b in range(4)]).astype("float32")
    return write_tif(
        owner_root / "world.tif", bands=4, width=64, height=32, values=data,
        transform=from_origin(-180.0, 90.0, 360.0 / 64, 180.0 / 32),
    )


@pytest.mark.asyncio
async def test_descriptor_probe_preview_and_explicit_error_codes(client, owner_root, session_id):
    ref = await register_raster_ref(session_id, write_tif(owner_root / "four.tif", bands=4))
    q = f"?session_id={session_id}"
    res = await client.get(f"/api/v1/raster-authoring/descriptor/{ref}{q}")
    assert res.status_code == 200, res.text
    assert res.json()["band_count"] == 4

    bad = await client.post(
        f"/api/v1/raster-authoring/expression/preview{q}",
        json={"ref_id": ref, "expression": {
            "bands": [1, 2], "stretch": {"min": 0, "max": 100},
        }},
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "BAND_COUNT_UNSUPPORTED"
    assert bad.json()["detail"]["field"] == "bands"

    preview = await client.post(
        f"/api/v1/raster-authoring/expression/preview{q}",
        json={"ref_id": ref, "expression": {
            "bands": [4, 3, 2], "stretch": {"min": 0, "max": 500},
        }},
    )
    assert preview.status_code == 200
    assert preview.json()["committed"] is False
    assert preview.json()["state"] == "preview"
    assert (await _read_mapspec(session_id)).get("layers") is None  # 预览零写入
    assert preview.json()["legend"]["type"] == "raster_rgb"

    probe = await client.post(
        f"/api/v1/raster-authoring/probe{q}",
        json={"ref_id": ref, "points": [[10.005, 19.995]], "crs": "EPSG:4326",
              "bands": [1, 4]},
    )
    assert probe.status_code == 200
    assert [v["raw"] for v in probe.json()["points"][0]["values"]] == [100.0, 400.0]


@pytest.mark.asyncio
async def test_commit_changes_real_tile_pixels_and_stale_style_is_rejected(
    client, owner_root, session_id, store_root
):
    ref = await register_raster_ref(session_id, _world(owner_root))
    q = f"?session_id={session_id}"
    rev = await current_revision(session_id)
    first = await client.post(
        f"/api/v1/raster-authoring/expression/commit{q}",
        json={"ref_id": ref, "layer_id": "layer-a", "expected_revision": rev,
              "expression": {"bands": [1], "stretch": {"min": 0, "max": 500},
                             "colormap": "viridis"}},
    )
    assert first.status_code == 200, first.text
    receipt = first.json()
    assert receipt["committed"] is True
    assert "mapspec" in receipt  # 回灌权威文档让 live map 立即 reconcile
    style_a = receipt["style_id"]
    doc = await _read_mapspec(session_id)
    assert doc["layers"][0]["raster_style"]["style_id"] == style_a
    assert doc["sources"][receipt["source_id"]]["type"] == "numeric_raster"

    def tile(style_id):
        return (
            f"/api/v1/raster-authoring/tiles/{ref}/0/0/0.png{q}"
            f"&layer_id=layer-a&style_id={style_id}"
        )

    png_a = await client.get(tile(style_a))
    assert png_a.status_code == 200, png_a.text
    assert png_a.content.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(png_a.content)).size == (256, 256)

    stale = await client.get(tile("0" * 24))
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_STYLE"

    second = await client.post(
        f"/api/v1/raster-authoring/expression/commit{q}",
        json={"ref_id": ref, "layer_id": "layer-a",
              "expected_revision": receipt["mapspec_revision"],
              "expression": {"bands": [4, 3, 2], "stretch": {"min": 0, "max": 500}}},
    )
    assert second.status_code == 200, second.text
    style_b = second.json()["style_id"]
    assert style_b != style_a
    png_b = await client.get(tile(style_b))
    assert png_b.status_code == 200
    assert png_b.content != png_a.content, "换波段/表达后真实瓦片像素必须变化"
    old = await client.get(tile(style_a))
    assert old.status_code == 409, "旧 style_id 不得继续出旧像素"


@pytest.mark.asyncio
async def test_export_writes_real_png_with_frozen_style(
    client, owner_root, session_id, store_root, export_dir
):
    ref = await register_raster_ref(session_id, _world(owner_root))
    q = f"?session_id={session_id}"
    receipt = (await client.post(
        f"/api/v1/raster-authoring/expression/commit{q}",
        json={"ref_id": ref, "layer_id": "layer-a",
              "expected_revision": await current_revision(session_id),
              "expression": {"bands": [1], "stretch": {"min": 0, "max": 500},
                             "colormap": "viridis"}},
    )).json()
    out = await client.post(
        f"/api/v1/raster-authoring/export{q}",
        json={"expected_revision": receipt["mapspec_revision"], "format": "png"},
    )
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["frozen_revision"] == receipt["mapspec_revision"]
    assert body["frozen_style_ids"] == [receipt["style_id"]]
    produced = export_dir / body["filename"]
    assert produced.exists() and produced.stat().st_size > 1000
    image = Image.open(io.BytesIO(produced.read_bytes()))
    assert image.width > 900
    sidecar = export_dir / f"{body['filename']}.sidecar.json"
    assert sidecar.exists()
    assert '"style_id"' in sidecar.read_text(encoding="utf-8")

    conflict = await client.post(
        f"/api/v1/raster-authoring/export{q}",
        json={"expected_revision": receipt["mapspec_revision"] + 99, "format": "png"},
    )
    assert conflict.status_code == 409
    pdf = await client.post(
        f"/api/v1/raster-authoring/export{q}",
        json={"expected_revision": receipt["mapspec_revision"], "format": "pdf"},
    )
    assert pdf.status_code == 422
    assert pdf.json()["detail"]["code"] == "EXPORT_UNSUPPORTED"


@pytest.mark.asyncio
async def test_cross_session_ref_guess_is_rejected(client, owner_root, session_id):
    other = "sess-g20-foreign"
    foreign_ref = await register_raster_ref(other, write_tif(owner_root / "foreign.tif"))
    q = f"?session_id={session_id}"
    res = await client.get(
        f"/api/v1/raster-authoring/descriptor/{foreign_ref}{q}"
    )
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "SOURCE_UNAVAILABLE"
    assert "file_path" not in res.text
    probe = await client.post(
        f"/api/v1/raster-authoring/probe{q}",
        json={"ref_id": foreign_ref, "points": [[0, 0]]},
    )
    assert probe.status_code == 404
    tile = await client.get(
        f"/api/v1/raster-authoring/tiles/{foreign_ref}/0/0/0.png{q}"
        f"&layer_id=x&style_id={'0' * 24}"
    )
    assert tile.status_code in (404, 409)
