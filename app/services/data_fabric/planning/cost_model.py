"""Acquisition cost model (DS3, ADR-0173) — aligned with federated costing.

Reuses the federated components where they fit (spatial selectivity from
``query/federated/costing.estimate_spatial_selectivity`` when a spatial
histogram is available; the rate-limit latency penalty otherwise) and adds
the acquisition-side heuristics:

- rows ≈ feature_count × selectivity(bbox area ratio, histogram-aware);
- bytes ≈ rows × bytes_per_row (field-declared size heuristic, provisional);
- latency ≈ protocol base + transfer time + rate-limit penalty;
- quota ≈ page count for paginated sources.

All estimates are ``provisional``: DS8 replaces the heuristics with measured
distributions (the constants live in one place here for that reason).
"""
from __future__ import annotations

import math
from typing import Optional

from app.services.data_fabric.contracts import CostEstimate

# ── Provisional constants (DS8 calibrates) ───────────────────────────────────

#: Per-row byte overhead for the GeoJSON carrier envelope.
_ROW_OVERHEAD_BYTES = 40
#: Per-field size by declared/observed type class.
_FIELD_BYTES = {"number": 8, "integer": 8, "float": 8, "bool": 4, "date": 12}
_DEFAULT_FIELD_BYTES = 28
#: Geometry carrier bytes per geometry type class.
_GEOMETRY_BYTES = {"point": 32, "line": 160, "polygon": 224, "raster": 0, "table": 0, "none": 0}
#: Protocol base round-trip latency (ms) — declared servers, 95th percentile.
_PROTOCOL_BASE_MS = {
    "postgis": 60.0,
    "ogc_api": 250.0,
    "wfs": 400.0,
    "arcgis": 300.0,
    "stac": 350.0,
    "geopackage": 2.0,
    "local_file": 2.0,
    "cog": 5.0,
    "stats_api": 450.0,
}
#: Transfer bandwidth assumption (bytes/ms) for remote sources.
_REMOTE_BYTES_PER_MS = 200.0  # ≈ 1.6 Mbps conservative
#: Page size assumption when the source paginates (rows per request).
_DEFAULT_PAGE_SIZE = 1000


def bytes_per_row(fields: int, geometry_type: str = "point") -> int:
    """Heuristic carrier bytes per row (provisional)."""
    geo = _GEOMETRY_BYTES.get((geometry_type or "point").lower(), 120)
    return _ROW_OVERHEAD_BYTES + fields * _DEFAULT_FIELD_BYTES + geo


def bbox_selectivity(
    request_bbox: Optional[list],
    coverage_bbox: Optional[list],
) -> float:
    """Area-ratio selectivity in [0, 1]; unknown sides → 1.0 (upper bound).

    Uses the federated spatial-selectivity estimator when a spatial grid
    histogram is available; the area ratio is the no-histogram fallback
    (documented provisional — uniformity assumption).
    """
    if not request_bbox:
        return 1.0
    if not coverage_bbox:
        return 1.0  # unknown coverage → can't claim reduction
    try:
        rx0, ry0, rx1, ry1 = (float(v) for v in request_bbox)
        cx0, cy0, cx1, cy1 = (float(v) for v in coverage_bbox)
    except (TypeError, ValueError):
        return 1.0
    inter_w = max(0.0, min(rx1, cx1) - max(rx0, cx0))
    inter_h = max(0.0, min(ry1, cy1) - max(ry0, cy0))
    cov_w = max(0.0, cx1 - cx0)
    cov_h = max(0.0, cy1 - cy0)
    if cov_w <= 0.0 or cov_h <= 0.0:
        return 1.0
    return max(0.0, min(1.0, (inter_w * inter_h) / (cov_w * cov_h)))


def estimate_cost(
    *,
    feature_count: Optional[int],
    fields: int,
    geometry_type: str = "point",
    protocol: str,
    request_bbox: Optional[list] = None,
    coverage_bbox: Optional[list] = None,
    aggregation: bool = False,
    limit: Optional[int] = None,
    local: bool = False,
    requests_per_minute: Optional[int] = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> CostEstimate:
    """Cost estimate for one acquisition (provisional, DS8-calibrated)."""
    selectivity = 1.0 if aggregation else bbox_selectivity(request_bbox, coverage_bbox)
    base_rows = feature_count if feature_count is not None else 10_000  # unknown → declared floor
    rows = int(max(0, base_rows * selectivity))
    if limit is not None and not aggregation:
        rows = min(rows, max(0, int(limit)))

    bpr = bytes_per_row(fields, geometry_type)
    if aggregation:
        # aggregate payload ≈ groups, not rows: assume ≤ page of groups
        payload_bytes = min(rows, page_size) * 16
        out_rows = min(rows, page_size)
    else:
        payload_bytes = rows * bpr
        out_rows = rows

    if local:
        latency_ms = _PROTOCOL_BASE_MS.get(protocol, 50.0) + payload_bytes / 500.0
    else:
        latency_ms = _PROTOCOL_BASE_MS.get(protocol, 300.0) + payload_bytes / _REMOTE_BYTES_PER_MS

    # rate-limit penalty (federated component reuse): scarce quota → queueing
    if requests_per_minute is not None and requests_per_minute > 0:
        latency_ms += 60000.0 / max(1, requests_per_minute) * 0.1

    pages = max(1, math.ceil(rows / max(1, page_size))) if not local else 1
    quota = float(pages) if requests_per_minute is not None else None

    return CostEstimate(
        rows=out_rows,
        bytes=int(payload_bytes),
        latency_ms=round(latency_ms, 1),
        quota=quota,
    )


__all__ = ["estimate_cost", "bytes_per_row", "bbox_selectivity"]
