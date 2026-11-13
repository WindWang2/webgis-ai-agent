"""#2006: summary slimming must keep per-zone numbers."""
import json

from app.services.llm_result_formatter import MSG_MAX_BYTES, slim_tool_result


def _zonal(zones: int = 2) -> dict:
    features = []
    for index in range(zones):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            "properties": {
                "district": f"区{index}",
                "rate": 100.0 + index,
                "numerator": 420.0 + index,
                "denominator": 2,
            },
        })
    return {
        "success": True,
        "summary": {"rate_kind": "count", "zones": zones},
        "data": {"type": "FeatureCollection", "features": features},
    }


def test_short_zonal_summary_keeps_zone_rates():
    result = _zonal()
    raw = json.dumps(result, ensure_ascii=False)
    assert len(raw.encode("utf-8")) < MSG_MAX_BYTES
    parsed = json.loads(slim_tool_result(result, raw, None))
    assert parsed["summary"] == {"rate_kind": "count", "zones": 2}
    assert parsed["zone_values"] == [
        {"name": "区0", "rate": 100.0, "numerator": 420.0, "denominator": 2},
        {"name": "区1", "rate": 101.0, "numerator": 421.0, "denominator": 2},
    ]
    assert "coordinates" not in json.dumps(parsed["zone_values"])


def test_zonal_overflow_records_omitted_count():
    result = _zonal(20)
    parsed = json.loads(slim_tool_result(result, "", None))
    assert len(parsed["zone_values"]) == 16
    assert parsed["zone_values_omitted"] == 4
    assert parsed["zone_values"][0]["rate"] == 100.0


def test_non_zonal_summary_does_not_invent_zone_values():
    result = {"summary": "找到 12 家商店", "feature_count": 12, "data": {"type": "FeatureCollection", "features": []}}
    parsed = json.loads(slim_tool_result(result, json.dumps(result), None))
    assert "zone_values" not in parsed
    assert parsed["feature_count"] == 12
