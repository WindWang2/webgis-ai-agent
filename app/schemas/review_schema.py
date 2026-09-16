"""空间审查/会签（Map Review）契约模型（ADR-0201）。

词表纪律（UBIQUITOUS_LANGUAGE）：本模块的 review 指 **governance review
（评审/会签）**，与 MapSpec "lifecycle review" 和 harness "stored review"
无关；完整术语为 ReviewProposal / AnchoredComment / ReviewDecision /
ApprovalPolicy。

additive 契约：
- mutation intents 直接复用 ``mapspec_mutation_schema`` 的 14 Body
  discriminated union —— proposal 是"待审查的 mutation 集合"，merge 时回放
  走同一条 apply_gis_mutation 路径，不建第二套 mutation 语义。
- intent Body 自带的 ``expected_revision`` 在 proposal 语境下仅作起草参考；
  合并的 CAS 锚是 ``ReviewProposal.base_revision``（merge 时链式推进，
  见 services/review/merge.py）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.mapspec_mutation_schema import (
    UserMapSpecMutationRequest,
)

#: 单 proposal 的 mutation intent 上界（治理对象，不是批量写通道）。
MAX_PROPOSAL_INTENTS = 50
#: 锚定评论/决策的持久上界（防无界增长）。
MAX_COMMENTS_PER_PROPOSAL = 500
MAX_DECISIONS_PER_PROPOSAL = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnchorKind(str, Enum):
    """评论/提案锚：MapSpec 结构（layer/component/feature）或证据面（claim/artifact）。"""

    LAYER = "layer"
    COMPONENT = "component"
    FEATURE = "feature"
    CLAIM = "claim"
    ARTIFACT = "artifact"


class Anchor(BaseModel):
    """锚 = (kind, id[, layer_id])。stale 判定见 services/review/anchors.py。"""

    model_config = ConfigDict(extra="forbid")

    kind: AnchorKind
    id: str = Field(min_length=1, max_length=200)
    # feature 锚的父图层（feature id 只在 layer 内唯一）。
    layer_id: Optional[str] = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _feature_needs_layer(self) -> "Anchor":
        if self.kind is AnchorKind.FEATURE and not self.layer_id:
            raise ValueError("feature anchor requires layer_id (feature ids are layer-scoped)")
        return self


class ReviewActor(BaseModel):
    """审查参与者身份（结构化归因；不存 token/凭据）。"""

    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, max_length=128)
    actor_kind: Literal["user", "agent"]
    role: Literal["anonymous", "viewer", "editor", "admin"] = "viewer"


class AnchoredComment(BaseModel):
    """锚定评论：锚可选（无锚 = 一般性讨论），body 人类可读短文本。"""

    model_config = ConfigDict(extra="forbid")

    comment_id: str = Field(min_length=1, max_length=64)
    author: ReviewActor
    body: str = Field(min_length=1, max_length=2000)
    anchor: Optional[Anchor] = None
    created_at: str = Field(default_factory=_now_iso, min_length=20, max_length=40)


class ReviewDecision(BaseModel):
    """审查决策（append-only）：approve / request_changes / reject。

    ``counted`` 是策略评估的**投影**（agent 决策不受理、base 漂移后旧
    approve 过期），由 policy.evaluate_approval 计算，不在写入时定死。
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(min_length=1, max_length=64)
    decision: Literal["approve", "request_changes", "reject"]
    actor: ReviewActor
    reason: str = Field(default="", max_length=2000)
    base_revision_at_decision: int = Field(ge=0)
    created_at: str = Field(default_factory=_now_iso, min_length=20, max_length=40)
    counted: bool = True


class ProposalRisk(str, Enum):
    LOW = "low"
    HIGH = "high"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    MERGED = "merged"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


TERMINAL_STATUSES = frozenset(
    {
        ProposalStatus.REJECTED,
        ProposalStatus.MERGED,
        ProposalStatus.SUPERSEDED,
        ProposalStatus.WITHDRAWN,
    }
)


class MergeAppliedStep(BaseModel):
    """合并回放的单步存证（结构化；无自由推理文本）。"""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    intent_kind: str = Field(min_length=1, max_length=64)
    target: str = Field(default="", max_length=200)
    revision: int = Field(ge=0)


