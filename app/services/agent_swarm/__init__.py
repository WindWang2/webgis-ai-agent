"""Agent Swarm（ADR-0187）—— 专业化空间智能体集群总控编排包。

公开面走惰性导出（PEP 562，harness_kernel 同款）：子模块间存在运行期
组合关系，急切重导出会引入循环导入；且网关默认关闭路径应零加载。
"""
from __future__ import annotations

from typing import Any

_LAZY: dict[str, tuple[str, str]] = {
    # contracts
    "SwarmSpecialistRole": ("app.services.agent_swarm.contracts", "SwarmSpecialistRole"),
    "SwarmReceiptStatus": ("app.services.agent_swarm.contracts", "SwarmReceiptStatus"),
    "SwarmRunState": ("app.services.agent_swarm.contracts", "SwarmRunState"),
    "SwarmErrorCode": ("app.services.agent_swarm.contracts", "SwarmErrorCode"),
    "SwarmContractError": ("app.services.agent_swarm.contracts", "SwarmContractError"),
    "WorldStateProjection": ("app.services.agent_swarm.contracts", "WorldStateProjection"),
    "SwarmTaskDescriptor": ("app.services.agent_swarm.contracts", "SwarmTaskDescriptor"),
    "SpecialistAssignment": ("app.services.agent_swarm.contracts", "SpecialistAssignment"),
    "SubagentReceipt": ("app.services.agent_swarm.contracts", "SubagentReceipt"),
    "SwarmAssetEntry": ("app.services.agent_swarm.contracts", "SwarmAssetEntry"),
    "SwarmAssetManifest": ("app.services.agent_swarm.contracts", "SwarmAssetManifest"),
    "SwarmExecutionStatus": ("app.services.agent_swarm.contracts", "SwarmExecutionStatus"),
    "MAX_SWARM_CONCURRENCY": ("app.services.agent_swarm.contracts", "MAX_SWARM_CONCURRENCY"),
    "MAX_SWARM_TASKS": ("app.services.agent_swarm.contracts", "MAX_SWARM_TASKS"),
    # orchestrator
    "SwarmOrchestrator": ("app.services.agent_swarm.orchestrator", "SwarmOrchestrator"),
    "SwarmTaskDecomposer": ("app.services.agent_swarm.orchestrator", "SwarmTaskDecomposer"),
    "HeuristicSpatialDecomposer": (
        "app.services.agent_swarm.orchestrator",
        "HeuristicSpatialDecomposer",
    ),
    "SwarmConcurrencyGovernor": (
        "app.services.agent_swarm.orchestrator",
        "SwarmConcurrencyGovernor",
    ),
    "WorkflowMachineGraphPort": (
        "app.services.agent_swarm.orchestrator",
        "WorkflowMachineGraphPort",
    ),
    "get_swarm_concurrency_governor": (
        "app.services.agent_swarm.orchestrator",
        "get_swarm_concurrency_governor",
    ),
    "validate_swarm_graph": ("app.services.agent_swarm.orchestrator", "validate_swarm_graph"),
    # dispatcher
    "SpecialistDispatcher": ("app.services.agent_swarm.dispatcher", "SpecialistDispatcher"),
    "SpecialistRuntime": ("app.services.agent_swarm.dispatcher", "SpecialistRuntime"),
    "SubagentDispatcherRuntime": (
        "app.services.agent_swarm.dispatcher",
        "SubagentDispatcherRuntime",
    ),
    "normalize_receipt": ("app.services.agent_swarm.dispatcher", "normalize_receipt"),
    # aggregator
    "SwarmAggregator": ("app.services.agent_swarm.aggregator", "SwarmAggregator"),
    "SessionPlanSwarmSink": ("app.services.agent_swarm.aggregator", "SessionPlanSwarmSink"),
    "NullSink": ("app.services.agent_swarm.aggregator", "NullSink"),
}

__all__ = list(_LAZY)


def __getattr__(name: str) -> Any:
    entry = _LAZY.get(name)
    if entry is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_path, attr = entry
    value = getattr(importlib.import_module(module_path), attr)
    globals()[name] = value
    return value
