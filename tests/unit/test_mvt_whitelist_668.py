"""TDD for #668: MVT encoder whitelist + descriptor filterable_fields + filter ack."""

from app.schemas.ref_descriptor import collect_filterable_fields, compute_descriptor
from app.services.mvt import encode_tile


def test_compute_descriptor_exposes_filterable_fields():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"category": "A", "pop": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]}, "properties": {"category": "B", "pop": 20, "extra": "x"}},
        ],
    }
    desc = compute_descriptor("ref:whitelist-1", fc)
    # Should expose distinct property keys for filter whitelist
    fields = getattr(desc, "filterable_fields", None) or getattr(desc, "property_keys", None) or (desc.to_dict().get("filterable_fields") if hasattr(desc, "to_dict") else None)
    assert fields is not None, "descriptor must expose filterable_fields/property_keys"
    assert "category" in fields
    assert "pop" in fields


def test_filterable_fields_parity_store_vs_fallback():
    """Both descriptor paths must use the shared helper and produce identical whitelist."""
    from app.api.routes.layer import _compute_descriptor_fallback

    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"a": 1, "b": 2}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]}, "properties": {"b": 3, "c": 4, "a": 5}},
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, "properties": {"c": 5, "d": 6}},
        ],
    }
    # store-time path
    desc = compute_descriptor("ref:parity", fc)
    store_fields = desc.filterable_fields
    # fallback path (sync helper)
    fallback = _compute_descriptor_fallback(fc)
    fallback_fields = fallback.get("filterable_fields")
    # direct helper
    direct = collect_filterable_fields(fc["features"])

    assert store_fields == fallback_fields == direct
    assert store_fields == ["a", "b", "c", "d"]

    # empty / non-FC → None
    assert collect_filterable_fields([]) is None
    assert collect_filterable_fields(None) is None  # type: ignore
    assert compute_descriptor("ref:empty", {"type": "FeatureCollection", "features": []}).filterable_fields is None


def test_mvt_encoder_preserves_filter_fields_in_tile():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"category": "A", "pop": 123}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]}, "properties": {"category": "B", "pop": 456}},
        ],
    }
    tile = encode_tile(fc, 0, 0, 0)
    # tile must be non-empty and contain the encoded keys (simple check: keys appear in tile bytes)
    assert tile != b""
    assert b"category" in tile
    assert b"pop" in tile
    # values also encoded
    assert b"A" in tile or b"B" in tile
