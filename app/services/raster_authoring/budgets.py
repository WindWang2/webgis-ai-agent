"""G20 领域预算（有界读取，超限如实披露，不无限排队）。"""
from __future__ import annotations

from typing import Any, Dict, Iterable

from app.schemas.raster_spec import (
    MAX_RASTER_HISTOGRAM_BUCKETS,
    MAX_RASTER_PROBE_BANDS,
    MAX_RASTER_PROBE_POINTS,
    MAX_RASTER_SNAPSHOT_SIDE,
    MAX_RASTER_STATS_SIDE,
    RasterExpressionError,
)

MAX_TILE_ZOOM = 20
MAX_TILE_SIZE = 512
MAX_BAND_COUNT_METADATA = 64
MAX_DESCRIPTOR_BANDS = 16
MAX_LEGEND_SWATCHES = 12


def bounded_points(points: Iterable[Any]) -> list:
    values = list(points)
    if not values:
        raise RasterExpressionError(
            "BUDGET_EXCEEDED", "at least one point is required", field="points",
        )
    if len(values) > MAX_RASTER_PROBE_POINTS:
        raise RasterExpressionError(
            "BUDGET_EXCEEDED",
            f"at most {MAX_RASTER_PROBE_POINTS} probe points per request, got {len(values)}",
            field="points",
            correction_hint="Page the points into multiple bounded requests.",
            status_code=413,
        )
    return values


def bounded_bands(bands: Iterable[int], band_count: int) -> list[int]:
    values = list(bands)
    if len(values) > MAX_RASTER_PROBE_BANDS:
        raise RasterExpressionError(
            "BUDGET_EXCEEDED",
            f"at most {MAX_RASTER_PROBE_BANDS} bands per probe, got {len(values)}",
            field="bands", status_code=413,
        )
    if any(int(b) < 1 or int(b) > band_count for b in values):
        raise RasterExpressionError(
            "BAND_OUT_OF_RANGE",
            f"band indexes must be within 1..{band_count}",
            field="bands",
            details={"band_count": band_count},
        )
    return [int(b) for b in values]


def bounded_buckets(requested: int | None) -> int:
    if requested is None:
        return 32
    if requested < 2 or requested > MAX_RASTER_HISTOGRAM_BUCKETS:
        raise RasterExpressionError(
            "BUDGET_EXCEEDED",
            f"histogram buckets must be within 2..{MAX_RASTER_HISTOGRAM_BUCKETS}",
            field="buckets", status_code=413,
        )
    return int(requested)


def truncation_fact(*, requested: int, used: int, what: str) -> Dict[str, Any]:
    """显式披露降采样/截断事实，绝不把近似写成全量。"""
    return {
        "what": what,
        "requested": requested,
        "used": used,
        "truncated": used < requested,
        "max_stats_side": MAX_RASTER_STATS_SIDE,
        "max_snapshot_side": MAX_RASTER_SNAPSHOT_SIDE,
    }
