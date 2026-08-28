"""MapSpec copy-on-write correctness + cost tests (Large Map Performance V3).

Two properties must hold simultaneously:

1. COST: a small mutation (SetView / SetLayout / RemoveLayer) must not copy the
   sources dict. Before V3 every mutation did two full ``copy.deepcopy`` of the
   whole spec, so a 50MB inline GeoJSON source was duplicated twice for a
   view-only change. The tests assert the candidate SHARES the source payload
   object with the prior spec (identity), which is what makes the cost O(1).

2. CORRECTNESS: the candidate must never alias the prior spec on any branch it
   mutates, otherwise a failed mutation would leave the prior state already
   modified and rollback would be a silent no-op.
"""
import asyncio
import copy
from pathlib import Path
import tempfile

import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    RemoveLayerIntent,
    SetLayoutIntent,
    SetViewIntent,
    UpsertLayerIntent,
)

LARGE_FEATURE_COUNT = 2000


def _large_fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.0 + i * 1e-5, 39.0]},
                "properties": {"idx": i},
            }
            for i in range(LARGE_FEATURE_COUNT)
        ],
    }


def _spec_with_large_source():
    return {
        "version": "1.0",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"big": {"type": "geojson", "inlineData": _large_fc()}},
        "layers": [{"id": "l1", "type": "circle", "source": "big"}],
        "layout": {"legend": {"visible": True, "position": "top-right"}, "controls": []},
        "thresholds": {"maxFeatures": 50000, "timeoutMs": 30000},
    }


class _FakeStore:
    """MapSpecStore double: keeps the spec in memory, records saves.

    Returns the SAME object from get_mapspec (the in-memory session store does
    exactly this), which is the aliasing hazard the COW candidate must survive.
    """

    def __init__(self, spec):
        self.spec = spec
        self.saved = []

    async def get_mapspec(self, session_id, **_kw):
        return self.spec

    async def save_mapspec(self, session_id, mapspec, **_kw):
        self.saved.append(mapspec)
        self.spec = mapspec
        return {"mapspec": mapspec}

    def get_session_dir(self, session_id):
        return Path(tempfile.mkdtemp())


class _FakeSDM:
    async def get_map_state(self, session_id):
        return {"layers": []}

    async def set_map_state(self, session_id, key, value, seq=None):
        return True

    async def update_layer_in_state(self, session_id, layer_id, updates):
        return None

    async def remove_layer_from_state(self, session_id, layer_id):
        return None

    async def get(self, session_id, ref):
        return None


class _FakeLockCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _FakeLockReg:
    def lock(self, session_id, **_kw):
        return _FakeLockCtx()


async def _fake_checkpoint(mapspec, session_dir, sdm, checkpoint_id=None):
    return {"checkpoint_id": "ckpt-test", "ref_count": 0}


@pytest.fixture
def engine_and_prior(monkeypatch):
    """Engine wired to a fake store + no-op session_data_manager/checkpoint."""
    prior = _spec_with_large_source()
    engine = MapSpecLifecycleEngine()
    engine.store = _FakeStore(prior)

    import app.services.mapspec.lifecycle_engine as le

    monkeypatch.setattr(le, "session_data_manager", _FakeSDM())
    monkeypatch.setattr(le, "create_checkpoint", _fake_checkpoint)
    monkeypatch.setattr(le, "session_lock_registry", _FakeLockReg())
    return engine, prior


def _source_payload(spec):
    return spec["sources"]["big"]["inlineData"]


# ---------------------------------------------------------------- cost


def test_set_view_does_not_copy_source_payload(engine_and_prior):
    """SetView cost is decoupled from source payload size (shared identity)."""
    engine, prior = engine_and_prior
    prior_payload = _source_payload(prior)

    res = asyncio.run(engine.apply_mutation("s1", SetViewIntent(center=[120.0, 30.0], zoom=12)))

    assert not res.is_error, res.error_msg
    assert _source_payload(res.mapspec) is prior_payload, (
        "SetView must not copy the source payload; expected same object identity"
    )


def test_set_layout_does_not_copy_source_payload(engine_and_prior):
    engine, prior = engine_and_prior
    prior_payload = _source_payload(prior)

    res = asyncio.run(
        engine.apply_mutation(
            "s1", SetLayoutIntent(legend={"visible": False, "position": "bottom-left"})
        )
    )

    assert not res.is_error, res.error_msg
    assert _source_payload(res.mapspec) is prior_payload


def test_remove_layer_does_not_copy_source_payload(engine_and_prior):
    engine, prior = engine_and_prior
    prior_payload = _source_payload(prior)

    res = asyncio.run(engine.apply_mutation("s1", RemoveLayerIntent(layer_id="l1")))

    assert not res.is_error, res.error_msg
    assert _source_payload(res.mapspec) is prior_payload


