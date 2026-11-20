"""G20 图例与表达说明同源（图面像素、图例、导出说明共用一个规范样式）。"""
from __future__ import annotations

from typing import Any, Dict, List

from app.schemas.raster_spec import RasterExpressionSpec
from app.services.raster_authoring.budgets import MAX_LEGEND_SWATCHES
from app.services.raster_authoring.palettes import legend_swatches
from app.services.raster_authoring.source import RasterSource


def _band_label(source: RasterSource, index: int) -> str:
    meta = source.band(index)
    description = str(meta.get("description") or "").strip()
    role = meta.get("role")
    if role:
        return f"band {index} ({role})"
    if description:
        return f"band {index}: {description[:48]}"
    return f"band {index}"


def build_legend(spec: RasterExpressionSpec, source: RasterSource) -> Dict[str, Any]:
    """派生图例（写入 ``layer.legend_spec``，与样式同一笔提交）。"""
    stretch = spec.stretch
    common = {
        "schema_version": "raster_expression.v1",
        "style_id": spec.style_id,
        "data_revision": spec.data_revision,
        "classification": spec.classification,
        "resampling": spec.resampling,
        "opacity": spec.opacity,
        "stretch_provenance": {
            "source": stretch.source,
            "method": stretch.method,
            "sample_count": stretch.sample_count,
            "approximate": stretch.approximate,
        },
        "invalid_semantics": "transparent (nodata / non-finite / mask)",
        "out_of_range_semantics": "clipped to the declared stretch endpoints",
    }
    if spec.mode == "single":
        lo, hi = spec.channel_stretch(0)
        meta = source.band(spec.bands[0])
        palette = spec.colormap or "gray"
        notes: List[str] = []
        if lo == hi:
            notes.append("constant_or_all_nodata: the resolved stretch has zero span; no pseudo-extremes are shown")
        if stretch.approximate:
            notes.append("approximate_range: endpoints come from a bounded decimated sample, not a full-scan statistic")
        if meta.get("unit") is None:
            notes.append("unit_unknown: the source declares no unit for this band")
        return {
            **common,
            "type": "continuous",
            "band": spec.bands[0],
            "band_label": _band_label(source, spec.bands[0]),
            "unit": meta.get("unit"),
            "unit_status": "declared" if meta.get("unit") else "unknown",
            "min": lo, "max": hi,
            "palette": palette,
            "palette_colors": legend_swatches(palette, MAX_LEGEND_SWATCHES),
            "constant": lo == hi,
            "reverse": bool(str(palette).endswith("_r")),
            "notes": notes,
        }
    channels = []
    for i, band in enumerate(spec.bands):
        lo, hi = spec.channel_stretch(i)
        meta = source.band(band)
        channels.append({
            "channel": ("R", "G", "B")[i],
            "band": band,
            "band_label": _band_label(source, band),
            "min": lo, "max": hi,
            "unit": meta.get("unit"),
            "unit_status": "declared" if meta.get("unit") else "unknown",
        })
    return {
        **common,
        "type": "raster_rgb",
        "channels": channels,
        "colorbar": None,
        "notes": [
            "rgb_composite: channel combination is not a single physical quantity; no continuous colorbar is shown",
            *(
                ["approximate_range: endpoints come from a bounded decimated sample"]
                if stretch.approximate else []
            ),
        ],
    }


def expression_statement(spec: RasterExpressionSpec, source: RasterSource) -> Dict[str, Any]:
    """简短表达说明（机器可读；导出 sidecar / Pi 回执共用，不含影像数组）。"""
    bands = []
    for i, b in enumerate(spec.bands):
        lo, hi = spec.channel_stretch(i)
        meta = source.band(b)
        bands.append({
            "index": b,
            "label": _band_label(source, b),
            "channel": ("R", "G", "B")[i] if spec.mode == "rgb" else "gray",
            "stretch": [lo, hi],
            "unit": meta.get("unit"),
            "unit_status": "declared" if meta.get("unit") else "unknown",
            "role": meta.get("role"),
        })
    return {
        "schema_version": spec.schema_version,
        "style_id": spec.style_id,
        "data_revision": spec.data_revision,
        "mode": spec.mode,
        "bands": bands,
        "colormap": spec.colormap,
        "classification": spec.classification,
        "resampling": spec.resampling,
        "opacity": spec.opacity,
        "stretch_provenance": {
            "source": spec.stretch.source,
            "method": spec.stretch.method,
            "sample_count": spec.stretch.sample_count,
            "approximate": spec.stretch.approximate,
        },
        "source": {
            "ref_id": source.ref_id,
            "band_count": source.band_count,
            "crs": source.crs,
            "nodata": source.descriptor.nodata,
            "width": source.descriptor.width,
            "height": source.descriptor.height,
        },
        "limits": [
            "display stretch does not change source pixel values",
            "increasing export DPI does not add remote-sensing detail",
        ],
    }
