"""ReviewService —— 空间审查/会签编排（ADR-0201）。

写时策略强制（fail-closed）：
- agent 身份一律不得记录决策（Forbidden）——即使低风险；
- 自批：高风险 proposal 与 agent 作者的 proposal 一律拒绝；
- 高风险 approve 要求 reviewer role ≥ editor 且非匿名；
- merge 只能从 APPROVED（或 SUBMITTED + 策略复核通过前的显式拒绝），
  且复核 verdict 不满足即 Forbidden（rebase 后旧批准过期的兜底）。

状态机以 ``policy.TRANSITIONS`` 为闭包真相；decision/merge 全量落
proposal 内的 append-only 存证（comments/decisions/merge_evidence）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.schemas.review_schema import (
    Anchor,
    AnchoredComment,
    ProposalStatus,
    TERMINAL_STATUSES,
    ReviewActor,
    ReviewDecision,
    ReviewProposal,
    mapspec_intent_payload,
)
from app.services.review import anchors as anchor_mod
from app.services.review.merge import (
    MergeOutcome,
    merge_proposal_intents,
    read_current_revision,
)
from app.services.review.policy import (
    TRANSITIONS,
    ApprovalPolicy,
    evaluate_approval,
)
from app.services.review.store import ReviewStore

logger = logging.getLogger(__name__)


class ReviewServiceError(Exception):
    """review 服务错误基类（路由层映射 HTTP）。"""


class ProposalNotFound(ReviewServiceError):
    pass


class InvalidTransition(ReviewServiceError):
    """状态机非法迁移 / 目标校验失败（映射 409）。"""


class Forbidden(ReviewServiceError):
    """策略拒绝（映射 403；message 含机器可读原因）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _same_person(a: ReviewActor, b: ReviewActor) -> bool:
    return a.actor_id == b.actor_id and a.actor_kind == b.actor_kind


def _transition(proposal: ReviewProposal, to: ProposalStatus) -> None:
    allowed = TRANSITIONS.get(proposal.status, frozenset())
    if to not in allowed:
        raise InvalidTransition(
            f"cannot move proposal from {proposal.status.value} to {to.value}"
        )
    proposal.status = to


