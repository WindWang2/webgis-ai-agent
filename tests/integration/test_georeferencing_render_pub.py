"""G48 WP10: Independent COG readback, live MapLibre tile rendering, G26 print export & scale benchmark (R32-R34)."""
from __future__ import annotations

import io
import math
import resource
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
import rasterio
from PIL import Image
from pyproj import Transformer

from app.core.config import settings
from app.main import app
from app.models.db_model import Conversation
from app.schemas.georeferencing_schema import GcpInputItem
from app.services.auth_history_bridge import require_owned_session
from app.services.durable_blob_store import reset_filesystem_blob_store
from app.services.georeferencing.intake import ingest_source_bytes
from app.services.georeferencing.refs import purge_session_georef_state
from app.services.georeferencing.service import bind_mapspec_layer, create_plan, execute_plan, revise_draft
from app.services.gis_world_state.mutation import apply_gis_mutation
from app.services.mapspec.intents import UpsertLayerIntent
from app.services.mixed_map_export import export_mixed_map
from app.services.raster_authoring.publication_window import invalidate_publication_window_cache
from app.services.raster_authoring.service import _engine, current_revision
from app.services.mvt import tile_lru_cache

try:
    import pypdf

    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    export_dir = (tmp_path / "exports").resolve()
    export_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir), raising=False)
    import app.services.session_data as sd
    import app.services.project_artifact_promotion as pap
    import app.services.export_paths as ep
    import app.services.mapspec.store as ms_store

    monkeypatch.setattr(sd, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(pap, "_ROOT_CACHE", None, raising=False)
    monkeypatch.setattr(ms_store, "BASE_STORAGE_DIR", (data_dir / ".webgis-agent").resolve(), raising=False)
    monkeypatch.setattr(ep, "exports_root", lambda: export_dir, raising=False)
    monkeypatch.setattr(ep, "ensure_exports_root", lambda: export_dir, raising=False)
    reset_filesystem_blob_store()
    invalidate_publication_window_cache()
    tile_lru_cache._cache.clear()
    sid = "sess-g48-render-pub"
    _engine().store._invalidate_process_cache(sid)
    app.dependency_overrides[require_owned_session] = lambda: Conversation(id=sid)
    yield sid, data_dir
    app.dependency_overrides.pop(require_owned_session, None)
    purge_session_georef_state(sid)
    reset_filesystem_blob_store()
    invalidate_publication_window_cache()
    tile_lru_cache._cache.clear()


def _deg2tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    lat_rad = math.radians(lat)
    n = 1 << zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 1 << z
    origin = 20037508.342789244
    tile_m = (2 * origin) / n
    minx = -origin + x * tile_m
    maxx = minx + tile_m
    maxy = origin - y * tile_m
    miny = maxy - tile_m
    return minx, miny, maxx, maxy


def _rgba_landmark_png(w: int = 128, h: int = 128) -> bytes:
    """Create RGBA image with red region, pure opaque black box, and bright yellow edge bordering transparent pixels."""
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    # Interior bright red background (upper-left & center)
    arr[16:112, 16:80] = [240, 20, 20, 255]
    # Paint semi-transparent green patch inside [16:40, 64:80] to verify interior [1..254] alpha (R21)
    arr[16:40, 64:80] = [40, 220, 60, 128]
    # Pure opaque black interior landmark (must stay 0,0,0,255, NOT become nodata!)
    arr[40:72, 32:64] = [0, 0, 0, 255]
    # Bright yellow strip next to fully transparent right half (bordering A=0)
    arr[24:104, 80:96] = [250, 240, 20, 255]
    # Right side [96:128] remains transparent black (0, 0, 0, 0)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_r32_r33_r34_cog_live_tiles_and_mixed_print_export(isolated_env):
    sid, _ = isolated_env
    src = await ingest_source_bytes(
        session_id=sid, filename="landmark_rgba.png", raw_bytes=_rgba_landmark_png(128, 128)
    )
    plan = create_plan(
        session_id=sid,
        source_id=src.source_id,
        name="Landmark Render Test",
        target_crs="EPSG:3857",
        resampling="bilinear",
        generate_cog=True,
    )

    # Place image strictly inside WebMercator z=16 tile at ~ (120.15E, 30.25N)
    z = 16
    tx, ty = _deg2tile(120.15, 30.25, z)
    tminx, tminy, tmaxx, tmaxy = _tile_bounds_3857(z, tx, ty)
    tile_w_m = tmaxx - tminx
    # Place 128x128 image occupying center 70% of that exact tile
    x0 = tminx + 0.15 * tile_w_m
    y0 = tmaxy - 0.15 * tile_w_m
    scale = (0.70 * tile_w_m) / 128.0

    gcps = [
        GcpInputItem(gcp_id="1", label="TL", px=0.0, py=0.0, map_x=x0, map_y=y0),
        GcpInputItem(gcp_id="2", label="TR", px=128.0, py=0.0, map_x=x0 + 128.0 * scale, map_y=y0),
        GcpInputItem(gcp_id="3", label="BR", px=128.0, py=128.0, map_x=x0 + 128.0 * scale, map_y=y0 - 128.0 * scale),
        GcpInputItem(gcp_id="4", label="BL", px=0.0, py=128.0, map_x=x0, map_y=y0 - 128.0 * scale),
    ]
    plan = revise_draft(session_id=sid, plan_id=plan.plan_id, expected_revision=1, gcps=gcps)
    assert plan.fit_metrics and plan.fit_metrics.status == "valid"

    res = await execute_plan(
        session_id=sid, plan_id=plan.plan_id, expected_revision=plan.revision, background=False
    )
    art = res["artifact"]
    assert art is not None
    assert art.cog_verified is True

    # R32: Independent Rasterio readback (small <512px COG has single block without pyramids)
    with rasterio.open(art.file_path) as ds:
        assert ds.crs.to_string() == "EPSG:3857"
        assert ds.count == 4
        assert ds.dtypes == ("uint8", "uint8", "uint8", "uint8")
        assert abs(ds.bounds.left - x0) < scale * 2.0
        assert abs(ds.bounds.top - y0) < scale * 2.0
        decimated = ds.read(out_shape=(4, max(8, ds.height // 2), max(8, ds.width // 2)))
        assert decimated.shape[0] == 4 and decimated[3].max() == 255

    # Bind to MapSpec & verify live tile rendering (R33)
    rev = await current_revision(sid)
    bound = await bind_mapspec_layer(
        session_id=sid,
        plan_id=plan.plan_id,
        expected_mapspec_revision=rev,
        layer_id="georef-landmark",
        layer_name="Georef Landmark",
        opacity=1.0,
    )
    assert bound.bound is True
    assert bound.artifact is not None
    assert bound.mapspec is not None
    bound_layer = next(
        ly for ly in bound.mapspec.get("layers", []) if ly.get("id") == bound.layer_id
    )
    style_id = bound_layer["raster_style"]["style_id"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        tile_url = (
            f"/api/v1/raster-authoring/tiles/{bound.artifact.raster_ref}/{z}/{tx}/{ty}.png"
            f"?session_id={sid}&layer_id={bound.layer_id}&style_id={style_id}"
        )
        resp = await client.get(tile_url)
        assert resp.status_code == 200, resp.text
        tile_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        assert tile_img.size == (256, 256)
        px = np.array(tile_img)

        # 1) Exterior margin pixels outside warped footprint are fully transparent
        assert px[5, 5, 3] == 0
        # 2) Red interior stays bright red (no unintended stretch)
        red_mask = (px[:, :, 0] > 210) & (px[:, :, 1] < 45) & (px[:, :, 3] == 255)
        assert int(red_mask.sum()) >= 500, "Opaque red region must render with faithful RGB values"
        # 3) Opaque black landmark stays solid black (not turned transparent as nodata!)
        black_mask = (px[:, :, 0] < 15) & (px[:, :, 1] < 15) & (px[:, :, 2] < 15) & (px[:, :, 3] == 255)
        assert int(black_mask.sum()) >= 100, "Opaque pure-black interior must NOT become transparent"
        # 4) Interior semi-transparent [1..254] patch AND edge pixels keep straight RGB + partial alpha
        semi = (px[:, :, 3] >= 90) & (px[:, :, 3] <= 170) & (px[:, :, 1] > 170)
        assert int(semi.sum()) >= 50, "Interior partial-alpha [1..254] must survive XYZ tile render"
        semi_edge = (px[:, :, 3] > 20) & (px[:, :, 3] < 245) & (px[:, :, 0] > 170) & (px[:, :, 1] > 160)
        if np.any(semi_edge):
            assert np.median(px[semi_edge, 0]) > 170 and np.median(px[semi_edge, 1]) > 160

    # R34: G26 Mixed-Map PNG & PDF export with vector landmark overlay (non-degenerate bounds)
    to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    nw_lon, nw_lat = to_4326.transform(x0 + 8.0 * scale, y0 - 8.0 * scale)
    cx_lon, cy_lat = to_4326.transform(x0 + 64.0 * scale, y0 - 64.0 * scale)
    se_lon, se_lat = to_4326.transform(x0 + 120.0 * scale, y0 - 120.0 * scale)
    vec_res = await apply_gis_mutation(
        sid,
        UpsertLayerIntent(
            layer={
                "id": "vec-cp",
                "source": "vec-cp-src",
                "type": "circle",
                "paint": {"circle-radius": 6, "circle-color": "#0066ff"},
                "label": {"field": "name"},
            },
            source_data={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "控制站-NW"},
                        "geometry": {"type": "Point", "coordinates": [nw_lon, nw_lat]},
                    },
                    {
                        "type": "Feature",
                        "properties": {"name": "控制站"},
                        "geometry": {"type": "Point", "coordinates": [cx_lon, cy_lat]},
                    },
                    {
                        "type": "Feature",
                        "properties": {"name": "控制站-SE"},
                        "geometry": {"type": "Point", "coordinates": [se_lon, se_lat]},
                    },
                ],
            },
        ),
        origin="user",
        actor="g48-test",
        expected_revision=bound.mapspec_revision,
        engine=_engine(),
    )
    assert not getattr(vec_res, "superseded", False)

    png_out = await export_mixed_map(sid, fmt="png")
    exp_img = Image.open(io.BytesIO(png_out["bytes"])).convert("RGB")
    exp_arr = np.array(exp_img)
    # Red georef pixels visible inside print canvas
    exp_red = (exp_arr[:, :, 0] > 190) & (exp_arr[:, :, 1] < 70)
    assert int(exp_red.sum()) >= 50, "Print PNG must render georeferenced RGBA layer faithfully"

    # R21: SVG snapshot export embeds RGBA PNG preserving partial interior alpha [1..254]
    import base64
    import re

    svg_out = await export_mixed_map(sid, fmt="svg")
    svg_text = svg_out["bytes"].decode("utf-8")
    b64_matches = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", svg_text)
    assert b64_matches, "SVG export must embed raster snapshot PNG"
    has_interior_semi_alpha = False
    for m in b64_matches:
        snap_img = Image.open(io.BytesIO(base64.b64decode(m))).convert("RGBA")
        sa = np.array(snap_img)
        if int(((sa[:, :, 3] >= 90) & (sa[:, :, 3] <= 170) & (sa[:, :, 1] > 160)).sum()) >= 10:
            has_interior_semi_alpha = True
            break
    assert has_interior_semi_alpha, "SVG embedded snapshot must preserve interior [1..254] straight alpha"

    if HAS_PYPDF:
        pdf_out = await export_mixed_map(sid, fmt="pdf", target_dpi=150)
        assert pdf_out["bytes"][:5] == b"%PDF-"
        reader = pypdf.PdfReader(io.BytesIO(pdf_out["bytes"]))
        assert len(reader.pages) >= 1
        page_text = reader.pages[0].extract_text() or ""
        assert "控制站" in page_text


@pytest.mark.asyncio
async def test_scale_benchmark_large_1500px_cog_warp(isolated_env):
    sid, _ = isolated_env
    w, h = 1500, 1500
    rng = np.random.default_rng(11)
    arr = np.empty((h, w, 4), dtype=np.uint8)
    arr[:, :, :3] = rng.integers(20, 230, (h, w, 3), dtype=np.uint8)
    arr[:, :, 3] = 255
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGBA").save(buf, format="PNG")

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    src = await ingest_source_bytes(session_id=sid, filename="bench_1500.png", raw_bytes=buf.getvalue())
    plan = create_plan(
        session_id=sid,
        source_id=src.source_id,
        name="Bench 1500",
        target_crs="EPSG:3857",
        resampling="bilinear",
        generate_cog=True,
    )
    gcps = [
        GcpInputItem(gcp_id="1", label="1", px=0.0, py=0.0, map_x=13370000.0, map_y=3530000.0),
        GcpInputItem(gcp_id="2", label="2", px=float(w), py=0.0, map_x=13373000.0, map_y=3530000.0),
        GcpInputItem(gcp_id="3", label="3", px=float(w), py=float(h), map_x=13373000.0, map_y=3527000.0),
        GcpInputItem(gcp_id="4", label="4", px=0.0, py=float(h), map_x=13370000.0, map_y=3527000.0),
    ]
    plan = revise_draft(session_id=sid, plan_id=plan.plan_id, expected_revision=1, gcps=gcps)
    res = await execute_plan(
        session_id=sid, plan_id=plan.plan_id, expected_revision=plan.revision, background=False
    )
    elapsed_s = time.perf_counter() - t0
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_delta_mb = max(0.0, (rss_after - rss_before) / 1024.0)

    art = res["artifact"]
    assert art is not None and art.cog_verified is True
    assert Path(art.file_path).is_file()
    with rasterio.open(art.file_path) as ds:
        assert ds.overviews(1), "1500x1500 COG must include internal overview pyramids"
        dec = ds.read(out_shape=(4, max(64, ds.height // 4), max(64, ds.width // 4)))
        assert dec.shape[0] == 4
    print(f"[G48-BENCH] 1500x1500 RGBA warp+COG: elapsed={elapsed_s:.2f}s peak_rss_mb={rss_after/1024.0:.1f} delta_mb={rss_delta_mb:.1f} size_bytes={art.byte_size}")
    assert elapsed_s < 25.0
