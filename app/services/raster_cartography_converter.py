"""Raster → Cartography Converter (ADR-0011).

Mirrors `analysis_cartography_converter.py` for the raster path: takes a computed
raster array + WGS84 bounds, renders a colormap-baked PNG, and builds a
`type:"raster"` MapSpec layer + a continuous `legend_spec` (for the live-map
overlay path; the colormap itself is baked into the PNG since MapLibre `image`
sources don't data-drive color).

Best-effort by design (never raises unhandled) — same posture as the vector
converter: a malformed raster degrades to a degenerate layer, not a crash.

Architecture (ADR-0011): rs_service keeps the array (previously discarded) →
this converter renders it → mapspec_store persists the PNG + a `type:"raster"`
source entry (via mapspec_source.store_data) → the TS compiler emits a MapLibre
`image` source from imageRef + bounds.
"""
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.lib.cartography.palettes import resolve_palette_colors

logger = logging.getLogger(__name__)

# Default colormap for raster overlays (NDVI/DEM). Viridis is perceptually
# uniform and colorblind-safe — the right default for continuous scalar fields.
DEFAULT_RASTER_PALETTE = "Viridis"

# Keys that mark a payload as a raster source (vs an analysis-result dict or
# GeoJSON). The array may appear at top level or nested under 'raster_source'
# (the shape rs_service will return).
_ARRAY_KEYS = ("array", "ndvi_array", "raster_array", "dem_array")


def _has_numeric_array(payload: Dict[str, Any]) -> bool:
  """True if `payload` carries a numpy/numeric array under any known array key,
  at top level or nested under 'raster_source'."""
  candidates: List[Any] = [payload.get(k) for k in _ARRAY_KEYS]
  nested = payload.get("raster_source")
  if isinstance(nested, dict):
    candidates += [nested.get(k) for k in _ARRAY_KEYS]
  return any(isinstance(c, np.ndarray) for c in candidates)


def _extract_array(payload: Dict[str, Any]) -> Optional[np.ndarray]:
  """Pull the array out of the payload (top-level or raster_source-nested)."""
  for k in _ARRAY_KEYS:
    if isinstance(payload.get(k), np.ndarray):
      return payload[k]
  nested = payload.get("raster_source")
  if isinstance(nested, dict):
    for k in _ARRAY_KEYS:
      if isinstance(nested.get(k), np.ndarray):
        return nested[k]
  return None


def _extract_bounds(payload: Dict[str, Any]) -> Optional[List[float]]:
  """Pull bounds [w,s,e,n] out of the payload (top-level or raster_source-nested)."""
  for src in (payload, payload.get("raster_source", {}) if isinstance(payload.get("raster_source"), dict) else {}):
    b = src.get("bounds")
    if isinstance(b, (list, tuple)) and len(b) == 4:
      return list(b)
  return None


def is_raster_source(source_data: Any) -> bool:
  """True if `source_data` is a raster payload: a dict carrying a numeric array
  AND bounds [w,s,e,n]. Distinguishes from analysis-result dicts (legend_spec/
  algorithm/success+data) and plain GeoJSON, both of which route through the
  vector converter.

  Detection priority (array+bounds is the *decisive* signal — a numeric array
  with georeferencing bounds can only be a raster; it wins over marker keys like
  `algorithm`/`legend_spec`, which raster payloads legitimately also carry):
    1. Not a dict → False
    2. Has a numeric array AND bounds → True  (raster, regardless of markers)
    3. Otherwise → False (analysis-result / GeoJSON / string → vector path)
  """
  if not isinstance(source_data, dict):
    return False
  # array+bounds is decisive — check it first, before any marker-key logic.
  return _has_numeric_array(source_data) and _extract_bounds(source_data) is not None


# ─── array → PNG rendering ─────────────────────────────────────────────────


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
  """'#rrggbb' → (r, g, b) ints."""
  h = hex_color.lstrip("#")
  return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def render_array_to_png(array: np.ndarray, palette: str = DEFAULT_RASTER_PALETTE) -> bytes:
  """Render a 2D numeric array to a colormap-baked PNG (1 pixel per cell).

  Min→palette[0], max→palette[-1], linear interpolation between. A constant
  array (max==min) maps every cell to palette[0] (no division-by-zero).
  Unknown palette → DEFAULT_RASTER_PALETTE. Never raises on valid arrays.
  """
  from PIL import Image

  from app.lib.cartography.palettes import COLOR_PALETTES

  arr = np.asarray(array, dtype=float)
  if arr.ndim != 2 or arr.size == 0:
    # Degenerate: emit a 1×1 transparent-ish pixel so callers get a valid PNG.
    arr = np.zeros((1, 1), dtype=float)

  colors = COLOR_PALETTES.get(palette) or COLOR_PALETTES[DEFAULT_RASTER_PALETTE]
  rgb_stops = np.array([_hex_to_rgb(c) for c in colors], dtype=float)

  # GIS-05: compute min/max over finite values only. NaN is the established
  # nodata convention (compute_raster_stats uses ~np.isnan masking); calling
  # arr.min()/arr.max() raw returns NaN when any nodata is present, which
  # propagates NaN through normalization and renders a flat single-color PNG.
  finite = arr[np.isfinite(arr)]
  if finite.size == 0:
    # Entirely nodata → single-color transparent-ish tile.
    a_min, a_max = 0.0, 0.0
  else:
    a_min, a_max = float(finite.min()), float(finite.max())
  if a_max == a_min:
    # Constant field → first palette color everywhere.
    norm = np.zeros_like(arr)
  else:
    norm = (arr - a_min) / (a_max - a_min)
  # NaN/nodata cells render as transparent (alpha=0) instead of leaking
  # palette[0] garbage from floor(NaN).
  nodata_mask = ~np.isfinite(arr)

  # Map normalized [0,1] → index into rgb_stops, with linear interp between stops.
  n_stops = len(rgb_stops)
  scaled = np.clip(norm * (n_stops - 1), 0, n_stops - 1)
  lower = np.floor(scaled).astype(int)
  upper = np.clip(lower + 1, 0, n_stops - 1)
  frac = (scaled - lower)[..., None]  # broadcast over RGB
  rgb = rgb_stops[lower] * (1 - frac) + rgb_stops[upper] * frac
  rgb = np.clip(rgb, 0, 255).astype(np.uint8)
  # Alpha channel: opaque for valid cells, transparent for nodata.
  alpha = np.where(nodata_mask, 0, 255).astype(np.uint8)
  rgba = np.dstack([rgb, alpha])

  # PIL expects (width, height); array is (rows=height, cols=width).
  img = Image.fromarray(rgba, mode="RGBA")
  buf = io.BytesIO()
  img.save(buf, format="PNG")
  return buf.getvalue()


