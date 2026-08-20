"""
TDD parity tests for issue #670 B4 (raster_capable) + B5 (filterable_fields cap).

B4: raster_capable must be identical across store-time descriptor,
route fast-path, and route fallback (parametrized: raster vs vector FC).
B5: filterable_fields fast-path vs fallback identical, including >100 truncation;
    cap documented in schema docstring with rationale.
"""

import asyncio
import pytest
from app.schemas.ref_descriptor import compute_descriptor
from app.services.session_data import MemorySessionStore


def _make_vector_fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"a": 1}}
        ],
    }


def _make_raster_payload_with_file_path():
    return {"file_path": "/data/raster.tif", "type": "raster"}


def _make_raster_payload_with_path():
    return {"path": "/tmp/a.tif", "type": "raster"}


@pytest.mark.parametrize("payload,expected_raster", [
    (_make_raster_payload_with_file_path(), True),
    (_make_raster_payload_with_path(), True),
    (_make_vector_fc(), False),
])
@pytest.mark.asyncio
async def test_b4_raster_capable_parity_store_fastpath_fallback(payload, expected_raster):
    """
    B4: same ref yields identical raster_capable via store-time descriptor,
    route fast-path (descriptor seam), and route fallback (_compute_descriptor_fallback).
    """
    # 1. store-time descriptor (compute_descriptor)
    desc = compute_descriptor("ref:b4", payload)
    # RefDescriptor must expose raster_capable
    assert hasattr(desc, "raster_capable"), "RefDescriptor must have raster_capable field"
    assert desc.raster_capable is expected_raster, f"compute_descriptor raster_capable mismatch: got {desc.raster_capable}, expected {expected_raster}"
    # to_dict/from_dict round-trip
    d = desc.to_dict()
    assert "raster_capable" in d, "to_dict must include raster_capable"
    assert d["raster_capable"] is expected_raster
    from app.schemas.ref_descriptor import RefDescriptor
    restored = RefDescriptor.from_dict(d)
    assert restored.raster_capable is expected_raster

    # 2. route fast-path via MemorySessionStore seam
    store = MemorySessionStore()
    sid = "b4_sid"
    ref_id = await store.store(sid, payload, prefix="data")
    seam = await store.get_ref_descriptor_authorized(sid, ref_id)
    assert seam.success, f"seam failed: {seam.error}"
    assert "raster_capable" in seam.data, "seam descriptor must include raster_capable"
    assert seam.data["raster_capable"] is expected_raster, f"seam raster_capable {seam.data['raster_capable']} != {expected_raster}"
    assert seam.data["raster_capable"] == desc.raster_capable

    # 3. route fallback path
    from app.api.routes.layer import _compute_descriptor_fallback
    fallback = await asyncio.to_thread(_compute_descriptor_fallback, payload)
    assert fallback["raster_capable"] is expected_raster, f"fallback raster_capable {fallback['raster_capable']} != {expected_raster}"
    # parity
    assert seam.data["raster_capable"] == fallback["raster_capable"] == desc.raster_capable

    # 4. HTTP route fast-path vs fallback parity (full route integration)
    # Fast-path is already asserted via seam; now verify fallback route via deleting descriptor
    from app.api.routes.layer import get_layer_descriptor
    # Simulate route-level fallback by deleting descriptor but keeping payload
    store._descriptors[sid].pop(ref_id, None)
    # Recompute via fallback helper should match store-time
    fb2 = await asyncio.to_thread(_compute_descriptor_fallback, payload)
    assert fb2["raster_capable"] is expected_raster


def test_b4_route_fastpath_serves_stored_raster_capable():
    """Route get_layer_descriptor fast-path must serve stored raster_capable, not hardcoded False."""
    import app.api.routes.layer as layer_mod
    src = open(layer_mod.__file__).read()
    # Fast-path return dict must read from descriptor, not hardcode False
    assert '"raster_capable": False' not in src, \
        "route fast-path still hardcodes raster_capable False (should read from descriptor)"
    # Ensure fast-path references descriptor raster_capable (stored value)
    assert 'descriptor["raster_capable"]' in src or 'descriptor.get("raster_capable"' in src, \
        "fast-path must serve descriptor['raster_capable'] or descriptor.get('raster_capable')"
    # The fallback detection logic should be shared — just ensure fallback still uses file_path/path check
    assert '"file_path" in data' in src or "'file_path' in data" in src or '"file_path"' in src


