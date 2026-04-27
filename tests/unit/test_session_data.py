"""Unit tests for app/services/session_data.py — SessionDataManager (in-memory)."""
import pytest
from app.services.session_data import SessionDataManager


@pytest.fixture
def mgr():
    """Fresh SessionDataManager with small capacity for eviction tests."""
    return SessionDataManager(capacity=5)


class TestStoreAndGet:
    def test_store_returns_ref_id(self, mgr):
        ref = mgr.store("s1", {"geojson": "..."}, prefix="layer")
        assert ref.startswith("ref:layer-")

    def test_store_and_get_roundtrip(self, mgr):
        ref = mgr.store("s1", {"type": "FeatureCollection"})
        result = mgr.get("s1", ref)
        assert result == {"type": "FeatureCollection"}

    def test_get_unknown_session_returns_none(self, mgr):
        assert mgr.get("missing", "ref:layer-abc") is None

    def test_get_unknown_ref_returns_none(self, mgr):
        mgr.store("s1", "data")
        assert mgr.get("s1", "ref:layer-nonexistent") is None


class TestAlias:
    def test_set_alias_and_get_by_alias(self, mgr):
        ref = mgr.store("s1", {"data": 1})
        mgr.set_alias("s1", ref, "my_layer")
        result = mgr.get("s1", "my_layer")
        assert result == {"data": 1}

    def test_get_by_original_ref_still_works(self, mgr):
        ref = mgr.store("s1", {"data": 1})
        mgr.set_alias("s1", ref, "alias1")
        # Both ref and alias should resolve
        assert mgr.get("s1", ref) == {"data": 1}
        assert mgr.get("s1", "alias1") == {"data": 1}


class TestListRefs:
    def test_list_refs_shows_aliases(self, mgr):
        ref = mgr.store("s1", "data")
        mgr.set_alias("s1", ref, "layer_a")
        refs = mgr.list_refs("s1")
        assert ref in refs
        assert refs[ref] == "layer_a"

    def test_list_refs_empty_for_unknown_session(self, mgr):
        assert mgr.list_refs("missing") == {}


class TestLRUEviction:
    def test_evicts_oldest_at_capacity(self, mgr):
        refs = []
        for i in range(6):  # capacity is 5
            ref = mgr.store("s1", f"data_{i}")
            refs.append(ref)

        # First ref should have been evicted
        assert mgr.get("s1", refs[0]) is None
        # Latest ref should still be there
        assert mgr.get("s1", refs[5]) == "data_5"

    def test_eviction_removes_alias(self, mgr):
        refs = []
        for i in range(6):
            ref = mgr.store("s1", f"data_{i}")
            mgr.set_alias("s1", ref, f"alias_{i}")
            refs.append(ref)

        # Evicted item's alias should also be gone
        result = mgr.get("s1", "alias_0")
        assert result is None


class TestMapState:
    def test_set_and_get_map_state(self, mgr):
        mgr.set_map_state("s1", "base_layer", "dark")
        mgr.set_map_state("s1", "zoom", 12)
        state = mgr.get_map_state("s1")
        assert state == {"base_layer": "dark", "zoom": 12}

    def test_get_map_state_empty(self, mgr):
        assert mgr.get_map_state("missing") == {}


class TestEventLog:
    def test_append_and_get_events(self, mgr):
        mgr.append_event("s1", "layer_added", {"id": "l1"})
        mgr.append_event("s1", "query_sent", {"text": "hello"})
        log = mgr.get_event_log("s1")
        assert len(log) == 2
        assert log[0]["event"] == "layer_added"
        assert log[1]["data"] == {"text": "hello"}

    def test_event_log_maxlen_cap(self, mgr):
        for i in range(30):
            mgr.append_event("s1", f"event_{i}", {})
        log = mgr.get_event_log("s1")
        assert len(log) == 20  # deque maxlen=20

    def test_get_event_log_empty(self, mgr):
        assert mgr.get_event_log("missing") == []


class TestClearSession:
    def test_clear_session_removes_everything(self, mgr):
        mgr.store("s1", "data")
        mgr.set_map_state("s1", "key", "val")
        mgr.append_event("s1", "ev", {})
        mgr.clear_session("s1")
        assert mgr.get("s1", "anything") is None
        assert mgr.get_map_state("s1") == {}
        assert mgr.get_event_log("s1") == []
