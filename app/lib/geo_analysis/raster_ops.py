import json
import logging
import math
import os
from typing import Optional, Union
import numpy as np
import rasterio

from app.utils.coord_transform import transform_geojson

logger = logging.getLogger(__name__)


def _has_inf_coords(obj) -> bool:
    """Return True if any leaf coordinate in a GeoJSON is non-finite.

    pyproj silently returns (inf, inf) for out-of-bounds reprojections
    (e.g. EPSG:3857 metres misread as EPSG:4326 degrees) without raising.
    Those infinities survive into rasterstats and produce hole/zero stats
    that look plausible. Catch them here to fail loudly (GIS-23 / #682).
    """
    def _walk(v):
        if isinstance(v, dict):
            if "coordinates" in v:
                if _walk(v["coordinates"]):
                    return True
            if "geometries" in v:
                for g in v["geometries"] or []:
                    if _walk(g):
                        return True
            if "features" in v:
                for f in v["features"] or []:
                    if _walk(f):
                        return True
            if "geometry" in v and isinstance(v["geometry"], dict):
                if _walk(v["geometry"]):
                    return True
        elif isinstance(v, (list, tuple)):
            if v and isinstance(v[0], (int, float)) and not isinstance(v[0], bool):
                # leaf coordinate tuple
                for c in v[:2]:
                    if isinstance(c, float) and not math.isfinite(c):
                        return True
            else:
                for c in v:
                    if _walk(c):
                        return True
        return False

    return _walk(obj)


def zonal_statistics(
    polygons_geojson: Union[dict, str],
    raster_path: str,
    stats: Optional[list[str]] = None,
    *,
    strict: bool = True,
) -> list[dict]:
    """Compute zonal statistics for polygons against a raster.

    Raises ValueError when polygon reprojection to the raster CRS fails — the
    previous behavior silently fed source-CRS polygons to a projected raster,
    producing plausible-looking zero statistics (GIS-23, deep-audit round 3).

    science-v4 W3（geometry repair disclosure）：自交等无效几何默认
    ``strict=True`` 类型化拒绝（InvalidGeometry）；``strict=False`` 走
    make_valid 修复并在对应统计行携带 ``geometry_repair`` 披露记录
    （方法/无效原因/面积变化）—— 修复永不静默。
    """
    if stats is None:
        stats = ['mean', 'sum', 'max', 'min']

    with rasterio.open(raster_path) as src:
        raster_crs = src.crs

    geojson_obj: Union[dict, None] = None
    if isinstance(polygons_geojson, str):
        candidate = polygons_geojson.strip()
        if os.path.exists(polygons_geojson):
            # 路径输入走 validate_data_path 工作区校验（#2134：不再对
            # 任意 LLM 提供的字符串直接 os.path.exists + open）。
            from app.utils.path import validate_data_path

            safe_path = validate_data_path(polygons_geojson)
            with open(safe_path, "r", encoding="utf-8") as f:
                geojson_obj = json.load(f)
        elif candidate.startswith("{"):
            try:
                geojson_obj = json.loads(polygons_geojson)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"polygons_geojson 是 JSON 起始但解析失败: {e}"
                ) from e
        else:
            # #2134：未识别输入（既非存在路径也非 GeoJSON）typed 拒绝——
            # 旧行为静默返回 []（feat_list 空），调用方无法区分
            # "无重叠" 与 "输入 entirely 无效"。
            raise ValueError(
                "polygons_geojson 字符串既非存在的文件路径，也非 GeoJSON "
                "对象文本——无法识别输入（拒绝静默空结果）")
    elif isinstance(polygons_geojson, dict):
        geojson_obj = polygons_geojson

    if geojson_obj is not None and raster_crs is not None:
        # #708: honor both GeoJSON crs member forms — the dict form
        # ({"crs": {"properties": {"name": ...}}}) and the string shorthand
        # ("crs": "EPSG:…") that extract_declared_crs (the contract authority)
        # accepts; the old dict-only read raised AttributeError on the string
        # form.
        from app.lib.geo_processor.core import extract_declared_crs
        src_crs = extract_declared_crs(geojson_obj) or "EPSG:4326"
        target_crs_str = str(raster_crs)
        try:
            reprojected_geojson = transform_geojson(geojson_obj, from_crs=src_crs, to_crs=target_crs_str)
            polygons_input = reprojected_geojson
        except Exception as e:
            # GIS-23: do NOT silently fall back to source-CRS polygons — a
            # WGS84 polygon fed to a projected raster yields all-zero stats
            # that look like real "no data". Fail loudly with both CRS strings.
            logger.warning(
                "zonal_statistics reprojection failed (%s -> %s): %s",
                src_crs, target_crs_str, e,
            )
            raise ValueError(
                f"Polygon reprojection {src_crs} -> {target_crs_str} failed: {e}. "
                "Refusing to compute zonal stats with mismatched CRS."
            ) from e
        # GIS-682: pyproj returns (inf, inf) silently for out-of-bounds
        # reprojections without raising (e.g. EPSG:3857 metres misread as
        # EPSG:4326 degrees). Those infinities reach rasterstats as
        # hole/zero stats that look correct. Fail loudly when any
        # reprojected coordinate is non-finite so the GIS-23 guard fires.
        if _has_inf_coords(polygons_input):
            raise ValueError(
                f"Polygon reprojection {src_crs} -> {target_crs_str} produced "
                "non-finite coordinates (inf/nan) — input CRS likely mismatched. "
                "Refusing to compute zonal stats with mismatched CRS."
            )
    else:
        polygons_input = polygons_geojson

    return _windowed_zonal_stats(polygons_input, raster_path, stats=stats, strict=strict)


