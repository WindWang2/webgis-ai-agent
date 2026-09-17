"""ReviewProposal/AnchoredComment/ReviewDecision 契约测试（TDD 先行）。

验收面：字段边界、序列化往返、风险分级、审批策略纯函数矩阵。
边界语义（Oracle）：Agent 不自批（fail-closed）；高风险需 distinct human
reviewer 且 role≥editor；匿名会话高风险拒绝。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.review_schema import (
    Anchor,
    AnchorKind,
    AnchoredComment,
    MergeEvidence,
    ProposalRisk,
    ProposalStatus,
    ReviewActor,
    ReviewDecision,
    ReviewProposal,
    mapspec_intent_payload,
)
from app.schemas.mapspec_mutation_schema import (
    PatchLayerStyleBody,
    RemoveLayerBody,
    SetViewBody,
)
from app.services.review.policy import (
    ApprovalPolicy,
    classify_risk,
    evaluate_approval,
    proposal_targets,
)


def _actor(actor_id: str = "u1", kind: str = "user", role: str = "editor") -> ReviewActor:
    return ReviewActor(actor_id=actor_id, actor_kind=kind, role=role)


def _intents(revision: int = 7):
    return [
        PatchLayerStyleBody(
            intent="patch_layer_style", expected_revision=revision,
            layer_id="L1", paint={"circle-color": "#0f0"},
        ),
    ]


class TestContracts:
    def test_proposal_round_trip(self):
        p = ReviewProposal(
            proposal_id="rp_1", session_id="s1", title="重着色河流图层",
            author=_actor(), base_revision=7, mutation_intents=_intents(),
        )
        assert p.status is ProposalStatus.DRAFT
        assert p.risk is ProposalRisk.LOW
        data = p.model_dump(mode="json")
        p2 = ReviewProposal.model_validate(data)
        assert p2 == p

    def test_intent_union_discriminated(self):
        body = {
            "intent": "remove_layer", "expected_revision": 3, "layer_id": "L9",
        }
        payload = mapspec_intent_payload(body)
        assert isinstance(payload, RemoveLayerBody)
        with pytest.raises(ValidationError):
            mapspec_intent_payload({"intent": "nuke_everything"})

    def test_intent_bounds(self):
        with pytest.raises(ValidationError):
            PatchLayerStyleBody(
                intent="patch_layer_style", expected_revision=0,
                layer_id="", paint={},
            )
        # proposal intents 数量上界
        with pytest.raises(ValidationError):
            ReviewProposal(
                proposal_id="rp_2", session_id="s1", title="t",
                author=_actor(), base_revision=0,
                mutation_intents=_intents() * 51,
            )

    def test_comment_anchor_shapes(self):
        c = AnchoredComment(
            comment_id="rc_1", author=_actor("u2"),
            body="这条河流颜色应该用 Class 2 断点",
            anchor=Anchor(kind=AnchorKind.LAYER, id="L1"),
        )
        assert c.anchor is not None and c.anchor.kind is AnchorKind.LAYER
        # feature 锚必须带父 layer
        with pytest.raises(ValidationError):
            Anchor(kind=AnchorKind.FEATURE, id="f-1001")
        a = Anchor(kind=AnchorKind.FEATURE, id="f-1001", layer_id="L1")
        assert a.layer_id == "L1"
        # 空 body 拒绝
        with pytest.raises(ValidationError):
            AnchoredComment(comment_id="rc_2", author=_actor("u2"), body="")

    def test_decision_contract(self):
        d = ReviewDecision(
            decision_id="rd_1", decision="approve", actor=_actor("u2"),
            base_revision_at_decision=7, reason="LGTM",
        )
        assert d.counted is True
        # 非法 decision 词
        with pytest.raises(ValidationError):
            ReviewDecision(
                decision_id="rd_2", decision="meh", actor=_actor("u2"),
                base_revision_at_decision=7,
            )

    def test_merge_evidence_no_cot_field(self):
        """契约层面禁 CoT：MergeEvidence 无自由文本推理字段（failure 为短码）。"""
        me = MergeEvidence(
            merged_at="2026-09-17T00:00:00+00:00", actor=_actor(),
            proposal_id="rp_1", base_revision=7, merged_revision=9,
            checkpoint_id="merge_ckpt_rp_1", applied=[],
            mutation_ids=["merge:rp_1:0"], approvals_considered=["rd_1"],
            policy_snapshot={"min_approvals_high": 1},
        )
        dumped = set(me.model_dump().keys())
        assert "reasoning" not in dumped and "chain_of_thought" not in dumped
        assert not hasattr(me, "thoughts")

    def test_status_vocabulary(self):
        assert {s.value for s in ProposalStatus} == {
            "draft", "submitted", "changes_requested", "approved",
            "rejected", "merged", "superseded", "withdrawn",
        }


class TestRiskClassification:
    def test_low_risk_presentation(self):
        assert classify_risk(_intents()) is ProposalRisk.LOW

    def test_high_risk_remove(self):
        intents = [
            RemoveLayerBody(intent="remove_layer", expected_revision=7, layer_id="L1"),
        ]
        assert classify_risk(intents) is ProposalRisk.HIGH

    def test_high_risk_dominates(self):
        intents = [
            *_intents(),
            RemoveLayerBody(intent="remove_layer", expected_revision=7, layer_id="L2"),
        ]
        assert classify_risk(intents) is ProposalRisk.HIGH

    def test_high_risk_workbench_replace_and_rebind(self):
        from app.schemas.mapspec_mutation_schema import (
            SetWorkbenchStateBody,
            RebindComponentBody,
        )
        assert classify_risk([
            SetWorkbenchStateBody(
                intent="patch_workbench_state", expected_revision=7, doc={},
            ),
        ]) is ProposalRisk.HIGH
        assert classify_risk([
            RebindComponentBody(
                intent="rebind_component", expected_revision=7,
                component_id="c1", layer_id="L1",
            ),
        ]) is ProposalRisk.HIGH


class TestProposalTargets:
    def test_targets_extracted(self):
        intents = [
            PatchLayerStyleBody(
                intent="patch_layer_style", expected_revision=7,
                layer_id="L1", paint={"circle-color": "#0f0"},
            ),
            SetViewBody(intent="set_view", expected_revision=7, zoom=10),
        ]
        assert proposal_targets(intents) == {"L1"}


class TestApprovalPolicy:
    def _proposal(self, risk=ProposalRisk.LOW, author=None, base=7):
        return ReviewProposal(
            proposal_id="rp_p", session_id="s1", title="t",
            author=author or _actor("u1"), base_revision=base,
            mutation_intents=_intents(base), risk=risk, status=ProposalStatus.SUBMITTED,
        )

    def test_low_risk_self_approval_allowed(self):
        p = self._proposal()
        v = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_1", decision="approve", actor=_actor("u1"),
                base_revision_at_decision=7,
            )],
        )
        assert v.satisfied and v.counted_approvals == 1

    def test_agent_author_needs_distinct_human(self):
        p = self._proposal(author=_actor("agent:carto", kind="agent", role="viewer"))
        # agent 作者自批（假设 agent 冒名 user 决策）→ 不满足 distinct
        v = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_1", decision="approve", actor=_actor("agent:carto"),
                base_revision_at_decision=7,
            )],
        )
        assert not v.satisfied
        # 同人 distinct 缺席：自己批自己也不行（agent 作者永远要别人）
        v2 = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_2", decision="approve", actor=_actor("u1"),
                base_revision_at_decision=7,
            )],
        )
        assert v2.satisfied and v2.counted_approvals == 1

    def test_agent_approval_never_counts(self):
        """agent 记录的 approve 永不满足策略（即使审查者身份是别的 agent）。"""
        p = self._proposal(author=_actor("u1"))
        v = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_1", decision="approve",
                actor=_actor("agent:carto", kind="agent"),
                base_revision_at_decision=7,
            )],
        )
        assert not v.satisfied
        assert v.counted_approvals == 0

    def test_high_risk_requires_distinct_editor(self):
        p = self._proposal(risk=ProposalRisk.HIGH)
        # 自批拒绝
        v_self = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_1", decision="approve", actor=_actor("u1", role="admin"),
                base_revision_at_decision=7,
            )],
        )
        assert not v_self.satisfied
        # 同人 viewer 角色不够
        v_role = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_2", decision="approve", actor=_actor("u2", role="viewer"),
                base_revision_at_decision=7,
            )],
        )
        assert not v_role.satisfied
        # 他人 editor 通过
        v_ok = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_3", decision="approve", actor=_actor("u2", role="editor"),
                base_revision_at_decision=7,
            )],
        )
        assert v_ok.satisfied

    def test_anonymous_high_risk_failclosed(self):
        p = self._proposal(risk=ProposalRisk.HIGH, author=_actor("anonymous", role="anonymous"))
        v = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_1", decision="approve",
                actor=_actor("anonymous", role="anonymous"),
                base_revision_at_decision=7,
            )],
        )
        assert not v.satisfied
        assert any("anonymous" in r.lower() or "authenticated" in r.lower() for r in v.blocking_reasons)

    def test_stale_approval_base_revision_mismatch(self):
        """base revision 变化（rebase 后）旧 approve 不再计数。"""
        p = self._proposal()
        v = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_1", decision="approve", actor=_actor("u2"),
                base_revision_at_decision=6,
            )],
        )
        assert not v.satisfied

    def test_rejected_overrides_approvals(self):
        p = self._proposal()
        v = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[
                ReviewDecision(
                    decision_id="rd_1", decision="approve", actor=_actor("u2"),
                    base_revision_at_decision=7,
                ),
                ReviewDecision(
                    decision_id="rd_2", decision="reject", actor=_actor("u3"),
                    base_revision_at_decision=7,
                ),
            ],
        )
        assert not v.satisfied

    def test_policy_is_failclosed_by_default(self):
        pol = ApprovalPolicy()
        assert pol.agent_author_requires_human_approval is True
        assert pol.anonymous_high_risk_allowed is False
        assert pol.require_distinct_reviewer_high is True
