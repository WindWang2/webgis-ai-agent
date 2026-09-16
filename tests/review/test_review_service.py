"""ReviewService 编排测试：状态机、写时策略强制、bus 事件、冲突投影。

全部走真实引擎 + in-memory 会话态；bus 断言经本地 listener 捕获。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.schemas.mapspec_mutation_schema import (
    PatchLayerStyleBody,
    RemoveLayerBody,
    SetViewBody,
)
from app.schemas.review_schema import (
    Anchor,
    AnchorKind,
    ProposalStatus,
    ReviewActor,
)
from app.services.collab import bus as bus_mod
from app.services.review.service import (
    Forbidden,
    InvalidTransition,
    ProposalNotFound,
    ReviewService,
)


def _sid():
    return f"rv-svc-{uuid4().hex[:8]}"


def _actor(uid="u1", kind="user", role="editor"):
    return ReviewActor(actor_id=uid, actor_kind=kind, role=role)


async def _seed_layer(sid):
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
    return engine, r.mutation_revision


def _intents(base):
    return [{
        "intent": "patch_layer_style", "expected_revision": base,
        "layer_id": "L1", "paint": {"circle-color": "#654321"},
    }]


@pytest.fixture
def service():
    return ReviewService()


@pytest.fixture
def events():
    got = []

    async def _sink(envelope):
        got.append(envelope)

    token = bus_mod.bus._listeners.setdefault("x", set())
    yield got
    bus_mod.bus._listeners.pop("x", None)


async def _listen(sid, got):
    remove = await bus_mod.bus.add_local_listener(sid, lambda env: got.append(env))
    return remove


@pytest.mark.asyncio
async def test_full_flow_proposal_comment_approve_merge(service):
    sid = _sid()
    engine, base = await _seed_layer(sid)
    events = []
    remove = await _listen(sid, events)
    try:
        p = await service.create_proposal(
            sid, author=_actor("u1"), title="河流改色",
            description="对齐 Class 2", intents=_intents(base),
        )
        assert p.status is ProposalStatus.DRAFT
        submitted = await service.submit(sid, p.proposal_id, _actor("u1"), base_revision=base)
        assert submitted.status is ProposalStatus.SUBMITTED
        p2 = await service.add_comment(
            sid, p.proposal_id, _actor("u2", role="viewer"),
            body="这个断点对齐 Class 2 吗", anchor=Anchor(kind=AnchorKind.LAYER, id="L1"),
        )
        assert len(p2.comments) == 1
        p3 = await service.record_decision(
            sid, p.proposal_id, _actor("u2", role="editor"),
            decision="approve", reason="LGTM",
        )
        assert p3.status is ProposalStatus.APPROVED
        merged, outcome = await service.merge(sid, p.proposal_id, _actor("u1"))
        assert merged.status is ProposalStatus.MERGED
        assert outcome.ok
        assert merged.merge_evidence is not None
        # 权威状态验证（不是 mock 计数）。
        final = await engine.store.get_mapspec(sid)
        layer = next(ly for ly in final["layers"] if ly["id"] == "L1")
        assert layer["paint"]["circle-color"] == "#654321"
        # review 事件到达总线（新 kind additive）。
        kinds = [e.get("kind") for e in events]
        assert "review" in kinds
        review_events = [e for e in events if e.get("kind") == "review"]
        assert any(d["data"].get("event") == "state" and d["data"]["status"] == "merged"
                   for d in review_events)
    finally:
        remove()


@pytest.mark.asyncio
async def test_agent_cannot_record_decision(service):
    sid = _sid()
    engine, base = await _seed_layer(sid)
    p = await service.create_proposal(
        sid, author=_actor("u1"), title="t", intents=_intents(base),
    )
    await service.submit(sid, p.proposal_id, _actor("u1"), base_revision=base)
    with pytest.raises(Forbidden):
        await service.record_decision(
            sid, p.proposal_id,
            _actor("agent:carto", kind="agent", role="viewer"),
            decision="approve",
        )


@pytest.mark.asyncio
async def test_agent_authored_low_risk_requires_human_approval(service):
    sid = _sid()
    engine, base = await _seed_layer(sid)
    p = await service.create_proposal(
        sid, author=_actor("agent:carto", kind="agent", role="viewer"),
        title="agent 提案", intents=_intents(base),
    )
    await service.submit(sid, p.proposal_id, _actor("agent:carto", kind="agent"), base_revision=base)
    # 人类批准后才能合并（由其他 actor 合并也行 —— 合并者不必是作者）。
    with pytest.raises(Forbidden):
        await service.merge(sid, p.proposal_id, _actor("u9", role="viewer"))
    p2 = await service.record_decision(
        sid, p.proposal_id, _actor("u2", role="editor"), decision="approve",
    )
    assert p2.status is ProposalStatus.APPROVED
    merged, outcome = await service.merge(sid, p.proposal_id, _actor("u9"))
    assert merged.status is ProposalStatus.MERGED


@pytest.mark.asyncio
async def test_high_risk_requires_distinct_editor(service):
    sid = _sid()
    engine, base = await _seed_layer(sid)
    p = await service.create_proposal(
        sid, author=_actor("u1"), title="删层",
        intents=[{
            "intent": "remove_layer", "expected_revision": base, "layer_id": "L1",
        }],
    )
    assert p.risk.value == "high"
    await service.submit(sid, p.proposal_id, _actor("u1"), base_revision=base)
    # 自批拒绝
    with pytest.raises(Forbidden):
        await service.record_decision(
            sid, p.proposal_id, _actor("u1", role="admin"), decision="approve",
        )
    # 他人但 viewer 拒绝
    with pytest.raises(Forbidden):
        await service.record_decision(
            sid, p.proposal_id, _actor("u2", role="viewer"), decision="approve",
        )
    # 他人 editor 通过
    p2 = await service.record_decision(
        sid, p.proposal_id, _actor("u2", role="editor"), decision="approve",
    )
    assert p2.status is ProposalStatus.APPROVED


@pytest.mark.asyncio
async def test_anonymous_high_risk_failclosed(service):
    sid = _sid()
    engine, base = await _seed_layer(sid)
    anon = _actor("anonymous", role="anonymous")
    p = await service.create_proposal(
        sid, author=anon, title="匿名删层",
        intents=[{"intent": "remove_layer", "expected_revision": base, "layer_id": "L1"}],
    )
    await service.submit(sid, p.proposal_id, anon, base_revision=base)
    with pytest.raises(Forbidden):
        await service.record_decision(
            sid, p.proposal_id, anon, decision="approve",
        )


@pytest.mark.asyncio
async def test_merge_refused_when_base_drifted(service):
    sid = _sid()
    engine, base = await _seed_layer(sid)
    p = await service.create_proposal(
        sid, author=_actor("u1"), title="t", intents=_intents(base),
    )
    await service.submit(sid, p.proposal_id, _actor("u1"), base_revision=base)
    await service.record_decision(
        sid, p.proposal_id, _actor("u2", role="editor"), decision="approve",
    )
    # base 漂移：外部再动一次 spec。
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.lifecycle_engine import SetViewIntent

    await apply_gis_mutation(
        sid, SetViewIntent(zoom=5), origin="agent", actor="drift",
    )
    proposal, outcome = await service.merge(sid, p.proposal_id, _actor("u1"))
    assert outcome.ok is False and outcome.conflict is True
    # 状态保持 approved（可 rebase），未被静默合并。
    current = await service.get_proposal(sid, p.proposal_id)
    assert current.status is ProposalStatus.APPROVED
    final = await engine.store.get_mapspec(sid)
    layer = next(ly for ly in final["layers"] if ly["id"] == "L1")
    assert layer["paint"]["circle-color"] == "#123456"  # 未被覆盖


@pytest.mark.asyncio
async def test_rebase_updates_base_and_invalidates_approvals(service):
    sid = _sid()
    engine, base = await _seed_layer(sid)
    p = await service.create_proposal(
        sid, author=_actor("u1"), title="t", intents=_intents(base),
    )
    await service.submit(sid, p.proposal_id, _actor("u1"), base_revision=base)
    await service.record_decision(
        sid, p.proposal_id, _actor("u2", role="editor"), decision="approve",
    )
    p_ok = await service.get_proposal(sid, p.proposal_id)
    assert p_ok.status is ProposalStatus.APPROVED
    # 外部推进 spec
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.lifecycle_engine import SetViewIntent

    drift = await apply_gis_mutation(
        sid, SetViewIntent(zoom=6), origin="agent", actor="drift",
    )
    new_base = int(drift.mutation_revision)
    # rebase 后回到 submitted（旧批准过期），需重审。
    p2 = await service.rebase(sid, p.proposal_id, _actor("u1"))
    assert p2.base_revision == new_base
    assert p2.status is ProposalStatus.SUBMITTED
    # 旧批准不再计数：merge 被拒。
    with pytest.raises(Forbidden):
        await service.merge(sid, p.proposal_id, _actor("u1"))
    # 重批后可合并
    await service.record_decision(
        sid, p.proposal_id, _actor("u2", role="editor"), decision="approve",
    )
    merged, outcome = await service.merge(sid, p.proposal_id, _actor("u1"))
    assert outcome.ok


@pytest.mark.asyncio
async def test_rebase_refuses_when_target_gone(service):
    sid = _sid()
    engine, base = await _seed_layer(sid)
    p = await service.create_proposal(
        sid, author=_actor("u1"), title="改色", intents=_intents(base),
    )
    await service.submit(sid, p.proposal_id, _actor("u1"), base_revision=base)
    # 删除 L1（用户直改）
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.lifecycle_engine import RemoveLayerIntent

    await apply_gis_mutation(
        sid, RemoveLayerIntent(layer_id="L1"), origin="agent", actor="cleanup",
    )
    with pytest.raises(InvalidTransition):
        await service.rebase(sid, p.proposal_id, _actor("u1"))


@pytest.mark.asyncio
async def test_state_machine_guard(service):
    sid = _sid()
    engine, base = await _seed_layer(sid)
    p = await service.create_proposal(
        sid, author=_actor("u1"), title="t", intents=_intents(base),
    )
    with pytest.raises(InvalidTransition):
        await service.merge(sid, p.proposal_id, _actor("u1"))  # draft 不能合并
    with pytest.raises(InvalidTransition):
        await service.record_decision(
            sid, p.proposal_id, _actor("u2", role="editor"), decision="approve",
        )  # draft 不能批
    await service.withdraw(sid, p.proposal_id, _actor("u1"))
    p2 = await service.get_proposal(sid, p.proposal_id)
    assert p2.status is ProposalStatus.WITHDRAWN
    with pytest.raises(InvalidTransition):
        await service.submit(sid, p.proposal_id, _actor("u1"), base_revision=base)


@pytest.mark.asyncio
async def test_not_found(service):
    with pytest.raises(ProposalNotFound):
        await service.get_proposal("s-x", "ghost")


@pytest.mark.asyncio
async def test_projection_reports_conflict_and_anchor_states(service):
    sid = _sid()
    engine, base = await _seed_layer(sid)
    p = await service.create_proposal(
        sid, author=_actor("u1"), title="t", intents=_intents(base),
    )
    await service.submit(sid, p.proposal_id, _actor("u1"), base_revision=base)
    await service.add_comment(
        sid, p.proposal_id, _actor("u2", role="viewer"),
        body="看这层", anchor=Anchor(kind=AnchorKind.LAYER, id="L1"),
    )
    proj = await service.get_proposal_projection(sid, p.proposal_id)
    assert proj["conflict"] is False
    assert proj["anchor_states"]
    # 制造漂移 + 删除锚层
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.lifecycle_engine import RemoveLayerIntent, SetViewIntent

    await apply_gis_mutation(sid, SetViewIntent(zoom=4), origin="agent", actor="d")
    await apply_gis_mutation(sid, RemoveLayerIntent(layer_id="L1"), origin="agent", actor="d")
    proj2 = await service.get_proposal_projection(sid, p.proposal_id)
    assert proj2["conflict"] is True
    assert proj2["current_revision"] > proj2["base_revision"]
    for cid, state in proj2["anchor_states"].items():
        assert state == "stale"
