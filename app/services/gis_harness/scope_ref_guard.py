"""Refuse a product ref whose place evidence belongs to a different scope.

Intent diff only drops plan rows. ``webgis_map_product`` still authors whatever
``primary_ref`` the caller passes. This guard compares explicit place tokens
on the ref (descriptor metadata and a bounded feature sample) with
``intent.scope.name``. Missing place evidence is not a mismatch: we do not
invent geography. A city-level token that does not match is.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal, Mapping, Optional, TypedDict

_CITY_KEYS = ("cityname", "city", "place", "scope", "district", "pname", "query")
_FEATURE_KEYS = ("cityname", "city", "district", "adname", "pname")


class ScopeGeographyVerdict(TypedDict):
    verdict: Literal["matched", "mismatched", "unverified"]
    reason: str
    evidence: Literal["name", "bbox", "none"]


def _norm(value: str) -> str:
    return value.strip().replace(" ", "")


def _stem(value: str) -> str:
    text = _norm(value)
    for suffix in ("特别行政区", "自治区", "地区", "市", "省"):
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


def places_compatible(scope_name: str, place: str) -> bool:
    """True when two place strings name the same administrative unit."""
    left = _norm(scope_name)
    right = _norm(place)
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    return _stem(left) == _stem(right) and bool(_stem(left))


def _city_level(token: str, key: str) -> bool:
    if key in ("cityname", "city", "pname", "place", "scope", "query"):
        return True
    return token.endswith(("市", "省"))


def _push(found: list[tuple[str, str]], key: str, value: object) -> None:
    if not isinstance(value, str):
        return
    text = value.strip()
    if not text or len(text) > 64:
        return
    found.append((key, text))


def attach_query_place(result: dict, district: str | None) -> dict:
    """Stamp the query place onto a POI payload so a later city switch can see it.

    Feature properties are not the only place evidence. A reused ref must
    carry the district the query actually asked for.
    """
    place = str(district or "").strip()
    if not place or not isinstance(result, dict):
        return result
    stamped = dict(result)
    stamped.setdefault("district", place)
    return stamped


def place_evidence_from_payload(
    descriptor: Optional[Mapping[str, Any]],
    payload: Optional[Mapping[str, Any]],
) -> dict:
    """Merge descriptor place fields with collection-level query stamps."""
    merged: dict = dict(descriptor or {})
    if not isinstance(payload, Mapping):
        return merged
    for key in ("district", "cityname", "city", "place", "query", "pname", "crs", "bbox"):
        value = payload.get(key)
        if value and not merged.get(key):
            merged[key] = value
    return merged


def collect_place_tokens(
    descriptor: Optional[Mapping[str, Any]],
    features: Optional[Iterable[Any]] = None,
    *,
    feature_limit: int = 24,
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(descriptor, Mapping):
        for key in _CITY_KEYS:
            _push(found, key, descriptor.get(key))
        meta = descriptor.get("metadata")
        if isinstance(meta, Mapping):
            for key in _CITY_KEYS:
                _push(found, key, meta.get(key))
    if features is not None:
        seen = 0
        for feature in features:
            if seen >= feature_limit:
                break
            seen += 1
            if not isinstance(feature, Mapping):
                continue
            props = feature.get("properties")
            if not isinstance(props, Mapping):
                continue
            for key in _FEATURE_KEYS:
                _push(found, key, props.get(key))
    return found


def scope_ref_mismatch(
    scope_name: str,
    *,
    descriptor: Optional[Mapping[str, Any]] = None,
    features: Optional[Iterable[Any]] = None,
) -> Optional[str]:
    """Return a reason code when place evidence contradicts ``scope_name``.

    ``None`` means no contradiction was proved (including "no place evidence").
    """
    scope = _norm(scope_name)
    if not scope:
        return None
    tokens = collect_place_tokens(descriptor, features)
    city_tokens = [(key, text) for key, text in tokens if _city_level(text, key)]
    if not city_tokens:
        return None
    if any(places_compatible(scope, text) for _, text in city_tokens):
        return None
    sample = city_tokens[0][1]
    return f"scope_ref_mismatch:{scope}!={sample}"


_CONTAIN_PAD_DEG = 0.05
_GEOGRAPHIC_TOKENS = (
    "4326", "4490", "CRS84", "CRS:84", "WGS84", "WGS-84", "GCJ02", "GCJ-02", "BD09",
)
_PROJECTED_TOKENS = ("3857", "900913", "4544", "4545", "4546", "4547", "4548", "4549")


def _finite_bbox(value: object) -> Optional[list[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        west, south, east, north = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if not all(item == item and abs(item) != float("inf") for item in (west, south, east, north)):
        return None
    if west > east or south > north:
        return None
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        return None
    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        return None
    return [west, south, east, north]


def _crs_class(descriptor: Optional[Mapping[str, Any]]) -> str:
    """geographic, projected, or absent.

    Degree systems that are not WGS84 still describe lon/lat, including
    GCJ-02 and CGCS2000 (EPSG:4490). An unknown explicit CRS is not compared.
    """
    if not isinstance(descriptor, Mapping):
        return "absent"
    crs = descriptor.get("crs")
    if not isinstance(crs, str) or not crs.strip():
        return "absent"
    text = crs.strip().upper().replace(" ", "")
    if any(token in text for token in _PROJECTED_TOKENS) or ":326" in text or ":327" in text:
        return "projected"
    if any(token in text for token in _GEOGRAPHIC_TOKENS):
        return "geographic"
    return "projected"


def _descriptor_bbox(descriptor: Optional[Mapping[str, Any]]) -> Optional[list[float]]:
    if not isinstance(descriptor, Mapping) or _crs_class(descriptor) == "projected":
        return None
    bbox = _finite_bbox(descriptor.get("bbox"))
    if bbox is not None:
        return bbox
    meta = descriptor.get("metadata")
    if isinstance(meta, Mapping):
        return _finite_bbox(meta.get("bbox"))
    return None


def _walk_coords(coordinates: object, found: list[tuple[float, float]], budget: list[int]) -> None:
    if budget[0] <= 0 or not isinstance(coordinates, list):
        return
    if coordinates and isinstance(coordinates[0], (int, float)) and not isinstance(coordinates[0], bool):
        if len(coordinates) < 2:
            return
        try:
            lon, lat = float(coordinates[0]), float(coordinates[1])
        except (TypeError, ValueError):
            return
        budget[0] -= 1
        found.append((lon, lat))
        return
    for child in coordinates[:64]:
        _walk_coords(child, found, budget)
        if budget[0] <= 0:
            return


def feature_sample_bbox(
    features: Optional[Iterable[Any]],
    *,
    feature_limit: int = 24,
) -> Optional[list[float]]:
    """Bounded lon/lat sample. Out-of-range coordinates are not a geography."""
    if features is None:
        return None
    found: list[tuple[float, float]] = []
    budget = [256]
    seen = 0
    for feature in features:
        if seen >= feature_limit or budget[0] <= 0:
            break
        seen += 1
        if not isinstance(feature, Mapping):
            continue
        geometry = feature.get("geometry")
        if isinstance(geometry, Mapping):
            _walk_coords(geometry.get("coordinates"), found, budget)
    if not found:
        return None
    lons = [item[0] for item in found]
    lats = [item[1] for item in found]
    return _finite_bbox([min(lons), min(lats), max(lons), max(lats)])


def _disjoint(left: list[float], right: list[float]) -> bool:
    return (
        left[2] < right[0] - 1e-6
        or right[2] < left[0] - 1e-6
        or left[3] < right[1] - 1e-6
        or right[3] < left[1] - 1e-6
    )


def _contained(inner: list[float], outer: list[float], pad: float = _CONTAIN_PAD_DEG) -> bool:
    return (
        inner[0] >= outer[0] - pad
        and inner[2] <= outer[2] + pad
        and inner[1] >= outer[1] - pad
        and inner[3] <= outer[3] + pad
    )


def scope_geography_verdict(
    scope_name: str,
    *,
    descriptor: Optional[Mapping[str, Any]] = None,
    features: Optional[Iterable[Any]] = None,
    ref_bbox: Optional[object] = None,
    scope_bbox: Optional[object] = None,
) -> ScopeGeographyVerdict:
    """matched / mismatched / unverified. Missing evidence is not a mismatch.

    Name tokens win. Bbox comparison runs only when no city token exists.
    Disjoint lon/lat boxes reject. Containment matches. Partial overlap stays
    unverified so a national dataset is not invented as a city match.
    """
    scope = _norm(scope_name)
    if not scope:
        return {
            "verdict": "unverified",
            "reason": "scope_name_absent",
            "evidence": "none",
        }
    reason = scope_ref_mismatch(scope_name, descriptor=descriptor, features=features)
    if reason:
        return {"verdict": "mismatched", "reason": reason, "evidence": "name"}
    tokens = collect_place_tokens(descriptor, features)
    if any(_city_level(text, key) for key, text in tokens):
        return {"verdict": "matched", "reason": "", "evidence": "name"}
    crs_class = _crs_class(descriptor)
    if crs_class == "projected":
        return {
            "verdict": "unverified",
            "reason": "scope_unverified",
            "evidence": "none",
        }
    # Absent CRS: trust a stored descriptor bbox (lon/lat by contract) but
    # do not reject from a feature sample alone. A local grid can sit inside
    # the lon/lat numeric range.
    ref = _finite_bbox(ref_bbox) or _descriptor_bbox(descriptor)
    if ref is None and crs_class == "geographic":
        ref = feature_sample_bbox(features)
    scope_box = _finite_bbox(scope_bbox)
    if ref is None or scope_box is None:
        return {
            "verdict": "unverified",
            "reason": "scope_unverified",
            "evidence": "none",
        }
    if _disjoint(ref, scope_box):
        return {
            "verdict": "mismatched",
            "reason": f"scope_bbox_disjoint:{scope}!={ref[0]:.4f},{ref[1]:.4f},{ref[2]:.4f},{ref[3]:.4f}",
            "evidence": "bbox",
        }
    if _contained(ref, scope_box):
        return {"verdict": "matched", "reason": "", "evidence": "bbox"}
    return {
        "verdict": "unverified",
        "reason": "scope_unverified",
        "evidence": "bbox",
    }


def unverified_scope_warning(scope_name: str) -> str:
    return (
        "scope_unverified: 无法用地名或范围框证实 "
        f"primary_ref 属于 {scope_name}，不能把这次组装当成范围已核对。"
    )


def product_scope_geography(
    scope_name: str,
    *,
    descriptor: Optional[Mapping[str, Any]] = None,
    features: Optional[Iterable[Any]] = None,
    scope_bbox_lookup: Optional[Any] = None,
) -> ScopeGeographyVerdict:
    """Product-path verdict. Admin bbox is resolved only when names are absent."""
    tokens = collect_place_tokens(descriptor, features)
    scope_bbox = None
    if scope_name and not any(_city_level(text, key) for key, text in tokens):
        if scope_bbox_lookup is not None:
            try:
                scope_bbox = scope_bbox_lookup(scope_name)
            except Exception:
                scope_bbox = None
    return scope_geography_verdict(
        scope_name,
        descriptor=descriptor,
        features=features,
        scope_bbox=scope_bbox,
    )
