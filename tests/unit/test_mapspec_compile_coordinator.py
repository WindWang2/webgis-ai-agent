"""Tests for CompileCoordinator — the pure-ish compile/validate collaborator.

Demonstrates the testability win from architecture review Candidate #2
(decision ii): validate() is a pure function of a MapSpec dict. No session,
no storage, no fixture setup — pass a dict, assert the result. Contrast with
the store's session-bound validate_mapspec which required a clean_session
fixture and Redis-backed state.
"""
from app.services.mapspec_compile_coordinator import validate


def test_validate_passes_a_well_formed_mapspec():
  mapspec = {
      "sources": {"s1": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}}},
      "layers": [{"id": "l1", "source": "s1", "type": "circle", "paint": {"color": "#f00"}}],
  }
  result = validate(mapspec)
  assert result["success"] is True
  assert result["errors"] == []


def test_validate_flags_missing_sources():
  result = validate({"sources": {}, "layers": []})
  assert result["success"] is False
  assert any(e["code"] == "MISSING_SOURCES" for e in result["errors"])


def test_validate_flags_unknown_layer_source():
  mapspec = {
      "sources": {"s1": {"type": "geojson"}},
      "layers": [{"id": "l1", "source": "nonexistent", "type": "circle"}],
  }
  result = validate(mapspec)
  assert result["success"] is False
  assert any(e["code"] == "INVALID_SOURCE_REF" for e in result["errors"])


def test_validate_flags_non_increasing_stops():
  mapspec = {
      "sources": {"s1": {"type": "geojson"}},
      "layers": [{
          "id": "l1", "source": "s1", "type": "circle",
          "paint": {"color": {"method": "interpolate", "field": "mag", "stops": [[8, "#f00"], [0, "#0f0"]]}},
      }],
  }
  result = validate(mapspec)
  assert result["success"] is False
  assert any(e["code"] == "NON_INCREASING_STOPS" for e in result["errors"])


def test_validate_flags_too_few_stops():
  # Method-aware minimum: a `step` with ZERO stops is degenerate (flagged);
  # a `step` with ONE threshold + default is a valid MapLibre step (NOT flagged).
  mapspec = {
      "sources": {"s1": {"type": "geojson"}},
      "layers": [{
          "id": "l1", "source": "s1", "type": "circle",
          "paint": {"color": {"method": "step", "field": "mag", "default": "#000", "stops": []}},
      }],
  }
  result = validate(mapspec)
  assert result["success"] is False
  assert any(e["code"] == "INVALID_STOPS_COUNT" for e in result["errors"])

  # A single-threshold step is a valid graduated classification — must NOT be flagged.
  valid = {
      "sources": {"s1": {"type": "geojson"}},
      "layers": [{
          "id": "l1", "source": "s1", "type": "circle",
          "paint": {"color": {"method": "step", "field": "mag", "default": "#000", "stops": [[5, "#f00"]]}},
      }],
  }
  ok = validate(valid)
  assert ok["success"] is True, ok["errors"]
