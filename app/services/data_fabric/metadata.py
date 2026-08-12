"""Metadata truthfulness helpers (Section 27 / 28 / 29).

The catalog previously fabricated metadata when a remote source did not declare
it: an undeclared CRS became ``EPSG:4326``; an unknown feature count became a
precise ``0``/``100``/``10000``; geometry type was classified as vector/raster
via a fragile ``"raster" in geom_type.lower()`` substring test (so tile
pyramids were mislabeled ``vector``).

These normalizers return ``None`` / ``"unknown"`` for genuinely unknown values
instead of inventing data, and canonicalize the forms that ARE declared.
"""
import re
from typing import Any, Optional

# Canonical geometry-type vocabulary (Section 28).
_VECTOR_TYPES = frozenset({
    "Point", "MultiPoint", "LineString", "MultiLineString",
    "Polygon", "MultiPolygon", "GeometryCollection",
})
_RASTER_TOKENS = frozenset({"raster", "coverage", "grid"})
_TILE_TOKENS = frozenset({"tile", "tilepyramid", "pyramid", "mvt", "wms", "wmts"})


# ArcGIS / common geometry-type aliases → canonical OGC name.
_GEOMETRY_ALIASES = {
    "esrigeometrypoint": "Point",
    "esrigeometrymultipoint": "MultiPoint",
    "esrigeometrypolyline": "MultiLineString",
    "esrigeometrypolygon": "Polygon",
    "esrigeometrymultipolygon": "MultiPolygon",
    "linestring": "LineString",
    "multilinestring": "MultiLineString",
    "multipolygon": "MultiPolygon",
    "geometrycollection": "GeometryCollection",
    "feature": "Geometry",          # OGC/WFS generic — unknown concrete type
    "geometry": "Geometry",
    "tilepyramid": "TilePyramid",
}


def normalize_geometry_type(raw: Any) -> str:
    """Return a canonical geometry-type string.

    Recognizes the OGC simple-feature types, ArcGIS ``esriGeometry*`` codes,
    and raster/tile services. Anything unrecognizable becomes ``"unknown"``
    rather than a guessed vector type.
    """
    if not raw or not isinstance(raw, str):
        return "unknown"
    val = raw.strip()
    if not val:
        return "unknown"
    low = val.lower()

    # Direct canonical match (case-insensitive).
    for t in _VECTOR_TYPES:
        if low == t.lower():
            return t

    # Aliases (esri*, feature, geometry, tilepyramid, ...).
    aliased = _GEOMETRY_ALIASES.get(low)
    if aliased:
        return aliased

    # Substring detection for raster/tile services.
    if any(tok in low for tok in _RASTER_TOKENS):
        return "Raster"
    if any(tok in low for tok in _TILE_TOKENS):
        return "TilePyramid"

    return "unknown"


def classify_feature_type(geometry_type: Any) -> str:
    """Classify a geometry type into ``vector`` / ``raster`` / ``tile`` / ``unknown``.

    Replaces the fragile ``"raster" in geom_type.lower()`` heuristic — tile
    pyramids (PMTiles) are no longer mislabeled ``vector``.
    """
    norm = normalize_geometry_type(geometry_type)
    if norm == "Raster":
        return "raster"
    if norm == "TilePyramid":
        return "tile"
    if norm in _VECTOR_TYPES or norm == "Geometry":
        return "vector"
    return "unknown"


# ── CRS normalization ───────────────────────────────────────────────────────

# OGC CRS URIs (used by OGC API Features) → EPSG form.
_CRS_URI_RE = re.compile(
    r"/def/crs/(?:EPSG|OGC)/(?:0|1\.3|\d+(?:\.\d+)?)/(?P<code>\d{4,6})\b", re.IGNORECASE
)


def normalize_crs(raw: Any) -> Optional[str]:
    """Return a canonical ``EPSG:NNNN`` CRS string, or ``None`` if unknown.

    NEVER fabricates ``EPSG:4326``. Accepts:
    - ``EPSG:4326`` (passthrough);
    - OGC CRS URIs (``http://www.opengis.net/def/crs/OGC/1.3/CRS84`` → ``CRS84``
      which we keep verbatim as a recognized CRS84 marker, and EPSG URIs → EPSG);
    - bare authority codes (``4326`` → ``EPSG:4326``).
    Returns ``None`` for empty / unparseable / ``unknown``.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    val = raw.strip()
    if not val or val.lower() in ("unknown", "none", "null"):
        return None

    up = val.upper()
    if up.startswith("EPSG:") or up.startswith("ESRI:") or up == "CRS84":
        return val if up != "CRS84" else "CRS84"

    # OGC CRS84 URI (http://www.opengis.net/def/crs/OGC/1.3/CRS84) → CRS84.
    if up.endswith("/CRS84"):
        return "CRS84"

    # OGC CRS URI → EPSG:NNNN (CRS84 stays CRS84).
    m = _CRS_URI_RE.search(val)
    if m:
        code = m.group("code")
        return "CRS84" if code == "84" else f"EPSG:{code}"

    # Bare numeric code → assume EPSG.
    if val.isdigit() and 4 <= len(val) <= 6:
        return f"EPSG:{val}"

    # urn:ogc:def:crs:EPSG::4326
    if "EPSG" in up and val.rstrip("/").split(":")[-1].isdigit():
        code = val.rstrip("/").split(":")[-1]
        return f"EPSG:{code}"

    # Unrecognized CRS string — keep it verbatim but don't invent.
    return val


# ── Feature-count semantics ─────────────────────────────────────────────────

def normalize_feature_count(raw: Any) -> Optional[int]:
    """Return a non-negative feature count, or ``None`` for unknown.

    ``None`` is the truthful representation of "the source did not report a
    count"; ``0`` is reserved for a genuine zero-count dataset.
    """
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


__all__ = [
    "normalize_geometry_type",
    "classify_feature_type",
    "normalize_crs",
    "normalize_feature_count",
]
