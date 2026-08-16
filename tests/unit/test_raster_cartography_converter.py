"""Unit tests for Unified Raster Cartography Converter."""
import io

import numpy as np
import pytest
from PIL import Image

from app.services.raster_cartography_converter import (
    build_raster_layer,
    convert_raster_to_mapspec_layer,
    is_raster_source,
    render_array_to_png,
)


def test_is_raster_source_detection():
    payload = {
        "array": np.zeros((10, 10)),
        "bounds": [100.0, 20.0, 105.0, 25.0],
    }
    assert is_raster_source(payload) is True
    assert is_raster_source({"type": "FeatureCollection"}) is False
    assert is_raster_source({"array": np.zeros((2, 2))}) is True


def test_convert_raster_without_session_dir():
    payload = {
        "array": np.array([[0.1, 0.5], [0.8, 0.9]]),
        "bounds": [100.0, 20.0, 105.0, 25.0],
    }
    layer = {"id": "ndvi-layer", "source": "src-ndvi"}

    raster_layer, legend, png, source_data = convert_raster_to_mapspec_layer(payload, layer)

    assert raster_layer["id"] == "ndvi-layer"
    assert raster_layer["type"] == "raster"
    assert "legend_spec" in raster_layer
    assert raster_layer["legend_spec"]["type"] == "continuous"
    assert png is not None
    assert source_data is not None
    assert source_data["imageRef"] is None
    assert source_data["bounds"] == [100.0, 20.0, 105.0, 25.0]


def test_convert_raster_with_session_dir_persists_png(tmp_path):
    payload = {
        "array": np.array([[0.2, 0.4], [0.6, 0.8]]),
        "bounds": [116.0, 39.0, 117.0, 40.0],
    }
    layer = {"id": "dem-layer", "source": "src-dem"}

    raster_layer, legend, png, source_data = convert_raster_to_mapspec_layer(
        payload, layer, session_dir=tmp_path
    )

    assert source_data["imageRef"] == "ref:raster/src-dem"
    saved_png_path = tmp_path / "raster" / "src-dem.png"
    assert saved_png_path.exists()
    assert saved_png_path.stat().st_size > 0


def test_missing_bounds_are_not_fabricated(tmp_path):
    payload = {"array": np.array([[0.2, 0.4], [0.6, 0.8]])}

    _layer, _legend, _png, source_data = convert_raster_to_mapspec_layer(
        payload,
        {"id": "truthful", "source": "truthful-source"},
        session_dir=tmp_path,
    )

    assert source_data is not None
    assert "bounds" not in source_data


# ─── #480: NaN nodata rendering ──────────────────────────────────────────────


def _decode_rgba(png_bytes):
    """Decodes PNG bytes back to an HxWx4 uint8 array."""
    img = Image.open(io.BytesIO(png_bytes))
    assert img.mode == "RGBA"
    return np.asarray(img)


def test_render_array_to_png_nan_nodata_renders_transparent():
    """Issue #480: NaN is this codebase's nodata convention (NDVI denominators
    ≤0, DEM ≤ -9999, flat aspect cells). A NaN-containing array must render as
    a valid PNG with alpha=0 at the NaN cells instead of raising IndexError
    (floor(NaN).astype(int) == INT64_MIN → rgb_stops[lower] out of bounds)."""
    arr = np.array([[0.1, 0.5], [np.nan, 0.9]])

    png = render_array_to_png(arr)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    rgba = _decode_rgba(png)
    assert rgba.shape == (2, 2, 4)
    # NaN cell transparent, valid cells opaque.
    assert rgba[1, 0, 3] == 0
    assert rgba[0, 0, 3] == 255
    assert rgba[0, 1, 3] == 255
    assert rgba[1, 1, 3] == 255


def test_render_array_to_png_nan_colors_follow_palette_order():
    """Valid cells must keep their colormap ordering: the max value cell is
    strictly later on the Viridis ramp than the min value cell."""
    arr = np.array([[0.0, 0.5, np.nan], [0.25, 0.75, 1.0]])
    rgba = _decode_rgba(render_array_to_png(arr))
    assert not np.array_equal(rgba[0, 0, :3], rgba[1, 1, :3])  # min != max color


def test_render_array_to_png_all_nan_is_valid_transparent_png():
    """Adversarial: entirely-nodata raster still yields a mountable PNG."""
    arr = np.full((3, 3), np.nan)
    rgba = _decode_rgba(render_array_to_png(arr))
    assert np.all(rgba[..., 3] == 0)


def test_render_array_to_png_inf_nodata_transparent():
    """Adversarial: Inf (another non-finite value) must not crash indexing."""
    arr = np.array([[0.2, np.inf], [-np.inf, 0.7]])
    rgba = _decode_rgba(render_array_to_png(arr))
    assert rgba[0, 1, 3] == 0
    assert rgba[1, 0, 3] == 0
    assert rgba[0, 0, 3] == 255


def test_render_array_to_png_constant_with_nan():
    """Adversarial: constant valid value + NaN nodata (flat DEM edge)."""
    arr = np.array([[5.0, 5.0], [5.0, np.nan]])
    rgba = _decode_rgba(render_array_to_png(arr))
    assert rgba[1, 1, 3] == 0
    alpha = rgba[..., 3]
    assert alpha.sum() == 3 * 255  # the 3 valid cells are opaque


def test_build_raster_layer_with_nan_mounts_visible_layer():
    """Issue #480 regression: through build_raster_layer a NaN-bearing array
    must produce a visible layer (opacity 0.85) + PNG + legend, not the
    degenerate invisible fallback (opacity 0.0 / png None)."""
    arr = np.array([[0.1, 0.5], [np.nan, 0.9]])

    layer, legend, png = build_raster_layer(
        source_id="src-ndvi", bounds=[116.0, 39.0, 117.0, 40.0], array=arr
    )

    assert layer["paint"]["opacity"] == 0.85
    assert png is not None
    assert legend is not None
    # Legend min/max computed over finite values only.
    assert legend["min"] == 0.1
    assert legend["max"] == 0.9
    rgba = _decode_rgba(png)
    assert rgba[1, 0, 3] == 0  # NaN transparent
    assert "raster_converter_error" not in layer["provenance"].get("warnings", [])