# ── B5: filterable_fields cap parity + docstring ──

def test_b5_filterable_fields_cap_documented():
    """B5: 100-key cap must be documented in RefDescriptor/collect_filterable_fields docstring with rationale."""
    import app.schemas.ref_descriptor as m
    # Check RefDescriptor docstring
    rd_doc = (m.RefDescriptor.__doc__ or "")
    cf_doc = (m.collect_filterable_fields.__doc__ or "")
    combined = rd_doc + "\n" + cf_doc
    # Must mention cap, rationale: bounded SSE payload, whitelist is advisory for honest ack
    assert "100" in combined, "docstring must mention 100-key cap"
    # rationale: bounded SSE payload
    low = combined.lower()
    assert "bounded" in low and "sse" in low, "docstring must mention 'bounded SSE payload' rationale"
    assert "payload" in low, "docstring must mention payload"
    # whitelist is advisory for honest ack
    assert ("advisory" in low and "honest" in low) or ("advisory" in low and "ack" in low), \
        "docstring must mention 'advisory' + 'honest'/'ack' rationale (whitelist is advisory for honest ack)"


@pytest.mark.asyncio
async def test_b5_filterable_fields_parity_including_truncation():
    """B5: fast-path and fallback return identical filterable_fields, including truncation >100."""
    from app.api.routes.layer import _compute_descriptor_fallback

    # Normal case: few keys
    fc_small = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"a": 1, "b": 2}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]}, "properties": {"c": 3}},
        ],
    }
    desc_small = compute_descriptor("ref:b5-small", fc_small)
    fb_small = await asyncio.to_thread(_compute_descriptor_fallback, fc_small)
    store = MemorySessionStore()
    sid = "b5_sid_small"
    ref_small = await store.store(sid, fc_small, prefix="data")
    seam_small = await store.get_ref_descriptor_authorized(sid, ref_small)
    assert seam_small.success
    # All three must be identical
    assert desc_small.filterable_fields == fb_small["filterable_fields"] == seam_small.data["filterable_fields"]
    assert seam_small.data["filterable_fields"] == ["a", "b", "c"]

    # Truncation case: >100 distinct keys
    # Create 150 distinct keys spread across features
    many_keys = [f"key_{i:03d}" for i in range(150)]
    features = []
    # Distribute keys across 5 features, 30 keys each, plus overlap to ensure 150 unique
    for i in range(5):
        props = {k: i for k in many_keys[i*30:(i+1)*30]}
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [i, i]}, "properties": props})
    fc_big = {"type": "FeatureCollection", "features": features}
    # Direct helper
    from app.schemas.ref_descriptor import collect_filterable_fields
    direct = collect_filterable_fields(fc_big["features"])
    assert direct is not None and len(direct) == 100, f"direct helper should truncate to 100, got {len(direct) if direct else None}"
    assert direct == sorted(many_keys)[:100], "truncation must be alphabetical sorted first 100"

    desc_big = compute_descriptor("ref:b5-big", fc_big)
    fb_big = await asyncio.to_thread(_compute_descriptor_fallback, fc_big)
    # Store-time descriptor vs fallback
    assert desc_big.filterable_fields == fb_big["filterable_fields"] == direct
    # Seam fast-path
    ref_big = await store.store(sid, fc_big, prefix="data")
    seam_big = await store.get_ref_descriptor_authorized(sid, ref_big)
    assert seam_big.success
    assert seam_big.data["filterable_fields"] == direct
    # All three identical
    assert seam_big.data["filterable_fields"] == desc_big.filterable_fields == fb_big["filterable_fields"]
    # Also verify fallback after descriptor deletion still matches
    store._descriptors[sid].pop(ref_big, None)
    fb_big2 = await asyncio.to_thread(_compute_descriptor_fallback, fc_big)
    assert fb_big2["filterable_fields"] == direct
