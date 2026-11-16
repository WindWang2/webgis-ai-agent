"""G48 WP10: Intake & EXIF & Resource Limit Verification Tests (R01-R07)."""
from __future__ import annotations

import io
import struct

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.control import GroundControlPoint
from rasterio.transform import from_origin

from app.core.config import settings
from app.schemas.georeferencing_schema import GeorefErrorCode
from app.services.durable_blob_store import reset_filesystem_blob_store
from app.services.georeferencing.intake import _apply_exif_orientation_2d, ingest_source_bytes
from app.services.georeferencing.refs import purge_session_georef_state
from app.services.georeferencing.service import GeorefDomainError


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir), raising=False)
    import app.services.session_data as sd
    import app.services.project_artifact_promotion as pap

    monkeypatch.setattr(sd, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(pap, "_ROOT_CACHE", None, raising=False)
    reset_filesystem_blob_store()
    sid = "sess-intake-test"
    yield sid
    purge_session_georef_state(sid)
    reset_filesystem_blob_store()


def _png_bytes(w: int, h: int, mode: str = "RGB", color=(20, 120, 220)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_with_exif_orientation(w: int, h: int, orientation: int | bytes) -> bytes:
    img = Image.new("RGB", (w, h), (30, 30, 30))
    for y in range(min(16, h)):
        for x in range(min(16, w)):
            img.putpixel((x, y), (255, 0, 0))
    buf = io.BytesIO()
    if isinstance(orientation, int):
        exif = Image.Exif()
        exif[0x0112] = orientation
        img.save(buf, format="JPEG", exif=exif.tobytes())
        return buf.getvalue()
    img.save(buf, format="JPEG")
    raw = buf.getvalue()
    assert raw[:2] == b"\xff\xd8"
    payload = b"Exif\x00\x00" + orientation
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return raw[:2] + seg + raw[2:]


@pytest.mark.asyncio
async def test_r01_png_jpeg_modes_and_hashes(isolated_env):
    sid = isolated_env
    for name, mode, col in [
        ("rgb.png", "RGB", (255, 10, 20)),
        ("rgba.png", "RGBA", (255, 10, 20, 128)),
        ("la.png", "LA", (180, 100)),
        ("gray.png", "L", 99),
    ]:
        rec = await ingest_source_bytes(
            session_id=sid, filename=name, raw_bytes=_png_bytes(40, 24, mode=mode, color=col)
        )
        assert rec.width == 40
        assert rec.height == 24
        assert rec.dtype == "uint8"
        assert len(rec.original_sha256) == 64
        assert len(rec.normalized_sha256) == 64


@pytest.mark.asyncio
async def test_r02_and_r03_tiff_uint16_and_embedded_geo_warning(isolated_env):
    sid = isolated_env
    u16_buf = io.BytesIO()
    arr = (np.arange(32 * 48, dtype=np.uint16).reshape(1, 32, 48)) * 40
    with rasterio.open(
        u16_buf, "w", driver="GTiff", width=48, height=32, count=1, dtype="uint16"
    ) as ds:
        ds.write(arr)
    rec = await ingest_source_bytes(
        session_id=sid, filename="dem16.tif", raw_bytes=u16_buf.getvalue()
    )
    assert rec.dtype == "uint16"
    assert rec.bands == 1
    assert rec.existing_georef.warnings == []
    with rasterio.open(rec.normalized_path) as ds:
        assert ds.dtypes == ("uint16",)

    geo_buf = io.BytesIO()
    rgb = np.full((3, 20, 30), 120, dtype=np.uint8)
    with rasterio.open(
        geo_buf,
        "w",
        driver="GTiff",
        width=30,
        height=20,
        count=3,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_origin(1000.0, 2000.0, 10.0, 10.0),
    ) as ds:
        ds.write(rgb)
    rec_geo = await ingest_source_bytes(
        session_id=sid, filename="already_geo.tif", raw_bytes=geo_buf.getvalue()
    )
    assert len(rec_geo.existing_georef.warnings) >= 1
    assert rec_geo.existing_georef.has_existing_crs is True
    assert rec_geo.existing_georef.has_existing_transform is True
    assert rec_geo.existing_georef.has_embedded_gcps is False

    gcp_buf = io.BytesIO()
    with rasterio.open(
        gcp_buf,
        "w",
        driver="GTiff",
        width=30,
        height=20,
        count=3,
        dtype="uint8",
    ) as ds:
        ds.write(rgb)
        ds.gcps = ([GroundControlPoint(0, 0, 100.0, 200.0)], "EPSG:4326")
    rec_gcp = await ingest_source_bytes(
        session_id=sid, filename="already_gcp.tif", raw_bytes=gcp_buf.getvalue()
    )
    assert len(rec_gcp.existing_georef.warnings) >= 1
    assert rec_gcp.existing_georef.has_embedded_gcps is True


@pytest.mark.asyncio
async def test_r04_reject_unsupported_and_disguised_formats(isolated_env):
    sid = isolated_env

    mp_buf = io.BytesIO()
    im1 = Image.new("RGB", (16, 16), (10, 20, 30))
    im2 = Image.new("RGB", (16, 16), (40, 50, 60))
    im1.save(mp_buf, format="TIFF", save_all=True, append_images=[im2])

    gif_buf = io.BytesIO()
    im1.save(gif_buf, format="GIF", save_all=True, append_images=[im2], duration=100, loop=0)

    samples = [
        ("bad.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'),
        ("fake.png", b"%PDF-1.7\n1 0 obj\n<<>>\nendobj"),
        ("bad.vrt", b"<VRTDataset rasterXSize='10' rasterYSize='10'></VRTDataset>"),
        ("zip.png", b"PK\x03\x04dummy"),
        ("multipage.tif", mp_buf.getvalue()),
        ("anim.gif", gif_buf.getvalue()),
    ]
    for fname, raw in samples:
        with pytest.raises(GeorefDomainError) as exc_info:
            await ingest_source_bytes(session_id=sid, filename=fname, raw_bytes=raw)
        assert exc_info.value.code == GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT


@pytest.mark.asyncio
async def test_r05_pixel_and_byte_budget_guards(isolated_env, monkeypatch):
    sid = isolated_env
    from app.services.georeferencing import intake

    noisy = Image.fromarray(
        np.random.default_rng(0).integers(0, 256, (64, 64, 3), dtype=np.uint8),
        mode="RGB",
    )
    noisy_buf = io.BytesIO()
    noisy.save(noisy_buf, format="PNG")
    assert len(noisy_buf.getvalue()) > 256

    monkeypatch.setattr(intake, "MAX_UPLOAD_BYTES", 256)
    with pytest.raises(GeorefDomainError) as exc_byte:
        await ingest_source_bytes(
            session_id=sid, filename="big.png", raw_bytes=noisy_buf.getvalue()
        )
    assert exc_byte.value.code == GeorefErrorCode.SOURCE_TOO_LARGE

    monkeypatch.undo()
    monkeypatch.setattr(intake, "MAX_DECODE_PIXELS", 1000)
    with pytest.raises(GeorefDomainError) as exc_px:
        await ingest_source_bytes(
            session_id=sid, filename="toomanypx.png", raw_bytes=_png_bytes(50, 30)
        )
    assert exc_px.value.code == GeorefErrorCode.SOURCE_TOO_LARGE


@pytest.mark.asyncio
async def test_r06_exif_orientation_physical_canonicalization(isolated_env):
    from PIL import ImageOps

    sid = isolated_env
    # Asymmetric multi-quadrant RGB image to verify orientations 3, 6, 8 cleanly
    base = np.full((24, 40, 3), 40, dtype=np.uint8)
    base[:10, :12] = [240, 20, 20]    # top-left red
    base[:10, 28:] = [20, 230, 20]    # top-right green
    base[14:, :12] = [20, 40, 240]    # bottom-left blue
    for ori in (3, 6, 8):
        img = Image.fromarray(base, mode="RGB")
        exif = Image.Exif()
        exif[0x0112] = ori
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        rec = await ingest_source_bytes(
            session_id=sid, filename=f"rot{ori}.jpg", raw_bytes=buf.getvalue()
        )
        assert rec.exif_orientation == ori
        assert rec.exif_overridden is False
        expected = np.array(ImageOps.exif_transpose(Image.open(io.BytesIO(buf.getvalue()))))
        got = np.array(Image.open(rec.normalized_path).convert("RGB"))
        assert got.shape == expected.shape
        assert (rec.height, rec.width) == expected.shape[:2]
        assert np.mean(np.abs(got.astype(np.float32) - expected.astype(np.float32))) < 8.0

    # Single-band uint16 TIFF orientations 2, 4, 5, 7 must transpose/flip pixels faithfully!
    base_u16 = np.zeros((16, 24), dtype=np.uint16)
    base_u16[:8, :12] = 1000
    base_u16[:8, 12:] = 2000
    base_u16[8:, :12] = 3000
    base_u16[8:, 12:] = 4000
    for ori in (2, 4, 5, 7):
        tbuf = io.BytesIO()
        Image.fromarray(base_u16).save(tbuf, format="TIFF", tiffinfo={274: ori})
        rec_t = await ingest_source_bytes(
            session_id=sid, filename=f"u16_ori{ori}.tif", raw_bytes=tbuf.getvalue()
        )
        assert rec_t.dtype == "uint16"
        assert rec_t.exif_orientation == ori
        expected_u16 = _apply_exif_orientation_2d(base_u16, ori)
        with rasterio.open(rec_t.normalized_path) as ds:
            assert ds.dtypes == ("uint16",)
            got_u16 = ds.read(1)
        assert got_u16.shape == expected_u16.shape
        assert (rec_t.height, rec_t.width) == expected_u16.shape
        assert np.array_equal(got_u16, expected_u16)


@pytest.mark.asyncio
async def test_r07_corrupt_exif_fail_closed_and_explicit_override(isolated_env):
    sid = isolated_env
    bad_exif_jpg = _jpeg_with_exif_orientation(32, 24, b"CORRUPT_IFD_BYTES")
    with pytest.raises(GeorefDomainError) as exc_info:
        await ingest_source_bytes(
            session_id=sid, filename="corrupt_exif.jpg", raw_bytes=bad_exif_jpg, ignore_exif=False
        )
    assert exc_info.value.code == GeorefErrorCode.SOURCE_EXIF_FAILED

    rec = await ingest_source_bytes(
        session_id=sid, filename="corrupt_exif.jpg", raw_bytes=bad_exif_jpg, ignore_exif=True
    )
    assert rec.exif_overridden is True
    assert rec.width == 32 and rec.height == 24
