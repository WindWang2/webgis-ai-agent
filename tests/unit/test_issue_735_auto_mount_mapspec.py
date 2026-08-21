"""#735: auto-mounted analysis layers must ride the step_result with the
committed MapSpec — composeLiveMapSpec takes layers ONLY from committed.layers,
so a source-only runtime_patch rendered nothing in any session holding a
committed spec (agent narrated success, map unchanged)."""
import pytest

import app.services.tool_dispatch_service as tds
from app.services.mapspec_store import mapspec_store


def _fc(n: int = 30) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
             "properties": {"name": f"p{i}"}}
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_author_result_carries_mapspec_doc(monkeypatch):
    fc = _fc()
    descriptor = {"feature_count": 30, "bbox": [104.0, 30.6, 104.29, 30.6], "geometry_types": ["Point"]}

    committed_spec = {
        "version": "1.0",
        "layers": [
            {"id": "result-prev", "source": "src-prev", "type": "circle", "paint": {}},
            {"id": "result-new", "source": "src-new", "type": "circle", "paint": {}},
        ],
        "sources": {
            "src-prev": {"type": "geojson", "ref": "ref:geojson:prev"},
            "src-new": {"type": "geojson", "ref": "ref:geojson:new"},
        },
    }

    async def fake_upsert(sid, layer, source_data):
        return {"success": True, "layer": layer, "mapspec": committed_spec,
                "mapspec_fingerprint": "fp-735"}

    monkeypatch.setattr(mapspec_store, "layer_upsert", fake_upsert)

    service = tds.ToolDispatchService.__new__(tds.ToolDispatchService)
    result = await service._author_display_result(
        session_id="s-735",
        tool_call_id="tc-735",
        tool_name="query_osm_poi",
        result={"type": "FeatureCollection", "features": fc["features"]},
        target_data=fc,
        result_ref="ref:geojson:test-735",
        descriptor=descriptor,
    )
    assert result.get("success") is True
    doc = result.get("mapspec")
    assert isinstance(doc, dict), "step_result must carry the committed MapSpec doc"
    assert any(ly.get("id") == "result-new" for ly in doc.get("layers", []))
    # and the SSE projection preserves the layer ids the frontend composes from
    from app.services.tool_dispatch_service import slim_event_result
    slim = slim_event_result(result)
    slim_doc = slim.get("mapspec")
    assert isinstance(slim_doc, dict), "slim_event_result keeps a (projected) mapspec"
    assert any(ly.get("id") == "result-new" for ly in slim_doc.get("layers", []))
