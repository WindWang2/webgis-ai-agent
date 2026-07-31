"""Tests for MapSpecView and the view_has_center predicate (#3).

Two layers:
- MapSpecView / view_has_center are pure — fast unit tests, no session fixture.
- The auto-view regression lives in test_mapspec_store (needs a session).
"""
from app.services.mapspec_view import MapSpecView, view_has_center, view_from_dict


# ─── MapSpecView model ────────────────────────────────────────────────────────

def test_unset_view_is_not_set():
  v = MapSpecView()
  assert v.center is None
  assert v.is_set() is False
  assert v.center_is_set() is False


def test_explicit_origin_counts_as_set():
  # The bug: center == [0.0, 0.0] used to mean "unset". Now it's a real value.
  v = MapSpecView(center=[0.0, 0.0], zoom=2.0)
  assert v.center_is_set() is True
  assert v.is_set() is True


def test_to_dict_omits_unset_by_default():
  v = MapSpecView(center=[1.0, 2.0])  # zoom/pitch/bearing unset
  d = v.to_dict()
  assert d == {"center": [1.0, 2.0]}
  assert "zoom" not in d


def test_view_from_dict_tolerates_missing():
  assert view_from_dict(None).is_set() is False
  assert view_from_dict({}).is_set() is False
  assert view_from_dict({"center": [5.0, 5.0]}).center == [5.0, 5.0]


# ─── view_has_center predicate (the auto-view gate) ───────────────────────────

def test_view_has_center_false_when_absent():
  assert view_has_center({}) is False
  assert view_has_center({"view": {}}) is False
  assert view_has_center({"view": {"zoom": 5.0}}) is False


def test_view_has_center_false_when_none():
  # Defensive: a center key carrying None is treated as unset.
  assert view_has_center({"view": {"center": None}}) is False


def test_view_has_center_true_for_origin():
  # The regression: an explicitly-set [0.0, 0.0] must count as set.
  assert view_has_center({"view": {"center": [0.0, 0.0]}}) is True
  assert view_has_center({"view": {"center": [120.0, 30.0]}}) is True
