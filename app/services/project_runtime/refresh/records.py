"""G10 刷新事务账本模型（proposal / candidate / receipts）。

这些是**刷新事务的领域状态**，不是 session truth store：
- 唯一真相仍是既有 MapSpec / runtime snapshot / lakehouse version；
- 本账本只记录「绑定到哪一组输入版本、预览了什么、候选跑到哪一步、
  采纳回执是什么」，供刷新页面后恢复进度与幂等重放。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    REFRESH_SCHEMA_VERSION,
    CandidateStatus,
    ProposalStatus,
    RefreshPlan,
    SourceDiff,
    SourceInventory,
)


class RefreshProposal(BaseModel):
    """只读预览提案（绑定输入版本集 / MapSpec revision / 用户锁 / owner）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = REFRESH_SCHEMA_VERSION
    proposal_id: str
    project_id: str
    session_id: str = ""
    owner_id: str = ""
    org_id: str = ""
    status: ProposalStatus = ProposalStatus.PREVIEW
    created_at: float = 0.0
    updated_at: float = 0.0

    baseline_snapshot_id: str = ""
    mapspec_revision: int = 0
    mapspec_fingerprint: str = ""
    user_locks: List[str] = Field(default_factory=list)
    source_versions: Dict[str, str] = Field(default_factory=dict)
    proposal_fingerprint: str = ""

    inventory: SourceInventory = Field(default_factory=SourceInventory)
    diffs: List[SourceDiff] = Field(default_factory=list)
    plan: RefreshPlan = Field(default_factory=RefreshPlan)
    warnings: List[str] = Field(default_factory=list)

    #: 采纳后由回执填充；不倒写 revision
    adopted_revision: Optional[int] = None
    #: 采纳时写入的 MapSpec 指纹（回退前检测采纳后是否有用户后续编辑）
    adopted_mapspec_fingerprint: str = ""


class RefreshCandidate(BaseModel):
    """隔离候选运行状态（新产物先写 candidate 命名空间，不污染当前地图）。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    proposal_id: str
    project_id: str
    status: CandidateStatus = CandidateStatus.PENDING
    created_at: float = 0.0
    updated_at: float = 0.0
    attempts: int = 0
    retry_key: str = ""                 # source-version set + proposal + operation
    source_version_set: Dict[str, str] = Field(default_factory=dict)
    progress: List[Dict[str, Any]] = Field(default_factory=list)
    completed_nodes: List[str] = Field(default_factory=list)
    failed_nodes: List[str] = Field(default_factory=list)
    #: 候选 MapSpec 存独立文件路径（不内嵌大对象）
    candidate_doc_path: str = ""
    candidate_fingerprint: str = ""
    baseline_fingerprint: str = ""
    merge_preserved: List[str] = Field(default_factory=list)
    merge_conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    hard_semantic_errors: List[str] = Field(default_factory=list)
    adoptable: bool = False
    last_error: str = ""
    cancelled: bool = False


class CheckReceipt(BaseModel):
    """「检查更新」回执（类型化，供 UI / Pi 投影）。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    checked_at: float
    sources_total: int = 0
    changed: int = 0
    unchanged: int = 0
    missing: int = 0
    unverifiable: int = 0
    inventory: SourceInventory = Field(default_factory=SourceInventory)
    stale: bool = False
    warnings: List[str] = Field(default_factory=list)
    truncated: bool = False


class AdoptionReceipt(BaseModel):
    """采纳 / 放弃 / 回退回执（三态分离，禁止互相替代）。"""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    action: str                       # adopt | discard | rollback
    status: str                       # adopted | superseded | blocked | discarded | rolled_back
    tool_execution: str = "not_run"   # ok | failed | not_run
    task_execution: str = "not_run"   # ok | failed | partial | not_run
    map_observation: str = "unverified"  # observed | unverified —— 不伪造 renderer ACK
    version_adoption: str = "unchanged"  # adopted | rolled_back | unchanged
    revision_before: int = 0
    revision_after: int = 0
    proposal_fingerprint: str = ""
    mapspec_fingerprint: str = ""
    restored_snapshot_id: str = ""
    warnings: List[str] = Field(default_factory=list)
    replayed: bool = False


__all__ = [
    "RefreshProposal", "RefreshCandidate", "CheckReceipt", "AdoptionReceipt",
]
