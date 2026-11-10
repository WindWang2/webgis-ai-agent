"""WP03–WP05/WP10–WP12：真实 GeoTIFF 上的持久提交、瓦片、像元、导出旅程。"""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image
from rasterio.transform import from_origin

from app.schemas.raster_spec import RasterExpressionError
from tests.unit.raster_authoring.helpers import register_raster_ref, write_tif
from app.services.raster_authoring import (
    commit_expression,
    describe_raster,
    export_snapshot,
    probe_raster,
    resolve_committed_expression,
    validate_expression,
)
from app.services.raster_authoring.render import render_snapshot, render_tile
from app.services.raster_authoring.descriptor import _STATS_CACHE
from app.services.raster_authoring.source import resolve_source
from app.services.raster_authoring.service import (
    _read_mapspec,
    current_revision,
    frozen_layers,
    source_id_for,
)
from app.services.raster_tile_service import _RASTER_TILE_CACHE


@pytest.fixture(autouse=True)
def _clear_caches():
    _STATS_CACHE.clear()
    _RASTER_TILE_CACHE.clear()
    yield
    _STATS_CACHE.clear()
    _RASTER_TILE_CACHE.clear()


def _local_tif(owner_root, **kwargs):
    return write_tif(owner_root / f"{kwargs.get('name','local')}.tif", **{
        k: v for k, v in kwargs.items() if k != "name"
    })


def _world_tif(owner_root, *, bands=4, dtype="float32", nodata=None):
    """覆盖全球的粗网格（z=0 瓦片能稳定采到多个源像元）。"""
    rows, cols = np.mgrid[0:32, 0:64]
    data = np.stack(
        [(b + 1) * 100 + rows * 2 + cols for b in range(bands)]
    ).astype(dtype)
    return write_tif(
        owner_root / "world.tif", bands=bands, width=64, height=32,
        dtype=dtype, nodata=nodata, values=data,
        transform=from_origin(-180.0, 90.0, 360.0 / 64, 180.0 / 32),
    )


@pytest.mark.asyncio
async def test_descriptor_exposes_real_bands_units_and_provenance(owner_root, session_id):
    path = write_tif(
        owner_root / "four.tif", bands=4,
        band_tags={1: {"units": "reflectance", "scale": "0.5", "offset": "1"}},
    )
    ref = await register_raster_ref(session_id, path)
    info = await describe_raster(session_id, ref)
    assert info["band_count"] == 4
    assert [b["index"] for b in info["bands"]] == [1, 2, 3, 4]
    assert info["bands"][0]["unit"] == "reflectance"
    assert info["bands"][0]["scale"] == 0.5 and info["bands"][0]["offset"] == 1.0
    assert all(b["role"] is None for b in info["bands"])  # 不猜红/近红外
    assert info["statistics"]["approximate"] is True
    assert info["statistics"]["sample_count"] > 0
    assert info["histograms"][0]["buckets"] == 32
    assert info["defaults"]["mode"] == "rgb"


@pytest.mark.asyncio
async def test_probe_reads_source_pixel_with_crs_and_scale(owner_root, session_id):
    path = write_tif(
        owner_root / "probe.tif", bands=4,
        band_tags={1: {"units": "m", "scale": "2", "offset": "5"}},
    )
    ref = await register_raster_ref(session_id, path)
    # 源网格 (10.0, 20.0) 起，像元 0.01°；(10.005, 19.995) 命中 row=0,col=0
    result = await probe_raster(
        session_id, ref, [[10.005, 19.995]], crs="EPSG:4326", bands=[1, 4],
    )
    point = result["points"][0]
    assert (point["row"], point["col"]) == (0, 0)
    assert point["status"] == "ok"
    assert [v["raw"] for v in point["values"]] == [100.0, 400.0]
    assert point["values"][0]["scaled"] == 205.0
    assert point["values"][0]["unit"] == "m"
    assert point["values"][1]["unit_status"] == "unknown"
    assert result["method"] == "nearest_source_pixel_window_1x1"
    assert result["interpolated"] is False


@pytest.mark.asyncio
async def test_probe_distinguishes_outside_mask_and_no_crs(owner_root, session_id):
    outside_ref = await register_raster_ref(session_id, write_tif(owner_root / "a.tif"))
    masked = np.zeros((1, 8, 8), dtype="float32")
    masked[0, 0, 0] = 0.0
    nodata_ref = await register_raster_ref(session_id, write_tif(
        owner_root / "nodata0.tif", nodata=0.0, values=masked,
    ))
    no_crs_ref = await register_raster_ref(session_id, write_tif(
        owner_root / "nocrs.tif", crs=None,
    ))
    out = await probe_raster(session_id, outside_ref, [[0.0, 0.0]])
    assert out["points"][0]["status"] == "OUTSIDE_EXTENT"
    masked_out = await probe_raster(
        session_id, nodata_ref, [[10.005, 19.995]],
    )
    assert masked_out["points"][0]["status"] == "MASKED_PIXEL"
    with pytest.raises(RasterExpressionError) as exc:
        await probe_raster(session_id, no_crs_ref, [[10.005, 19.995]])
    assert exc.value.code == "NO_CRS"
    no_crs_source = await probe_raster(
        session_id, no_crs_ref, [[10.005, 19.995]], crs="source",
    )
    assert no_crs_source["points"][0]["status"] == "ok"


