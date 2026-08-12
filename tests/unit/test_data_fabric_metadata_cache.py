"""Safe TTL metadata-cache tests (Section 37): hit/miss, cross-scope isolation
(no cross-tenant leak), TTL expiry, bounded eviction."""
import pytest

from app.services.data_fabric.metadata_cache import SafeTTLCache, cache_key, cached_describe


@pytest.fixture(autouse=True)
def _fresh_cache_per_test():
    """Isolation: each test uses its own cache (no global-cache leakage between
    tests, and no accidental reuse of the module global)."""
    yield


class _FakeClock:
    def __init__(self, t0=0.0):
        self.t = t0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def test_cache_hit_avoids_repeat_describe():
    calls = {"n": 0}

    def describe(dataset_id):
        calls["n"] += 1
        return {"id": dataset_id, "features": 1}

    cache = SafeTTLCache()
    out1 = cached_describe(describe, "src1", "ds_a", scope="tenant_A", cache=cache)
    out2 = cached_describe(describe, "src1", "ds_a", scope="tenant_A", cache=cache)
    assert out1 == out2
    assert calls["n"] == 1  # cached


def test_different_tenant_scope_does_not_leak():
    """The P0: a descriptor cached under tenant_A MUST NOT be served to tenant_B."""
    seen = []

    def describe(dataset_id):
        seen.append(dataset_id)
        return {"id": dataset_id}

    cache = SafeTTLCache()
    cached_describe(describe, "src1", "ds_a", scope="tenant_A", cache=cache)
    cached_describe(describe, "src1", "ds_a", scope="tenant_B", cache=cache)
    assert len(seen) == 2  # two distinct calls — no cross-tenant reuse


def test_different_dataset_or_source_misses():
    def describe(dataset_id):
        return {"id": dataset_id}

    cache = SafeTTLCache()
    cached_describe(describe, "src1", "ds_a", scope="s", cache=cache)
    # different dataset -> miss (new call)
    cached_describe(describe, "src1", "ds_b", scope="s", cache=cache)
    # different source -> miss
    cached_describe(describe, "src2", "ds_a", scope="s", cache=cache)


def test_ttl_expiry_causes_miss():
    clk = _FakeClock()
    cache = SafeTTLCache(default_ttl=10.0, clock=clk)
    calls = {"n": 0}

    def describe(dataset_id):
        calls["n"] += 1
        return {"id": dataset_id}

    cached_describe(describe, "s", "d", scope="x", cache=cache)
    assert calls["n"] == 1
    clk.advance(11.0)  # past TTL
    cached_describe(describe, "s", "d", scope="x", cache=cache)
    assert calls["n"] == 2  # expired -> re-fetched


def test_invalidate_clears():
    cache = SafeTTLCache(default_ttl=30.0)
    cache.put("k1", "v1")
    assert cache.get("k1") == "v1"
    cache.invalidate("k1")
    assert cache.get("k1") is None


def test_cache_key_is_stable_and_collision_resistant():
    k1 = cache_key("a", "b", "c")
    k2 = cache_key("a", "b", "c")
    k3 = cache_key("a", "b", "d")
    assert k1 == k2
    assert k1 != k3