# ─── main entry point ──────────────────────────────────────────────────────


def build_raster_layer(
    source_id: str,
    bounds: List[float],
    array: Any,
    palette: str = DEFAULT_RASTER_PALETTE,
    provenance: Optional[Dict[str, Any]] = None,
    layer_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[bytes]]:
  """Render `array` to a PNG and build a `type:"raster"` MapSpec layer.

  Returns (layer, legend_spec, png_bytes). Best-effort: on any failure returns a
  degenerate layer + None legend + None PNG (never raises), matching the vector
  converter's guarantee.
  """
  prov = dict(provenance or {})
  prov.setdefault("algorithm", "raster_analysis")
  prov.setdefault("computed_at", datetime.now(timezone.utc).isoformat())

  lid = layer_id or f"{source_id}_raster"

  try:
    if not isinstance(array, np.ndarray):
      raise ValueError("array is not a numpy ndarray")

    png = render_array_to_png(array, palette=palette)

    # Legend min/max must use the SAME finite mask as render_array_to_png
    # (GIS-05): raw np.min/np.max return NaN when any nodata is present, which
    # would make legend_spec.min/max disagree with the correctly-masked PNG.
    resolved_palette = palette if palette in _known_palettes() else DEFAULT_RASTER_PALETTE
    finite = array[np.isfinite(array)]
    if finite.size == 0:
      a_min, a_max = 0.0, 0.0
    else:
      a_min, a_max = float(finite.min()), float(finite.max())
    legend = {
        "type": "continuous",
        "min": round(a_min, 6),
        "max": round(a_max, 6),
        "palette": resolved_palette,
        # Carry the resolved ramp so the legend swatches match the baked PNG
        # pixels exactly (both derive from COLOR_PALETTES via one path).
        "palette_colors": resolve_palette_colors(resolved_palette, fallback=DEFAULT_RASTER_PALETTE),
    }

    layer = {
        "id": lid,
        "source": source_id,
        "type": "raster",
        "paint": {"opacity": 0.85},
        "provenance": prov,
    }
    return layer, legend, png

  except Exception as e:
    logger.exception("Raster cartography converter failed: %s", e)
    prov["warnings"] = [f"raster_converter_error: {e}"]
    layer = {
        "id": lid,
        "source": source_id,
        "type": "raster",
        "paint": {"opacity": 0.0},
        "provenance": prov,
    }
    return layer, None, None


def _known_palettes() -> Tuple[str, ...]:
  """Palette names available in COLOR_PALETTES."""
  try:
    from app.lib.cartography.palettes import COLOR_PALETTES
    return tuple(COLOR_PALETTES.keys())
  except Exception:
    return (DEFAULT_RASTER_PALETTE,)


def convert_raster_to_mapspec_layer(
    payload: Dict[str, Any],
    layer: Optional[Dict[str, Any]] = None,
    session_dir: Optional[Any] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[bytes], Optional[Dict[str, Any]]]:
  """Convert a raster payload into a MapSpec raster layer + source-ready data."""
  base_layer = dict(layer) if isinstance(layer, dict) else {}
  source_id = base_layer.get("source") or "raster_source"
  bounds = _extract_bounds(payload) or [0.0, 0.0, 1.0, 1.0]
  arr = _extract_array(payload)
  palette = (payload.get("raster_source", {}) or {}).get("suggested_palette") or DEFAULT_RASTER_PALETTE
  provenance = {
      k: v for k, v in payload.items()
      if k in ("algorithm", "computed_at", "item_id", "datetime", "source_ref")
  }

  raster_layer, legend, png = build_raster_layer(
      source_id=source_id, bounds=bounds, array=arr, palette=palette,
      provenance=provenance, layer_id=base_layer.get("id"),
  )
  if legend is not None:
    raster_layer.setdefault("legend_spec", legend)

  source_data: Optional[Dict[str, Any]] = None
  if png is not None and isinstance(arr, np.ndarray) and arr.ndim == 2:
    h, w = arr.shape
    image_ref: Optional[str] = None
    if session_dir is not None:
      from pathlib import Path
      from app.services.raster_store import save_png
      s_dir = Path(session_dir) if not isinstance(session_dir, Path) else session_dir
      try:
        image_ref = save_png(s_dir, source_id, png)
      except Exception as e:
        logger.warning("Internal save_png failed in raster converter: %s", e)

    source_data = {
        "imageRef": image_ref,
        "bounds": bounds,
        "imageSize": [int(w), int(h)],
    }
  return raster_layer, legend, png, source_data
