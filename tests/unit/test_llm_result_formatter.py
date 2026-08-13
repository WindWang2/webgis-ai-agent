"""Unit tests for Decoupled LLM Result Formatter."""
import json
from app.services.llm_result_formatter import (
    is_error_dict,
    wrap_error_dict_for_llm,
    slim_tool_result,
    slim_event_result,
    _truncate_value,
    _truncate_properties,
    VALUE_MAX_CHARS,
)


def test_is_error_dict_detection():
    err_dict = {"success": False, "code": "RESOURCE_NOT_FOUND", "message": "File missing"}
    ok_dict = {"success": True, "data": 42}
    assert is_error_dict(err_dict) is True
    assert is_error_dict(ok_dict) is False


def test_wrap_error_dict_for_llm():
    err = {
        "success": False,
        "code": "INVALID_GEOJSON",
        "message": "Polygon is self-intersecting",
        "correction_hint": "Try fixing geometry via buffer(0)",
    }
    wrapped = wrap_error_dict_for_llm("webgis_layer_upsert", err)
    assert "webgis_layer_upsert" in wrapped
    assert "Polygon is self-intersecting" in wrapped
    assert "buffer(0)" in wrapped


def test_slim_tool_result_summary_pass_through():
    res = {
        "summary": "Processed 100 points",
        "bbox": [10.0, 20.0, 30.0, 40.0],
        "feature_count": 100,
    }
    slimmed = slim_tool_result(res, json.dumps(res), session_geojson_ref="ref:123")
    parsed = json.loads(slimmed)

    assert parsed["summary"] == "Processed 100 points"
    assert parsed["ref_id"] == "ref:123"
    assert parsed["feature_count"] == 100


def test_slim_event_result_strips_large_geometries():
    res = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}}],
        "layer_id": "test-layer",
    }
    event_res = slim_event_result(res)

    assert "features" not in event_res
    assert event_res["layer_id"] == "test-layer"
    assert "_streaming_note" in event_res


def test_cartographic_failure_survives_slimming_without_mapspec_data():
    res = {
        "summary": "Layer upserted",
        "mapspec_fingerprint": "carto-sha256:abc",
        "mapspec": {
            "version": "1.0",
            "sources": {
                "s": {
                    "type": "geojson",
                    "inlineData": {
                        "type": "FeatureCollection",
                        "features": [{"secret": "must-not-stream"}] * 100,
                    },
                }
            },
            "layers": [{"id": "result", "source": "s", "type": "circle"}],
        },
        "cartographic_review": {
            "stage": "desired_state",
            "status": "failed_unrepairable",
            "termination_reason": "no_auto_safe_repair",
            "repair_count": 0,
            "review": {
                "checks": [{
                    "rule": "THEMATIC_LEGEND",
                    "status": "fail",
                    "message": "legend missing",
                    "repairability": "not_repairable",
                    "evidence": {"legend_present": False},
                }],
            },
        },
    }

    llm = json.loads(slim_tool_result(res, json.dumps(res), None))
    event = slim_event_result(res)

    assert llm["cartographic_review"]["status"] == "failed_unrepairable"
    assert llm["cartographic_review"]["checks"][0]["rule"] == "THEMATIC_LEGEND"
    assert "mapspec" not in llm
    assert "inlineData" not in event["mapspec"]
    assert "must-not-stream" not in json.dumps(event)


def test_truncate_helpers():
    long_str = "a" * (VALUE_MAX_CHARS + 50)
    truncated = _truncate_value(long_str)
    assert len(truncated) == VALUE_MAX_CHARS
    assert truncated.endswith("…")

    props = {f"k{i}": f"v{i}" for i in range(30)}
    truncated_props = _truncate_properties(props, max_keys=20)
    assert len(truncated_props) == 21
    assert "__more_keys__" in truncated_props
    assert truncated_props["__more_keys__"] == 10