def _windowed_zonal_stats(
    polygons_geojson: dict, raster_path: str, *, stats: list[str],
    strict: bool = True,
) -> list[dict]:
    """Zonal statistics through the V4 raster runtime (bounded memory).

    Replaces the rasterstats library hand-off, whose internals materialize
    whole-band reads for every zone. Each zone reads only its own pixel
    window (bbox → window, clamped to the raster), masks by the polygon,
    and computes the requested statistics in numpy; zones larger than the
    memory-budget window are read in row chunks with incremental
    (Welford-merged) statistics — per-zone memory stays O(chunk), so a
    national-scale polygon no longer means a whole-raster read (#2134).
    Semantics preserved from the rasterstats contract: statistic keys
    exactly as requested
    (min/max/mean/sum/count), nodata excluded, ``all_touched=False``-style
    cell-center membership. Every row also carries valid_pixel_coverage
    (None when no zone pixels can be measured); repaired zones additionally
    carry geometry_repair. These disclosure fields are not extra statistics.
    """
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds
    from shapely.geometry import shape

    from app.lib.geo_raster import RasterSource

    source = RasterSource.from_path(raster_path)
    reader = source.reader()
    try:
        meta = reader.metadata()
        if meta.crs is None:
            raise ValueError(
                f"raster {raster_path!r} has no CRS; refusing zonal stats"
            )
        # rasterstats accepted FeatureCollections, bare Features, AND raw
        # geometries — the temporal engine's _scene_aoi emits a bare
        # Feature. Normalize all three shapes (parity, not a behavior cut).
        if isinstance(polygons_geojson, dict):
            if isinstance(polygons_geojson.get("features"), list):
                feat_list = polygons_geojson["features"]
            elif polygons_geojson.get("type") == "Feature":
                feat_list = [polygons_geojson]
            elif polygons_geojson.get("geometry"):
                feat_list = [{"type": "Feature", "geometry": polygons_geojson["geometry"], "properties": {}}]
            elif polygons_geojson.get("type") in ("Polygon", "MultiPolygon"):
                feat_list = [{"type": "Feature", "geometry": polygons_geojson, "properties": {}}]
            else:
                feat_list = []
        else:
            feat_list = []
        out: list[dict] = []
        for f in feat_list:
            geom = (f or {}).get("geometry")
            if not geom:
                out.append({**{k: None for k in stats}, "valid_pixel_coverage": None})
                continue
            try:
                poly = shape(geom)
            except Exception as e:  # noqa: BLE001 — per-zone honest skip
                logger.warning("zonal: unparseable zone geometry skipped: %s", e)
                out.append({**{k: None for k in stats}, "valid_pixel_coverage": None})
                continue
            # science-v4 W3：无效几何门（strict 拒绝 / 修复+披露，永不静默）。
            from app.lib.geo_analysis.geometry_repair import ensure_valid_geometry

            poly, repair_rec = ensure_valid_geometry(
                poly, index=len(out), strict=strict, context="zonal_statistics")
            if repair_rec is not None:
                logger.warning(
                    "zonal: zone %d geometry repaired (%s), area delta %s",
                    len(out), repair_rec.reason[:80], repair_rec.area_delta,
                )
            minx, miny, maxx, maxy = poly.bounds
            # Disjoint zones: rasterio's Window.intersection RAISES on empty
            # overlap — clamp explicitly so an off-raster zone is a per-zone
            # null row (rasterstats parity), never a whole-call crash.
            import rasterio as _rio

            win = from_bounds(minx, miny, maxx, maxy, reader.dataset.transform)
            # Enclose the original floating endpoints, not a rounded width:
            # flooring the offset first can otherwise discard covered centers.
            col_start, row_start = math.floor(win.col_off), math.floor(win.row_off)
            col_stop = math.ceil(win.col_off + win.width)
            row_stop = math.ceil(win.row_off + win.height)
            win = _rio.windows.Window(
                col_start, row_start, col_stop - col_start, row_stop - row_start,
            )
            full = _rio.windows.Window(0, 0, meta.width, meta.height)
            if not _rio.windows.intersect([win, full]):
                out.append({**{k: None for k in stats}, "valid_pixel_coverage": None})
                continue
            win = win.intersection(full)
            # #2134：大 zone（如国家级 polygon = 整幅栅格）必须分块读——
            # 逐 zone 内存 O(chunk) 而非 O(zone bbox)。统计按块增量
            # 合并（Welford 方差合并，min/max/sum 直积），结果与整幅
            # 单次读一致（同一 float64 累加口径）。
            from app.lib.geo_analysis.raster_grid import (
                window_side_from_budget,
            )

            chunk_rows = max(1, int(window_side_from_budget()))
            n_vals = 0
            acc_mean = 0.0
            acc_m2 = 0.0
            acc_sum = 0.0
            acc_min = math.inf
            acc_max = -math.inf
            total_masked = 0
            for row0 in range(int(win.row_off),
                              int(win.row_off) + int(win.height), chunk_rows):
                n_rows = min(chunk_rows,
                             int(win.row_off) + int(win.height) - row0)
                sub = _rio.windows.Window(
                    int(win.col_off), row0, int(win.width), n_rows)
                arr_chunk = reader.read_window(
                    (int(sub.col_off), int(sub.row_off),
                     int(sub.width), int(sub.height))
                ).astype(np.float64, copy=False)
                # #1736：掩膜必须用**修复后**的 poly —— 窗口由 poly.bounds 定，
                # 若仍用修复前的 geom 做掩膜，统计区与披露的 geometry_repair
                # 记录不是同一个几何（无效环在栅格化规则下甚至可能整片落空，
                # 产出全 None 的「貌似无数据」行）。
                mask_chunk = geometry_mask(
                    [poly], out_shape=arr_chunk.shape,
                    transform=reader.window_transform(sub), invert=True,
                )
                vals_all = arr_chunk[mask_chunk]
                total_masked += int(vals_all.size)
                if meta.nodata is not None:
                    vals = vals_all[vals_all != meta.nodata]
                else:
                    vals = vals_all
                vals = vals[np.isfinite(vals)]
                c = int(vals.size)
                if c == 0:
                    continue
                chunk_mean = float(vals.mean())
                chunk_m2 = float(((vals - chunk_mean) ** 2).sum())
                tot = n_vals + c
                delta = chunk_mean - acc_mean
                acc_mean += delta * c / tot
                acc_m2 += chunk_m2 + delta * delta * n_vals * c / tot
                n_vals = tot
                acc_sum += float(vals.sum())
                acc_min = min(acc_min, float(vals.min()))
                acc_max = max(acc_max, float(vals.max()))
            # ADR-0284（断点 9 修复）：逐 zone 有效像元覆盖率披露——
            # 90% nodata 的 zone 均值与全有效 zone 在产物上不再不可区分。
            coverage = (float(n_vals) / total_masked
                        if total_masked > 0 else None)
            row: dict = {}
            if repair_rec is not None:
                row["geometry_repair"] = repair_rec.to_dict()
            for key in stats:
                if key == "count":
                    row["count"] = int(n_vals)
                elif n_vals == 0:
                    row[key] = None
                elif key == "min":
                    row["min"] = float(acc_min)
                elif key == "max":
                    row["max"] = float(acc_max)
                elif key == "mean":
                    row["mean"] = float(acc_mean)
                elif key == "sum":
                    row["sum"] = float(acc_sum)
                elif key == "std":
                    row["std"] = float(math.sqrt(acc_m2 / n_vals))
                else:
                    logger.warning(
                        "zonal: unsupported stat %r returned as None "
                        "(supported: min/max/mean/sum/std/count)", key,
                    )
                    row[key] = None
            if "count" not in stats and n_vals == 0:
                repair_rec_kept = row.get("geometry_repair")
                row = {k: None for k in stats}
                if repair_rec_kept is not None:
                    # 修复披露不可被重置丢弃（修复永不静默）
                    row["geometry_repair"] = repair_rec_kept
            row["valid_pixel_coverage"] = coverage
            out.append(row)
        return out
    finally:
        reader.close()

