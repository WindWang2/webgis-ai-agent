"""J17 fixture (scripts/seed_journey_frames.py) fingerprint contract.

``seed()`` returns the cartographic fingerprint of the just-seeded MapSpec for
the POST fingerprint gate. When ``get_mapspec`` hands back anything that is not
a dict, folding it into ``{}`` silently yields the empty-projection fingerprint
and the gate then reports a meaningless mismatch instead of a clear type error.
"""
from __future__ import annotations

import pytest

from app.lib.cartography.quality_loop import cartographic_fingerprint
from scripts import seed_journey_frames


class _FakeStore:
    def __init__(self, spec):
        self._spec = spec

    async def get_mapspec(self, session_id: str):
        return self._spec


def _install_fake_engine(monkeypatch, spec) -> None:
    store = _FakeStore(spec)

    class _FakeEngine:
        def __init__(self):
            self.store = store

    monkeypatch.setattr(
        "app.services.mapspec.lifecycle_engine.MapSpecLifecycleEngine", _FakeEngine
    )


async def _fake_seed_intents(session_id: str, frame_kind: str) -> dict:
    return {
        "status": "ok",
        "frame_ids": ["fp-r-2020", "fp-r-2022", "fp-r-2024"],
        "layer_id": "lyr-j17-districts",
        "mutation_revision": 4,
    }


@pytest.fixture(autouse=True)
def _stub_seed_intents(monkeypatch):
    monkeypatch.setattr(seed_journey_frames, "_seed_intents", _fake_seed_intents)


@pytest.mark.parametrize("bad_spec", ["not-a-dict", 42, ["layers"], None])
async def test_seed_raises_type_error_when_mapspec_is_not_a_dict(monkeypatch, bad_spec):
    _install_fake_engine(monkeypatch, bad_spec)
    with pytest.raises(TypeError):
        await seed_journey_frames.seed("owned-session", "time_series")


async def test_seed_returns_fingerprint_of_the_real_mapspec(monkeypatch):
    spec = {"version": 1, "layers": [{"id": "lyr-j17-districts", "type": "circle"}]}
    _install_fake_engine(monkeypatch, spec)
    out = await seed_journey_frames.seed("owned-session", "time_series")
    assert out["mapspec_fingerprint"] == cartographic_fingerprint(spec)
