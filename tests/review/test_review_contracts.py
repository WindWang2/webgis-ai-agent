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
    TRANSITIONS,
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


class TestTransitions:
    """状态机闭包真相（policy.TRANSITIONS）契约。"""

    def test_changes_requested_can_reach_approved(self):
        """review #1722：CHANGES_REQUESTED 下 approve 必须能翻态（不再被吞）。"""
        assert ProposalStatus.APPROVED in TRANSITIONS[ProposalStatus.CHANGES_REQUESTED]
        assert ProposalStatus.SUBMITTED in TRANSITIONS[ProposalStatus.CHANGES_REQUESTED]

    def test_terminal_statuses_have_no_out_edges(self):
        for st in (
            ProposalStatus.REJECTED, ProposalStatus.MERGED,
            ProposalStatus.SUPERSEDED, ProposalStatus.WITHDRAWN,
        ):
            assert TRANSITIONS[st] == frozenset(), f"{st} 必须无出边"

    def test_every_status_has_table_entry(self):
        assert set(TRANSITIONS) == set(ProposalStatus)


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
        # review #1744-7：审批者身份按 (actor_id, actor_kind) 双重判定 ——
        # 同 id 不同 kind 是**不同主体**（agent 作者 vs user 审批者），不算
        # 自批；「agent 冒名 user 记录决策」的 fail-closed 在写时门槛
        # （service._approve_gate：非 user 身份一律拒收决策），策略层与
        # service 层 `_same_person` 保持同一口径。
        v = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_1", decision="approve", actor=_actor("agent:carto"),
                base_revision_at_decision=7,
            )],
        )
        assert v.satisfied and v.counted_approvals == 1
        # agent 审批者的 approve 永不计数（防御纵深）
        v_agent = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_1b", decision="approve",
                actor=_actor("agent:carto", kind="agent"),
                base_revision_at_decision=7,
            )],
        )
        assert not v_agent.satisfied
        # agent 作者永远要 distinct human：他人（user）审批才满足
        v2 = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_2", decision="approve", actor=_actor("u1"),
                base_revision_at_decision=7,
            )],
        )
        assert v2.satisfied and v2.counted_approvals == 1

    def test_self_approval_compares_id_and_kind(self):
        """review #1744-7：自批判定 = (actor_id, actor_kind) 双重相同
        （与 service._same_person 一致）。高风险 user 作者 + 同 id 同 kind
        的审批者必须拒绝；仅 id 相同而 kind 不同不构成自批。"""
        p = self._proposal(risk=ProposalRisk.HIGH)
        v_same = evaluate_approval(
            ApprovalPolicy(), p,
            approvals=[ReviewDecision(
                decision_id="rd_1", decision="approve",
                actor=_actor("u1", role="admin"),
                base_revision_at_decision=7,
            )],
        )
        assert not v_same.satisfied
        assert any("distinct" in r for r in v_same.blocking_reasons)
        # 同 id 但 kind 不同：不构成自批（该 approve 由 user 主体作出）
        p_agent_author = self._proposal(
            risk=ProposalRisk.HIGH,
            author=_actor("shared-id", kind="agent", role="viewer"),
        )
        v_cross_kind = evaluate_approval(
            ApprovalPolicy(), p_agent_author,
            approvals=[ReviewDecision(
                decision_id="rd_2", decision="approve",
                actor=_actor("shared-id", role="editor"),
                base_revision_at_decision=7,
            )],
        )
        assert not any("distinct" in r for r in v_cross_kind.blocking_reasons)

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
