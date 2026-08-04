"""Tests for geo_processor/core.py normalization (ADR-0037 Win 5)."""
import json

from app.lib.geo_processor.core import to_feature_collection, safe_parse, _repair_json


def test_fc_passthrough():
    """A FeatureCollection dict passes through unchanged."""
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}}]}
    assert to_feature_collection(fc) is fc


def test_single_feature_wrapped_into_fc():
    """A single Feature dict is wrapped into a 1-feature FeatureCollection."""
    feat = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}}
    out = to_feature_collection(feat)
    assert out["type"] == "FeatureCollection"
    assert out["features"] == [feat]


def test_bare_geometry_wrapped_into_fc():
    """A bare geometry dict (type+coordinates, no Feature wrapper) is wrapped."""
    geom = {"type": "Point", "coordinates": [1, 2]}
    out = to_feature_collection(geom)
    assert out["type"] == "FeatureCollection"
    assert len(out["features"]) == 1
    assert out["features"][0]["geometry"] == geom


def test_features_list_wrapped_into_fc():
    """A bare list of features becomes a FeatureCollection."""
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]}}]
    out = to_feature_collection(feats)
    assert out["type"] == "FeatureCollection"
    assert out["features"] == feats


def test_string_geojson_parsed():
    """A GeoJSON string is parsed into a dict."""
    fc = {"type": "FeatureCollection", "features": []}
    out = to_feature_collection(json.dumps(fc))
    assert out == fc


def test_truncated_json_string_repaired():
    """A truncated JSON string is repaired via safe_parse delegation.

    This is the new behavior from ADR-0037 Win 5: to_feature_collection now
    delegates string parsing to safe_parse, gaining its _repair_json logic.
    Previously it silently returned an empty FC on a truncated string.
    """
    # A complete FC, then truncated mid-feature.
    complete = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"a": 1}}]}
    truncated = json.dumps(complete).rsplit("}", 1)[0]  # drop the final closing brace
    out = to_feature_collection(truncated)
    # safe_parse repairs the missing brace → we get the FC back, not an empty one.
    assert out["type"] == "FeatureCollection"
    assert len(out["features"]) == 1


def test_empty_inputs_yield_empty_fc():
    """None, empty string, and empty dict all yield an empty FeatureCollection."""
    empty_fc = {"type": "FeatureCollection", "features": []}
    assert to_feature_collection(None) == empty_fc
    assert to_feature_collection("") == empty_fc
    assert to_feature_collection({}) == empty_fc


def test_invalid_shape_falls_back_to_empty_fc():
    """A dict with no recognized GeoJSON shape yields an empty FC."""
    assert to_feature_collection({"invalid": "shape"}) == {"type": "FeatureCollection", "features": []}


def test_repair_json_string_quotes_and_braces():
    """Test _repair_json with string quote tracking and escaped quotes."""
    # Complete JSON with braces inside string should not be altered
    valid_with_braces = '{"name": "foo {bar}"}'
    assert _repair_json(valid_with_braces) == valid_with_braces
    assert json.loads(_repair_json(valid_with_braces)) == {"name": "foo {bar}"}

    # Truncated inside string containing brace
    truncated_in_str = '{"name": "foo {bar'
    repaired_in_str = _repair_json(truncated_in_str)
    assert repaired_in_str == '{"name": "foo {bar"}'
    assert json.loads(repaired_in_str) == {"name": "foo {bar"}

    # Escaped quote inside string with brace
    escaped_quote = '{"name": "foo \\" {bar"'
    assert _repair_json(escaped_quote) == '{"name": "foo \\" {bar"}'
    assert json.loads(_repair_json(escaped_quote)) == {"name": 'foo " {bar'}

