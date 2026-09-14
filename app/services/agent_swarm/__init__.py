"""Agent Swarm（ADR-0187/0188/0189）—— 专业化空间智能体集群总控编排包。

- ADR-0187：Master→Specialist 委派链路总控（orchestrator/dispatcher/
  aggregator），契约位于 ``delegation_contracts``（调和自原 contracts 模块，
  ADR-0189 §合并纪要）；
- ADR-0188：专家包基座（BaseSpecialistAgent、RBAC 白名单、注册表）与
  首批专家（Data Scout / GeoCompute），输出契约位于 ``contracts``；
- ADR-0189：制图专家（Cartographer）与独立审计裁判（CriticAuditor）
  及其对抗博弈闭环（duo session）。

公开面走惰性导出（PEP 562，harness_kernel 同款）：子模块间存在运行期
组合关系，急切重导出会引入循环导入；仅 registry 侧需急切初始化以完成
子代理角色注册（ADR-0188 既有行为，保持不变）。
"""
from __future__ import annotations

from typing import Any

from app.services.agent_swarm.registry import (
    SPECIALIST_REGISTRY,
    ensure_subagent_roles_registered,
    get_specialist,
)

ensure_subagent_roles_registered()

_LAZY: dict[str, tuple[str, str]] = {
    # delegation contracts（ADR-0187 委派链路；原 contracts.py 改名列）
    "SwarmSpecialistRole": (
        "app.services.agent_swarm.delegation_contracts",
        "SwarmSpecialistRole",
    ),
    "SwarmReceiptStatus": (
        "app.services.agent_swarm.delegation_contracts",
        "SwarmReceiptStatus",
    ),
    "SwarmRunState": (
        "app.services.agent_swarm.delegation_contracts",
        "SwarmRunState",
    ),
    "SwarmErrorCode": (
        "app.services.agent_swarm.delegation_contracts",
        "SwarmErrorCode",
    ),
    "SwarmContractError": (
        "app.services.agent_swarm.delegation_contracts",
        "SwarmContractError",
    ),
    "WorldStateProjection": (
        "app.services.agent_swarm.delegation_contracts",
        "WorldStateProjection",
    ),
    "SwarmTaskDescriptor": (
        "app.services.agent_swarm.delegation_contracts",
        "SwarmTaskDescriptor",
    ),
    "SpecialistAssignment": (
        "app.services.agent_swarm.delegation_contracts",
        "SpecialistAssignment",
    ),
    "SubagentReceipt": (
        "app.services.agent_swarm.delegation_contracts",
        "SubagentReceipt",
    ),
    "SwarmAssetEntry": (
        "app.services.agent_swarm.delegation_contracts",
        "SwarmAssetEntry",
    ),
    "SwarmAssetManifest": (
        "app.services.agent_swarm.delegation_contracts",
        "SwarmAssetManifest",
    ),
    "SwarmExecutionStatus": (
        "app.services.agent_swarm.delegation_contracts",
        "SwarmExecutionStatus",
    ),
    "MAX_SWARM_CONCURRENCY": (
        "app.services.agent_swarm.delegation_contracts",
        "MAX_SWARM_CONCURRENCY",
    ),
    "MAX_SWARM_TASKS": (
        "app.services.agent_swarm.delegation_contracts",
        "MAX_SWARM_TASKS",
    ),
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
    # specialists 输出契约（ADR-0188）
    "D1DatasetDescriptor": ("app.services.agent_swarm.contracts", "D1DatasetDescriptor"),
    "SpatialProfileRef": ("app.services.agent_swarm.contracts", "SpatialProfileRef"),
    "SERIALIZATION_BUDGET_BYTES": (
        "app.services.agent_swarm.contracts",
        "SERIALIZATION_BUDGET_BYTES",
    ),
}

__all__ = [
    "SPECIALIST_REGISTRY",
    "ensure_subagent_roles_registered",
    "get_specialist",
    *_LAZY,
]


def __getattr__(name: str) -> Any:
    entry = _LAZY.get(name)
    if entry is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_path, attr = entry
    value = getattr(importlib.import_module(module_path), attr)
    globals()[name] = value
    return value
