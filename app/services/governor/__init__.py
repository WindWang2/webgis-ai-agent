"""Harness Resource Governor（V1，ADR-0182）。

跨域 Resource / Cost / Budget / Backpressure / Admission / Scheduling
Governor 的公共 API。身份与分界（决策日志 D1/D2）：

- 本包是 **harness 层协调者**，不是第二套计算平台；
- geocompute ``ResourceGovernor``（rows/bytes/nodes L1 权威）、
  ``context_budget``（token 估算单一语义）、Data Fabric breaker（provider
  健康 owner）、``observability/budgets``（SLO 观测面）全部**只消费不改写**；
- 未列出的模块（estimation/render_budget/backpressure/fairness/
  session_budget/admission/degradation/context_link/health/retry_budget/
  cancellation/storage_pressure/dispatch_adapter）经 ``app.services.governor.X``
  直接 import —— 它们是包内实现细节，公共面收敛在这里。
"""
from app.services.governor.admission import AdmissionPolicy
from app.services.governor.backpressure import (
    AcquireTicket,
    BackpressureManager,
    QueueTimeoutError,
)
from app.services.governor.cancellation import (
    CancellationCoordinator,
    CancellationRecord,
)
from app.services.governor.config import (
    GOVERNOR_MANIFEST_PATH,
    GovernorConfig,
    GovernorMode,
    get_governor_config,
    load_manifest,
    reset_governor_config_for_tests,
)
from app.services.governor.contract import (
    SCHEMA_VERSION,
    AdmissionDecision,
    CancelReason,
    Certainty,
    DegradationSemantics,
    Dimension,
    DimValue,
    ExecutionPriority,
    ResourceBudget,
    ResourceClass,
    ResourceDecision,
    ResourceDemand,
    ResourceEstimate,
    ResourceReservation,
    ResourceUsage,
    RetryClass,
    Subsystem,
)
from app.services.governor.dispatch_adapter import (
    GovernorDispatchAdapter,
    classify_tool,
    surface_enabled,
)
from app.services.governor.governor import (
    HarnessResourceGovernor,
    get_governor,
    reset_governor_for_tests,
)
from app.services.governor.retry_budget import RetryBudget
from app.services.governor.session_budget import SessionBudgetLedger

__all__ = [
    # contract
    "SCHEMA_VERSION",
    "Dimension",
    "DimValue",
    "Certainty",
    "Subsystem",
    "ResourceClass",
    "ExecutionPriority",
    "AdmissionDecision",
    "RetryClass",
    "CancelReason",
    "DegradationSemantics",
    "ResourceEstimate",
    "ResourceDemand",
    "ResourceBudget",
    "ResourceReservation",
    "ResourceUsage",
    "ResourceDecision",
    # config
    "GOVERNOR_MANIFEST_PATH",
    "GovernorConfig",
    "GovernorMode",
    "get_governor_config",
    "reset_governor_config_for_tests",
    "load_manifest",
    # facade
    "HarnessResourceGovernor",
    "get_governor",
    "reset_governor_for_tests",
    # components
    "AdmissionPolicy",
    "BackpressureManager",
    "AcquireTicket",
    "QueueTimeoutError",
    "SessionBudgetLedger",
    "RetryBudget",
    "CancellationCoordinator",
    "CancellationRecord",
    # tool surface
    "GovernorDispatchAdapter",
    "classify_tool",
    "surface_enabled",
]
