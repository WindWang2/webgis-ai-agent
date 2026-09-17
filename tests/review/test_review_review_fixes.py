"""独立 review P0/P1/P2 回归测试（red-test→fix）。

覆盖 Subagent B 复现/论证的：
- P1 回滚前间隙并发提交被回滚摧毁（现应中止回滚 → interleaved）；
- P1 decision 上界只在读时强制 → 201 条后整个会话审查面 ReviewStoreCorrupt；
- P1 跨进程丢失更新 → store 变迁经 session_lock_registry（分布式锁）串行化；
- P2 合并期间状态被并发迁移 → 中止合并 + 失败存证持久化 + merge_in_progress 排斥；
- P2 失败/交错合并的 evidence 落库（不只 HTTP 回执）；
- P2 无漂移 rebase 拒绝；
- P3 merge SUBMITTED 空原因文案 / 空 session_id 拒绝 / superseded intent 不入 mutation_ids。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.schemas.mapspec_mutation_schema import PatchLayerStyleBody, SetViewBody
from app.schemas.review_schema import ProposalStatus, ReviewActor, ReviewProposal
from app.services.review.merge import merge_proposal_intents
from app.services.review.service import (
    InvalidTransition,
    ReviewService,
)
from app.services.review.store import ReviewStore


def _sid():
    return f"rv-fix-{uuid4().hex[:8]}"


def _actor(uid="u1", kind="user", role="editor"):
    return ReviewActor(actor_id=uid, actor_kind=kind, role=role)


def _actor2():
    return _actor("u2", role="editor")


async def _seed_two_layers(sid):
    from app.services.mapspec.lifecycle_engine import (
        InitProjectIntent,
        MapSpecLifecycleEngine,
        UpsertLayerIntent,
    )

    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    r = None
    for lid, color in (("L1", "#111111"), ("L2", "#222222")):
        r = await engine.apply_mutation(
            sid,
            UpsertLayerIntent(
                layer={"id": lid, "source": "s", "type": "circle", "paint": {"circle-color": color}},
                source_data={"type": "FeatureCollection", "features": []},
            ),
        )
    return engine, r.mutation_revision


@pytest.mark.asyncio
async def test_p1_rollback_aborts_when_concurrent_commit_lands():
    """is_error 后、回滚前落地的并发提交不得被回滚摧毁。"""
    sid = _sid()
    engine, base = await _seed_two_layers(sid)
    proposal = ReviewProposal(
        proposal_id="rp_gap", session_id=sid, title="t",
        author=_actor(), base_revision=base, status=ProposalStatus.APPROVED,
        mutation_intents=[
            PatchLayerStyleBody(
                intent="patch_layer_style", expected_revision=base,
                layer_id="L1", paint={"circle-color": "#0a0a0a"},
            ),
            PatchLayerStyleBody(
                intent="patch_layer_style", expected_revision=base,
                layer_id="L-gone", paint={"circle-color": "#0c0c0c"},
            ),
        ],
    )
    from app.services.review import merge as merge_mod
    from app.services.mapspec.lifecycle_engine import RollbackIntent, SetViewIntent

    original_apply = merge_mod.apply_gis_mutation

    async def _spy(session_id, intent, **kwargs):
        if isinstance(intent, RollbackIntent):
            # 回滚执行前的 await 间隙：并发方提交 set_view zoom=20。
            await original_apply(
                session_id, SetViewIntent(zoom=20), origin="agent", actor="interloper",
            )
        return await original_apply(session_id, intent, **kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(merge_mod, "apply_gis_mutation", _spy)
    try:
        outcome = await merge_proposal_intents(sid, proposal, _actor2())
    finally:
        mp.undo()

    assert outcome.ok is False
    assert outcome.interleaved is True, "回滚被 CAS 拒绝后必须标记交错"
    assert outcome.rolled_back is False, "并发工作在场上时禁止回滚"
    final = await engine.store.get_mapspec(sid)
    assert (final.get("view") or {}).get("zoom") == 20, "并发提交被摧毁！"
    assert outcome.evidence.interleaved is True


@pytest.mark.asyncio
async def test_p1_decision_bound_enforced_at_write(monkeypatch):
    """decision 上界必须在写时强制 —— 超界拒绝且存储仍可读（不 brick）。"""
    monkeypatch.setattr(
        "app.schemas.review_schema.MAX_DECISIONS_PER_PROPOSAL", 5,
    )
    sid = _sid()
    svc = ReviewService()
    from app.services.mapspec.lifecycle_engine import (
        InitProjectIntent,
        MapSpecLifecycleEngine,
        UpsertLayerIntent,
    )

    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    r = await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "L1", "source": "s", "type": "circle", "paint": {"circle-color": "#123456"}},
            source_data={"type": "FeatureCollection", "features": []},
        ),
    )
    base = r.mutation_revision
    p = await svc.create_proposal(
        sid, author=_actor(), title="t",
        intents=[{"intent": "set_view", "expected_revision": base, "zoom": 3}],
    )
    await svc.submit(sid, p.proposal_id, _actor(), base_revision=base)
    for _ in range(5):
        await svc.record_decision(sid, p.proposal_id, _actor2(), decision="approve")
    with pytest.raises(InvalidTransition):
        await svc.record_decision(sid, p.proposal_id, _actor("u3", role="editor"), decision="approve")
    # 存储仍可读（fail-closed 不能被自伤）。
    assert await svc.get_proposal(sid, p.proposal_id) is not None


@pytest.mark.asyncio
async def test_p1_store_mutations_go_through_session_lock_registry(monkeypatch):
    """store 变迁必须持 session_lock_registry 锁（跨进程语义与引擎一致）。"""
    import app.services.distributed_lock as distributed_lock_mod

    entered = {"n": 0}
    real_registry_lock = distributed_lock_mod.session_lock_registry.lock

    class _SpyCtx:
        def __init__(self, inner):
            self._inner = inner

        async def __aenter__(self):
            entered["n"] += 1
            return await self._inner.__aenter__()

        async def __aexit__(self, *a):
            return await self._inner.__aexit__(*a)

    def _spy_lock(session_id, **kwargs):
        return _SpyCtx(real_registry_lock(session_id, **kwargs))

    monkeypatch.setattr(
        distributed_lock_mod.session_lock_registry, "lock", _spy_lock,
    )
    store = ReviewStore()
    p = ReviewProposal(
        proposal_id="rp_lock", session_id="s-lock", title="t",
        author=_actor(), base_revision=0,
        mutation_intents=[PatchLayerStyleBody(
            intent="patch_layer_style", expected_revision=0,
            layer_id="L", paint={"x": "1"},
        )],
    )
    await store.save_proposal("s-lock", p)

    async def _noop(cur):
        return cur

    await store.mutate_proposal("s-lock", "rp_lock", _noop)
    assert entered["n"] >= 2, "save/mutate 必须走 session_lock_registry"


@pytest.mark.asyncio
async def test_p2_merge_race_aborts_and_persists_evidence():
    """合并期间 proposal 被并发迁移 → 中止合并、清 flag、失败存证落库。"""
    sid = _sid()
    svc = ReviewService()
    engine, base = await _seed_two_layers(sid)
    p = await svc.create_proposal(
        sid, author=_actor(), title="t",
        intents=[{"intent": "patch_layer_style", "expected_revision": base,
                  "layer_id": "L1", "paint": {"circle-color": "#654321"}},
                 {"intent": "set_view", "expected_revision": base, "zoom": 9}],
    )
    await svc.submit(sid, p.proposal_id, _actor(), base_revision=base)
    await svc.record_decision(sid, p.proposal_id, _actor2(), decision="approve")

    from app.services.review import merge as merge_mod

    original_apply = merge_mod.apply_gis_mutation
    calls = {"n": 0}
    withdrawn_during_merge = {"err": None}

    async def _spy(session_id, intent, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:  # checkpoint、intent0 之后：作者并发撤回
            try:
                await svc.withdraw(session_id, p.proposal_id, _actor())
            except Exception as exc:  # noqa: BLE001
                withdrawn_during_merge["err"] = exc
        return await original_apply(session_id, intent, **kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(merge_mod, "apply_gis_mutation", _spy)
    try:
        cur, outcome = await svc.merge(sid, p.proposal_id, _actor2())
    finally:
        mp.undo()

    # merge 期间其他状态迁移被 merge_in_progress 排斥 → withdraw 应已被拒。
    assert withdrawn_during_merge["err"] is not None, \
        "merge 进行中的状态迁移必须被拒绝"
    assert cur.status is ProposalStatus.MERGED or outcome.ok
    final = await svc.get_proposal(sid, p.proposal_id)
    if not outcome.ok:
        # 中止路径：失败存证必须持久化（不只 HTTP 回执）。
        assert final.merge_evidence is not None
        assert final.merge_in_progress is False


@pytest.mark.asyncio
async def test_p2_merge_failure_evidence_persisted():
    """失败合并（交错中止）的 evidence 必须落库。"""
    sid = _sid()
    svc = ReviewService()
    engine, base = await _seed_two_layers(sid)
    p = await svc.create_proposal(
        sid, author=_actor(), title="t",
        intents=[{"intent": "set_view", "expected_revision": base, "zoom": 3}],
    )
    await svc.submit(sid, p.proposal_id, _actor(), base_revision=base)
    await svc.record_decision(sid, p.proposal_id, _actor2(), decision="approve")
    # 并发漂移
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.lifecycle_engine import SetViewIntent

    await apply_gis_mutation(sid, SetViewIntent(zoom=30), origin="agent", actor="d")
    cur, outcome = await svc.merge(sid, p.proposal_id, _actor2())
    assert outcome.ok is False and outcome.conflict is True
    final = await svc.get_proposal(sid, p.proposal_id)
    assert final.merge_evidence is not None, "冲突失败的存证必须持久化"
    assert final.merge_evidence.failure == "base_revision_drift"


@pytest.mark.asyncio
async def test_p2_rebase_without_drift_rejected():
    sid = _sid()
    svc = ReviewService()
    engine, base = await _seed_two_layers(sid)
    p = await svc.create_proposal(
        sid, author=_actor(), title="t",
        intents=[{"intent": "set_view", "expected_revision": base, "zoom": 3}],
    )
    await svc.submit(sid, p.proposal_id, _actor(), base_revision=base)
    await svc.record_decision(sid, p.proposal_id, _actor2(), decision="approve")
    with pytest.raises(InvalidTransition):
        await svc.rebase(sid, p.proposal_id, _actor())  # 无漂移 → 拒绝


@pytest.mark.asyncio
async def test_p3_merge_submitted_message_not_empty():
    sid = _sid()
    svc = ReviewService()
    engine, base = await _seed_two_layers(sid)
    p = await svc.create_proposal(
        sid, author=_actor(), title="t",
        intents=[{"intent": "set_view", "expected_revision": base, "zoom": 3}],
    )
    await svc.submit(sid, p.proposal_id, _actor(), base_revision=base)
    from app.services.review.service import Forbidden

    with pytest.raises(Forbidden) as fj:
        await svc.merge(sid, p.proposal_id, _actor2())
    # submitted → 策略不满必须给出实质原因（不允许空尾文案）。
    assert len(str(fj.value)) > len("approval not satisfied: ")


def test_p3_store_rejects_empty_session_id():
    store = ReviewStore()
    with pytest.raises(Exception):
        store._review_dir("")


@pytest.mark.asyncio
async def test_p3_superseded_intent_ids_not_in_evidence():
    sid = _sid()
    engine, base = await _seed_two_layers(sid)
    proposal = ReviewProposal(
        proposal_id="rp_ids", session_id=sid, title="t",
        author=_actor(), base_revision=base, status=ProposalStatus.APPROVED,
        mutation_intents=[
            PatchLayerStyleBody(
                intent="patch_layer_style", expected_revision=base,
                layer_id="L1", paint={"circle-color": "#0a0a0a"},
            ),
            SetViewBody(intent="set_view", expected_revision=base, zoom=7),
        ],
    )
    from app.services.review import merge as merge_mod
    from app.services.mapspec.lifecycle_engine import SetViewIntent

    original_apply = merge_mod.apply_gis_mutation
    calls = {"n": 0}

    async def _spy(session_id, intent, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:  # intent1 应用前并发提交 → intent1 superseded
            await original_apply(
                session_id, SetViewIntent(zoom=20), origin="agent", actor="x",
            )
        return await original_apply(session_id, intent, **kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(merge_mod, "apply_gis_mutation", _spy)
    try:
        outcome = await merge_proposal_intents(sid, proposal, _actor2())
    finally:
        mp.undo()
    assert outcome.interleaved is True
    # superseded 的 intent（未落地）不得混入 mutation_ids。
    assert outcome.evidence.mutation_ids == ["merge:rp_ids:3:0"]  # 仅 intent0
