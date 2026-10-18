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
        """#1386 R02：清理只针对 *idle* 溢出 —— 活跃/新会话不清。
        12 个刚写入的会话（时间戳=now）一个都不该被清；把最旧的 2 个
        回填成 idle 后，恰清 2 个。"""
        import time as _time

        from app.services.session_data_protocol import (
            IDLE_SESSION_TTL,
        )

        mgr = SessionDataManager(capacity=10)
        for i in range(12):
            await mgr.store(f"s{i}", f"data_{i}")
        await mgr.cleanup_idle_sessions(max_sessions=10)
        # 全部活跃 → #1386 R02 语义：不清活跃会话
        assert len(mgr._store) == 12

        # 最旧的两个会话 idle（时间戳回拨超过 TTL）
        for sid in ("s0", "s1"):
            mgr._session_order[sid] = (
                _time.time() - IDLE_SESSION_TTL - 60
            )
        await mgr.cleanup_idle_sessions(max_sessions=10)
        assert len(mgr._store) == 10
        assert "s0" not in mgr._store and "s1" not in mgr._store


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
    """评审[High]（融合定案 · S1 语义）: get_map_state 的 to_thread deepcopy
    与并发写方互不撕裂。

    读侧走审查 S1 的「循环线程浅快照 + 工作线程 deepcopy 快照」：顶层
    dict / layer dict 是私有拷贝，写方（set_map_state / set_map_state_fields /
    commit_mapspec_state / update_layer_in_state）对活 dict 的就地写全部
    落在快照之外 —— 历史缺陷形态（大 state ~100ms 级拷贝窗口内并发写触发
    ``RuntimeError: dictionary changed size during iteration`` 或撕裂快照）
    不可复现。写方彼此在 ``_map_state_lock`` 上串行，但不等待读侧拷贝
    窗口。用受控慢拷贝把 deepcopy 精确挂起在中段（choke 锚点用快照与
    活 dict 共享的嵌套 value —— 浅快照只拷贝顶层与 layer dict 两层），
    期间并发执行公共 API 写方：断言 reader 不抛、快照是写前一致状态、
    写事后落地。
    """

    async def _snapshot_while_writing(self, mgr, sid, writer, choke):
        """deepcopy 在 choke 对象的迭代中段挂起，期间并发执行 writer。

        dict/list 由慢拷贝钩子手工遍历（嵌套 choke 对象同样走钩子），
        标量走真 deepcopy；choke 对象先落地存活迭代器再挂起。choke 锚点
        必须选浅快照未拷贝的嵌套 value（如 viewport dict —— 快照与活
        dict 共享该对象），deepcopy 才会真正到达。
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
            # 此时写方运行在 deepcopy 拷贝窗口内。S1 下活 dict 与快照的
            # 顶层/layers 两层是不同对象：写不撕裂快照，reader 不抛
            # RuntimeError；快照保持写前一致状态。
            release.set()
            snapshot = await reader
            assert await writer_task, "并发写必须正常落地"

        assert snapshot.get("_started_at"), "快照必须是完整一致的写前状态"
        return snapshot

    @pytest.mark.parametrize(
        "method", ["set_map_state", "set_map_state_fields", "commit_mapspec_state"]
    )
    async def test_top_level_writers_excluded_from_inflight_snapshot(self, mgr, method):
        sid = "s1"
        await mgr.set_map_state(sid, "viewport", {"zoom": 5})

        async def writer():
            if method == "set_map_state":
                return await mgr.set_map_state(sid, "late_key", "late_value")
            if method == "set_map_state_fields":
                return await mgr.set_map_state_fields(sid, {"late_key": "late_value"})
            return await mgr.commit_mapspec_state(sid, {"late_key": "late_value"})

        # choke 锚点：viewport dict —— 浅快照未拷贝该嵌套 value，快照与活
        # dict 共享同一对象，deepcopy 必然到达。
        snapshot = await self._snapshot_while_writing(
            mgr, sid, writer, {id(mgr._map_state[sid]["viewport"])}
        )
        assert "late_key" not in snapshot
        assert snapshot["viewport"] == {"zoom": 5}
        assert mgr._map_state[sid]["late_key"] == "late_value"

    async def test_layer_cow_update_excluded_from_inflight_snapshot(self, mgr):
        sid = "s1"
        await mgr.set_map_state(sid, "viewport", {"zoom": 5})
        await mgr.set_map_state(sid, "layers", [{"id": "L1", "opacity": 1.0}])

        async def writer():
            # update_layer_in_state 走 copy-on-write（审查 S1）—— 旧 layer
            # dict 绝不就地改；快照的 layer 拷贝保持写前值。
            return await mgr.update_layer_in_state(sid, "L1", {"opacity": 0.5, "note": "x"})

        snapshot = await self._snapshot_while_writing(
            mgr, sid, writer, {id(mgr._map_state[sid]["viewport"])}
        )
        assert snapshot["layers"] == [{"id": "L1", "opacity": 1.0}]
        assert snapshot["viewport"] == {"zoom": 5}
        assert mgr._map_state[sid]["layers"][0]["opacity"] == 0.5
