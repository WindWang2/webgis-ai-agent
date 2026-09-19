"""#1396: LoadedModelCache invalidate/orphan/warmup must unload correctly."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.modelops.loaded_cache import LoadedModelCache
from app.services.modelops.scheduling import WarmPoolManager


class _Model:
    def __init__(self, name):
        self.name = name


def test_invalidate_unloads_when_refcount_zero():
    unloaded = []
    cache = LoadedModelCache(max_models=4, idle_ttl_s=3600)

    def load():
        return _Model("m1")

    def unload(m):
        unloaded.append(m.name)

    desc = SimpleNamespace(model_id="m", model_version="1", checksum="c")
    model, _ = cache.acquire(
        "k1", desc, provider_ref="p", device="cpu",
        load_fn=load, unload_fn=unload,
    )
    cache.release("k1")
    assert cache.size() == 1
    cache.invalidate("k1")
    assert unloaded == ["m1"]
    assert cache.size() == 0


def test_concurrent_orphans_each_unload_own_model():
    unloaded = []
    cache = LoadedModelCache(max_models=4, idle_ttl_s=3600)
    # Simulate two orphan registrations for same key (invalidate race path)
    m_a, m_b = _Model("A"), _Model("B")
    cache._orphans["k"] = []
    cache._orphans["k"].append((m_a, lambda m: unloaded.append(m.name)))
    cache._orphans["k"].append((m_b, lambda m: unloaded.append(m.name)))
    cache.release("k")
    cache.release("k")
    assert sorted(unloaded) == ["A", "B"]
    assert "k" not in cache._orphans


def test_warm_pool_pin_keyed_by_device():
    class FakeCache:
        def __init__(self):
            self.acquires = []
            self.releases = []
            self._n = 0

        def acquire(self, key, desc, **kw):
            self.acquires.append((key, kw.get("device")))
            self._n += 1
            return _Model(f"m{self._n}"), 0.01

        def release(self, key):
            self.releases.append(key)

    class FakeRegistry:
        def resolve(self, mid, ver):
            return SimpleNamespace(descriptor=SimpleNamespace(
                model_id=mid, model_version=ver or "1", checksum="c",
                provider_ref="prov"))

    class FakeProviders:
        def get(self, ref):
            return SimpleNamespace(
                load=lambda d, device="cpu": _Model(device),
                unload=lambda m: None,
                warmup=lambda m: None,
            )

    mgr = WarmPoolManager(FakeCache(), FakeRegistry(), FakeProviders())
    a = mgr.pin("model-x", device="cpu")
    b = mgr.pin("model-x", device="cuda")
    assert a["pinned"] and b["pinned"]
    assert a["device"] == "cpu" and b["device"] == "cuda"
    assert len(mgr._cache.acquires) == 2


def test_warm_pool_releases_on_warmup_failure():
    class FakeCache:
        def __init__(self):
            self.refcount = 0

        def acquire(self, *a, **k):
            self.refcount += 1
            return _Model("m"), 0.0

        def release(self, key):
            self.refcount -= 1

    class FakeRegistry:
        def resolve(self, mid, ver):
            return SimpleNamespace(descriptor=SimpleNamespace(
                model_id=mid, model_version=ver or "1", checksum="c",
                provider_ref="prov"))

    class BoomProv:
        def load(self, d, device="cpu"):
            return _Model("m")

        def unload(self, m):
            pass

        def warmup(self, m):
            raise RuntimeError("warmup boom")

    class FakeProviders:
        def get(self, ref):
            return BoomProv()

    cache = FakeCache()
    mgr = WarmPoolManager(cache, FakeRegistry(), FakeProviders())
    state = mgr.pin("m1", device="cpu")
    assert state["pinned"] is False
    assert cache.refcount == 0
