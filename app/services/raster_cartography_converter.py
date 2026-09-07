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

# V4 渲染模式（Design System：hillshade/classified/hillshade_blend/
# bivariate 走同一条 raster 渲染链；缺省 continuous 与既有行为完全一致）
_RENDER_MODES = ("continuous", "hillshade", "classified", "hillshade_blend",
                 "bivariate")


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


def _extract_render_mode(payload: Dict[str, Any]) -> str:
  """V4：payload 顶层或 raster_source 嵌套的 render_mode。非法值回落
  continuous（纯解析无副作用；与历史 docstring 声明不同，非法但非空的
  值不写 provenance 警告 —— 本处如实记录实际行为）。"""
  for src in (payload, payload.get("raster_source") if isinstance(payload.get("raster_source"), dict) else None):
    if isinstance(src, dict):
      mode = src.get("render_mode")
      if isinstance(mode, str) and mode in _RENDER_MODES:
        return mode
  return "continuous"


def _extract_second_array(payload: Dict[str, Any]) -> Optional[np.ndarray]:
  """V4 bivariate 模式：第二个波段数组（array_b / band_b）。"""
  for src in (payload, payload.get("raster_source") if isinstance(payload.get("raster_source"), dict) else None):
    if isinstance(src, dict):
      for k in ("array_b", "band_b", "array2"):
        v = src.get(k)
        if isinstance(v, np.ndarray):
          return v
  return None


def is_raster_source(source_data: Any) -> bool:
  """True if `source_data` is a raster payload: a dict carrying a numeric array
    An ndarray is the decisive signal. Bounds are validated later instead of
    being used to route malformed raster input through the vector converter.
    Distinguishes from analysis-result dicts (legend_spec/
  algorithm/success+data) and plain GeoJSON, both of which route through the
  vector converter.

  Detection priority (array+bounds is the *decisive* signal — a numeric array
  with georeferencing bounds can only be a raster; it wins over marker keys like
  `algorithm`/`legend_spec`, which raster payloads legitimately also carry):
    1. Not a dict → False
    2. Has a numeric array → True  (raster, regardless of markers)
    3. Otherwise → False (analysis-result / GeoJSON / string → vector path)
  """
  if not isinstance(source_data, dict):
    return False
  return _has_numeric_array(source_data)


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
  # #480: NaN must be zeroed BEFORE the integer stop indexing below —
  # np.clip does not remove NaN (NaN comparisons are False), so
  # floor(NaN).astype(int) yields INT64_MIN on x86-64 and rgb_stops[lower]
  # raises IndexError. Color value is irrelevant for masked cells; the alpha
  # channel keeps them transparent.
  norm = np.where(nodata_mask, 0.0, norm)

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


def _render_hillshade_png(array: np.ndarray, params: Dict[str, Any]) -> bytes:
  """V4 hillshade 模式：DEM 数组 → Horn 法晕渲灰度 PNG（服务端预渲染；
  MapLibre 原生 hillshade 图层（raster-dem 源）未接线 —— 见模型 pitfalls）。"""
  from app.lib.cartography.raster_render import hillshade_array
  dem = np.asarray(array, dtype=float)
  if dem.ndim != 2 or dem.size < 9:
    raise ValueError("hillshade 需要 2D 且 ≥3×3 的 DEM 数组")
  shade = hillshade_array(
      dem,
      cell_size=float(params.get("cell_size", 1.0)),
      azimuth=float(params.get("azimuth", 315.0)),
      altitude=float(params.get("altitude", 45.0)),
  )
  return render_array_to_png(shade, palette=params.get("palette", "Gray"))


