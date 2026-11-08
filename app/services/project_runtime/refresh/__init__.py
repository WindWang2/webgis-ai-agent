"""G10 项目数据刷新（ADR-0226 project_runtime 体系内的增量扩展）。

不新建第二执行器 / registry / MapSpec / session truth store：
- 版本证据与差异：``inventory`` / ``diff``（复用 app.lib.data.fingerprints）
- 影响预览：``impact``（复用 workflow_v4.recompute.compute_affected_subgraph）
- user-wins 三方合并：``merge``（复用 app.contracts.workbench_locks）
- 隔离候选 / 原子采纳 / 回退：``execution`` / ``store``
- 对外单一门面：``service``
"""
from .contracts import (
    REFRESH_SCHEMA_VERSION,
    CandidateStatus,
    ChangeEvidence,
    DiffBudget,
    EvidenceLevel,
    FeatureCounts,
    FeatureIdKind,
    MergeConflict,
    MergeResult,
    PlanDecision,
    ProposalStatus,
    RefreshPlan,
    SourceDiff,
    SourceInventory,
    SourceInventoryItem,
    SourceSnapshot,
    SourceState,
)
from .diff import compute_source_diff
from .impact import build_refresh_plan
from .merge import three_way_merge

__all__ = [
    "REFRESH_SCHEMA_VERSION",
    "CandidateStatus", "ChangeEvidence", "DiffBudget", "EvidenceLevel",
    "FeatureCounts", "FeatureIdKind", "MergeConflict", "MergeResult",
    "PlanDecision", "ProposalStatus", "RefreshPlan", "SourceDiff",
    "SourceInventory", "SourceInventoryItem", "SourceSnapshot", "SourceState",
    "compute_source_diff", "build_refresh_plan", "three_way_merge",
]