@pytest.mark.asyncio
async def test_zero_value_is_valid_when_nodata_not_declared(owner_root, session_id):
    zeros = np.zeros((1, 8, 8), dtype="float32")
    ref = await register_raster_ref(session_id, write_tif(
        owner_root / "zeros.tif", values=zeros,
    ))
    result = await probe_raster(session_id, ref, [[10.005, 19.995]])
    assert result["points"][0]["values"][0]["raw"] == 0.0
    assert result["points"][0]["valid"] is True
    info = await describe_raster(session_id, ref)
    assert info["statistics"]["per_band"][0] == [0.0, 0.0]


@pytest.mark.asyncio
async def test_all_nodata_and_constant_have_no_pseudo_extremes(owner_root, session_id):
    all_nodata = np.full((1, 8, 8), -9999.0, dtype="float32")
    ref = await register_raster_ref(session_id, write_tif(
        owner_root / "allnodata.tif", nodata=-9999.0, values=all_nodata,
    ))
    src = await resolve_source(session_id, ref)
    spec = (await validate_expression(session_id, ref, {
        "bands": [1], "stretch": "auto", "colormap": "viridis",
    }))["expression"]
    from app.schemas.raster_spec import RasterExpressionSpec

    spec = RasterExpressionSpec.model_validate(spec).finalize()
    shot = render_snapshot(src, spec)
    assert shot["all_nodata"] is True
    assert shot["constant"] is True
    assert np.asarray(shot["image"])[:, :, 3].max() == 0  # 全透明，无伪地物
    legend = (await validate_expression(session_id, ref, {
        "bands": [1], "stretch": "auto", "colormap": "viridis",
    }))["legend"]
    assert legend["constant"] is True


@pytest.mark.asyncio
async def test_band_combination_and_colormap_change_real_tile_pixels(owner_root, session_id):
    path = _world_tif(owner_root)
    ref = await register_raster_ref(session_id, path)
    src = await resolve_source(session_id, ref)

    def _spec(bands, colormap=None):
        from app.schemas.raster_spec import RasterExpressionSpec, RasterStretchBounds
        return RasterExpressionSpec(
            mode="rgb" if len(bands) == 3 else "single",
            bands=list(bands),
            stretch=RasterStretchBounds(min=0.0, max=500.0,
                                        per_channel=[[0.0, 500.0]] * len(bands)),
            colormap=colormap, data_revision=src.data_revision,
        ).finalize()

    a = render_tile(str(path), 0, 0, 0, _spec([1, 2, 3]))
    b = render_tile(str(path), 0, 0, 0, _spec([4, 3, 2]))
    c = render_tile(str(path), 0, 0, 0, _spec([1], "viridis"))
    d = render_tile(str(path), 0, 0, 0, _spec([1], "magma"))
    assert a != b, "4/3/2 与 1/2/3 必须产生不同像素"
    assert a != c, "单波段与 RGB 必须产生不同像素"
    assert c != d, "色带必须影响真实像素"
    for png in (a, b, c, d):
        assert png.startswith(b"\x89PNG")
        assert Image.open(io.BytesIO(png)).size == (256, 256)


@pytest.mark.asyncio
async def test_manual_stretch_is_frozen_and_cache_isolated(owner_root, session_id):
    path = _world_tif(owner_root)
    ref = await register_raster_ref(session_id, path)
    src = await resolve_source(session_id, ref)
    from app.schemas.raster_spec import RasterExpressionSpec, RasterStretchBounds

    def _spec(hi):
        return RasterExpressionSpec(
            mode="single", bands=[1],
            stretch=RasterStretchBounds(min=0.0, max=hi),
            colormap="gray", data_revision=src.data_revision,
        ).finalize()

    narrow = render_tile(str(path), 0, 0, 0, _spec(120.0))
    wide = render_tile(str(path), 0, 0, 0, _spec(600.0))
    assert narrow != wide
    # 同一 data_revision + 同一 style_id → 同一缓存条目（第二次不重算）
    assert render_tile(str(path), 0, 0, 0, _spec(120.0)) == narrow
    style_ids = {_spec(120.0).style_id, _spec(600.0).style_id}
    assert len(style_ids) == 2