def _render_classified_png(
    array: np.ndarray, params: Dict[str, Any]
) -> Tuple[bytes, Dict[str, Any], str]:
  """V4 classified 模式：连续栅格按断点分级为离散色阶 PNG。

  断点来源优先级：显式 ``breaks`` > ``n_classes``（等距）。返回
  (png, graduated legend_spec, resolved palette)。
  """
  from app.lib.cartography.palettes import COLOR_PALETTES
  from app.lib.cartography.raster_render import classify_array, equal_interval_breaks
  from PIL import Image

  arr = np.asarray(array, dtype=float)
  if arr.ndim != 2 or arr.size == 0:
    raise ValueError("classified 需要 2D 数组")
  resolved = params.get("palette") or DEFAULT_RASTER_PALETTE
  colors_src = COLOR_PALETTES.get(resolved) or COLOR_PALETTES[DEFAULT_RASTER_PALETTE]
  breaks = params.get("breaks")
  if isinstance(breaks, (list, tuple)) and breaks:
    brk = sorted(float(b) for b in breaks if np.isfinite(b))
  else:
    brk = equal_interval_breaks(arr, int(params.get("n_classes", 5)))
  if not brk:
    raise ValueError("classified 断点为空（常数场无法分级）")
  n_classes = len(brk) + 1
  # 离散取色（端点含括的均匀重采样：n_classes 档跨满整条 ramp，
  # 不做插值 —— 分级栅格语义；深端不丢色）
  if n_classes == 1:
    colors = [colors_src[0]]
  elif len(colors_src) >= n_classes:
    step = (len(colors_src) - 1) / (n_classes - 1)
    colors = [colors_src[round(i * step)] for i in range(n_classes)]
  else:
    colors = list(colors_src[:n_classes]) + [colors_src[-1]] * (
        n_classes - len(colors_src))
  rgb_stops = np.array([_hex_to_rgb(c) for c in colors], dtype=float)

  cls = classify_array(arr, brk)   # NaN → -1
  finite = arr[np.isfinite(arr)]
  a_min = float(finite.min()) if finite.size else 0.0
  a_max = float(finite.max()) if finite.size else 0.0
  valid = cls >= 0
  rgb = np.zeros(arr.shape + (3,), dtype=np.uint8)
  rgb[valid] = rgb_stops[np.clip(cls[valid], 0, n_classes - 1)].astype(np.uint8)
  alpha = np.where(valid, 255, 0).astype(np.uint8)
  rgba = np.dstack([rgb, alpha])
  img = Image.fromarray(rgba, mode="RGBA")
  buf = io.BytesIO()
  img.save(buf, format="PNG")

  legend = {
      "type": "graduated",
      "min": round(a_min, 6),
      "max": round(a_max, 6),
      "palette": resolved,
      "palette_colors": colors,
      "breaks": [round(b, 6) for b in brk],
      "nodata_zh": "无数据（透明）",
  }
  return buf.getvalue(), legend, resolved