class MergeEvidence(BaseModel):
    """合并存证：谁、基于哪个 base、经哪个 checkpoint、落了哪些 revision。

    刻意**没有**推理/CoT 字段 —— audit 只记录结构化决策证据与可验证 refs。
    """

    model_config = ConfigDict(extra="forbid")

    merged_at: str = Field(min_length=20, max_length=40)
    actor: ReviewActor
    proposal_id: str = Field(min_length=1, max_length=64)
    base_revision: int = Field(ge=0)
    merged_revision: int = Field(ge=0)
    checkpoint_id: str = Field(min_length=1, max_length=128)
    applied: List[MergeAppliedStep] = Field(default_factory=list, max_length=MAX_PROPOSAL_INTENTS)
    rolled_back: bool = False
    failure: Optional[str] = Field(default=None, max_length=200)
    interleaved: bool = False
    mutation_ids: List[str] = Field(default_factory=list, max_length=MAX_PROPOSAL_INTENTS)
    approvals_considered: List[str] = Field(
        default_factory=list, max_length=MAX_DECISIONS_PER_PROPOSAL,
    )
    policy_snapshot: Dict[str, Any] = Field(default_factory=dict)


class ReviewProposal(BaseModel):
    """空间审查提案 = 基于 base_revision 的一组待审 mutation intents。"""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    author: ReviewActor
    base_revision: int = Field(ge=0)
    mutation_intents: List[UserMapSpecMutationRequest] = Field(
        min_length=1, max_length=MAX_PROPOSAL_INTENTS,
    )
    status: ProposalStatus = ProposalStatus.DRAFT
    risk: Optional[ProposalRisk] = None
    comments: List[AnchoredComment] = Field(
        default_factory=list, max_length=MAX_COMMENTS_PER_PROPOSAL,
    )
    decisions: List[ReviewDecision] = Field(
        default_factory=list, max_length=MAX_DECISIONS_PER_PROPOSAL,
    )
    merge_evidence: Optional[MergeEvidence] = None
    # 合并进行中的排斥闸（service 在 replay 前 test-and-set；期间一切状态
    # 迁移被拒）。不是 ProposalStatus —— 中止后必须无痕回到原状态。
    merge_in_progress: bool = False
    created_at: str = Field(default_factory=_now_iso, min_length=20, max_length=40)
    updated_at: str = Field(default_factory=_now_iso, min_length=20, max_length=40)

    @model_validator(mode="after")
    def _derive_risk(self) -> "ReviewProposal":
        if self.risk is None:
            from app.services.review.policy import classify_risk

            self.risk = classify_risk(self.mutation_intents)
        return self


def mapspec_intent_payload(body: Dict[str, Any]) -> UserMapSpecMutationRequest:
    """裸 dict → 14 Body discriminated union（API 入口/存储回读共用）。"""
    from pydantic import TypeAdapter

    return TypeAdapter(UserMapSpecMutationRequest).validate_python(body)


# ── API 请求/响应契约（ADR-0201）────────────────────────────────────────


class ReviewCreateRequest(BaseModel):
    """POST /review/proposals：起草提案（intents = 14 Body discriminated union）。"""

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    mutation_intents: List[Dict[str, Any]] = Field(
        min_length=1, max_length=MAX_PROPOSAL_INTENTS,
    )


class ReviewSubmitRequest(BaseModel):
    """POST .../submit：作者提交审查；base_revision 取提交时的权威 revision。"""

    base_revision: int = Field(ge=0)


class ReviewCommentRequest(BaseModel):
    """POST .../comments：锚定评论（anchor 可省略 = 一般性讨论）。"""

    body: str = Field(min_length=1, max_length=2000)
    anchor: Optional[Anchor] = None


class ReviewDecisionRequest(BaseModel):
    """POST .../decisions：approve / request_changes / reject。"""

    decision: Literal["approve", "request_changes", "reject"]
    reason: str = Field(default="", max_length=2000)


class ReviewActionRequest(BaseModel):
    """POST .../merge|rebase|withdraw|supersede：动作占位（actor 由鉴权给出）。"""

    reason: str = Field(default="", max_length=2000)


class AnchorStateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    comment_id: str
    state: Literal["ok", "stale", "unverified"]


class ProposalProjectionResponse(BaseModel):
    """GET proposal 详情投影（proposal + 锚态 + 冲突 + 策略 verdict）。"""

    model_config = ConfigDict(extra="allow")

    proposal: Dict[str, Any]
    anchor_states: Dict[str, str] = Field(default_factory=dict)
    conflict: bool = False
    current_revision: int = 0
    base_revision: int = 0
    policy: Dict[str, Any] = Field(default_factory=dict)


class ProposalListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str
    proposals: List[Dict[str, Any]] = Field(default_factory=list)


class ReviewExportResponse(BaseModel):
    """GET .../review/export：审计导出（allowlist 投影；无凭据/无 CoT 字段）。"""

    model_config = ConfigDict(extra="allow")

    session_id: str
    exported_at: str
    proposal_count: int
    proposals: List[Dict[str, Any]] = Field(default_factory=list)