class ReviewService:
    def __init__(
        self,
        store: Optional[ReviewStore] = None,
        policy: Optional[ApprovalPolicy] = None,
    ) -> None:
        self.store = store or ReviewStore()
        self.policy = policy or ApprovalPolicy()

    # ── 内部 ────────────────────────────────────────────────────────────
    async def _must_get(self, session_id: str, proposal_id: str) -> ReviewProposal:
        p = await self.store.get_proposal(session_id, proposal_id)
        if p is None:
            raise ProposalNotFound(f"proposal {proposal_id} not found in {session_id}")
        return p

    def _require_author(self, proposal: ReviewProposal, actor: ReviewActor) -> None:
        if not _same_person(proposal.author, actor):
            raise Forbidden(
                f"actor {actor.actor_id} is not the author of {proposal.proposal_id}"
            )

    def _approve_gate(
        self, proposal: ReviewProposal, actor: ReviewActor,
    ) -> None:
        """单条 approve 决策的写时门槛（比聚合评估更严：直接拒绝违规尝试）。"""
        if actor.actor_kind != "user":
            raise Forbidden("agents cannot record review decisions (fail-closed)")
        is_high = proposal.risk is not None and proposal.risk.value == "high"
        author_is_agent = proposal.author.actor_kind == "agent"
        if (is_high or author_is_agent) and actor.actor_id == proposal.author.actor_id \
                and actor.actor_kind == proposal.author.actor_kind:
            raise Forbidden(
                "self-approval refused: distinct human reviewer required"
            )
        if is_high:
            if actor.role == "anonymous":
                raise Forbidden(
                    "authenticated (non-anonymous) reviewers required for high-risk proposals"
                )
            if actor.role not in ("editor", "admin"):
                raise Forbidden(
                    "reviewer role editor+ required for high-risk proposals"
                )

    async def _publish(self, session_id: str, event: str, proposal: ReviewProposal,
                       actor: ReviewActor) -> None:
        """协作总线 review 事件（通知平面；失败绝不影响主流程）。"""
        try:
            from app.services.collab.bus import bus

            await bus.publish(
                session_id,
                "review",
                {
                    "proposalId": proposal.proposal_id,
                    "event": event,
                    "status": proposal.status.value,
                    "actor": actor.actor_id,
                    "risk": proposal.risk.value if proposal.risk else "low",
                },
            )
        except Exception:  # noqa: BLE001 — 通知平面不倒灌
            logger.debug("[review] bus publish skipped", exc_info=True)

    # ── 查询 ────────────────────────────────────────────────────────────
    async def list_proposals(self, session_id: str) -> List[ReviewProposal]:
        return await self.store.list_proposals(session_id)

    async def get_proposal(self, session_id: str, proposal_id: str) -> ReviewProposal:
        return await self._must_get(session_id, proposal_id)

    async def get_proposal_projection(
        self, session_id: str, proposal_id: str,
    ) -> Dict[str, Any]:
        """详情投影：proposal + 锚态 + 冲突投影 + 策略 verdict（前端 drawer 直用）。"""
        p = await self._must_get(session_id, proposal_id)
        from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

        engine = MapSpecLifecycleEngine()
        current_rev = await read_current_revision(session_id)
        spec = None
        try:
            state = None
            spec = await engine.store.get_mapspec(session_id, state_hint=state)
        except Exception:  # noqa: BLE001 — 投影尽力而为
            spec = None
        anchor_states = await anchor_mod.proposal_anchor_states(session_id, p, spec)
        verdict = evaluate_approval(self.policy, p, p.decisions)
        return {
            "proposal": p.model_dump(mode="json"),
            "anchor_states": {k: s.value for k, s in anchor_states.items()},
            "conflict": current_rev != p.base_revision,
            "current_revision": current_rev,
            "base_revision": p.base_revision,
            "policy": {
                "snapshot": self.policy.policy_snapshot(),
                "satisfied": verdict.satisfied,
                "counted_approvals": verdict.counted_approvals,
                "blocking_reasons": verdict.blocking_reasons,
            },
        }

    # ── 写路径 ──────────────────────────────────────────────────────────
    async def create_proposal(
        self,
        session_id: str,
        author: ReviewActor,
        title: str,
        intents: List[dict],
        description: str = "",
    ) -> ReviewProposal:
        try:
            payloads = [mapspec_intent_payload(body) for body in intents]
        except ValidationError as exc:
            raise ReviewServiceError(f"malformed mutation intent: {exc}") from exc
        now = _now()
        proposal = ReviewProposal(
            proposal_id=_new_id("rp"),
            session_id=session_id,
            title=title,
            description=description,
            author=author,
            base_revision=await read_current_revision(session_id),
            mutation_intents=payloads,
            status=ProposalStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        await self.store.save_proposal(session_id, proposal)
        await self._publish(session_id, "state", proposal, author)
        return proposal

    async def submit(
        self,
        session_id: str,
        proposal_id: str,
        actor: ReviewActor,
        base_revision: int,
    ) -> ReviewProposal:
        def _mutate(p: ReviewProposal) -> ReviewProposal:
            self._require_author(p, actor)
            _transition(p, ProposalStatus.SUBMITTED)
            p.base_revision = int(base_revision)
            p.updated_at = _now()
            return p

        updated = await self.store.mutate_proposal(session_id, proposal_id, _mutate)
        if updated is None:
            raise ProposalNotFound(proposal_id)
        await self._publish(session_id, "state", updated, actor)
        return updated

    async def add_comment(
        self,
        session_id: str,
        proposal_id: str,
        actor: ReviewActor,
        body: str,
        anchor: Optional[Anchor] = None,
    ) -> ReviewProposal:
        comment = AnchoredComment(
            comment_id=_new_id("rc"),
            author=actor,
            body=body,
            anchor=anchor,
            created_at=_now(),
        )

        def _mutate(p: ReviewProposal) -> ReviewProposal:
            from app.schemas.review_schema import MAX_COMMENTS_PER_PROPOSAL

            if len(p.comments) >= MAX_COMMENTS_PER_PROPOSAL:
                raise InvalidTransition("comment bound exceeded")
            p.comments.append(comment)
            p.updated_at = _now()
            return p

        updated = await self.store.mutate_proposal(session_id, proposal_id, _mutate)
        if updated is None:
            raise ProposalNotFound(proposal_id)
        await self._publish(session_id, "comment", updated, actor)
        return updated

    async def record_decision(
        self,
        session_id: str,
        proposal_id: str,
        actor: ReviewActor,
        decision: str,
        reason: str = "",
    ) -> ReviewProposal:
        if decision not in ("approve", "request_changes", "reject"):
            raise InvalidTransition(f"unknown decision {decision!r}")
        if actor.actor_kind != "user":
            # 决策是审批权：agent 一律不受理（评论可以，审批不行）。
            raise Forbidden("agents cannot record review decisions (fail-closed)")

        def _mutate(p: ReviewProposal) -> ReviewProposal:
            entry = ReviewDecision(
                decision_id=_new_id("rd"),
                decision=decision,  # type: ignore[arg-type]
                actor=actor,
                reason=reason,
                base_revision_at_decision=p.base_revision,
                created_at=_now(),
            )
            if decision == "approve":
                self._approve_gate(p, actor)
                if p.status is ProposalStatus.DRAFT or p.status in TERMINAL_STATUSES:
                    raise InvalidTransition(
                        f"cannot approve proposal in status {p.status.value}"
                    )
                p.decisions.append(entry)
                verdict = evaluate_approval(self.policy, p, p.decisions)
                if verdict.satisfied and p.status is ProposalStatus.SUBMITTED:
                    p.status = ProposalStatus.APPROVED
            elif decision == "request_changes":
                if p.status is not ProposalStatus.SUBMITTED:
                    raise InvalidTransition(
                        f"request_changes requires submitted; status={p.status.value}"
                    )
                p.decisions.append(entry)
                _transition(p, ProposalStatus.CHANGES_REQUESTED)
            else:  # reject
                if p.status not in (ProposalStatus.SUBMITTED, ProposalStatus.APPROVED):
                    raise InvalidTransition(
                        f"reject requires submitted/approved; status={p.status.value}"
                    )
                p.decisions.append(entry)
                _transition(p, ProposalStatus.REJECTED)
            p.updated_at = _now()
            return p

        updated = await self.store.mutate_proposal(session_id, proposal_id, _mutate)
        if updated is None:
            raise ProposalNotFound(proposal_id)
        await self._publish(session_id, "decision", updated, actor)
        return updated

    async def merge(
        self,
        session_id: str,
        proposal_id: str,
        actor: ReviewActor,
    ) -> Tuple[ReviewProposal, MergeOutcome]:
        p = await self._must_get(session_id, proposal_id)
        if p.status is ProposalStatus.SUBMITTED:
            verdict = evaluate_approval(self.policy, p, p.decisions)
            raise Forbidden(
                "approval not satisfied: " + "; ".join(verdict.blocking_reasons)
            )
        if p.status is not ProposalStatus.APPROVED:
            raise InvalidTransition(
                f"merge requires approved proposal; status={p.status.value}"
            )
        verdict = evaluate_approval(self.policy, p, p.decisions)
        if not verdict.satisfied:
            raise Forbidden(
                "approval recheck failed: " + "; ".join(verdict.blocking_reasons)
            )
        counted_ids = [
            d.decision_id for d in p.decisions
            if d.decision == "approve"
            and d.base_revision_at_decision == p.base_revision
            and d.actor.actor_kind == "user"
        ]
        outcome = await merge_proposal_intents(
            session_id, p, actor,
            policy=self.policy,
            approvals_considered=counted_ids,
        )
        if not outcome.ok:
            # 冲突/失败：proposal 保持 approved（可 rebase 重审），存证留痕。
            def _keep(p: ReviewProposal) -> ReviewProposal:
                p.updated_at = _now()
                return p

            await self.store.mutate_proposal(session_id, proposal_id, _keep)
            return p, outcome

        def _mark_merged(cur: ReviewProposal) -> ReviewProposal:
            if cur.status is not ProposalStatus.APPROVED:
                raise InvalidTransition(
                    f"concurrent transition: status={cur.status.value}"
                )
            cur.status = ProposalStatus.MERGED
            cur.merge_evidence = outcome.evidence
            cur.updated_at = _now()
            return cur

        try:
            merged = await self.store.mutate_proposal(
                session_id, proposal_id, _mark_merged,
            )
        except InvalidTransition:
            # 并发双 merge：后者失败（proposal 已 merged 终态）。
            merged = await self._must_get(session_id, proposal_id)
            return merged, MergeOutcome(
                ok=False, failure="already_merged", conflict=False,
            )
        assert merged is not None
        await self._publish(session_id, "state", merged, actor)
        return merged, outcome

    async def rebase(
        self,
        session_id: str,
        proposal_id: str,
        actor: ReviewActor,
    ) -> ReviewProposal:
        """Rebase：更新 base 到当前 revision 并复核目标存在性；旧批准全部过期。"""
        p = await self._must_get(session_id, proposal_id)
        self._require_author(p, actor)
        if p.status not in (ProposalStatus.SUBMITTED, ProposalStatus.APPROVED):
            raise InvalidTransition(
                f"rebase requires submitted/approved; status={p.status.value}"
            )
        from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

        engine = MapSpecLifecycleEngine()
        spec = await engine.store.get_mapspec(session_id)
        new_base = await read_current_revision(session_id)
        _validate_targets(p.mutation_intents, spec)
        old_base = p.base_revision

        def _mutate(cur: ReviewProposal) -> ReviewProposal:
            cur.base_revision = new_base
            # 回到 submitted：rebase 后必须重新会签（旧 approve 因 base 不匹配
            # 自然过期 —— 见 policy.evaluate_approval）。
            cur.status = ProposalStatus.SUBMITTED
            cur.updated_at = _now()
            return cur

        updated = await self.store.mutate_proposal(session_id, proposal_id, _mutate)
        assert updated is not None
        logger.info(
            "[review] proposal %s rebased %s -> %s by %s",
            proposal_id, old_base, new_base, actor.actor_id,
        )
        await self._publish(session_id, "state", updated, actor)
        return updated

    async def withdraw(
        self, session_id: str, proposal_id: str, actor: ReviewActor,
    ) -> ReviewProposal:
        def _mutate(p: ReviewProposal) -> ReviewProposal:
            self._require_author(p, actor)
            _transition(p, ProposalStatus.WITHDRAWN)
            p.updated_at = _now()
            return p

        updated = await self.store.mutate_proposal(session_id, proposal_id, _mutate)
        if updated is None:
            raise ProposalNotFound(proposal_id)
        await self._publish(session_id, "state", updated, actor)
        return updated

    async def supersede(
        self, session_id: str, proposal_id: str, actor: ReviewActor,
    ) -> ReviewProposal:
        def _mutate(p: ReviewProposal) -> ReviewProposal:
            self._require_author(p, actor)
            _transition(p, ProposalStatus.SUPERSEDED)
            p.updated_at = _now()
            return p

        updated = await self.store.mutate_proposal(session_id, proposal_id, _mutate)
        if updated is None:
            raise ProposalNotFound(proposal_id)
        await self._publish(session_id, "state", updated, actor)
        return updated


def _validate_targets(intents: List, spec: Optional[dict]) -> None:
    """rebase 目标存在性复核：被引用的 layer/component 必须仍在当前 spec。"""
    if not isinstance(spec, dict):
        return  # spec 不可读时交由 merge 事务内引擎校验兜底
    layer_ids = {
        str(ly.get("id"))
        for ly in (spec.get("layers") or [])
        if isinstance(ly, dict) and ly.get("id")
    }
    layout = spec.get("layout") if isinstance(spec.get("layout"), dict) else {}
    component_ids = {
        str(c.get("id"))
        for c in (layout.get("components") or [])
        if isinstance(c, dict) and c.get("id")
    }
    from app.schemas.mapspec_mutation_schema import PatchComponentBody

    for i, body in enumerate(intents):
        kind = getattr(body, "intent", "")
        if kind in ("patch_layer_style", "patch_layer_presentation", "remove_layer"):
            if body.layer_id not in layer_ids:
                raise InvalidTransition(
                    f"intent[{i}] target layer {body.layer_id!r} no longer exists; "
                    "drop or reword it before rebase"
                )
        elif kind == "reorder_layers":
            if not any(lid in layer_ids for lid in body.layer_ids):
                raise InvalidTransition(f"intent[{i}] reorder references no existing layer")
        elif kind in ("remove_component", "duplicate_component", "rebind_component"):
            if body.component_id not in component_ids:
                raise InvalidTransition(
                    f"intent[{i}] target component {body.component_id!r} no longer exists"
                )
        elif kind == "patch_component" and isinstance(body, PatchComponentBody) and not body.upsert:
            if body.component_id not in component_ids:
                raise InvalidTransition(
                    f"intent[{i}] target component {body.component_id!r} no longer exists"
                )


#: 进程级单例（路由与 agent 工具 seam 共用）。
review_service = ReviewService()
