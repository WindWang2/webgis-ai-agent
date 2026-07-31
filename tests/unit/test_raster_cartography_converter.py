"""Tests for the Raster → Cartography Converter (ADR-0011).

Mirrors the vector test pattern in test_analysis_cartography_converter.py:
pure-function tests over in-memory arrays. The converter renders a numpy
array to a colormap-baked PNG and builds a `type:"raster"` MapSpec layer +
continuous legend_spec. Best-effort; never raises.
"""
import numpy as np
import pytest

from app.services.raster_cartography_converter import (
    DEFAULT_RASTER_PALETTE,
    build_raster_layer,
    is_raster_source,
    render_array_to_png,
)


# ─── is_raster_source: discriminates raster payloads from other source_data ─


def test_is_raster_source_true_for_array_plus_bounds():
  """A payload carrying a numeric array AND bounds is a raster source."""
  payload = {
      "array": np.array([[0.1, 0.5], [0.9, 0.2]]),
      "bounds": [100.0, 20.0, 101.0, 21.0],
  }
  assert is_raster_source(payload) is True


def test_is_raster_source_true_for_ndvi_payload():
  """NDVI payloads may key the array as 'ndvi_array' or nest under
  'raster_source' (what rs_service will return)."""
  assert is_raster_source({
      "raster_source": {"array": np.zeros((2, 2)), "bounds": [0, 0, 1, 1]}
  }) is True


def test_is_raster_source_false_for_analysis_result():
  """An analysis-result dict (legend_spec/algorithm/data) is NOT raster —
  it routes through the vector converter. is_raster_source must not steal it."""
  assert is_raster_source({
      "success": True, "algorithm": "hotspot",
      "data": {"type": "FeatureCollection", "features": []},
  }) is False


def test_is_raster_source_false_for_geojson():
  geojson = {"type": "FeatureCollection", "features": []}
  assert is_raster_source(geojson) is False


def test_is_raster_source_false_for_string_or_none():
  assert is_raster_source("ref:abc") is False
  assert is_raster_source(None) is False


def test_is_raster_source_requires_bounds():
  """An array without bounds is not enough — we cannot georeference the image."""
  assert is_raster_source({"array": np.zeros((2, 2))}) is False


# ─── render_array_to_png: array → colormap-baked PNG bytes ─────────────────


def test_render_array_to_png_returns_png_bytes():
  """Output is valid PNG bytes (PIL can re-open it)."""
  arr = np.array([[0.0, 0.5], [1.0, 0.25]])
  png = render_array_to_png(arr, palette="Viridis")
  assert isinstance(png, (bytes, bytearray))
  assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG (bad magic bytes)"


def test_render_array_to_png_dimensions_match_array():
  """The PNG's pixel dimensions equal the array shape (1 pixel per cell)."""
  arr = np.array([[0.0, 0.5, 1.0], [0.25, 0.75, 0.1]])  # 2×3
  png = render_array_to_png(arr, palette="Viridis")
  from PIL import Image
  import io
  img = Image.open(io.BytesIO(png))
  assert img.size == (3, 2), f"expected (3,2), got {img.size}"


def test_render_array_to_png_normalizes_min_max():
  """Min → palette[0], max → palette[-1]. Two arrays with the same relative
  spread but different absolute values produce the same image."""
  from PIL import Image
  import io
  a1 = np.array([[0.0, 10.0]])
  a2 = np.array([[100.0, 110.0]])  # same spread, shifted
  p1 = Image.open(io.BytesIO(render_array_to_png(a1, "Viridis"))).getpixel((0, 0))
  p2 = Image.open(io.BytesIO(render_array_to_png(a2, "Viridis"))).getpixel((0, 0))
  assert p1 == p2, "min normalization should be shift-invariant"


def test_render_array_to_png_unknown_palette_falls_back():
  """An unknown palette name falls back to DEFAULT_RASTER_PALETTE, not raise."""
  arr = np.array([[0.0, 1.0]])
  png = render_array_to_png(arr, palette="NoSuchPalette")
  assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_array_to_png_constant_array_does_not_divide_by_zero():
  """A constant array (max==min) must not raise; all cells get palette[0]."""
  arr = np.array([[0.5, 0.5], [0.5, 0.5]])
  png = render_array_to_png(arr, palette="Viridis")
  assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ─── build_raster_layer: the main entry (array → layer + legend + PNG) ──────


def test_build_raster_layer_returns_layer_legend_png():
  arr = np.array([[0.1, 0.5], [0.9, 0.2]])
  layer, legend, png = build_raster_layer(
      source_id="ndvi_src",
      bounds=[100.0, 20.0, 101.0, 21.0],
      array=arr,
      provenance={"algorithm": "compute_ndvi", "computed_at": "2026-07-31T00:00:00Z"},
  )
  # Layer is type:"raster" + source ref.
  assert layer["type"] == "raster"
  assert layer["source"] == "ndvi_src"
  assert "id" in layer and "paint" in layer and "provenance" in layer
  # Legend is continuous (min/max + palette) for the live-map overlay path.
  assert legend is not None
  assert legend["type"] == "continuous"
  assert "min" in legend and "max" in legend and "palette" in legend
  # PNG is renderable.
  assert isinstance(png, (bytes, bytearray))
  assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_build_raster_layer_legend_min_max_match_array():
  """The legend's min/max are the array's actual min/max (the data range the
  colormap encodes)."""
  arr = np.array([[0.1, 0.5], [0.9, 0.2]])
  _, legend, _ = build_raster_layer(
      source_id="src", bounds=[0, 0, 1, 1], array=arr, provenance={}
  )
  assert legend["min"] == pytest.approx(0.1)
  assert legend["max"] == pytest.approx(0.9)


def test_build_raster_layer_default_palette_used_when_unspecified():
  arr = np.array([[0.0, 1.0]])
  layer, legend, _ = build_raster_layer(
      source_id="src", bounds=[0, 0, 1, 1], array=arr, provenance={}
  )
  assert legend["palette"] == DEFAULT_RASTER_PALETTE


def test_build_raster_layer_never_raises_on_bad_input():
  """Best-effort: a non-array input must not raise; returns a degenerate
  layer + None legend + empty PNG (mirrors vector converter's guarantee)."""
  layer, legend, png = build_raster_layer(
      source_id="src", bounds=[0, 0, 1, 1], array=None, provenance={}
  )
  assert layer is not None and "id" in layer
  assert legend is None or isinstance(legend, dict)
  assert png is None or isinstance(png, (bytes, bytearray))
