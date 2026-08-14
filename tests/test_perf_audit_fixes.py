"""Regression tests for the performance findings of the master full review.

- PERF-F2: dereferenced payloads are opaque — the resolver must not rebuild
  or re-resolve the stored payload tree (and strings inside payload data
  that merely MATCH aliases must not be dereferenced).
- PERF-F3: oversized tool args bypass the content-addressed cache.
- PERF-F4: profiler null_count is arithmetic and stays correct.
- PERF-F9: the upload sampler extracts samples from a bounded prefix
  without full-parsing the file.
"""
import json
import os
import tempfile

import pytest


@pytest.mark.asyncio
async def test_F2_dereferenced_payload_is_opaque(monkeypatch):
    """A stored payload containing a string that HAPPENS to equal an alias
    must flow through unchanged — the old resolver recursed into resolved
    data and re-dereferenced anything alias-shaped inside it."""
    from app.tools.registry import ToolRegistry

    payload = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"note": "my-alias"},  # string == a live alias
             "geometry": {"type": "Point", "coordinates": [0, 0]}},
        ],
    }

    async def fake_resolve_aliases(session_id, strings):
        return {"my-alias": "ref:other"}

    async def fake_get(session_id, ref):
        # The OUTER argument "my-alias" resolves to the payload; an inner
        # re-resolution would ask for it AGAIN (or return other data).
        return payload if ref == "my-alias" else {"unexpected": ref}

    from app.services.session_data import session_data_manager

    monkeypatch.setattr(session_data_manager, "resolve_aliases", fake_resolve_aliases)
    monkeypatch.setattr(session_data_manager, "get", fake_get)

    reg = ToolRegistry()

    captured = {}

    @reg.tool(name="audit_take", description="x")
    async def take(data: dict = None):
        captured["data"] = data
        return {"success": True}

    res = await reg.dispatch("audit_take", {"data": "my-alias"}, session_id="s-f2")
    assert res.get("success") is True
    # The payload arrived BY the store's object, properties untouched — the
    # alias-shaped property string was NOT re-dereferenced.
    assert captured["data"]["features"][0]["properties"]["note"] == "my-alias"


def test_F3_oversized_args_bypass_cache():
    from app.lib.tool_cache import make_cache_key

    small = make_cache_key("buffer_analysis", {"radius": 100})
    assert small is not None
    big = make_cache_key(
        "buffer_analysis",
        {"coords": [[float(i), float(i)] for i in range(20000)]},
    )
    assert big is None, "oversized args must bypass the content-addressed cache"


def test_F4_profiler_null_count_arithmetic():
    from app.services.spatial_meta_profiler import profile_geojson_source

    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {"type": "Feature", "properties": {"a": 1, "b": None},
             "geometry": {"type": "Point", "coordinates": [0, 0]}},
            {"type": "Feature", "properties": {"a": 2},
             "geometry": {"type": "Point", "coordinates": [1, 1]}},
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [2, 2]}},
        ],
    }
    profile = profile_geojson_source(fc)
    fields = {f["name"]: f for f in profile["fields"]} if isinstance(
        profile.get("fields"), list) else profile.get("fields", {})
    # b is None once and absent twice → 3 nulls; a is absent once → 1 null.
    assert fields["b"]["null_count"] == 3, fields
    assert fields["a"]["null_count"] == 1, fields


def test_F9_prefix_sampler_without_full_parse():
    from app.utils.geojson_prefix_sampler import sample_feature_properties

    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": i},
             "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.001, 39.0]}}
            for i in range(5000)
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False) as f:
        json.dump(fc, f)
        path = f.name
    try:
        got = sample_feature_properties(path, count=3, max_bytes=8192)
        assert got == [{"id": 0}, {"id": 1}, {"id": 2}]
    finally:
        os.unlink(path)


def test_F9_sampler_terminates_on_string_braces_in_header():
    """Review P1-1: a '{' inside a string VALUE in the collection header
    used to send the scanner into an infinite alternation (attacker-
    controlled upload content → worker-thread DoS)."""
    import json
    import tempfile
    import time

    from app.utils.geojson_prefix_sampler import sample_feature_properties

    content = json.dumps({
        "type": "FeatureCollection",
        "name": "foo {bar {baz",  # braces inside a string value
        "features": [
            {"type": "Feature", "properties": {"id": 0},
             "geometry": {"type": "Point", "coordinates": [1, 2]}},
            {"type": "Feature", "properties": {"id": 1},
             "geometry": {"type": "Point", "coordinates": [3, 4]}},
        ],
    })
    with tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        t0 = time.time()
        got = sample_feature_properties(path, count=3)
        dt = time.time() - t0
        assert dt < 2.0, f"sampler did not terminate promptly ({dt:.2f}s)"
        assert got == [{"id": 0}, {"id": 1}]
    finally:
        os.unlink(path)


def test_F9_sampler_handles_ndjson_and_truncated_input():
    import tempfile

    from app.utils.geojson_prefix_sampler import sample_feature_properties

    ndjson = "\n".join(
        json.dumps({"type": "Feature", "properties": {"id": i},
                    "geometry": {"type": "Point", "coordinates": [0, 0]}})
        for i in range(5)
    )
    with tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False) as f:
        f.write(ndjson)
        p1 = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False) as f:
        f.write('{"type": "FeatureColl')
        p2 = f.name
    try:
        assert sample_feature_properties(p1, count=2) == [{"id": 0}, {"id": 1}]
        assert sample_feature_properties(p2, count=2) is None  # no hang, no crash
    finally:
        os.unlink(p1)
        os.unlink(p2)
