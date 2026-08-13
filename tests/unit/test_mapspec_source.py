"""Tests for the MapSpec source-shape module (ADR-0008).

Pure-function tests over bare source dicts — mirrors the `view_has_center`
block in test_mapspec_store.py. No session fixtures: the module owns only
shape knowledge, not storage.
"""
from typing import Any, Dict

from app.services.mapspec_source import (
    is_raster_entry,
    profile_data,
    raster_bounds,
    raster_image_ref,
    ref,
    store_data,
)


# ─── store_data: the shared dict→inlineData / str→url classifier ────────────


def test_store_data_dict_payload_writes_inline_data():
  """A dict payload lands under `inlineData` (the doc-carried path)."""
  entry: Dict[str, Any] = {"type": "geojson"}
  payload = {"type": "FeatureCollection", "features": []}

  store_data(entry, payload)

  assert entry["inlineData"] == payload
  assert "url" not in entry


def test_store_data_string_payload_writes_url():
  """A string payload lands under `url` (which is overloaded: real URL, ref:,
  or bare path — the classifier does not distinguish; that's the caller's policy)."""
  entry: Dict[str, Any] = {"type": "geojson"}

  store_data(entry, "https://example.com/data.geojson")
  assert entry["url"] == "https://example.com/data.geojson"
  assert "inlineData" not in entry


def test_store_data_ref_cursor_string_lands_in_url():
  """A ref: cursor string is string-shaped, so it lands in `url`.

  This is the known overload (ADR-0008) — the classifier is policy-free; it
  does not promote `ref` to first-class. checkpoint.ref() reads it back out.
  """
  entry: Dict[str, Any] = {"type": "geojson"}
  store_data(entry, "ref:geojson-abc123")
  assert entry["url"] == "ref:geojson-abc123"


def test_store_data_string_replacement_clears_stale_raster_type():
  entry: Dict[str, Any] = {
      "type": "raster", "imageRef": "ref:raster-old", "bounds": [0, 0, 1, 1]
  }

  store_data(entry, "ref:geojson-new")

  assert entry["type"] == "geojson"
  assert entry["url"] == "ref:geojson-new"


def test_store_data_none_is_a_noop():
  """None is 'no data supplied' — the entry is left untouched.

  This lets a caller pass through `source_data or None` without a separate
  guard: store_data never invents keys for missing input.
  """
  entry: Dict[str, Any] = {"type": "geojson"}
  store_data(entry, None)
  assert entry == {"type": "geojson"}


def test_store_data_is_unconditional_no_idempotency():
  """store_data itself overwrites unconditionally — the idempotency guard
  (skip if inlineData/url already present) is layer_upsert's policy, not the
  classifier's. Keeps the primitive single-purpose (ADR-0008, 'house only')."""
  entry: Dict[str, Any] = {"type": "geojson", "inlineData": {"old": True}}
  store_data(entry, {"new": True})
  assert entry["inlineData"] == {"new": True}


# ─── profile_data: the inlineData → url → dataPath fallback read ────────────


def test_profile_data_prefers_inline_data():
  """inlineData wins over url/dataPath — it's the materialized payload the
  profiler can actually inspect."""
  entry = {"inlineData": {"type": "FeatureCollection"}, "url": "http://x", "dataPath": "p"}
  assert profile_data(entry) == {"type": "FeatureCollection"}


def test_profile_data_falls_back_to_url():
  entry = {"url": "https://example.com/data.geojson"}
  assert profile_data(entry) == "https://example.com/data.geojson"


def test_profile_data_falls_back_to_data_path():
  entry = {"dataPath": "ref:abc"}
  assert profile_data(entry) == "ref:abc"


def test_profile_data_returns_none_when_empty():
  assert profile_data({"type": "geojson"}) is None
  assert profile_data({}) is None


# ─── ref: the url | dataPath-as-cursor lookup ──────────────────────────────


