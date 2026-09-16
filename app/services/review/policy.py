"""审查策略纯函数（ADR-0201）：风险分级 + 审批评估 + 状态机词表。

fail-closed 默认：
- Agent 作者的 proposal 永远需要 **distinct human** 审批（agent 决策根本
  不受理 —— services 层拒收，本模块再兜底不计数）；
- 高风险（删除/重绑定/工作台全量替换）需要 distinct reviewer 且
  role ≥ editor；
- 匿名会话的高风险 proposal 一律不可批（需认证 editor）；
- base_revision 漂移后旧 approve 自然过期（rebase 需重新审批）。

纯函数、零 I/O —— 策略矩阵全表可测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Set

from app.schemas.review_schema import (
    ProposalRisk,
    ProposalStatus,
    ReviewDecision,
    ReviewProposal,
)

#: 高风险 intent 词（破坏性 / 数据血缘变更 / 全量组织态替换）。
HIGH_RISK_INTENT_KINDS = frozenset(
    {"remove_layer", "remove_component", "rebind_component", "patch_workbench_state"}
)

#: 状态机合法迁移表（action 语义在 service 层；这里是闭包校验的真相）。
TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.DRAFT: frozenset(
        {ProposalStatus.SUBMITTED, ProposalStatus.WITHDRAWN}
    ),
    ProposalStatus.SUBMITTED: frozenset(
        {
            ProposalStatus.CHANGES_REQUESTED,
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
            ProposalStatus.SUPERSEDED,
            ProposalStatus.WITHDRAWN,
        }
    ),
    ProposalStatus.CHANGES_REQUESTED: frozenset(
        {ProposalStatus.SUBMITTED, ProposalStatus.WITHDRAWN}
    ),
    ProposalStatus.APPROVED: frozenset(
        {
            ProposalStatus.MERGED,
            ProposalStatus.REJECTED,
            ProposalStatus.SUPERSEDED,
            ProposalStatus.WITHDRAWN,
        }
    ),
    # 终态不可迁出。
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.MERGED: frozenset(),
    ProposalStatus.SUPERSEDED: frozenset(),
    ProposalStatus.WITHDRAWN: frozenset(),
}

_ROLE_ORDER = {"anonymous": 0, "viewer": 1, "editor": 2, "admin": 3}


def classify_risk(intents: Iterable) -> ProposalRisk:
    """确定性风险分级：命中高风险词即 high（破坏性支配）。"""
    for intent in intents:
        kind = getattr(intent, "intent", type(intent).__name__)
        if kind in HIGH_RISK_INTENT_KINDS:
            return ProposalRisk.HIGH
    return ProposalRisk.LOW


def proposal_targets(intents: Iterable) -> Set[str]:
    """提取 proposal 触及的结构目标 id（layer/component）用于冲突/rebase 校验。"""
    targets: Set[str] = set()
    for intent in intents:
        for attr in ("layer_id", "component_id"):
            value = getattr(intent, attr, None)
            if value:
                targets.add(str(value))
    return targets


@dataclass
class PolicyVerdict:
    satisfied: bool
    counted_approvals: int = 0
    blocking_reasons: List[str] = field(default_factory=list)


@dataclass
class ApprovalPolicy:
    """v1 默认策略（部署常量；不做 per-proposal 覆盖 —— 审批语义要可预期）。"""

    version: str = "v1"
    min_approvals_low: int = 1
    min_approvals_high: int = 1
    require_distinct_reviewer_high: bool = True
    min_role_high: str = "editor"  # viewer/editor/admin 之一
    agent_author_requires_human_approval: bool = True
    anonymous_high_risk_allowed: bool = False

    def policy_snapshot(self) -> dict:
        return {
            "version": self.version,
            "min_approvals_low": self.min_approvals_low,
            "min_approvals_high": self.min_approvals_high,
            "require_distinct_reviewer_high": self.require_distinct_reviewer_high,
            "min_role_high": self.min_role_high,
            "agent_author_requires_human_approval": (
                self.agent_author_requires_human_approval
            ),
            "anonymous_high_risk_allowed": self.anonymous_high_risk_allowed,
        }


def evaluate_approval(
    policy: ApprovalPolicy,
    proposal: ReviewProposal,
    approvals: List[ReviewDecision],
) -> PolicyVerdict:
    """纯策略评估：给定决策流水，当前是否满足可合并条件。

    - 只有人类（actor_kind=="user"）的 approve 计数；agent 的决策在写入层
      已拒收，这里再兜底不计数（防御纵深）。
    - approve 必须针对当前 base_revision（base 漂移 = 旧批过期）。
    - 当前 base 上存在 reject → 阻塞（拒绝优先于批准）。
    """
    reasons: List[str] = []
    base = proposal.base_revision
    is_high = proposal.risk is ProposalRisk.HIGH
    min_needed = policy.min_approvals_high if is_high else policy.min_approvals_low

    rejects = [
        d for d in approvals
        if d.decision == "reject" and d.base_revision_at_decision == base
    ]
    if rejects:
        reasons.append("proposal rejected at current base revision")

    human_approvals = [
        d for d in approvals
        if d.decision == "approve"
        and d.base_revision_at_decision == base
        and d.actor.actor_kind == "user"
    ]
    counted = len(human_approvals)

    if counted < min_needed:
        reasons.append(f"requires {min_needed} human approval(s), got {counted}")

    author = proposal.author
    needs_distinct = is_high and policy.require_distinct_reviewer_high
    if (
        not is_high
        and author.actor_kind == "agent"
        and policy.agent_author_requires_human_approval
    ):
        # 低风险 agent 提案同样要求审批者 != 作者 id（agent 借用户身份自批面）。
        needs_distinct = True
    if needs_distinct:
        if any(d.actor.actor_id == author.actor_id for d in human_approvals):
            reasons.append("distinct human reviewer required (author cannot self-approve)")

    if is_high:
        floor = _ROLE_ORDER.get(policy.min_role_high, 2)
        for d in human_approvals:
            if _ROLE_ORDER.get(d.actor.role, 0) < floor:
                reasons.append(
                    f"reviewer role {policy.min_role_high}+ required for high-risk proposals"
                )
                break
        if not policy.anonymous_high_risk_allowed and (
            author.role == "anonymous"
            or any(d.actor.role == "anonymous" for d in human_approvals)
        ):
            reasons.append(
                "authenticated (non-anonymous) reviewers required for high-risk proposals"
            )

    return PolicyVerdict(
        satisfied=not reasons,
        counted_approvals=counted,
        blocking_reasons=reasons,
    )
