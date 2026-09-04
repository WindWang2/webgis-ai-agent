"""GeoCompute 统一执行平面（ADR-0096）。

边界：本包是 Data Plane 的一部分 —— 只理解执行图/查询/数据集契约，
绝不依赖 gis_harness、Pi 桥、聊天或前端状态（import 契约测试锁定：
tests/unit/test_geocompute_boundary.py）。
"""
from app.services.geocompute.errors import (
    AuthorizationError,
    BudgetExceededError,
    DeadlineExceededError,
    GeoComputeError,
    NodeExecutionError,
    UnsupportedOperationError,
)
from app.services.geocompute.executor import GeoExecutionEngine, NodeResultStore, engine
from app.services.geocompute.graph import (
    invalidation_set,
    node_reuse_key,
    topo_wave_order,
    validate_plan,
)
from app.services.geocompute.plan import (
    EXECUTION_PLAN_VERSION,
    CrsExpectation,
    ExecutionNode,
    ExecutionPlan,
    ExecutionPolicyKind,
    ExecutionRun,
    ExecutionRunStatus,
    NodeCategory,
    NodeEvidence,
    NodeReusePolicy,
    ResourceBudget,
    ResourceEstimate,
    RetryPolicy,
)

__all__ = [
    "EXECUTION_PLAN_VERSION",
    "AuthorizationError",
    "BudgetExceededError",
    "CrsExpectation",
    "DeadlineExceededError",
    "ExecutionNode",
    "ExecutionPlan",
    "ExecutionPolicyKind",
    "ExecutionRun",
    "ExecutionRunStatus",
    "GeoComputeError",
    "GeoExecutionEngine",
    "NodeCategory",
    "NodeEvidence",
    "NodeExecutionError",
    "NodeReusePolicy",
    "NodeResultStore",
    "ResourceBudget",
    "ResourceEstimate",
    "RetryPolicy",
    "UnsupportedOperationError",
    "engine",
    "invalidation_set",
    "node_reuse_key",
    "topo_wave_order",
    "validate_plan",
]
