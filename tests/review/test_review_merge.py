"""Merge 引擎测试：CAS 链式回放、checkpoint 回滚、并发交错保护、冲突拒绝。

全部走真实 MapSpecLifecycleEngine（in-memory 会话态，conftest 钉 USE_REDIS=false）。
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
    MergeEvidence,
    ProposalRisk,
    ProposalStatus,
    ReviewActor,
    ReviewProposal,
)
from app.services.review.merge import MergeOutcome, merge_proposal_intents
from app.services.review.policy import ApprovalPolicy


def _sid():
    return f"rv-merge-{uuid4().hex[:8]}"


def _actor(uid="u1", kind="user", role="editor"):
    return ReviewActor(actor_id=uid, actor_kind=kind, role=role)


def _proposal(sid, base, intents, pid="rp_m1"):
    return ReviewProposal(
        proposal_id=pid, session_id=sid, title="merge probe",
        author=_actor(), base_revision=base, mutation_intents=intents,
        status=ProposalStatus.APPROVED,
    )


async def _seed_two_layers(sid):
    from app.services.mapspec.lifecycle_engine import (
        InitProjectIntent,
        MapSpecLifecycleEngine,
        UpsertLayerIntent,
    )

    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    for lid, color in (("L1", "#111111"), ("L2", "#222222")):
        r = await engine.apply_mutation(
            sid,
            UpsertLayerIntent(
                layer={"id": lid, "source": "s", "type": "circle", "paint": {"circle-color": color}},
                source_data={"type": "FeatureCollection", "features": []},
            ),
        )
    spec = r.mapspec
    rev = r.mutation_revision
    return engine, rev, spec


def _layer_paint(spec, layer_id):
    for ly in spec.get("layers", []):
        if ly.get("id") == layer_id:
            return ly.get("paint", {})
    return None


@pytest.mark.asyncio
async def test_merge_happy_path_chains_cas():
    sid = _sid()
    engine, base, spec = await _seed_two_layers(sid)
    proposal = _proposal(sid, base, [
        PatchLayerStyleBody(
            intent="patch_layer_style", expected_revision=base,
            layer_id="L1", paint={"circle-color": "#0f0f0f"},
        ),
        SetViewBody(intent="set_view", expected_revision=base, zoom=11),
    ])
    outcome = await merge_proposal_intents(sid, proposal, _actor("u2"))
    assert outcome.ok, outcome.failure
    assert outcome.evidence.merged_revision >= base + 2
    assert [s.index for s in outcome.evidence.applied] == [0, 1]
    assert outcome.evidence.checkpoint_id.startswith("mr_")
    assert outcome.evidence.rolled_back is False
    # 权威 spec 真的变了（最终状态断言，不是 mock 调用计数）。
    final = await engine.store.get_mapspec(sid)
    assert _layer_paint(final, "L1")["circle-color"] == "#0f0f0f"
    assert final["view"].get("zoom") == 11


@pytest.mark.asyncio
async def test_merge_refused_on_base_drift():
    sid = _sid()
    engine, base, spec = await _seed_two_layers(sid)
    # base 漂移：proposal 基于 rev=base-1（有人先动过）。
    proposal = _proposal(sid, base - 1, [
        SetViewBody(intent="set_view", expected_revision=base - 1, zoom=9),
    ])
    outcome = await merge_proposal_intents(sid, proposal, _actor("u2"))
    assert outcome.ok is False and outcome.conflict is True
    assert outcome.evidence.applied == []
    final = await engine.store.get_mapspec(sid)
    assert final["view"].get("zoom") != 9  # 未静默覆盖


@pytest.mark.asyncio
async def test_merge_failure_rolls_back_to_checkpoint():
    """第二个 intent 目标缺失（is_error）→ 整体回滚到 checkpoint。"""
    sid = _sid()
    engine, base, spec = await _seed_two_layers(sid)
    proposal = _proposal(sid, base, [
        PatchLayerStyleBody(
            intent="patch_layer_style", expected_revision=base,
            layer_id="L1", paint={"circle-color": "#0a0a0a"},
        ),
        # 缺失层上的样式补丁：引擎 pre-commit 报错（is_error）→ 触发回滚。
        PatchLayerStyleBody(
            intent="patch_layer_style", expected_revision=base,
            layer_id="L-gone", paint={"circle-color": "#0c0c0c"},
        ),
    ])
    outcome = await merge_proposal_intents(sid, proposal, _actor("u2"))
    assert outcome.ok is False
    assert outcome.rolled_back is True
    assert outcome.interleaved is False
    final = await engine.store.get_mapspec(sid)
    # 回滚诚实：L1 恢复原色（checkpoint 内容）。
    assert _layer_paint(final, "L1")["circle-color"] == "#111111"
    assert len(final.get("layers", [])) == 2
    assert outcome.evidence.rolled_back is True
    assert outcome.evidence.failure


@pytest.mark.asyncio
async def test_merge_interleaved_user_mutation_protected():
    """合并中途用户提交 → superseded → 不回滚（保护用户工作），interleaved 存证。"""
    sid = _sid()
    engine, base, spec = await _seed_two_layers(sid)
    proposal = _proposal(sid, base, [
        PatchLayerStyleBody(
            intent="patch_layer_style", expected_revision=base,
            layer_id="L1", paint={"circle-color": "#0b0b0b"},
        ),
        SetViewBody(intent="set_view", expected_revision=base, zoom=12),
    ])

    from app.services.review import merge as merge_mod

    original_apply = merge_mod.apply_gis_mutation
    calls = {"n": 0}

    async def _spy_apply(session_id, intent, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # checkpoint 之后、第 1 个 intent 前不动；第 2 个 intent 前插用户提交
            pass
        if calls["n"] == 3:  # 意图 i=1 应用前：并发方抢先 set_view（agent 通道免 expected_revision）
            await original_apply(
                session_id,
                __import__(
                    "app.services.mapspec.lifecycle_engine", fromlist=["SetViewIntent"]
                ).SetViewIntent(zoom=20),
                origin="agent", actor="interloper",
            )
        return await original_apply(session_id, intent, **kwargs)

    monkey_apply = pytest.MonkeyPatch()
    monkey_apply.setattr(merge_mod, "apply_gis_mutation", _spy_apply)
    try:
        outcome = await merge_proposal_intents(sid, proposal, _actor("u2"))
    finally:
        monkey_apply.undo()

    assert outcome.ok is False
    assert outcome.interleaved is True
    assert outcome.rolled_back is False  # 用户提交不得被回滚摧毁
    final = await engine.store.get_mapspec(sid)
    assert final["view"].get("zoom") == 20  # 用户工作幸存
    assert outcome.evidence.interleaved is True


@pytest.mark.asyncio
async def test_merge_high_risk_remove_layer_after_approval():
    sid = _sid()
    engine, base, spec = await _seed_two_layers(sid)
    proposal = _proposal(sid, base, [
        RemoveLayerBody(intent="remove_layer", expected_revision=base, layer_id="L2"),
    ])
    assert proposal.risk is ProposalRisk.HIGH
    outcome = await merge_proposal_intents(sid, proposal, _actor("u2"))
    assert outcome.ok, outcome.failure
    final = await engine.store.get_mapspec(sid)
    assert _layer_paint(final, "L2") is None
    assert len(final.get("layers", [])) == 1


@pytest.mark.asyncio
async def test_merge_evidence_records_policy_snapshot():
    sid = _sid()
    engine, base, _ = await _seed_two_layers(sid)
    proposal = _proposal(sid, base, [
        SetViewBody(intent="set_view", expected_revision=base, zoom=8),
    ])
    outcome = await merge_proposal_intents(
        sid, proposal, _actor("u2"),
        approvals_considered=["rd_ok"], policy=ApprovalPolicy(),
    )
    assert outcome.ok
    assert outcome.evidence.approvals_considered == ["rd_ok"]
    assert outcome.evidence.policy_snapshot["version"] == "v1"
    dumped = outcome.evidence.model_dump()
    assert "reasoning" not in dumped


@pytest.mark.asyncio
async def test_merge_empty_intents_rejected():
    from app.schemas.review_schema import ReviewProposal as RP

    sid = _sid()
    with pytest.raises(Exception):
        RP(
            proposal_id="rp_e", session_id=sid, title="t",
            author=_actor(), base_revision=0, mutation_intents=[],
        )
