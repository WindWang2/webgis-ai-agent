"""Agent Swarm — 专家子智能体包（ADR-0188）。

专精编排层（不是第二 agent 框架）：Data Scout（数据猎手）与 GeoCompute
（空间计算专家）。执行宿主仍是 SubagentDispatcher + 独立 ChatEngine
（ADR-0101 §31 不变式）；本包提供角色档、RBAC 工具白名单、确定性领域
编排方法与统一输出契约（D1DatasetDescriptor / SpatialProfileRef）。
"""
from __future__ import annotations

from app.services.agent_swarm.registry import (
    SPECIALIST_REGISTRY,
    ensure_subagent_roles_registered,
    get_specialist,
)

ensure_subagent_roles_registered()

__all__ = [
    "SPECIALIST_REGISTRY",
    "ensure_subagent_roles_registered",
    "get_specialist",
]
