"""Round-2 audit regression locks (board #54 Part A fixes on PR #2165).

Each test reproduces the auditor's verified probe for one finding:

- R2-A1  ``detect_alpha_or_mask`` read ``src.mask_flags`` (removed in rasterio
  1.3+; actual attribute is ``mask_flag_enums``) and swallowed the
  AttributeError, so every internal-mask TIFF rendered as fully opaque.
- R2-A2a Float32+alpha sources smeared NaN through premultiplied bilinear
  weights, poisoning the stretch min/max into an all-black tile.
- R2-A2b Per-tile min/max stretch on uint16/float alpha rasters produced
  visible seams between adjacent tiles (now dataset-global stats).
- R2-A2c ``cmap_name`` on non-uint8 rasters bypassed the alpha adapter and
  dropped the alpha band entirely.
- R2-A2d Declared ``nodata`` pixels stayed opaque when the alpha band was
  fully opaque.
- R2-A3   Promotion GC / quota retention deleted live ``georefbin-*`` and
  G40 symbol-version blobs (no domain protection set).
- R2-A4a  Antimeridian footprint bbox spanned ~357 deg after pyproj wrapped
  longitudes into [-180, 180].
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.enums import ColorInterp  # noqa: E402
from rasterio.transform import from_bounds  # noqa: E402


# ─── helpers ─────────────────────────────────────────────────────────────────


def _write_rgba_tif(
    path: Path,
    arr: np.ndarray,
    *,
    transform=None,
    crs: str = "EPSG:3857",
    nodata=None,
    internal_mask: bool = False,
) -> None:
    count, height, width = arr.shape
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": count,
        "dtype": str(arr.dtype),
        "crs": crs,
        "transform": transform or from_bounds(-1e6, 0, 1e6, 1e6, width, height),
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(arr)
        if internal_mask:
            mask = (arr[-1] > 0).astype(np.uint8) * 255
            ds.write_mask(mask)
        if count >= 4:
            ds.colorinterp = (
                ColorInterp.red,
                ColorInterp.green,
                ColorInterp.blue,
                ColorInterp.alpha,
            )


# ─── R2-A1: internal-mask detection on rasterio >= 1.3 ─────────────────────


def test_r2_a1_detect_internal_mask_on_rasterio_15(tmp_path: Path) -> None:
    from app.services.raster_authoring.alpha_adapter import detect_alpha_or_mask

    # uint16 grayscale + GDAL internal mask, alpha band absent: the G48 COG shape.
    p = tmp_path / "masked_u16.tif"
    data = np.zeros((1, 8, 8), dtype=np.uint16)
    data[0, :4] = 100
    _write_rgba_tif(p, data, internal_mask=True)

    with rasterio.open(p) as src:
        assert not hasattr(src, "mask_flags"), "rasterio 1.5 removed mask_flags"
        has_alpha, alpha_band, has_mask = detect_alpha_or_mask(src)

    assert (has_alpha, alpha_band, has_mask) == (True, None, True)


# ─── R2-A2a: NaN smear on float32 + alpha ───────────────────────────────────


def test_r2_a2a_nan_smear_no_longer_blacks_tile(tmp_path: Path) -> None:
    from rasterio.transform import from_origin

    from app.services.raster_authoring.alpha_adapter import (
        render_alpha_aware_window,
    )

    p = tmp_path / "f32_alpha.tif"
    h = w = 64
    vals = np.tile(np.linspace(5.0, 250.0, w, dtype=np.float32), (h, 1))[None]
    vals[0, : h // 2] = np.nan  # transparent half carries NaN color
    alpha = np.full((1, h, w), 255, dtype=np.uint8)
    alpha[0, : h // 2] = 0
    _write_rgba_tif(p, np.concatenate([vals, alpha]))
    tr = from_origin(0.0, 1000.0, 1000.0 / w, 1000.0 / h)

    with rasterio.open(p) as src:
        rgba, all_nodata = render_alpha_aware_window(
            src,
            color_indexes=(1,),
            alpha_band=2,
            has_internal_mask=False,
            src_crs="EPSG:3857",
            dst_transform=tr,
            dst_crs="EPSG:3857",
            out_h=h,
            out_w=w,
            resampling="bilinear",
        )

    assert not all_nodata
    opaque = rgba[..., 3] == 255
    assert opaque.sum() > 0
    # The valid half must NOT render pure black (the old NaN-poisoned stretch
    # normalized everything to 0).
    rgb_max = rgba[..., :3].max(axis=-1)
    assert int(rgb_max[opaque].max()) > 0


# ─── R2-A2b: adjacent tiles share one dataset-global stretch ────────────────


def _ramp_u16_alpha_tif(path: Path, w: int = 128) -> None:
    vals = np.linspace(10.0, 200.0, w, dtype=np.float32)[None, None, :].repeat(4, 1)
    arr = np.concatenate([vals.astype(np.uint16), np.full((1, 4, w), 255, np.uint8)])
    _write_rgba_tif(path, arr, transform=from_bounds(0.0, 0.0, w * 10.0, 40.0, w, 4))


def _tile(src, transform, x0: int, size: int = 4) -> np.ndarray:
    from app.services.raster_authoring.alpha_adapter import (
        render_alpha_aware_window,
    )

    rgba, _ = render_alpha_aware_window(
        src,
        color_indexes=(1,),
        alpha_band=2,
        has_internal_mask=False,
        src_crs="EPSG:3857",
        dst_transform=transform,
        dst_crs="EPSG:3857",
        out_h=size,
        out_w=size,
        resampling="nearest",
        stretch=((10.0, 200.0),),  # caller-supplied global stretch
        win=rasterio.windows.Window(x0, 0, size, 4),
    )
    return rgba


def test_r2_a2b_global_stats_drive_consistent_stretch(tmp_path: Path) -> None:
    """Adjacent windows of a continuous uint16 ramp must not flip from white to black."""
    p = tmp_path / "ramp.tif"
    _ramp_u16_alpha_tif(p)
    with rasterio.open(p) as src:
        tr = from_bounds(0.0, 0.0, 40.0, 40.0, 4, 4)
        left = _tile(src, tr, 0)
        right = _tile(src, tr, 4)

    left_px = left[2, 3, :3]  # right edge of left window (val ~ 50)
    right_px = right[2, 0, :3]  # left edge of right window (val ~ 100)
    # With one shared stretch both land in the same mid-gray band; the old
    # per-tile min/max rendered one edge fully white and the other fully black.
    assert abs(int(left_px.max()) - int(right_px.max())) <= 40


# ─── R2-A2c: cmap on non-uint8 keeps alpha ──────────────────────────────────


def test_r2_a2c_render_raster_tile_cmap_keeps_alpha(tmp_path: Path) -> None:
    from app.services.raster_tile_service import render_raster_tile

    p = tmp_path / "u16_alpha_cmap.tif"
    _ramp_u16_alpha_tif(p, w=32)
    png = render_raster_tile(str(p), 10, 512, 0, cmap_name="viridis")

    from PIL import Image

    img = np.asarray(Image.open(__import__("io").BytesIO(png)).convert("RGBA"))
    assert img.shape == (256, 256, 4)
    # The ramp covers only part of the tile extent; uncovered pixels must be
    # transparent (previously rendered opaque viridis colors).
    transparent = img[..., 3] == 0
    assert transparent.any(), "alpha band was dropped by the cmap bypass"


# ─── R2-A2d: declared nodata renders transparent ────────────────────────────


def test_r2_a2d_nodata_pixels_become_transparent(tmp_path: Path) -> None:
    from rasterio.transform import from_origin

    from app.services.raster_authoring.alpha_adapter import (
        render_alpha_aware_window,
    )

    p = tmp_path / "nodata.tif"
    h = w = 8
    vals = np.full((1, h, w), 5, dtype=np.uint16)  # 5 == nodata everywhere
    alpha = np.full((1, h, w), 65535, dtype=np.uint16)  # opaque alpha band
    _write_rgba_tif(p, np.concatenate([vals, alpha]), nodata=5)
    tr = from_origin(0.0, 1000.0, 100.0, 100.0)

    with rasterio.open(p) as src:
        rgba, all_nodata = render_alpha_aware_window(
            src,
            color_indexes=(1,),
            alpha_band=2,
            has_internal_mask=False,
            src_crs="EPSG:3857",
            dst_transform=tr,
            dst_crs="EPSG:3857",
            out_h=h,
            out_w=w,
            resampling="nearest",
        )

    assert all_nodata is True or not (rgba[..., 3] > 0).any()


# ─── R2-A3: promotion GC / quota retention protection sets ──────────────────


def test_r2_a3_georef_blob_keys_protected(tmp_path: Path, monkeypatch) -> None:
    from app.core.config import settings as app_settings

    refs_root = Path(app_settings.DATA_DIR) / "georeferencing" / "_refs"
    sha = "ab" * 32
    sidecar = refs_root / sha[:4] / f"{sha}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({"v": 1, "refs": {"sess-a": 1}}))

    from app.services.artifact_lifecycle import (
        _georef_live_blob_keys,
        _promotion_blob_protection,
    )

    keys, truncated = _georef_live_blob_keys()
    assert truncated is False
    assert f"georefbin-{sha}" in keys

    reason = _promotion_blob_protection(
        f"georefbin-{sha}",
        f"georefbin-{sha}",
        0.0,
        now=1e12,
        grace_hours=0.0,
        refcounts_by_location={},
        refcounts_by_sha={},
        artifact_locations=set(),
        pinned_shas=set(),
        georef_blob_keys=keys,
    )
    assert reason == "georeferencing session refcount"
    sidecar.unlink()


def test_r2_a3_symbol_version_blob_keys_protected() -> None:
    from app.services.artifact_lifecycle import _promotion_blob_protection

    reason = _promotion_blob_protection(
        "f" * 64,
        "f" * 64,
        0.0,
        now=1e12,
        grace_hours=0.0,
        refcounts_by_location={},
        refcounts_by_sha={},
        artifact_locations=set(),
        pinned_shas=set(),
        symbol_version_blob_keys={"f" * 64},
    )
    assert reason == "cartographic symbol asset version row"


# ─── R2-A4a: antimeridian footprint bbox ────────────────────────────────────


def test_r2_a4a_antimeridian_bbox_not_global() -> None:
    from app.services.georeferencing.crs import ring_to_wgs84_geojson

    # ~300 km wide box across the antimeridian in UTM zone 60N.
    corners = [(700000.0, 0.0), (1000000.0, 0.0), (1000000.0, 100000.0), (700000.0, 100000.0)]
    _geom, bbox = ring_to_wgs84_geojson(corners, "EPSG:32660")

    span = bbox[2] - bbox[0]
    assert span < 10.0, f"antimeridian ring collapsed into a {span:.1f} deg global bbox: {bbox}"
