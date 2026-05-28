"""Unit tests for app/services/session_data.py — SessionDataManager (in-memory)."""
import pytest
from app.services.session_data import SessionDataManager


@pytest.fixture
def mgr():
    """Fresh SessionDataManager with small capacity for eviction tests."""
    return SessionDataManager(capacity=5)


class TestStoreAndGet:
    async def test_store_returns_ref_id(self, mgr):
        ref = await mgr.store("s1", {"geojson": "..."}, prefix="layer")
        assert ref.startswith("ref:layer-")

    async def test_store_and_get_roundtrip(self, mgr):
        ref = await mgr.store("s1", {"type": "FeatureCollection"})
        result = await mgr.get("s1", ref)
        assert result == {"type": "FeatureCollection"}

    async def test_get_unknown_session_returns_none(self, mgr):
        assert await mgr.get("missing", "ref:layer-abc") is None

    async def test_get_unknown_ref_returns_none(self, mgr):
        await mgr.store("s1", "data")
        assert await mgr.get("s1", "ref:layer-nonexistent") is None


class TestAlias:
    async def test_set_alias_and_get_by_alias(self, mgr):
        ref = await mgr.store("s1", {"data": 1})
        await mgr.set_alias("s1", ref, "my_layer")
        result = await mgr.get("s1", "my_layer")
        assert result == {"data": 1}

    async def test_get_by_original_ref_still_works(self, mgr):
        ref = await mgr.store("s1", {"data": 1})
        await mgr.set_alias("s1", ref, "alias1")
        # Both ref and alias should resolve
        assert await mgr.get("s1", ref) == {"data": 1}
        assert await mgr.get("s1", "alias1") == {"data": 1}


class TestListRefs:
    async def test_list_refs_shows_aliases(self, mgr):
        ref = await mgr.store("s1", "data")
        await mgr.set_alias("s1", ref, "layer_a")
        refs = await mgr.list_refs("s1")
        assert ref in refs
        assert refs[ref] == "layer_a"

    async def test_list_refs_empty_for_unknown_session(self, mgr):
        assert await mgr.list_refs("missing") == {}


class TestLRUEviction:
    async def test_evicts_oldest_at_capacity(self, mgr):
        refs = []
        for i in range(6):  # capacity is 5
            ref = await mgr.store("s1", f"data_{i}")
            refs.append(ref)

        # First ref should have been evicted
        assert await mgr.get("s1", refs[0]) is None
        # Latest ref should still be there
        assert await mgr.get("s1", refs[5]) == "data_5"

    async def test_eviction_removes_alias(self, mgr):
        refs = []
        for i in range(6):
            ref = await mgr.store("s1", f"data_{i}")
            await mgr.set_alias("s1", ref, f"alias_{i}")
            refs.append(ref)

        # Evicted item's alias should also be gone
        result = await mgr.get("s1", "alias_0")
        assert result is None

    async def test_get_promotes_item_prevents_eviction(self, mgr):
        refs = []
        for i in range(5):
            ref = await mgr.store("s1", f"data_{i}")
            refs.append(ref)
        # Access item 0 — promotes it to end of LRU
        await mgr.get("s1", refs[0])
        # Store one more — should evict item 1 (oldest unaccessed), not item 0
        await mgr.store("s1", "data_6")
        assert await mgr.get("s1", refs[0]) == "data_0"
        assert await mgr.get("s1", refs[1]) is None


class TestMapState:
    async def test_set_and_get_map_state(self, mgr):
        await mgr.set_map_state("s1", "base_layer", "dark")
        await mgr.set_map_state("s1", "zoom", 12)
        state = await mgr.get_map_state("s1")
        # R6 引入了内部 _started_at 字段，断言改成 superset 比较
        assert state["base_layer"] == "dark"
        assert state["zoom"] == 12
        assert "_started_at" in state

    async def test_get_map_state_empty(self, mgr):
        assert await mgr.get_map_state("missing") == {}


class TestLayerState:
    async def test_update_existing_layer(self, mgr):
        await mgr.set_map_state("s1", "layers", [{"id": "l1", "opacity": 0.5}])
        await mgr.update_layer_in_state("s1", "l1", {"opacity": 0.8})
        layers = (await mgr.get_map_state("s1"))["layers"]
        assert len(layers) == 1
        assert layers[0]["opacity"] == 0.8

    async def test_update_adds_new_layer_if_missing(self, mgr):
        await mgr.set_map_state("s1", "layers", [])
        await mgr.update_layer_in_state("s1", "l_new", {"opacity": 1.0})
        layers = (await mgr.get_map_state("s1"))["layers"]
        assert len(layers) == 1
        assert layers[0]["id"] == "l_new"

    async def test_remove_layer(self, mgr):
        await mgr.set_map_state("s1", "layers", [{"id": "l1"}, {"id": "l2"}])
        await mgr.remove_layer_from_state("s1", "l1")
        layers = (await mgr.get_map_state("s1"))["layers"]
        assert len(layers) == 1
        assert layers[0]["id"] == "l2"


class TestEventLog:
    async def test_append_and_get_events(self, mgr):
        await mgr.append_event("s1", "layer_added", {"id": "l1"})
        await mgr.append_event("s1", "query_sent", {"text": "hello"})
        log = await mgr.get_event_log("s1")
        assert len(log) == 2
        assert log[0]["event"] == "layer_added"
        assert log[1]["data"] == {"text": "hello"}

    async def test_event_log_maxlen_cap(self, mgr):
        for i in range(30):
            await mgr.append_event("s1", f"event_{i}", {})
        log = await mgr.get_event_log("s1")
        assert len(log) == 20  # deque maxlen=20

    async def test_get_event_log_empty(self, mgr):
        assert await mgr.get_event_log("missing") == []


class TestClearSession:
    async def test_clear_session_removes_everything(self, mgr):
        await mgr.store("s1", "data")
        await mgr.set_map_state("s1", "key", "val")
        await mgr.append_event("s1", "ev", {})
        await mgr.clear_session("s1")
        assert await mgr.get("s1", "anything") is None
        assert await mgr.get_map_state("s1") == {}
        assert await mgr.get_event_log("s1") == []


class TestCleanupIdleSessions:
    async def test_evicts_oldest_sessions(self):
        mgr = SessionDataManager(capacity=10)
        for i in range(12):
            await mgr.store(f"s{i}", f"data_{i}")
        await mgr.cleanup_idle_sessions(max_sessions=10)
        # Should have cleaned up some sessions
        assert len(mgr._store) <= 10
