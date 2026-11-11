"""G20 真实像元检视（源窗口/像元读取，绝不从显示 PNG 反推数值）。

- 默认读取被坐标命中的**源像元**；本任务不提供插值采样（不把屏幕
  bilinear 混合值称为原始测量）。
- 分类语义由真实元数据或用户显式声明提供，整数 dtype 不单独证明类别。
- 三种不可混淆状态：范围外 / mask 洞 / 无 CRS；另有数据修订过期。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.schemas.raster_spec import RasterExpressionError
from app.services.raster_authoring.budgets import bounded_bands, bounded_points
from app.services.raster_authoring.source import RasterSource

SOURCE_CRS_KEYWORDS = frozenset({"source", "native", "pixel_crs", ""})


def _point_xy(raw: Any, position: int) -> Tuple[float, float, str]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        x, y = raw[0], raw[1]
        label = f"point[{position}]"
    elif isinstance(raw, dict):
        if "x" in raw and "y" in raw:
            x, y = raw["x"], raw["y"]
        elif "lng" in raw and "lat" in raw:
            x, y = raw["lng"], raw["lat"]
        elif "lon" in raw and "lat" in raw:
            x, y = raw["lon"], raw["lat"]
        else:
            raise RasterExpressionError(
                "INVALID_CRS", "point must carry x/y or lng/lat", field="points",
            )
        label = str(raw.get("id") or f"point[{position}]")
    else:
        raise RasterExpressionError(
            "INVALID_CRS", "point must be [x, y] or {x, y}", field="points",
        )
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError) as exc:
        raise RasterExpressionError(
            "INVALID_CRS", "point coordinates must be finite numbers", field="points",
        ) from exc
    if not (math.isfinite(fx) and math.isfinite(fy)):
        raise RasterExpressionError(
            "INVALID_CRS", "point coordinates must be finite numbers", field="points",
        )
    return fx, fy, label


def _transform_points(
    points: Sequence[Tuple[float, float]], crs: Optional[str], source: RasterSource
) -> List[Tuple[float, float]]:
    """把请求坐标转到源 CRS；缺 CRS 时只允许显式 source 坐标。"""
    requested = (crs or "source").strip()
    if requested.lower() in SOURCE_CRS_KEYWORDS or (source.crs and requested == source.crs):
        return [(x, y) for x, y in points]
    if not source.crs:
        raise RasterExpressionError(
            "NO_CRS",
            "the raster declares no CRS; geographic coordinate probing is unavailable",
            field="crs",
            correction_hint="Use source-grid coordinates (crs='source') or assign a CRS to the raster.",
        )
    try:
        from rasterio.warp import transform

        xs, ys = transform(requested, source.crs, [p[0] for p in points], [p[1] for p in points])
        return [(float(x), float(y)) for x, y in zip(xs, ys)]
    except RasterExpressionError:
        raise
    except Exception as exc:  # noqa: BLE001 - pyproj 错误不回显路径
        raise RasterExpressionError(
            "INVALID_CRS",
            f"cannot transform coordinates from {requested!r} to the source CRS",
            field="crs",
            correction_hint="Use EPSG:4326, EPSG:3857, or crs='source'.",
        ) from exc


def _read_pixel(
    source: RasterSource,
    bands: Sequence[int],
    coords: Sequence[Tuple[float, float]],
) -> List[Dict[str, Any]]:
    """一次打开、逐点窗口读；返回逐通道原值/有效性（源像元，不插值）。"""
    import rasterio
    from rasterio.windows import Window

    results: List[Dict[str, Any]] = []
    with rasterio.open(source.path) as src:
        inv = ~src.transform
        width, height = int(src.width), int(src.height)
        for (sx, sy) in coords:
            try:
                col_f, row_f = inv * (sx, sy)
            except Exception:  # noqa: BLE001
                results.append({"status": "OUTSIDE_EXTENT", "row": None, "col": None})
                continue
            col, row = int(math.floor(col_f)), int(math.floor(row_f))
            if not (0 <= col < width and 0 <= row < height):
                results.append({
                    "status": "OUTSIDE_EXTENT", "row": row, "col": col,
                    "source_coord": [sx, sy],
                })
                continue
            window = Window(col, row, 1, 1)
            data = src.read(indexes=list(bands), window=window, masked=True)
            arr = np.ma.getdata(data)
            mask = np.ma.getmaskarray(data)
            channel_values = []
            valid_flags = []
            for i, band in enumerate(bands):
                raw_value = arr[i, 0, 0]
                finite = bool(np.isfinite(raw_value))
                valid = bool(finite and not bool(mask[i, 0, 0]))
                meta = source.band(int(band))
                scale = float(meta.get("scale") or 1.0)
                offset = float(meta.get("offset") or 0.0)
                scaled = float(raw_value) * scale + offset if valid else None
                channel_values.append({
                    "band": int(band),
                    "label": meta.get("label") or f"band {band}",
                    "role": meta.get("role"),
                    "raw": float(raw_value) if finite else None,
                    "scaled": scaled,
                    "scale": scale,
                    "offset": offset,
                    "scale_applied": bool(meta.get("scale_declared") or meta.get("offset_declared")),
                    "unit": meta.get("unit"),
                    "unit_status": "declared" if meta.get("unit") else "unknown",
                    "valid": valid,
                    "invalid_reason": None if valid else ("non_finite" if not finite else "masked"),
                })
                valid_flags.append(valid)
            if not any(valid_flags):
                status = "MASKED_PIXEL"
            elif not all(valid_flags):
                status = "PARTIALLY_MASKED"
            else:
                status = "ok"
            results.append({
                "status": status,
                "row": row, "col": col,
                "source_coord": [sx, sy],
                "values": channel_values,
                "valid": status == "ok",
            })
    return results


def probe(
    source: RasterSource,
    points: Sequence[Any],
    *,
    crs: Optional[str] = "EPSG:4326",
    bands: Optional[Sequence[int]] = None,
    expected_data_revision: Optional[str] = None,
) -> Dict[str, Any]:
    """检视不会改地图、不生成分析产物、不推进 MapSpec 修订。"""
    if expected_data_revision and expected_data_revision != source.data_revision:
        raise RasterExpressionError(
            "STALE_BINDING",
            "probe is bound to an older data revision; re-read the descriptor",
            field="data_revision", status_code=409,
            correction_hint="Re-bind the raster expression to the current data revision.",
        )
    bounded = bounded_points(points)
    parsed = [_point_xy(p, i) for i, p in enumerate(bounded)]
    requested = [(x, y) for x, y, _ in parsed]
    coords = _transform_points(requested, crs, source)
    from app.services.raster_authoring.budgets import MAX_RASTER_PROBE_BANDS

    if bands is None:
        # 缺省「各波段」：与 UI 一致，按领域预算截取并**如实披露**，
        # 而不是让 >16 波段源直接 413（显式超限请求仍按预算拒绝）。
        all_bands = list(range(1, source.band_count + 1))
        truncated_bands = len(all_bands) > MAX_RASTER_PROBE_BANDS
        selected = bounded_bands(all_bands[:MAX_RASTER_PROBE_BANDS], source.band_count)
    else:
        selected = bounded_bands(bands, source.band_count)
        truncated_bands = False
    if not selected:
        raise RasterExpressionError(
            "BUDGET_EXCEEDED", "at least one band is required", field="bands",
        )
    pixels = _read_pixel(source, selected, coords)
    out_points = []
    label: str
    for (rx, ry, label), (sx, sy), pixel in zip(parsed, coords, pixels):
        out_points.append({
            "id": label,
            "requested_coord": [rx, ry],
            "requested_crs": crs or "source",
            "source_coord": [sx, sy],
            "source_crs": source.crs,
            **pixel,
        })
    return {
        "success": True,
        "ref_id": source.ref_id,
        "session_id": source.session_id,
        "data_revision": source.data_revision,
        "method": "nearest_source_pixel_window_1x1",
        "interpolated": False,
        "bands": selected,
        "truncated": truncated_bands,
        "points": out_points,
        "classification": source.dataset_tags.get("classification") or "undeclared",
    }
