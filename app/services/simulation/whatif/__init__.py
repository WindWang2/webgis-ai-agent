"""What-If 反事实假设推演分支管理器（ADR-0193）公共面。

职责（防重复施工）：
- 世界状态级分支（fork/干预/回滚/对比/释放）→ 本包；
- 单次型 What-If 评估 → ``app/tools/what_if_simulate.py``（保持不变）；
- 情境事实差分 → ``gis_situation/diff.py``（词汇分离）；
- 发布谱系 fork → ``map_product_service.fork_version``（语义不互通）。
"""
from .branch_manager import (
    BRANCH_ID_PATTERN,
    FORK_EVIDENCE_KEY,
    MAX_ACTIVE_BRANCHES,
    REGISTRY_KEY,
    BranchError,
    ScenarioBranchManager,
    ScenarioBranchMeta,
    branch_session_id,
    fingerprint_mapspec,
)
from .comparison_report import build_comparison_report, render_comparison_markdown
from .prescriptive_advisor import (
    BranchDiffResult,
    PrescriptionResult,
    PrescriptiveAdvisor,
    ScenarioComparison,
)
from .spatial_diff_engine import (
    COVERAGE_KEY,
    DEFAULT_SERVICE_RADIUS_M,
    GeometryDiff,
    build_diff_overlay,
    compute_metric_deltas,
    cost_proxy_of,
    diff_layers,
    evaluate_metrics,
    extract_layer_payloads,
    resolve_layer_role,
)

__all__ = [
    "BRANCH_ID_PATTERN",
    "BranchDiffResult",
    "BranchError",
    "COVERAGE_KEY",
    "DEFAULT_SERVICE_RADIUS_M",
    "FORK_EVIDENCE_KEY",
    "GeometryDiff",
    "MAX_ACTIVE_BRANCHES",
    "PrescriptionResult",
    "PrescriptiveAdvisor",
    "REGISTRY_KEY",
    "ScenarioBranchManager",
    "ScenarioBranchMeta",
    "ScenarioComparison",
    "branch_session_id",
    "build_comparison_report",
    "build_diff_overlay",
    "compute_metric_deltas",
    "cost_proxy_of",
    "diff_layers",
    "evaluate_metrics",
    "extract_layer_payloads",
    "fingerprint_mapspec",
    "render_comparison_markdown",
    "resolve_layer_role",
]
