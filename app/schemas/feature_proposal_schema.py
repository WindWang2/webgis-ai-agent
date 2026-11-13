"""Feature-proposals REST/存储契约（G32）。extra=forbid —— 未知键显式拒绝。

模型即持久真相：store.py 直接序列化 `FeatureProposalRecord`（model_dump）。
基础证据（base snapshot）是 BlobStore 不可变快照引用，不内嵌 dataset。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.feature_proposals.contracts import (
    MAX_COMMENTS_PER_PROPOSAL,
    MAX_DECISIONS_PER_PROPOSAL,
    MAX_OPS_PER_PROPOSAL,
)


class BaseEvidence(BaseModel):
    """提案的精确基础引用 —— 可验证的不可变快照。"""

    model_config = ConfigDict(extra="forbid")

    ref_id: str
    content_revision: int
    fingerprint: str                       # canonical payload sha256（G14 compute_payload_fingerprint 同源）
    snapshot_key: str                      # BlobStore CAS key（sha256 hex）
    snapshot_sha256: str                   # 快照字节摘要（读取时校验）
    snapshot_bytes: int
    # 物化状态：base 快照是否已含 _wfid（提案在已物化数据上冻结）。
    materialized: bool
    captured_at: float
    # snap 参考源等外部依赖在 base 时刻的指纹（dependency_changed 比较用）。
    dep_fingerprints: Dict[str, str] = Field(default_factory=dict)


class ProposalResolution(BaseModel):
    """一项冲突的显式裁决（产生新 proposal revision；旧批准失效）。

    decided_by/decided_at 可选：REST 面由服务端从 auth 上下文填充（R1-P1-3
    —— 客户端只提交 {conflict_id, action, ...}，裁决者身份不由客户端声明）。
    """

    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    action: Literal["keep_current", "take_proposed", "use_new_value"]
    # use_new_value 时必填；字段裁决是 {field: value}，几何裁决是 GeoJSON geometry。
    new_fields: Optional[Dict[str, Any]] = None
    new_geometry: Optional[Dict[str, Any]] = None
    decided_by: Optional[str] = None
    decided_at: Optional[float] = None


class ProposalDecision(BaseModel):
    """提交审阅决定（approve/request_changes/reject/merge 等）的审计记录。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["submit", "approve", "request_changes", "reject", "withdraw", "rebase", "resolve", "merge_start", "merge_done", "merge_failed", "recover"]
    actor_kind: Literal["user", "agent", "system"]
    actor_id: str
    at: float
    # 批准绑定：决定针对的候选内容摘要（approve 必填；内容变了旧批准失效）。
    approved_digest: Optional[str] = None
    approved_revision: Optional[int] = None
    note: Optional[str] = Field(default=None, max_length=500)
    detail: Optional[Dict[str, Any]] = None


class ProposalComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment_id: str
    author_kind: Literal["user", "agent"]
    author_id: str
    body: str = Field(min_length=1, max_length=2000)
    at: float
    revision: int                          # 评论指向的提案 revision（过时可辨）


class MergeOutcome(BaseModel):
    """提交桥的结果记录（持久回执关联 —— 重放/恢复的权威证据）。"""

    model_config = ConfigDict(extra="forbid")

    attempt: int
    started_at: float
    finished_at: Optional[float] = None
    status: Literal["succeeded", "failed", "unknown"]
    idempotency_key: str
    approval_digest: str
    base_revision_at_merge: int
    receipt: Optional[Dict[str, Any]] = None   # G14 EditReceipt 快照（成功时）
    error: Optional[Dict[str, Any]] = None     # typed 错误（失败时）
    recovery_state: Optional[Literal["replayable", "recovery_required"]] = None


class FeatureProposalRecord(BaseModel):
    """持久提案真相（per-session JSON 文件）。"""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str                        # fpr-<hex12>
    session_id: str
    ref_id: str
    title: str = Field(min_length=1, max_length=200)
    author_kind: Literal["agent", "user"]
    author_id: str
    created_at: float
    updated_at: float
    revision: int = Field(default=1, ge=1)   # 提案内容修订（CAS）
    status: str
    # ── 候选内容（revision N 的真相；内容变更 = revision+1 + 新 digest）──
    ops: List[Dict[str, Any]] = Field(min_length=1, max_length=MAX_OPS_PER_PROPOSAL)
    # plan 级目标（merge 父集 / 共享目标）：创建/更新时归一化到 wfid 空间后
    # 持久（R1-P1-5 —— feature_ids 此前被接受但静默丢弃，merge 提案不可创建）。
    feature_ids: Optional[List[str]] = Field(default=None, max_length=200)
    op_seed: str                            # 服务端生成并持久（child _wfid 跨重载稳定）
    candidate_digest: str                   # sha256(ops + resolutions + base 证据摘要)
    base: BaseEvidence
    resolutions: List[ProposalResolution] = Field(default_factory=list, max_length=64)
    # ── 审阅 ──
    decisions: List[ProposalDecision] = Field(default_factory=list, max_length=MAX_DECISIONS_PER_PROPOSAL)
    comments: List[ProposalComment] = Field(default_factory=list, max_length=MAX_COMMENTS_PER_PROPOSAL)
    # ── 批准绑定（approve 时写入；merge 时复核；内容变更时失效）──
    approval: Optional[ProposalDecision] = None
    # ── 合并事实（merging/merged/recovery 真相）──
    merge: Optional[MergeOutcome] = None
    merge_attempts: int = 0
    # ── 生命周期 ──
    snapshot_released: bool = False         # withdraw/归档后标记（引用释放见 service）
    withdrawn_at: Optional[float] = None


# ── REST 请求模型 ────────────────────────────────────────────────────────────

class ProposalOpModel(BaseModel):
    """提案 op —— 与 G14 EditOpModel 同词表（rename_field 被提案域拒绝）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    op: Literal[
        "set_fields", "repair_geometry", "move_by", "snap_to_ref",
        "create_features", "delete_features", "replace_geometry",
        "split_feature", "merge_features",
    ]
    fields: Optional[Dict[str, Any]] = None
    fixes: Optional[List[str]] = None
    dx_m: Optional[float] = None
    dy_m: Optional[float] = None
    ref_id: Optional[str] = None            # snap_to_ref
    tolerance_m: Optional[float] = None
    features: Optional[List[Dict[str, Any]]] = None
    geometry: Optional[Dict[str, Any]] = None
    wfid: Optional[str] = None
    line: Optional[Dict[str, Any]] = None
    field_policy: Optional[Dict[str, Any]] = None
    wfids: Optional[List[str]] = Field(default=None, min_length=1, max_length=200)


class ProposalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    ops: List[ProposalOpModel] = Field(min_length=1, max_length=MAX_OPS_PER_PROPOSAL)
    feature_ids: Optional[List[str]] = Field(default=None, max_length=200)
    submit: bool = False                    # True → 创建即 submitted（Agent 起草常规路径）


class ProposalUpdateRequest(BaseModel):
    """候选内容更新（ops/resolutions 变更 → revision+1，旧批准失效）。"""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)    # CAS：旧客户端不得覆盖新草稿
    ops: Optional[List[ProposalOpModel]] = Field(default=None, min_length=1, max_length=MAX_OPS_PER_PROPOSAL)
    feature_ids: Optional[List[str]] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)


class ResolutionSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    resolutions: List[ProposalResolution] = Field(min_length=1, max_length=64)


class ProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["submit", "approve", "request_changes", "reject", "withdraw"]
    note: Optional[str] = Field(default=None, max_length=500)


class RebaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    apply: bool = False                     # False → 仅预览（默认）


class CommentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=2000)