def test_set_view_no_spec_deepcopy(engine_and_prior, monkeypatch):
    """SetView must perform zero deepcopy of any dict containing sources."""
    engine, _prior = engine_and_prior

    import app.services.mapspec.lifecycle_engine as le

    copied_objects = []
    real_deepcopy = copy.deepcopy

    def _counting_deepcopy(obj, *a, **kw):
        copied_objects.append(obj)
        return real_deepcopy(obj, *a, **kw)

    monkeypatch.setattr(le.copy, "deepcopy", _counting_deepcopy)

    res = asyncio.run(engine.apply_mutation("s1", SetViewIntent(zoom=13)))
    assert not res.is_error, res.error_msg

    for obj in copied_objects:
        if isinstance(obj, dict):
            assert "sources" not in obj, (
                f"SetView deep-copied a dict containing 'sources': {list(obj.keys())[:5]}"
            )


# ---------------------------------------------------------------- rollback correctness


def test_set_view_does_not_mutate_prior_view(engine_and_prior):
    """Candidate must not alias the prior view dict — else rollback is a no-op."""
    engine, prior = engine_and_prior
    prior_view_obj = prior["view"]
    prior_view_snapshot = copy.deepcopy(prior_view_obj)

    res = asyncio.run(engine.apply_mutation("s1", SetViewIntent(center=[120.0, 30.0], zoom=12)))

    assert not res.is_error, res.error_msg
    assert prior_view_obj == prior_view_snapshot, "SetView mutated the prior view dict"
    assert res.mapspec["view"] is not prior_view_obj
    assert res.mapspec["view"]["center"] == [120.0, 30.0]


def test_set_layout_does_not_mutate_prior_layout(engine_and_prior):
    engine, prior = engine_and_prior
    prior_layout_obj = prior["layout"]
    prior_layout_snapshot = copy.deepcopy(prior_layout_obj)

    res = asyncio.run(
        engine.apply_mutation(
            "s1", SetLayoutIntent(legend={"visible": False, "position": "bottom-left"})
        )
    )

    assert not res.is_error, res.error_msg
    assert prior_layout_obj == prior_layout_snapshot, "SetLayout mutated the prior layout dict"
    assert res.mapspec["layout"] is not prior_layout_obj
    assert res.mapspec["layout"]["legend"]["visible"] is False


def test_remove_layer_does_not_mutate_prior_layers_list(engine_and_prior):
    engine, prior = engine_and_prior
    prior_layers_obj = prior["layers"]
    prior_layers_snapshot = copy.deepcopy(prior_layers_obj)

    res = asyncio.run(engine.apply_mutation("s1", RemoveLayerIntent(layer_id="l1")))

    assert not res.is_error, res.error_msg
    assert prior_layers_obj == prior_layers_snapshot, "RemoveLayer mutated the prior layers list"
    assert len(prior_layers_obj) == 1, "Original layers list must still hold l1"
    assert res.mapspec["layers"] == []
    assert res.mapspec["layers"] is not prior_layers_obj


def test_upsert_layer_does_not_mutate_prior_containers(engine_and_prior):
    """UpsertLayer touches sources+layers+view; none may alias the prior."""
    engine, prior = engine_and_prior
    prior_sources_obj = prior["sources"]
    prior_layers_obj = prior["layers"]
    prior_view_obj = prior["view"]
    prior_sources_keys = set(prior_sources_obj.keys())
    prior_layers_len = len(prior_layers_obj)

    small_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                "properties": {},
            }
        ],
    }
    res = asyncio.run(
        engine.apply_mutation(
            "s1",
            UpsertLayerIntent(
                layer={"id": "l2", "type": "circle", "source": "small"},
                source_data=small_fc,
            ),
        )
    )

    assert not res.is_error, res.error_msg
    assert set(prior_sources_obj.keys()) == prior_sources_keys, "UpsertLayer mutated prior sources"
    assert len(prior_layers_obj) == prior_layers_len, "UpsertLayer mutated prior layers list"
    assert res.mapspec["sources"] is not prior_sources_obj
    assert res.mapspec["layers"] is not prior_layers_obj
    assert res.mapspec["view"] is not prior_view_obj
    assert any(la.get("id") == "l2" for la in res.mapspec["layers"])
    assert not any(la.get("id") == "l2" for la in prior_layers_obj)


def test_upsert_layer_preserves_existing_large_source_identity(engine_and_prior):
    """Adding a second layer must not duplicate the unrelated large payload."""
    engine, prior = engine_and_prior
    prior_payload = _source_payload(prior)

    small_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                "properties": {},
            }
        ],
    }
    res = asyncio.run(
        engine.apply_mutation(
            "s1",
            UpsertLayerIntent(
                layer={"id": "l2", "type": "circle", "source": "small"},
                source_data=small_fc,
            ),
        )
    )

    assert not res.is_error, res.error_msg
    assert _source_payload(res.mapspec) is prior_payload, (
        "Unrelated large source payload must not be duplicated by UpsertLayer"
    )
