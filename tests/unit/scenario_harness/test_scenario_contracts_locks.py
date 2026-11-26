"""G05 WP1 单测：契约词表 / 注册表存取 / 用户锁定。"""

from __future__ import annotations

import asyncio

import pytest

from app.services.scenario_harness.contracts import (
    MAX_CANDIDATES,
    AdoptionReceipt,
    AdoptSelection,
    ScenarioCandidate,
    ScenarioLock,
    group_of_kind,
)
from app.services.scenario_harness.locks import (
    find_blocking_lock,
    list_active_locks,
    set_lock,
)
from app.services.scenario_harness.store import (
    RegistryError,
    load_registry,
    mint_id,
    remove_registry_entry,
    upsert_registry_entry,
    utc_now_iso,
)
from app.services.scenario_harness.contracts import REGISTRY_CANDIDATES, REGISTRY_LOCKS


@pytest.fixture()
def sid():
    return f"g05-unit-{mint_id('s', 'x').replace('-', '')[:16]}"


# ── 契约词表 ─────────────────────────────────────────────────────────


def test_candidate_state_vocabulary_frozen():
    """状态词表与默认态：新候选从 draft 出发。"""
    c = ScenarioCandidate(
        candidate_id="c1",
        session_id="s",
        branch_session_id="s__wif_scc1",
        branch_id="scc1",
        baseline_id="b1",
    )
    assert c.state == "draft"
    assert c.delta == []
    assert c.created_by == "agent"


def test_delta_group_mapping_deterministic():
    """intent kind → 语义分组（未知 kind 落 layout 披露，不抛）。"""
    assert group_of_kind("PatchLayerStyleIntent") == "style"
    assert group_of_kind("UpsertLayerIntent") == "analysis"
    assert group_of_kind("UpsertSourceIntent") == "data"
    assert group_of_kind("SetLayoutIntent") == "layout"
    assert group_of_kind("PatchComponentIntent") == "component"
    assert group_of_kind("TotallyNewFutureIntent") == "layout"


def test_lock_cover_semantics():
    """覆盖判定：精确字段 / 前缀 / 通配 / 全局锁 / 已释放不覆盖。"""
    lock = ScenarioLock(
        lock_id="l1",
        kind="layer_field",
        target_id="L1",
        field_paths=["paint.circle-color"],
    )
    assert lock.covers("L1", "paint.circle-color")
    assert not lock.covers("L1", "paint.circle-radius")
    assert not lock.covers("L2", "paint.circle-color")

    prefix_lock = ScenarioLock(
        lock_id="l2", kind="layer_field", target_id="L1", field_paths=["paint"]
    )
    assert prefix_lock.covers("L1", "paint.circle-color")

    released = ScenarioLock(
        lock_id="l3",
        kind="layer_field",
        target_id="L1",
        field_paths=["*"],
        released_at="2026-01-01T00:00:00Z",
    )
    assert not released.covers("L1", "anything")
    assert not released.active

    global_lock = ScenarioLock(
        lock_id="l4", kind="analysis_param", field_paths=["measure_unit"]
    )
    assert global_lock.covers("any-layer", "measure_unit")


def test_extra_forbid_catches_typos():
    """schema 红线：拼错字段名在构造期失败（extra=forbid）。"""
    with pytest.raises(Exception):
        ScenarioLock(
            lock_id="l", kind="layer_field", field_paths=["x"], targent_id="L1"
        )
    with pytest.raises(Exception):
        AdoptionReceipt(
            adoption_id="a", session_id="s", candidate_id="c", base_revison=1
        )


def test_adopt_selection_modes():
    sel = AdoptSelection(mode="groups", groups=["style"])
    assert sel.groups == ["style"]
    sel2 = AdoptSelection()
    assert sel2.mode == "all"


# ── 注册表存取 ────────────────────────────────────────────────────────