def _render_hillshade_blend_png(
    array: np.ndarray, params: Dict[str, Any]
) -> Tuple[bytes, Dict[str, Any]]:
  """V4 hillshade_blend 模式：分层设色（continuous colormap）× 晕渲 alpha
  合成（经典 hypsometric tint + hillshade）。光源参数进 legend 披露。"""
  from app.lib.cartography.palettes import COLOR_PALETTES
  from app.lib.cartography.raster_render import (
      blend_arrays,
      hillshade_array,
      normalize_min_max,
  )
  from PIL import Image

  arr = np.asarray(array, dtype=float)
  if arr.ndim != 2 or arr.size < 9:
    raise ValueError("hillshade_blend 需要 2D 且 ≥3×3 的数值数组")
  resolved = params.get("palette") or "Oranges"
  colors = COLOR_PALETTES.get(resolved) or COLOR_PALETTES["Oranges"]
  rgb_stops = np.array([_hex_to_rgb(c) for c in colors], dtype=float)

  lo, hi = normalize_min_max(arr)
  norm = (arr - lo) / (hi - lo) if hi > lo else np.zeros_like(arr)
  norm = np.where(np.isfinite(arr), np.clip(norm, 0, 1), 0.0)

  shade = hillshade_array(
      arr,
      cell_size=float(params.get("cell_size", 1.0)),
      azimuth=float(params.get("azimuth", 315.0)),
      altitude=float(params.get("altitude", 45.0)),
  )
  shade_norm = np.where(np.isfinite(shade), shade / 255.0, 0.0)
  alpha = float(np.clip(params.get("shade_alpha", 0.4), 0.0, 1.0))
  blended = blend_arrays(norm, shade_norm, overlay_alpha=alpha)

  n_stops = len(rgb_stops)
  scaled = np.clip(blended * (n_stops - 1), 0, n_stops - 1)
  lower = np.floor(scaled).astype(int)
  upper = np.clip(lower + 1, 0, n_stops - 1)
  frac = (scaled - lower)[..., None]
  rgb = np.clip(rgb_stops[lower] * (1 - frac) + rgb_stops[upper] * frac,
                0, 255).astype(np.uint8)
  nodata = ~np.isfinite(arr)
  alpha_ch = np.where(nodata, 0, 255).astype(np.uint8)
  rgba = np.dstack([rgb, alpha_ch])
  img = Image.fromarray(rgba, mode="RGBA")
  buf = io.BytesIO()
  img.save(buf, format="PNG")
  legend = {
      "type": "continuous",
      "min": round(lo, 6),
      "max": round(hi, 6),
      "palette": resolved,
      "palette_colors": list(colors),
      "hillshade_zh": (
          f"叠加山体晕渲（方位角 {params.get('azimuth', 315.0)}°、"
          f"高度角 {params.get('altitude', 45.0)}°、α={alpha}）"
      ),
  }
  return buf.getvalue(), legend


def _render_bivariate_png(
    array_a: np.ndarray, array_b: np.ndarray, params: Dict[str, Any]
) -> Tuple[bytes, Dict[str, Any]]:
  """V4 bivariate 模式：双波段逐格 3×3 分级色阵 PNG。"""
  from app.lib.cartography.bivariate import (
      BIVARIATE_MATRICES,
      DEFAULT_BIVARIATE_MATRIX,
      _breaks_quantiles,
  )
  from PIL import Image

  a = np.asarray(array_a, dtype=float)
  b = np.asarray(array_b, dtype=float)
  if a.shape != b.shape or a.ndim != 2 or a.size == 0:
    raise ValueError("bivariate 需要两个同形状 2D 数组")
  matrix = params.get("matrix") or DEFAULT_BIVARIATE_MATRIX
  if matrix not in BIVARIATE_MATRICES:
    matrix = DEFAULT_BIVARIATE_MATRIX
  colors = BIVARIATE_MATRICES[matrix]
  n = int(params.get("n", 3))
  fa = a[np.isfinite(a)].tolist()
  fb = b[np.isfinite(b)].tolist()
  br_a = _breaks_quantiles(fa, n)
  br_b = _breaks_quantiles(fb, n)
  if not br_a or not br_b:
    raise ValueError("bivariate 分级断点为空（常数场）")

  h, w = a.shape
  cls = np.full((h, w), -1, dtype=np.int16)
  both = np.isfinite(a) & np.isfinite(b)
  ia = np.zeros((h, w), dtype=np.int16)
  ib = np.zeros((h, w), dtype=np.int16)
  for i, br in enumerate(br_a):
    ia[both & (a > br)] = i + 1
  for i, br in enumerate(br_b):
    ib[both & (b > br)] = i + 1
  cls[both] = ib[both] * n + ia[both]

  rgb_stops = np.array([_hex_to_rgb(c) for c in colors], dtype=float)
  rgb = np.zeros((h, w, 3), dtype=np.uint8)
  valid = cls >= 0
  rgb[valid] = rgb_stops[cls[valid]].astype(np.uint8)
  alpha_ch = np.where(valid, 255, 0).astype(np.uint8)
  rgba = np.dstack([rgb, alpha_ch])
  img = Image.fromarray(rgba, mode="RGBA")
  buf = io.BytesIO()
  img.save(buf, format="PNG")
  legend = {
      "type": "bivariate",
      "matrix": matrix,
      "colors": list(colors),
      "n": n,
      "breaks_a": [round(x, 6) for x in br_a],
      "breaks_b": [round(x, 6) for x in br_b],
      "label_a": str(params.get("label_a", "变量 A")),
      "label_b": str(params.get("label_b", "变量 B")),
  }
  return buf.getvalue(), legend


