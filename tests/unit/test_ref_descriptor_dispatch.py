"""Regression guards for the adversarial-review findings on ref_descriptor delivery.

Findings fixed (Large Map Performance V3, review round 2):

P1-1: ``pi_event_mapper.py`` called ``asyncio.run(...)`` from inside the
      already-running FastAPI event loop (via the Pi bridge's async generator),
      which always raises ``RuntimeError`` and was silently swallowed — the
      descriptor was NEVER attached on the Pi path, so every large layer
      downloaded the full GeoJSON regardless of the V3 optimization.
      Fix: compute the descriptor once inside ``ToolDispatchService.dispatch``
      (already async) and carry it on ``ToolDispatchResult.ref_descriptor``;
      both ``execution_engine.py`` and ``pi_event_mapper.py`` now just read the
      attribute, no extra event-loop-unsafe calls.

P1-2: ``mvt_capable`` was computed as "has any Point feature", which predates
      the Phase 4 MVT geometry expansion (Line/Polygon). A 100k-feature
      LineString-only layer got ``mvt_capable=False`` even though the encoder
      now serves LineString tiles — and the frontend was ignoring the field
      entirely, so the bug was double-masked. Fixed both: descriptor now
      reports ``mvt_capable`` for any vector geometry type, and the frontend
      decision (``use-sse-stream.ts`` / ``adapter.ts``) now actually checks it.
"""
import asyncio


from app.schemas.ref_descriptor import compute_descriptor
from app.services.tool_dispatch_service import ToolDispatchResult


def _line_fc(n=6000):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[116.0 + i * 0.001, 39.9], [116.0 + i * 0.001 + 0.01, 39.91]],
                },
                "properties": {"id": i},
            }
            for i in range(n)
        ],
    }


def _polygon_fc(n=100):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [116.0 + i * 0.01, 39.9],
                            [116.01 + i * 0.01, 39.9],
                            [116.01 + i * 0.01, 39.91],
                            [116.0 + i * 0.01, 39.9],
                        ]
                    ],
                },
                "properties": {"id": i},
            }
            for i in range(n)
        ],
    }


# ---------------------------------------------------------------- P1-2: mvt_capable


def test_mvt_capable_true_for_large_linestring_fc():
    """A 6000-feature LineString-only layer must be tile-capable (encoder supports lines)."""
    desc = compute_descriptor("ref:test-lines", _line_fc(6000))
    assert desc.feature_count == 6000
    assert desc.point_count == 0
    assert "LineString" in desc.geometry_types
    assert desc.mvt_capable is True, (
        "LineString FC must be mvt_capable=True; the encoder supports lines (Phase 4)"
    )


def test_mvt_capable_true_for_polygon_fc():
    desc = compute_descriptor("ref:test-polys", _polygon_fc(100))
    assert desc.mvt_capable is True
    assert "Polygon" in desc.geometry_types


def test_mvt_capable_false_for_empty_fc():
    empty = {"type": "FeatureCollection", "features": []}
    desc = compute_descriptor("ref:test-empty", empty)
    assert desc.feature_count == 0
    assert desc.mvt_capable is False


def test_mvt_capable_false_for_non_fc_data():
    """Raster / non-geometry ref data must not claim mvt_capable."""
    desc = compute_descriptor("ref:test-raster", {"type": "heatmap_raster", "file_path": "/tmp/x.tif"})
    assert desc.feature_count == 0
    assert desc.mvt_capable is False


def test_bbox_computed_for_linestring_and_polygon():
    """Regression: bbox previously only scanned Point coordinates."""
    desc_lines = compute_descriptor("ref:test-lines-bbox", _line_fc(10))
    assert desc_lines.bbox is not None
    assert len(desc_lines.bbox) == 4

    desc_polys = compute_descriptor("ref:test-polys-bbox", _polygon_fc(10))
    assert desc_polys.bbox is not None
    assert len(desc_polys.bbox) == 4


# ---------------------------------------------------------------- P1-1: descriptor delivery


def test_tool_dispatch_result_carries_ref_descriptor_field():
    """ToolDispatchResult must have a ref_descriptor attribute (default None)."""
    result = ToolDispatchResult(
        status="ok",
        llm_payload="payload",
        slim_event={},
        geojson_ref=None,
        raw_result={},
        error_msg=None,
    )
    assert hasattr(result, "ref_descriptor")
    assert result.ref_descriptor is None


def test_dispatch_computes_descriptor_without_async_run_from_running_loop():
    """The descriptor must be computable from an async context WITHOUT asyncio.run
    (which raises when called from inside an already-running event loop, exactly
    the Pi-bridge calling context this regression guards against)."""
    from app.services.session_data import MemorySessionStore

    async def _scenario():
        store = MemorySessionStore()
        ref_id = await store.store("sess", _line_fc(6000), prefix="geojson")
        # This is the exact call tool_dispatch_service.dispatch() now makes,
        # awaited directly — no asyncio.run() wrapper, no RuntimeError risk.
        descriptor = await store.get_ref_descriptor("sess", ref_id)
        return descriptor

    descriptor = asyncio.run(_scenario())
    assert descriptor is not None
    assert descriptor["feature_count"] == 6000
    assert descriptor["mvt_capable"] is True


def test_pi_event_mapper_no_longer_imports_asyncio_run():
    """Static guard: asyncio.run must not appear in pi_event_mapper.py.

    Prevents reintroducing the always-raising, silently-swallowed asyncio.run()
    call inside the Pi bridge's already-running event loop.
    """
    import inspect
    import app.services.chat.pi_event_mapper as mod

    source = inspect.getsource(mod)
    assert "asyncio.run(" not in source, (
        "pi_event_mapper.py must not call asyncio.run() — it always runs inside "
        "the FastAPI event loop (via the Pi bridge's async generator) and would "
        "raise RuntimeError, silently disabling ref_descriptor delivery."
    )