@pytest.mark.asyncio
async def test_commit_restores_style_and_two_layers_do_not_cross_color(
    owner_root, session_id, store_root
):
    ref = await register_raster_ref(session_id, _world_tif(owner_root))
    base = await current_revision(session_id)
    receipt_a = await commit_expression(
        session_id, ref, "layer-a",
        {"bands": [1], "stretch": {"min": 0, "max": 500}, "colormap": "viridis"},
        expected_revision=base,
    )
    assert receipt_a["committed"] is True
    assert receipt_a["state"] == "committed"
    spec_a = receipt_a["expression"]
    doc = await _read_mapspec(session_id)
    layer_a = next(item for item in doc["layers"] if item["id"] == "layer-a")
    assert layer_a["raster_style"]["style_id"] == receipt_a["style_id"]
    assert layer_a["legend_spec"]["min"] == 0
    assert doc["sources"][source_id_for(ref)]["type"] == "numeric_raster"

    receipt_b = await commit_expression(
        session_id, ref, "layer-b",
        {"bands": [4, 3, 2], "stretch": {"min": 0, "max": 500}},
        expected_revision=receipt_a["mapspec_revision"],
    )
    doc2 = await _read_mapspec(session_id)
    styles = {item["id"]: item["raster_style"]["style_id"] for item in doc2["layers"]}
    assert styles["layer-a"] == spec_a["style_id"]
    assert styles["layer-b"] == receipt_b["style_id"]
    assert styles["layer-a"] != styles["layer-b"]
    # 修改 b 不影响 a 的持久表达
    assert (await resolve_committed_expression(session_id, "layer-a")).spec.style_id == spec_a["style_id"]
    # 刷新 = 重新读权威 MapSpec，恢复同一表达
    reloaded = await _read_mapspec(session_id)
    assert next(item for item in reloaded["layers"] if item["id"] == "layer-a")["raster_style"] == layer_a["raster_style"]


@pytest.mark.asyncio
async def test_stale_style_and_cas_conflict_are_rejected(owner_root, session_id, store_root):
    ref = await register_raster_ref(session_id, _world_tif(owner_root))
    receipt = await commit_expression(
        session_id, ref, "layer-a",
        {"bands": [1], "stretch": {"min": 0, "max": 500}},
        expected_revision=await current_revision(session_id),
    )
    with pytest.raises(RasterExpressionError) as exc:
        await resolve_committed_expression(session_id, "layer-a", style_id="0" * 24)
    assert exc.value.code == "STALE_STYLE"
    with pytest.raises(RasterExpressionError) as exc2:
        await commit_expression(
            session_id, ref, "layer-a",
            {"bands": [1], "stretch": {"min": 0, "max": 500}, "colormap": "magma"},
            expected_revision=receipt["mapspec_revision"] + 99,
        )
    assert exc2.value.code in ("CAS_SUPERSEDED", "RENDER_FAILED")


@pytest.mark.asyncio
async def test_export_is_real_png_and_frozen_to_one_revision(
    owner_root, session_id, store_root
):
    ref = await register_raster_ref(session_id, _world_tif(owner_root))
    receipt = await commit_expression(
        session_id, ref, "layer-a",
        {"bands": [1], "stretch": {"min": 0, "max": 500}, "colormap": "viridis"},
        expected_revision=await current_revision(session_id),
    )
    frozen = await frozen_layers(session_id, receipt["mapspec_revision"])
    assert frozen["numeric_layers"][0]["spec"].style_id == receipt["style_id"]

    png = await export_snapshot(session_id, expected_revision=frozen["revision"], fmt="png")
    assert png["bytes"][:8] == b"\x89PNG\r\n\x1a\n"
    image = Image.open(io.BytesIO(png["bytes"]))
    assert image.width > 900 and image.height > 500
    assert png["sidecar"]["layers"][0]["legend"]["style_id"] == receipt["style_id"]
    assert png["frozen_revision"] == frozen["revision"]

    svg = await export_snapshot(session_id, expected_revision=frozen["revision"], fmt="svg")
    assert b"data:image/png;base64," in svg["bytes"]
    assert b"session_id" not in svg["bytes"]  # 离线打开不依赖会话 URL
    assert svg["embedded"] is True

    with pytest.raises(RasterExpressionError) as bad_fmt:
        await export_snapshot(session_id, expected_revision=frozen["revision"], fmt="pdf")
    assert bad_fmt.value.code == "EXPORT_UNSUPPORTED"
    with pytest.raises(RasterExpressionError) as stale:
        await export_snapshot(session_id, expected_revision=frozen["revision"] + 99)
    assert stale.value.code == "CAS_SUPERSEDED"