def build_raster_layer(
    source_id: str,
    bounds: List[float],
    array: Any,
    palette: str = DEFAULT_RASTER_PALETTE,
    provenance: Optional[Dict[str, Any]] = None,
    layer_id: Optional[str] = None,
    render_mode: str = "continuous",
    render_params: Optional[Dict[str, Any]] = None,
    second_array: Any = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[bytes]]:
  """Render `array` to a PNG and build a `type:"raster"` MapSpec layer.

  V4：``render_mode`` 支持 continuous（缺省，原行为）/ hillshade /
  classified / hillshade_blend / bivariate。返回 (layer, legend_spec,
  png_bytes)。Best-effort：失败回落退化层（opacity 0）。
  """
  prov = dict(provenance or {})
  prov.setdefault("algorithm", "raster_analysis")
  prov.setdefault("computed_at", datetime.now(timezone.utc).isoformat())

  lid = layer_id or f"{source_id}_raster"
  params = dict(render_params or {})

  try:
    if not isinstance(array, np.ndarray):
      raise ValueError("array is not a numpy ndarray")

    png = None
    legend = None
    resolved_palette = palette
    if render_mode == "hillshade":
      png = _render_hillshade_png(array, params)
      resolved_palette = params.get("palette", "Gray")
      legend = {
          "type": "continuous",
          "min": 0.0,
          "max": 255.0,
          "palette": "Gray",
          "palette_colors": resolve_palette_colors("Gray"),
          "hillshade_zh": (
              f"山体晕渲（方位角 {params.get('azimuth', 315.0)}°、"
              f"高度角 {params.get('altitude', 45.0)}°）—— 服务端预渲染灰度"
          ),
      }
    elif render_mode == "classified":
      png, legend, resolved_palette = _render_classified_png(array, params)
    elif render_mode == "hillshade_blend":
      png, legend = _render_hillshade_blend_png(array, params)
      resolved_palette = legend.get("palette", palette)
    elif render_mode == "bivariate":
      if not isinstance(second_array, np.ndarray):
        raise ValueError("bivariate 需要第二个波段数组（array_b）")
      png, legend = _render_bivariate_png(array, second_array, params)
      resolved_palette = legend["matrix"]
    else:
      png = render_array_to_png(array, palette=palette)
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
    if render_mode != "continuous":
      prov["render_mode"] = render_mode

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
  # Never invent Null-Island-like spatial evidence. A missing/malformed extent
  # remains absent and the deterministic raster bounds rule fails truthfully.
  bounds = _extract_bounds(payload)
  arr = _extract_array(payload)
  palette = (payload.get("raster_source", {}) or {}).get("suggested_palette") or DEFAULT_RASTER_PALETTE
  render_mode = _extract_render_mode(payload)
  render_params = (payload.get("raster_source", {}) or {}).get("render_params") or {}
  second_array = _extract_second_array(payload)
  provenance = {
      k: v for k, v in payload.items()
      if k in ("algorithm", "computed_at", "item_id", "datetime", "source_ref")
  }

  raster_layer, legend, png = build_raster_layer(
      source_id=source_id, bounds=bounds or [], array=arr, palette=palette,
      provenance=provenance, layer_id=base_layer.get("id"),
      render_mode=render_mode, render_params=render_params,
      second_array=second_array,
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

    source_data = {"imageRef": image_ref, "imageSize": [int(w), int(h)]}
    if bounds is not None:
      source_data["bounds"] = bounds
  return raster_layer, legend, png, source_data
