"""Proposal 合并引擎（ADR-0203）：把已批准的 mutation intents 回放进 MapSpec。

复用纪律（DECISIONS #2）：合并**只**经 ``apply_gis_mutation``（锁/CAS/守卫/
provenance/协作事件全复用），不建第二条写路径：

    读当前 revision（≠ base → conflict，不写任何状态）
    → 显式 checkpoint（mr_<pid>）
    → 逐 intent 链式 CAS 回放（expected_revision 逐步推进，
      mutation_id = merge:<pid>:<base>:<i> 幂等键）
    → 全部落地 = 成功（存证 MergeEvidence）
    → 失败：
        * intent 被拒（is_error）且无并发交错 → rollback 到 checkpoint；
        * superseded（合并期间有并发已提交变更）→ **不回滚**（保护并发方
          已落地的工作），interleaved=True 存证，proposal 需 rebase 重审。

合并 origin="system"（治理系统在审批后执行）——user-wins presentation
守卫不适用于 system：审批即治理覆盖；空间反幻觉守护网关对所有 origin
生效，合并同样过闸（documented in ADR-0203）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from app.schemas.review_schema import (
    MergeAppliedStep,
    MergeEvidence,
    ReviewActor,
    ReviewProposal,
)
from app.services.mapspec.intent_codec import (
    body_to_intent,
    intent_kind,
    intent_target,
)
from app.services.mapspec.lifecycle_engine import (
    CheckpointIntent,
    RollbackIntent,
)
from app.services.gis_world_state.mutation import apply_gis_mutation
from app.services.review.policy import ApprovalPolicy

logger = logging.getLogger(__name__)


@dataclass
class MergeOutcome:
    ok: bool
    conflict: bool = False
    interleaved: bool = False
    rolled_back: bool = False
    failure: Optional[str] = None
    evidence: Optional[MergeEvidence] = None
    final_spec: Optional[dict] = field(default=None, repr=False)


async def read_current_revision(session_id: str) -> int:
    """权威 CAS 锚读取（与 workbench meta 通道同字段）。"""
    from app.services.session_data import session_data_manager

    state = await session_data_manager.get_map_state(session_id)
    try:
        return int(state.get("_cartographic_mutation_revision", 0))
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence(
    proposal: ReviewProposal,
    actor: ReviewActor,
    checkpoint_id: str,
    merged_revision: int,
    *,
    applied: Optional[List[MergeAppliedStep]] = None,
    failure: Optional[str] = None,
    rolled_back: bool = False,
    interleaved: bool = False,
    mutation_ids: Optional[List[str]] = None,
    approvals_considered: Optional[List[str]] = None,
    policy: Optional[ApprovalPolicy] = None,
) -> MergeEvidence:
    return MergeEvidence(
        merged_at=_now(),
        actor=actor,
        proposal_id=proposal.proposal_id,
        base_revision=proposal.base_revision,
        merged_revision=merged_revision,
        checkpoint_id=checkpoint_id,
        applied=applied or [],
        failure=failure,
        rolled_back=rolled_back,
        interleaved=interleaved,
        mutation_ids=mutation_ids or [],
        approvals_considered=approvals_considered or [],
        policy_snapshot=(policy or ApprovalPolicy()).policy_snapshot(),
    )


async def merge_proposal_intents(
    session_id: str,
    proposal: ReviewProposal,
    actor: ReviewActor,
    *,
    policy: Optional[ApprovalPolicy] = None,
    approvals_considered: Optional[List[str]] = None,
    engine=None,
) -> MergeOutcome:
    """已批准 proposal 的事务性合并（冲突拒绝 / 失败回滚 / 交错保护）。"""
    pid = proposal.proposal_id
    merge_actor_str = f"review_merge:{pid}"

    current = await read_current_revision(session_id)
    if current != proposal.base_revision:
        # base 漂移：不写任何状态 —— 旧 proposal 不静默覆盖新工作（Oracle）。
        return MergeOutcome(
            ok=False,
            conflict=True,
            failure="base_revision_drift",
            evidence=_evidence(
                proposal, actor, "none", current,
                failure="base_revision_drift",
                approvals_considered=approvals_considered,
                policy=policy,
            ),
        )

    ckpt_id = f"mr_{pid}"[:120]
    ckpt_res = await apply_gis_mutation(
        session_id,
        CheckpointIntent(checkpoint_id=ckpt_id),
        origin="system",
        actor=merge_actor_str,
    )
    if ckpt_res.is_error:
        return MergeOutcome(
            ok=False,
            failure="checkpoint_failed",
            evidence=_evidence(
                proposal, actor, ckpt_id, current,
                failure="checkpoint_failed",
                approvals_considered=approvals_considered,
                policy=policy,
            ),
        )

    rev = int(ckpt_res.mutation_revision or current + 1)
    applied: List[MergeAppliedStep] = []
    mutation_ids: List[str] = []
    failure: Optional[str] = None
    interleaved = False

    for i, body in enumerate(proposal.mutation_intents):
        try:
            intent = body_to_intent(body)
        except ValueError:
            failure = f"intent_invalid:{i}"
            break
        result = await apply_gis_mutation(
            session_id,
            intent,
            origin="system",
            actor=merge_actor_str,
            expected_revision=rev,
            mutation_id=f"merge:{pid}:{proposal.base_revision}:{i}",
        )
        if result.superseded:
            # 并发已提交变更进入：保护它，不回滚（存证后由 rebase 通道收口）。
            # superseded 的 intent 未落地 —— 其幂等键不进 mutation_ids 存证。
            interleaved = True
            failure = "superseded_mid_merge"
            break
        if result.mutation_id:
            mutation_ids.append(str(result.mutation_id))
        if result.duplicate:
            # 同 base 重放（响应丢失重试）：幂等命中即视为已落地。
            rev = int(result.mutation_revision or rev)
            applied.append(MergeAppliedStep(
                index=i, intent_kind=intent_kind(body),
                target=intent_target(body), revision=rev,
            ))
            continue
        if result.is_error:
            failure = f"intent_failed:{i}"
            break
        rev = int(result.mutation_revision or rev + 1)
        applied.append(MergeAppliedStep(
            index=i, intent_kind=intent_kind(body),
            target=intent_target(body), revision=rev,
        ))

    rolled_back = False
    if failure is not None and not interleaved:
        # 回滚也走 CAS（expected_revision=rev）：引擎锁逐笔，is_error 之后、
        # 回滚之前的 await 间隙允许并发提交 —— 此刻 revision 已被推进则
        # 回滚被拒（superseded），并发方工作完好，按交错存证（review R1-P1）。
        rb = await apply_gis_mutation(
            session_id,
            RollbackIntent(checkpoint_id=ckpt_id),
            origin="system",
            actor=merge_actor_str,
            expected_revision=rev,
        )
        if rb.superseded:
            interleaved = True
            rolled_back = False
            failure = f"{failure}+interleaved_before_rollback"
        elif rb.is_error:
            # 回滚失败必须大声存证（不能静默留在半合并态）。
            rolled_back = False
            failure = f"{failure}+rollback_failed"
        else:
            rolled_back = True

    evidence = _evidence(
        proposal, actor, ckpt_id, rev,
        applied=applied,
        failure=failure,
        rolled_back=rolled_back,
        interleaved=interleaved,
        mutation_ids=mutation_ids,
        approvals_considered=approvals_considered,
        policy=policy,
    )
    return MergeOutcome(
        ok=failure is None,
        conflict=False,
        interleaved=interleaved,
        rolled_back=rolled_back,
        failure=failure,
        evidence=evidence,
    )