def test_registry_roundtrip_and_corruption_disclosure(sid):
    async def scenario():
        cand = ScenarioCandidate(
            candidate_id="c1",
            session_id=sid,
            branch_session_id=f"{sid}__wif_scc1",
            branch_id="scc1",
            baseline_id="b1",
            title="B 方案",
            created_at=utc_now_iso(),
        )
        await upsert_registry_entry(sid, REGISTRY_CANDIDATES, cand)
        loaded = await load_registry(sid, REGISTRY_CANDIDATES)
        assert "c1" in loaded and loaded["c1"].title == "B 方案"
        # 坏条目：schema 不合 → 跳过并披露，不抛
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(sid)
        state[REGISTRY_CANDIDATES]["bad"] = {"nope": 1}
        await session_data_manager.set_map_state(
            sid, REGISTRY_CANDIDATES, state[REGISTRY_CANDIDATES]
        )
        corrupt: list[str] = []
        loaded2 = await load_registry(sid, REGISTRY_CANDIDATES, corrupt=corrupt)
        assert "c1" in loaded2 and "bad" not in loaded2
        assert any("schema" in c for c in corrupt)
        # 幂等删除：不存在返回 False
        assert await remove_registry_entry(sid, REGISTRY_CANDIDATES, "missing") is False
        assert await remove_registry_entry(sid, REGISTRY_CANDIDATES, "c1") is True

    asyncio.run(scenario())


def test_registry_limit_rejects_overflow(sid):
    async def scenario():
        for i in range(MAX_CANDIDATES):
            await upsert_registry_entry(
                sid,
                REGISTRY_CANDIDATES,
                ScenarioCandidate(
                    candidate_id=f"c{i}",
                    session_id=sid,
                    branch_session_id=f"{sid}__wif_scc{i}",
                    branch_id=f"scc{i}",
                    baseline_id="b1",
                ),
            )
        with pytest.raises(RegistryError) as ei:
            await upsert_registry_entry(
                sid,
                REGISTRY_CANDIDATES,
                ScenarioCandidate(
                    candidate_id="overflow",
                    session_id=sid,
                    branch_session_id=f"{sid}__wif_scoverflow",
                    branch_id="scoverflow",
                    baseline_id="b1",
                ),
            )
        assert "limit" in ei.value.code

    asyncio.run(scenario())


# ── 用户锁定 ──────────────────────────────────────────────────────────


def test_lock_idempotent_and_release_audit(sid):
    async def scenario():
        l1 = await set_lock(
            sid,
            kind="layer_field",
            target_id="L1",
            field_paths=["paint.circle-color"],
            observed_value="#123456",
            reason="用户说配色定死",
            turn_id="t1",
        )
        l2 = await set_lock(
            sid,
            kind="layer_field",
            target_id="L1",
            field_paths=["paint.circle-color"],
            reason="重复请求",
            turn_id="t2",
        )
        assert l1.lock_id == l2.lock_id, "同 (kind,target,fields) 幂等"

        blocking = await find_blocking_lock(sid, "L1", ["paint.circle-color"])
        assert blocking is not None and blocking.lock_id == l1.lock_id
        none = await find_blocking_lock(sid, "L1", ["visibility"])
        assert none is None

        # Agent 不能解用户锁
        from app.services.scenario_harness.locks import release_lock as rl

        with pytest.raises(RegistryError):
            await rl(sid, l1.lock_id, reason="agent 尝试", origin="agent")

        released = await rl(sid, l1.lock_id, reason="用户改变主意", origin="user")
        assert released.released_at
        assert await find_blocking_lock(sid, "L1", ["paint.circle-color"]) is None
        # 释放后仍保留审计（环内不物理删除）
        entries = await load_registry(sid, REGISTRY_LOCKS)
        assert (
            l1.lock_id in entries
            and entries[l1.lock_id].released_reason == "用户改变主意"
        )

    asyncio.run(scenario())


def test_active_lock_listing_stable_order(sid):
    async def scenario():
        a = await set_lock(
            sid, kind="analysis_param", field_paths=["measure_unit"], reason="u1"
        )
        b = await set_lock(
            sid, kind="analysis_param", field_paths=["time_period"], reason="u2"
        )
        locks = await list_active_locks(sid)
        ids = [lock.lock_id for lock in locks]
        assert sorted(ids) == sorted([a.lock_id, b.lock_id])
        assert ids == sorted(ids), "同秒创建时按 (created_at, lock_id) 稳定排序"
        assert all(lock.active for lock in locks)

    asyncio.run(scenario())