def test_ref_reads_url_as_cursor_carrier():
  """ref() returns the string a checkpoint should materialize — today that's
  `url` or `dataPath`. Known overload (ADR-0008): real URLs share the field."""
  assert ref({"url": "ref:geojson-abc"}) == "ref:geojson-abc"
  assert ref({"url": "https://example.com/data.geojson"}) == "https://example.com/data.geojson"


def test_ref_reads_data_path_as_fallback():
  assert ref({"dataPath": "ref:store-xyz"}) == "ref:store-xyz"


def test_ref_prefers_url_over_data_path():
  """Matches checkpoint_store's current `url or dataPath` ordering exactly."""
  assert ref({"url": "ref:a", "dataPath": "ref:b"}) == "ref:a"


def test_ref_returns_none_when_absent():
  assert ref({"type": "geojson"}) is None
  assert ref({}) is None


def test_ref_ignores_inline_data():
  """A ref cursor is never an inline dict — inlineData is the materialized
  payload, not a reference. ref() must not return it."""
  assert ref({"inlineData": {"type": "FeatureCollection"}}) is None


# ─── raster source entries (ADR-0011) ──────────────────────────────────────
# A raster source is {type:"raster", imageRef, bounds, imageSize}. imageRef is
# an opaque ref:-style cursor pointing at the PNG on disk; bounds is [w,s,e,n]
# WGS84; imageSize is [w,h] px. Distinct from the geojson inlineData/url shape.


_RASTER_PAYLOAD = {"array": [[0.1, 0.5], [0.9, 0.2]], "bounds": [100.0, 20.0, 101.0, 21.0]}


def test_store_data_raster_payload_marks_type_and_carries_ref():
  """store_data with a raster payload (carries 'bounds') sets type:"raster" +
  imageRef + bounds + imageSize, NOT inlineData/url. The caller supplies the
  already-resolved imageRef (a path/ref string) since the array→PNG render
  happens upstream in raster_cartography_converter."""
  entry: Dict[str, Any] = {}
  store_data(entry, {"imageRef": "ref:raster-abc", "bounds": [100.0, 20.0, 101.0, 21.0],
                     "imageSize": [256, 256]})
  assert entry["type"] == "raster"
  assert entry["imageRef"] == "ref:raster-abc"
  assert entry["bounds"] == [100.0, 20.0, 101.0, 21.0]
  assert entry["imageSize"] == [256, 256]
  assert "inlineData" not in entry and "url" not in entry


def test_is_raster_entry_true_for_raster_source():
  assert is_raster_entry({"type": "raster", "imageRef": "ref:x", "bounds": [0, 0, 1, 1]}) is True


def test_is_raster_entry_false_for_geojson():
  assert is_raster_entry({"type": "geojson"}) is False
  assert is_raster_entry({"inlineData": {}}) is False
  assert is_raster_entry({}) is False


def test_raster_image_ref_reads_imageref():
  assert raster_image_ref({"type": "raster", "imageRef": "ref:raster-abc"}) == "ref:raster-abc"


def test_raster_image_ref_none_when_absent():
  assert raster_image_ref({"type": "geojson"}) is None
  assert raster_image_ref({}) is None


def test_raster_bounds_reads_bounds():
  assert raster_bounds({"type": "raster", "bounds": [100.0, 20.0, 101.0, 21.0]}) == [100.0, 20.0, 101.0, 21.0]


def test_raster_bounds_none_when_absent():
  assert raster_bounds({"type": "raster"}) is None
  assert raster_bounds({}) is None


def test_ref_reads_raster_imageref_as_fallback():
  """Checkpoint materialization: a raster source's cursor lives in imageRef.
  ref() returns it as a fallback so the checkpoint can materialize the PNG,
  mirroring how it materializes geojson ref: cursors today."""
  assert ref({"type": "raster", "imageRef": "ref:raster-abc"}) == "ref:raster-abc"


def test_profile_data_skips_raster_entries():
  """profile_data is for the GeoJSON profiler; a raster entry carries no
  GeoJSON to profile, so it returns None (the caller checks is_raster_entry
  first and skips profiling)."""
  assert profile_data({"type": "raster", "imageRef": "ref:x", "bounds": [0, 0, 1, 1]}) is None
