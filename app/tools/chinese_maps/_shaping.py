"""Shared GeoJSON shaping for the Chinese-maps POI tools.

The three providers (Amap, Baidu, Tianditu) all build the same
``{type:Feature, geometry:Point, properties:{...}}`` envelope around POI
records, then wrap it in a ``{type:FeatureCollection, ...}`` envelope. That
shaping was duplicated across 8 POI-search functions (~13× total).

This module owns the envelope. Each provider supplies only the two things
that genuinely diverge:

- ``extract_coord(poi) -> (lng, lat) | None`` — how a coordinate is pulled
  from that provider's record shape (Amap: ``"lng,lat"`` string; Baidu:
  ``{lng, lat}`` dict; Tianditu: ``"lng lat"`` string).
- ``properties_fn(poi) -> dict`` — the per-record property keys, handling
  minor field-name divergences (``telephone`` vs ``tel``, ``area`` vs
  ``district``).

CRS correction (architecture-review C3): rather than each provider calling
``gcj02_to_wgs84`` / ``bd09_to_wgs84`` per point, the helper builds the
FeatureCollection in the provider's *source* CRS and runs
:func:`app.utils.coord_transform.transform_geojson` **once** to normalize to
WGS84. Tianditu is already WGS84 (CGCS2000 ≈ WGS84), so it passes
``src_crs=None`` and the transform is skipped. Verified byte-identical to the
prior per-point transforms (see ``test_shaping``).

The provider-API-call half of each function stays where it is — the genuine,
load-bearing divergence is the HTTP request and response parsing, not the
envelope. Only the shaping consolidates.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from app.utils.coord_transform import transform_geojson

# A coord pulled from a provider POI record: ``(lng, lat)`` in the provider's
# source CRS, or ``None`` if the record has no usable coordinate (the helper
# skips such records, matching the prior ``if len(loc) != 2: continue`` guard).
CoordExtractor = Callable[[Dict[str, Any]], Optional[Tuple[float, float]]]
PropertiesFn = Callable[[Dict[str, Any]], Dict[str, Any]]


def shape_poi_collection(
    raw_pois: list[Dict[str, Any]],
    *,
    extract_coord: CoordExtractor,
    properties_fn: PropertiesFn,
    provider: str,
    src_crs: Optional[str] = None,
    limit: Optional[int] = None,
    extra_envelope: Optional[Dict[str, Any]] = None,
    total: Optional[int] = None,
) -> Dict[str, Any]:
    """Shape provider POI records into a WGS84 GeoJSON FeatureCollection.

    Args:
        raw_pois: the provider's raw POI records (in their source CRS).
        extract_coord: pulls ``(lng, lat)`` from one record, or ``None`` to
            skip it (mirrors the prior ``continue`` on malformed coords).
        properties_fn: builds the ``properties`` dict from one record.
        provider: the provider name, stamped on the envelope (``"amap"`` /
            ``"baidu"`` / ``"tianditu"``).
        src_crs: the CRS the provider's coordinates arrive in (``"gcj02"`` or
            ``"bd09"``); ``None`` means already WGS84 (Tianditu) and skips the
            transform.
        limit: optional cap on the number of records shaped.
        extra_envelope: optional extra keys merged into the FeatureCollection
            envelope — e.g. ``{"center": ..., "radius_m": ...}`` for
            ``search_poi_around``, ``{"polygon": ...}`` for ``search_poi_polygon``.

    Returns:
        A ``FeatureCollection`` dict whose features carry WGS84 coordinates.
    """
    records = raw_pois[:limit] if limit is not None else raw_pois
    features = []
    for p in records:
        coord = extract_coord(p)
        if coord is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [coord[0], coord[1]]},
            "properties": properties_fn(p),
        })

    envelope: Dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": provider,
    }
    if total is not None:
        try:
            envelope["total"] = int(total)
        except Exception:
            envelope["total"] = total
    if extra_envelope:
        envelope.update(extra_envelope)

    if src_crs is not None:
        # Normalize the whole FC to WGS84 in one pass. The transform preserves
        # the envelope keys (it only walks geometry.coordinates) and deep-copies,
        # so the source-coord list is not mutated.
        envelope = transform_geojson(envelope, src_crs, "wgs84")

    return envelope
