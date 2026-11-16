"""G48 WP10: Premultiplied Straight-Alpha Warp, Categorical Nearest, Budget Preflight & R39 Exact-Affine Proof."""
from __future__ import annotations

import io
import math

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.enums import ColorInterp

from app.core.config import settings
from app.schemas.georeferencing_schema import (
    AffineCoefficients,
    GcpPoint,
    GeorefErrorCode,
)
from app.services.durable_blob_store import reset_filesystem_blob_store
from app.services.georeferencing.intake import ingest_source_bytes
from app.services.georeferencing.refs import purge_session_georef_state
from app.services.georeferencing.service import GeorefDomainError
from app.services.georeferencing.solver import solve_affine_gcps
from app.services.georeferencing.warp import compute_output_grid, run_direct_affine_warp


@pytest.fixture
def warp_session(tmp_path, monkeypatch):
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir), raising=False)
    import app.services.session_data as sd
    import app.services.project_artifact_promotion as pap

    monkeypatch.setattr(sd, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(pap, "_ROOT_CACHE", None, raising=False)
    reset_filesystem_blob_store()
    sid = "sess-warp-oracle"
    yield sid
    purge_session_georef_state(sid)
    reset_filesystem_blob_store()


def _coeffs_from_params(a1: float, a2: float, a0: float, b1: float, b2: float, b0: float) -> AffineCoefficients:
    sx = math.hypot(a1, b1)
    sy = math.hypot(a2, b2)
    det = a1 * b2 - a2 * b1
    return AffineCoefficients(
        a0=a0,
        a1=a1,
        a2=a2,
        b0=b0,
        b1=b1,
        b2=b2,
        px_scale_x=sx,
        px_scale_y=sy,
        rotation_deg=math.degrees(math.atan2(b1, a1)),
        shear_deg=0.0,
        determinant=det,
        is_reflection=det > 0.0,
    )


@pytest.mark.asyncio
async def test_r19_categorical_nearest_preserves_exact_labels(warp_session, tmp_path):
    W, H = 64, 64
    arr = np.ones((1, H, W), dtype=np.uint8)
    arr[0, :32, 32:] = 2
    arr[0, 32:, :] = 7
    buf = io.BytesIO()
    with rasterio.open(buf, "w", driver="GTiff", width=W, height=H, count=1, dtype="uint8") as ds:
        ds.write(arr)
    src = await ingest_source_bytes(session_id=warp_session, filename="cat.tif", raw_bytes=buf.getvalue())

    coeffs = _coeffs_from_params(1.8, 0.7, 500000.0, 0.7, -1.8, 4000000.0)
    out = tmp_path / "cat_warped.tif"
    res = run_direct_affine_warp(
        source=src,
        coeffs=coeffs,
        target_crs="EPSG:32650",
        resampling="nearest",
        target_resolution_m=None,
        generate_cog=True,
        out_path=out,
    )
    assert res["cog_verified"] is True
    with rasterio.open(out) as ds:
        b1 = ds.read(1)
        alpha = ds.read(2) if ds.count == 2 else ds.read_masks(1)
        inside_vals = set(np.unique(b1[alpha == 255]).tolist())
        assert inside_vals.issubset({1, 2, 7})
        assert inside_vals == {1, 2, 7}


@pytest.mark.asyncio
async def test_r20_straight_alpha_premultiplied_no_black_halo_and_opaque_black_preserved(warp_session, tmp_path):
    W, H = 64, 64
    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    rgba[16:48, 16:48] = [255, 0, 0, 255]
    rgba[28:36, 28:36] = [0, 0, 0, 255]
    rgba[10:15, 20:44] = [20, 220, 40, 128]

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    src = await ingest_source_bytes(session_id=warp_session, filename="halo.png", raw_bytes=buf.getvalue())

    c = 2.0 * math.cos(0.26)
    s = 2.0 * math.sin(0.26)
    coeffs = _coeffs_from_params(c, s, 500000.0, s, -c, 4000000.0)
    out = tmp_path / "halo_warped.tif"
    run_direct_affine_warp(
        source=src,
        coeffs=coeffs,
        target_crs="EPSG:32650",
        resampling="bilinear",
        target_resolution_m=None,
        generate_cog=True,
        out_path=out,
    )
    with rasterio.open(out) as ds:
        assert ds.count == 4
        assert ds.colorinterp[3] == ColorInterp.alpha
        bands = ds.read()
        r, g, b, a = bands[0], bands[1], bands[2], bands[3]

        assert (r[a == 0] == 0).all()
        semi_red = (a >= 20) & (a <= 230) & (g < 20) & (b < 20)
        assert semi_red.any()
        assert (r[semi_red] >= 235).all(), f"Dark halo detected! min R={r[semi_red].min()}"

        opaque_black = (a == 255) & (r < 5) & (g < 5) & (b < 5)
        assert opaque_black.any(), "Opaque black pixels must NOT become nodata!"

        semi_green = (a >= 90) & (a <= 165) & (g > 150)
        assert semi_green.any()


@pytest.mark.asyncio
async def test_r20b_uint16_tiff_internal_mask_and_dtype_preserved(warp_session, tmp_path):
    W, H = 48, 48
    u16 = (np.arange(W * H, dtype=np.uint16).reshape(1, H, W)) + 100
    buf = io.BytesIO()
    with rasterio.open(buf, "w", driver="GTiff", width=W, height=H, count=1, dtype="uint16") as ds:
        ds.write(u16)
    src = await ingest_source_bytes(session_id=warp_session, filename="dem16.tif", raw_bytes=buf.getvalue())

    coeffs = _coeffs_from_params(1.8, 0.4, 500000.0, 0.4, -1.8, 4000000.0)
    out = tmp_path / "dem16_warped.tif"
    res = run_direct_affine_warp(
        source=src,
        coeffs=coeffs,
        target_crs="EPSG:32650",
        resampling="bilinear",
        target_resolution_m=None,
        generate_cog=True,
        out_path=out,
    )
    assert res["dtype"] == "uint16"
    with rasterio.open(out) as ds:
        assert ds.dtypes == ("uint16",)
        mask = ds.read_masks(1)
        assert (mask == 0).any() and (mask == 255).any()


@pytest.mark.asyncio
async def test_r21_budget_preflight_guard_blocks_before_allocation(warp_session):
    buf = io.BytesIO()
    Image.new("RGBA", (1000, 1000), (10, 20, 30, 255)).save(buf, format="PNG")
    src = await ingest_source_bytes(session_id=warp_session, filename="budget.png", raw_bytes=buf.getvalue())
    coeffs = _coeffs_from_params(1000.0, 0.0, 500000.0, 0.0, -1000.0, 4000000.0)
    with pytest.raises(GeorefDomainError) as exc_info:
        compute_output_grid(src, coeffs, "EPSG:32650", target_resolution_m=0.05)
    assert exc_info.value.code == GeorefErrorCode.OUTPUT_BUDGET_EXCEEDED


@pytest.mark.asyncio
async def test_r20c_la_two_band_non_square_warp(warp_session, tmp_path):
    """Regression: 2-band LA PNG (height != width) warps cleanly with preserved luminance and alpha."""
    W, H = 40, 24
    la = np.zeros((H, W, 2), dtype=np.uint8)
    la[4:20, 6:34, 0] = 210
    la[4:20, 6:34, 1] = 160
    la[8:16, 14:26, 0] = 45
    la[8:16, 14:26, 1] = 255
    buf = io.BytesIO()
    Image.fromarray(la, mode="LA").save(buf, format="PNG")
    src = await ingest_source_bytes(session_id=warp_session, filename="la_nonsq.png", raw_bytes=buf.getvalue())
    assert src.bands == 2 and src.has_alpha is True

    coeffs = _coeffs_from_params(2.0 * math.cos(0.2), 2.0 * math.sin(0.2), 500000.0, 2.0 * math.sin(0.2), -2.0 * math.cos(0.2), 4000000.0)
    out = tmp_path / "la_warped.tif"
    res = run_direct_affine_warp(
        source=src,
        coeffs=coeffs,
        target_crs="EPSG:32650",
        resampling="bilinear",
        target_resolution_m=None,
        generate_cog=False,
        out_path=out,
    )
    assert res["bands"] == 2
    with rasterio.open(out) as ds:
        assert ds.count == 2
        gray = ds.read(1)
        alpha = ds.read(2)
        opaque_dark = (alpha == 255) & (gray < 65)
        semi_bright = (alpha >= 130) & (alpha <= 190) & (gray > 180)
        assert opaque_dark.any()
        assert semi_bright.any()


@pytest.mark.asyncio
async def test_r39_many_noisy_gcps_warp_uses_6param_affine_not_poly2_or_tps(warp_session, tmp_path):
    """Proof for R39: even with 9 nonlinearly-perturbed GCPs, textured grid warps via exact 6-param linear Affine without curvature."""
    W, H = 100, 80
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:, :] = [20, 20, 20]
    img[:40, :50] = [200, 30, 30]
    img[40:, 50:] = [30, 200, 30]
    # Paint crisp cyan vertical line along px=50 and magenta horizontal line along py=40
    img[:, 49:52] = [0, 255, 255]
    img[39:42, :] = [255, 0, 255]
    buf = io.BytesIO()
    Image.fromarray(img, mode="RGB").save(buf, format="PNG")
    src = await ingest_source_bytes(session_id=warp_session, filename="noisy_grid.png", raw_bytes=buf.getvalue())

    rng = np.random.default_rng(42)
    gcps = []
    # Add non-linear quadratic bowing + noise that would bend px=50 into a curve under TPS or 2nd-order poly
    for i in range(9):
        px = 10.0 + (i % 3) * 40.0
        py = 10.0 + (i // 3) * 30.0
        mx = 500000.0 + 2.0 * px
        my = 4000000.0 - 2.0 * py
        nonlinear_bow = 6.0 if abs(py - 40.0) < 1.0 else -3.0
        dx, dy = rng.normal(0.0, 0.8, size=2)
        gcps.append(
            GcpPoint(
                gcp_id=f"n{i}",
                label=f"N{i}",
                px=px,
                py=py,
                map_x=float(mx + nonlinear_bow + dx),
                map_y=float(my + dy),
            )
        )
    diag = solve_affine_gcps(gcps, width=W, height=H)
    assert diag.status == "valid" and diag.coefficients is not None
    c = diag.coefficients

    out = tmp_path / "noisy_warped.tif"
    run_direct_affine_warp(
        source=src,
        coeffs=c,
        target_crs="EPSG:32650",
        resampling="nearest",
        target_resolution_m=2.0,
        generate_cog=True,
        out_path=out,
    )
    with rasterio.open(out) as ds:
        assert ds.count == 4
        assert abs(ds.transform.b) < 1e-9 and abs(ds.transform.d) < 1e-9
        # Sample along predicted straight affine image of px=50.5 across multiple py rows
        for py_s in (15.0, 28.0, 52.0, 65.0):
            mx = c.a0 + c.a1 * 50.5 + c.a2 * py_s
            my = c.b0 + c.b1 * 50.5 + c.b2 * py_s
            row, col = ds.index(mx, my)
            assert 0 <= row < ds.height and 0 <= col < ds.width
            win = ds.read(window=rasterio.windows.Window(max(0, col - 1), max(0, row - 1), min(3, ds.width - max(0, col - 1)), min(3, ds.height - max(0, row - 1))))
            # Cyan centerline [0, 255, 255] must stay cleanly on the linear affine trajectory
            cyan = (win[0] < 60) & (win[1] > 200) & (win[2] > 200) & (win[3] == 255)
            assert cyan.any(), f"Linear affine centerline missed at py={py_s} -> col={col}, row={row}"
