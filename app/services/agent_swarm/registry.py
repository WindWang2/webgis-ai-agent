"""agent_swarm 专家注册表（ADR-0188 D1）。

- ``SPECIALIST_REGISTRY``：专家名 → 编排器类（确定性领域方法入口）；
- ``ensure_subagent_roles_registered``：把两专家角色档幂等注入全局
  ``SUBAGENT_ROLES``（spawn_subagent 工具描述与 fail-closed role 校验
  随之自动生效），并重跑注册表自检（身份一致 + 无重复定义）。

注册是本包唯一的全局副作用（幂等、import 期由 ``__init__`` 触发）；
不 import 本包则既有系统零行为变化。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Type

from app.services.agent_swarm.base import BaseSpecialistAgent
from app.services.agent_swarm.data_scout import DataScoutAgent
from app.services.agent_swarm.geocompute import GeoComputeAgent
from app.services.subagent_roles import SubagentRole, validate_subagent_role_registry

logger = logging.getLogger(__name__)

#: data_scout 角色档：只读、检索型预算（research 档收紧 tool/heavy 数字）。
DATA_SCOUT_ROLE = SubagentRole(
    name="data_scout",
    title="数据猎手：跨源检索/格式适配/CRS 对齐/轻量元数据探测，绝不拉全量数据",
    model_role="subagent_worker",
    allowed_domains=("dataset", "chinese", "osm"),
    max_rounds=8,
    max_wall_time_s=240.0,
    max_tool_calls=24,
    max_heavy_tool_calls=2,
    allow_mutation=False,
    expected_outputs=(
        "d1_dataset_descriptor",
        "fallback_decisions",
        "source_health_notes",
    ),
    failure_behavior="degrade_with_disclosure",
    budget_class="research",
)

#: geocompute 角色档：只读、计算型预算（heavy 档；role 数字仍是最严约束）。
GEOCOMPUTE_ROLE = SubagentRole(
    name="geocompute",
    title="空间计算专家：算子编排/Celery 异步调度/提货券回传，绝不内联重算",
    model_role="subagent_worker",
    allowed_domains=("statistics", "raster", "network", "temporal", "dataset"),
    max_rounds=10,
    max_wall_time_s=300.0,
    max_tool_calls=24,
    max_heavy_tool_calls=6,
    allow_mutation=False,
    expected_outputs=(
        "spatial_profile_ref",
        "plan_digest",
        "volume_estimate",
    ),
    failure_behavior="fail_closed",
    budget_class="heavy",
)

SPECIALIST_ROLE_DEFINITIONS: Dict[str, SubagentRole] = {
    DATA_SCOUT_ROLE.name: DATA_SCOUT_ROLE,
    GEOCOMPUTE_ROLE.name: GEOCOMPUTE_ROLE,
}

#: 专家名 → 编排器类。构造即校验白名单（fail-closed）。
SPECIALIST_REGISTRY: Dict[str, Type[BaseSpecialistAgent]] = {
    "data_scout": DataScoutAgent,
    "geocompute": GeoComputeAgent,
}

_roles_registered = False


def ensure_subagent_roles_registered() -> None:
    """把两专家角色幂等注入 SUBAGENT_ROLES；同名异档冲突显式失败。"""
    global _roles_registered
    if _roles_registered:
        return
    from app.services import subagent_roles

    for name, role in SPECIALIST_ROLE_DEFINITIONS.items():
        existing = subagent_roles.SUBAGENT_ROLES.get(name)
        if existing is not None:
            if existing != role:
                raise ValueError(
                    f"专家角色 {name!r} 已存在且配置不同：拒绝静默覆盖"
                    "（冲突定义须先在 subagent_roles 中显式迁移）"
                )
            continue
        subagent_roles.SUBAGENT_ROLES[name] = role
    validate_subagent_role_registry()
    _roles_registered = True
    logger.info(
        "[agent_swarm] specialist roles registered: %s",
        ", ".join(sorted(SPECIALIST_ROLE_DEFINITIONS)),
    )


def get_specialist(name: str, **kwargs: Any) -> BaseSpecialistAgent:
    """按名构造专家编排器（未知专家 fail-closed）。"""
    ensure_subagent_roles_registered()
    cls = SPECIALIST_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"未知专家 {name!r}；可用: {', '.join(sorted(SPECIALIST_REGISTRY))}"
        )
    return cls(**kwargs)


__all__ = [
    "DATA_SCOUT_ROLE",
    "GEOCOMPUTE_ROLE",
    "SPECIALIST_ROLE_DEFINITIONS",
    "SPECIALIST_REGISTRY",
    "ensure_subagent_roles_registered",
    "get_specialist",
]
