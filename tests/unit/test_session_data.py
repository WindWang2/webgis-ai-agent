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


class TestMapStateSequencing:
    """F4: viewport has two unsequenced writers (turn-start + throttled POST).

    The fix adds a monotonic per-key `seq` to set_map_state: a sequenced write
    is accepted only when its seq is strictly newer than the stored one, so
    out-of-order arrivals resolve to the latest seq instead of last-write-wins.
    Unsequenced writes (server-side truth: ws_service, layer_manager) always
    apply and never invalidate the client's outstanding seq.
    """

    async def test_newer_seq_write_wins_over_stale_replay(self, mgr):
        # newer write lands first
        assert await mgr.set_map_state("s1", "viewport", {"zoom": 12}, seq=2) is True
        # stale write arrives after — must be rejected, not clobber
        assert await mgr.set_map_state("s1", "viewport", {"zoom": 5}, seq=1) is False
        state = await mgr.get_map_state("s1")
        assert state["viewport"] == {"zoom": 12}
        assert state["_viewport_seq"] == 2

    async def test_out_of_order_writes_resolve_to_latest_seq(self, mgr):
        await mgr.set_map_state("s1", "viewport", {"zoom": 5}, seq=1)
        await mgr.set_map_state("s1", "viewport", {"zoom": 12}, seq=2)
        # stale replay of seq 1 after seq 2 is already stored
        assert await mgr.set_map_state("s1", "viewport", {"zoom": 5}, seq=1) is False
        state = await mgr.get_map_state("s1")
        assert state["viewport"] == {"zoom": 12}
        assert state["_viewport_seq"] == 2

    async def test_jump_ahead_seq_is_accepted(self, mgr):
        await mgr.set_map_state("s1", "viewport", {"zoom": 5}, seq=1)
        assert await mgr.set_map_state("s1", "viewport", {"zoom": 12}, seq=3) is True
        assert (await mgr.get_map_state("s1"))["viewport"] == {"zoom": 12}

    async def test_unsequenced_write_applies_without_bumping_seq(self, mgr):
        # turn-start write carries the client's send-time seq
        await mgr.set_map_state("s1", "viewport", {"zoom": 5}, seq=3)
        # unsequenced server-side write (ws_service / layer_manager path) —
        # always applies, but leaves the stored seq untouched so the client's
        # NEXT sequenced write (seq 4) still passes.
        await mgr.set_map_state("s1", "viewport", {"zoom": 6})
        state = await mgr.get_map_state("s1")
        assert state["viewport"] == {"zoom": 6}
        assert state["_viewport_seq"] == 3
        # a stale sequenced write still loses against it
        assert await mgr.set_map_state("s1", "viewport", {"zoom": 5}, seq=3) is False
        # and the next client write with a newer seq wins
        assert await mgr.set_map_state("s1", "viewport", {"zoom": 7}, seq=4) is True
        assert (await mgr.get_map_state("s1"))["viewport"] == {"zoom": 7}

    async def test_updated_at_metadata_is_recorded(self, mgr):
        await mgr.set_map_state("s1", "viewport", {"zoom": 5}, seq=1)
        state = await mgr.get_map_state("s1")
        assert state["_viewport_updated_at"]  # non-empty ISO timestamp
        # unsequenced write also refreshes the timestamp
        await mgr.set_map_state("s1", "viewport", {"zoom": 6})
        assert (await mgr.get_map_state("s1"))["_viewport_updated_at"] >= state["_viewport_updated_at"]

    async def test_seq_metadata_is_per_key(self, mgr):
        await mgr.set_map_state("s1", "viewport", {"zoom": 5}, seq=1)
        await mgr.set_map_state("s1", "layers", [{"id": "l1"}], seq=1)
        # stale viewport replay must not affect the layers key
        assert await mgr.set_map_state("s1", "viewport", {"zoom": 5}, seq=1) is False
        assert await mgr.set_map_state("s1", "layers", [{"id": "l2"}], seq=2) is True
        state = await mgr.get_map_state("s1")
        assert state["layers"] == [{"id": "l2"}]
        assert state["_layers_seq"] == 2
