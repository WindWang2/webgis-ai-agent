"""Tests for the MapSpec source-shape module (ADR-0008).

Pure-function tests over bare source dicts — mirrors the `view_has_center`
block in test_mapspec_store.py. No session fixtures: the module owns only
shape knowledge, not storage.
"""
from typing import Any, Dict

from app.services.mapspec_source import (
    profile_data,
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
