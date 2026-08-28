"""GIS Harness Runtime v2 — Phase 1 atomic-state invariant tests.

Adversarial coverage for the audit findings fixed in this branch:
- F1: revision regression on the disk-revival path (prior captured before
  revival → N→1 rewrite + replayed expected_revision=0 passing CAS).
- F4: MapSpec commit lands spec+revision+fingerprint+runtime layers in ONE
  commit transaction (commit_mapspec_state); rollback advances the revision
  alongside the restored spec (never rewinds the CAS token).
- F2: shared-state writers (SessionPlan envelope) fail closed on a degraded
  distributed lock instead of proceeding process-local against shared Redis.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.distributed_lock as dl
from app.services.distributed_lock import (
    _InProcessLock,
    _ResilientSessionLock,
    LockDegradedError,
)
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    SetViewIntent,
    UpsertLayerIntent,
)
from app.services.session_data import session_data_manager, MemorySessionStore


@pytest.fixture
async def clean_session():
    sid = f"v2-atomic-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


async def _expire_redis_state(session_id: str) -> None:
    """模拟 Redis 状态哈希 TTL 到期（磁盘 spec + .rev sidecar 存活）。"""
    session_data_manager._map_state.pop(session_id, None)


# ---------------------------------------------------------------- F1 ----

@pytest.mark.asyncio
async def test_mutation_after_disk_revival_keeps_revision_monotonic(clean_session):
    """Redis 过期 + 磁盘复活后，下一次 mutation 的 revision 必须是 N+1。

    F1 回归形态：prior 从复活前的 pre_state 捕获（stale 0）→ commit 写
    revision=1 覆盖世代 N。
    """
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(
        clean_session, SetViewIntent(center=[104.0, 30.6], zoom=10.0)
    )
    res2 = await engine.apply_mutation(
        clean_session, SetViewIntent(center=[104.1, 30.7], zoom=11.0)
    )
    assert res2.mutation_revision == 2

    await _expire_redis_state(clean_session)

    res3 = await engine.apply_mutation(
        clean_session, SetViewIntent(center=[104.2, 30.8], zoom=12.0)
    )
    assert res3.is_error is False
    assert res3.mutation_revision == 3, (
        "复活后的 mutation 必须在复活令牌之上递增（N+1），不得回退到 1"
    )
    assert res3.mapspec["view"]["center"] == [104.2, 30.8], (
        "mutation 必须应用在复活的 spec 之上，而非从空 spec 重建"
    )


@pytest.mark.asyncio
async def test_replayed_expected_revision_zero_rejected_after_revival(clean_session):
    """复活后重放的 expected_revision=0 开世 mutation 必须被 CAS 拒绝。"""
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(
        clean_session, SetViewIntent(center=[104.0, 30.6], zoom=10.0)
    )
    await engine.apply_mutation(
        clean_session, SetViewIntent(center=[104.1, 30.7], zoom=11.0)
    )
    await _expire_redis_state(clean_session)

    res = await engine.apply_mutation(
        clean_session,
        SetViewIntent(center=[0.0, 0.0], zoom=1.0),
        origin="user",
        expected_revision=0,
    )
    assert res.superseded is True, "过期 CAS 不得在复活后的 spec 上通过"
    assert res.mutation_revision == 2, "superseded 必须报告复活后的真实令牌"


# ---------------------------------------------------------------- F4 ----

@pytest.mark.asyncio
async def test_layer_mutation_commits_layers_in_single_transaction(clean_session):
    """UpsertLayer 的 spec+revision+layers 必须一次 commit 落地。

    旧序列是 save_mapspec（spec/令牌 MULTI）→ update_layer_in_state（另一
    笔 WATCH/MULTI）；crash 落在中间留下 spec=世代 N+1 而 layers=世代 N。
    """
    engine = MapSpecLifecycleEngine()
    commit_calls = []
    orig_commit = MemorySessionStore.commit_mapspec_state

    async def spy_commit(self, session_id, fields, layer_op=None):
        commit_calls.append((sorted(fields.keys()), layer_op))
        return await orig_commit(self, session_id, fields, layer_op=layer_op)

    separate_layer_writes = []
    orig_update = MemorySessionStore.update_layer_in_state

    async def spy_update(self, session_id, layer_id, updates):
        separate_layer_writes.append(layer_id)
        return await orig_update(self, session_id, layer_id, updates)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(MemorySessionStore, "commit_mapspec_state", spy_commit)
        mp.setattr(MemorySessionStore, "update_layer_in_state", spy_update)
        res = await engine.apply_mutation(
            clean_session,
            UpsertLayerIntent(
                layer={
                    "id": "v2-single-tx",
                    "type": "circle",
                    "source": "v2-src",
                    "paint": {"color": "#fff"},
                },
                source_data={"type": "FeatureCollection", "features": []},
            ),
        )

    assert res.is_error is False
    layer_commits = [c for c in commit_calls if c[1] is not None]
    assert len(layer_commits) == 1, (
        f"layers 写必须并入唯一一次 commit（实测 {len(layer_commits)} 次带 layer_op 的 commit）"
    )
    fields, layer_op = layer_commits[0]
    assert "mapspec" in fields and "_cartographic_mutation_revision" in fields, (
        "spec 与 CAS 令牌必须与 layers 同一笔事务"
    )
    assert layer_op[0] == "upsert" and layer_op[1] == "v2-single-tx"
    assert separate_layer_writes == [], "不得再有独立的第二笔 layers 写"

    state = await session_data_manager.get_map_state(clean_session)
    runtime_ids = {ly.get("id") for ly in state.get("layers", [])}
    assert "v2-single-tx" in runtime_ids


@pytest.mark.asyncio
async def test_failed_commit_rollback_keeps_pair_consistent(clean_session):
    """commit 失败 → 回滚恢复旧 spec 且令牌单调前进（绝不回拨）。"""
    engine = MapSpecLifecycleEngine()
    res1 = await engine.apply_mutation(
        clean_session, SetViewIntent(center=[104.0, 30.6], zoom=10.0)
    )
    assert res1.mutation_revision == 1
    spec1 = res1.mapspec

    orig_commit = MemorySessionStore.commit_mapspec_state
    calls = {"n": 0}

    async def fail_first_commit(self, session_id, fields, layer_op=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return False  # 模拟 Redis 拒绝第一笔 commit
        return await orig_commit(self, session_id, fields, layer_op=layer_op)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(MemorySessionStore, "commit_mapspec_state", fail_first_commit)
        res2 = await engine.apply_mutation(
            clean_session, SetViewIntent(center=[0.0, 0.0], zoom=1.0)
        )

    assert res2.is_error is True
    state = await session_data_manager.get_map_state(clean_session)
    assert state["mapspec"] == spec1, "回滚必须恢复 mutation 前的 spec"
    assert state["_cartographic_mutation_revision"] == 2, (
        "回滚不得把令牌回拨到 1 —— 失败尝试的 N+1 可能已被读者观察到"
    )


# ---------------------------------------------------------------- F2 ----

@pytest.mark.asyncio
async def test_session_plan_fails_closed_on_degraded_lock(monkeypatch):
    """降级锁（Redis 配置但不可达）下 SessionPlan envelope 变更必须拒绝。

    旧行为：默认 fail_on_degraded=False → 两 pod 各持进程内锁 last-write-wins。
    """
    fake_client = MagicMock()
    fake_client.set = AsyncMock(side_effect=ConnectionError("redis down"))

    lock_factory_calls = {"n": 0}

    def degraded_factory(key, *args, **kwargs):
        lock_factory_calls["n"] += 1
        return _ResilientSessionLock(
            fake_client, f"webgis:sessionlock:{key}", _InProcessLock(),
            fail_on_degraded=kwargs.get("fail_on_degraded", False),
        )

    import app.services.session_plan as sp_mod

    class _DegradedReg:
        def lock(self, key, *args, **kwargs):
            lock_factory_calls["n"] += 1
            return _ResilientSessionLock(
                fake_client, f"webgis:sessionlock:{key}", _InProcessLock(),
                fail_on_degraded=kwargs.get("fail_on_degraded", False),
            )

    # 全量套件稳健性：patch 消费方模块引用（见 mutation_batch 同款注释）。
    monkeypatch.setattr(sp_mod, "session_lock_registry", _DegradedReg())

    from app.services.session_plan import apply_tool_result

    with pytest.raises(LockDegradedError):
        await apply_tool_result(
            "v2-degraded-plan-session",
            "webgis_map_intent",
            {"plan": {"query": "x"}},
            success=True,
        )
    assert lock_factory_calls["n"] >= 1
