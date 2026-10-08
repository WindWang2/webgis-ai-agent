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


class TestMapStateSnapshotIsolation:
    """评审[High]: get_map_state 的 to_thread deepcopy 必须与写方互斥。

    修复前读侧全程不持锁，写方（set_map_state / set_map_state_fields /
    commit_mapspec_state / update_layer_in_state）就地改同一 dict —— 大
    state（~100ms 级拷贝窗口）下并发写触发 ``RuntimeError: dictionary
    changed size during iteration`` 或撕裂快照。用受控慢拷贝把写方精确注入
    deepcopy 迭代中段：修复前 await reader 必抛 RuntimeError，修复后写方
    在 ``_map_state_lock`` 上排队、快照是写前一致状态。
    """

    async def _snapshot_while_writing(self, mgr, sid, writer, choke):
        """deepcopy 在 choke 对象的迭代中段挂起，期间并发执行 writer。

        dict/list 由慢拷贝钩子手工遍历（嵌套 choke 对象同样走钩子），
        标量走真 deepcopy；choke 对象先落地存活迭代器再挂起 —— 挂起期间
        对该 dict 的任何写都会让迭代抛 RuntimeError（修复前的缺陷形态）。
        """
        import asyncio
        import copy as copy_mod
        import threading

        real_deepcopy = copy_mod.deepcopy
        inside_copy = threading.Event()
        release = threading.Event()

        def _slow_deepcopy(x, *args, **kwargs):
            if isinstance(x, dict):
                if id(x) in choke:
                    it = iter(x.items())
                    inside_copy.set()
                    assert release.wait(timeout=5), "release 未置位（测试协调失败）"
                    return {k: _slow_deepcopy(v) for k, v in it}
                return {k: _slow_deepcopy(v) for k, v in x.items()}
            if isinstance(x, list):
                return [_slow_deepcopy(i) for i in x]
            return real_deepcopy(x, *args, **kwargs)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(copy_mod, "deepcopy", _slow_deepcopy)
            reader = asyncio.create_task(mgr.get_map_state(sid))
            # 等 deepcopy 工作线程停在 choke 点（拿到存活迭代器）
            reached = await asyncio.to_thread(inside_copy.wait, 5)
            assert reached, "deepcopy 未到达 choke 点（测试前提失败）"
            writer_task = asyncio.create_task(writer())
            await asyncio.sleep(0.05)
            # 修复前：写即刻落在 deepcopy 迭代窗口内 → 下面 await reader 抛
            # RuntimeError；修复后：写方在 _map_state_lock 上排队，快照不受影响。
            release.set()
            snapshot = await reader
            assert await writer_task, "并发写必须在快照完成后落地"

        assert snapshot.get("_started_at"), "快照必须是完整一致的写前状态"
        return snapshot

    @pytest.mark.parametrize(
        "method", ["set_map_state", "set_map_state_fields", "commit_mapspec_state"]
    )
    async def test_top_level_writers_wait_for_snapshot(self, mgr, method):
        sid = "s1"
        await mgr.set_map_state(sid, "viewport", {"zoom": 5})

        async def writer():
            if method == "set_map_state":
                return await mgr.set_map_state(sid, "late_key", "late_value")
            if method == "set_map_state_fields":
                return await mgr.set_map_state_fields(sid, {"late_key": "late_value"})
            return await mgr.commit_mapspec_state(sid, {"late_key": "late_value"})

        snapshot = await self._snapshot_while_writing(
            mgr, sid, writer, {id(mgr._map_state[sid])}
        )
        assert "late_key" not in snapshot
        assert snapshot["viewport"] == {"zoom": 5}
        assert mgr._map_state[sid]["late_key"] == "late_value"

    async def test_inplace_layer_update_waits_for_snapshot(self, mgr):
        sid = "s1"
        await mgr.set_map_state(sid, "layers", [{"id": "L1", "opacity": 1.0}])

        async def writer():
            # update_layer_in_state 就地 layer.update —— 挂起中的嵌套迭代
            # 同样会被撕裂（修复前）
            return await mgr.update_layer_in_state(sid, "L1", {"opacity": 0.5, "note": "x"})

        snapshot = await self._snapshot_while_writing(
            mgr, sid, writer, {id(mgr._map_state[sid]["layers"][0])}
        )
        assert snapshot["layers"] == [{"id": "L1", "opacity": 1.0}]
        assert mgr._map_state[sid]["layers"][0]["opacity"] == 0.5
