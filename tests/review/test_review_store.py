"""ReviewStore 持久层测试：原子 JSON、有界、锁内变迁、损坏 fail-closed。"""
from __future__ import annotations

import json

import pytest

from app.schemas.review_schema import (
    Anchor,
    AnchorKind,
    AnchoredComment,
    ProposalStatus,
    ReviewActor,
    ReviewDecision,
    ReviewProposal,
)
from app.schemas.mapspec_mutation_schema import PatchLayerStyleBody
from app.services.review.store import (
    MAX_PROPOSALS_PER_SESSION,
    ReviewStore,
    ReviewStoreCorrupt,
    ReviewStoreFull,
)


def _actor(uid="u1", kind="user", role="editor"):
    return ReviewActor(actor_id=uid, actor_kind=kind, role=role)


def _proposal(pid="rp_1", base=7, title="t"):
    return ReviewProposal(
        proposal_id=pid, session_id="s-fix", title=title,
        author=_actor(), base_revision=base,
        mutation_intents=[
            PatchLayerStyleBody(
                intent="patch_layer_style", expected_revision=base,
                layer_id="L1", paint={"circle-color": "#0f0"},
            ),
        ],
    )


@pytest.fixture
def store(tmp_path):
    return ReviewStore(base_dir=tmp_path)


@pytest.mark.asyncio
async def test_missing_session_returns_empty(store):
    assert await store.list_proposals("nope") == []
    assert await store.get_proposal("nope", "rp_1") is None


@pytest.mark.asyncio
async def test_save_and_reload_round_trip(store):
    p = _proposal()
    await store.save_proposal("s-fix", p)
    loaded = await store.get_proposal("s-fix", "rp_1")
    assert loaded == p
    # 重开一个 store 实例（模拟进程重启）仍可读 —— 文件即真相。
    store2 = ReviewStore(base_dir=store.base_dir)
    assert await store2.get_proposal("s-fix", "rp_1") == p
    assert len(await store2.list_proposals("s-fix")) == 1


@pytest.mark.asyncio
async def test_update_in_place(store):
    await store.save_proposal("s-fix", _proposal())
    p = await store.get_proposal("s-fix", "rp_1")
    p.status = ProposalStatus.SUBMITTED
    await store.save_proposal("s-fix", p)
    assert (await store.get_proposal("s-fix", "rp_1")).status is ProposalStatus.SUBMITTED


@pytest.mark.asyncio
async def test_comments_and_decisions_persist(store):
    p = _proposal()
    p.comments.append(AnchoredComment(
        comment_id="rc_1", author=_actor("u2"),
        body="颜色断点建议对齐 Class 2",
        anchor=Anchor(kind=AnchorKind.LAYER, id="L1"),
    ))
    p.decisions.append(ReviewDecision(
        decision_id="rd_1", decision="approve", actor=_actor("u2"),
        base_revision_at_decision=7,
    ))
    await store.save_proposal("s-fix", p)
    loaded = await store.get_proposal("s-fix", "rp_1")
    assert loaded.comments[0].anchor.id == "L1"
    assert loaded.decisions[0].decision == "approve"


@pytest.mark.asyncio
async def test_proposal_capacity_failclosed(store):
    for i in range(MAX_PROPOSALS_PER_SESSION):
        await store.save_proposal("s-fix", _proposal(pid=f"rp_{i}"))
    with pytest.raises(ReviewStoreFull):
        await store.save_proposal("s-fix", _proposal(pid="rp_new"))
    # 更新已有 proposal 不受容量约束。
    await store.save_proposal("s-fix", _proposal(pid="rp_0", title="updated"))


@pytest.mark.asyncio
async def test_corrupt_file_failclosed(store, tmp_path):
    await store.save_proposal("s-fix", _proposal())
    path = store._review_dir("s-fix") / "proposals.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ReviewStoreCorrupt):
        await store.list_proposals("s-fix")


@pytest.mark.asyncio
async def test_atomic_write_no_tmp_leftover(store):
    await store.save_proposal("s-fix", _proposal())
    rdir = store._review_dir("s-fix")
    files = [f.name for f in rdir.iterdir()]
    assert files == ["proposals.json"]
    data = json.loads((rdir / "proposals.json").read_text(encoding="utf-8"))
    assert data["version"] == 1


@pytest.mark.asyncio
async def test_mutate_serializes_transitions(store):
    """锁内 read-modify-write：并发 submit + withdraw 只有一个胜出。"""
    await store.save_proposal("s-fix", _proposal())

    applied = []

    async def _flip_to(p):
        if p.status is ProposalStatus.DRAFT:
            p.status = ProposalStatus.SUBMITTED
            applied.append("submit")
        return p

    async def _flip_to_withdraw(p):
        if p.status is ProposalStatus.DRAFT:
            p.status = ProposalStatus.WITHDRAWN
            applied.append("withdraw")
        return p

    import asyncio

    results = await asyncio.gather(
        store.mutate_proposal("s-fix", "rp_1", _flip_to),
        store.mutate_proposal("s-fix", "rp_1", _flip_to_withdraw),
    )
    final = await store.get_proposal("s-fix", "rp_1")
    # 恰好一个迁移生效，另一个看到非 DRAFT 原样返回。
    statuses = {r.status for r in results}
    assert len(applied) == 1
    assert final.status in {ProposalStatus.SUBMITTED, ProposalStatus.WITHDRAWN}
    assert final.status in statuses


@pytest.mark.asyncio
async def test_mutate_missing_proposal(store):
    async def _noop(p):
        return p

    assert await store.mutate_proposal("s-fix", "ghost", _noop) is None
