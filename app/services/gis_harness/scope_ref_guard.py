"""Refuse a product ref whose place evidence belongs to a different scope.

Intent diff only drops plan rows. ``webgis_map_product`` still authors whatever
``primary_ref`` the caller passes. This guard compares explicit place tokens
on the ref (descriptor metadata and a bounded feature sample) with
``intent.scope.name``. Missing place evidence is not a mismatch: we do not
invent geography. A city-level token that does not match is.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

_CITY_KEYS = ("cityname", "city", "place", "scope", "district", "pname", "query")
_FEATURE_KEYS = ("cityname", "city", "district", "adname", "pname")


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
    for key in ("district", "cityname", "city", "place", "query", "pname"):
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
