"""角色模型策略（ADR-0102 Wave 4, §18）。

角色 ≠ 厂商：每个角色 profile 只声明**策略**（预算/超时/采样/能力要求/
降级链），模型本体由路由器按描述符 + 健康度确定性解析 —— 换 vendor 不改
角色表。

既有 ModelRole（execution/planner/title/spatial，model_config.py）保持原义；
本模块扩展 subagent / 结构化抽取等新角色的默认 profile。全部字段可被
``MODEL_ROLE_PROFILES`` 环境变量 JSON 覆盖（运维不动代码）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelRoleProfile:
    """单个角色的模型策略（全部字段可选 → 继承全局缺省）。"""

    role: str
    max_output_tokens: Optional[int] = None
    timeout_s: Optional[float] = None
    temperature: Optional[float] = None
    reasoning: str = "default"            # on | off | default
    tool_access: str = "full"             # full | none | restricted
    context_budget_tokens: Optional[int] = None   # Wave 5 预算器的角色输入
    preferred_group: str = "default"
    fallbacks: Tuple[str, ...] = ()       # 降级候选 model_id（按序）
    max_attempts: int = 2                 # 主模型内重试上限（不含降级跳转）
    require_tools: bool = True            # 该角色的调用是否必须 tool-capable
    require_json: bool = False

    def contract_payload(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "max_output_tokens": self.max_output_tokens,
            "timeout_s": self.timeout_s,
            "temperature": self.temperature,
            "reasoning": self.reasoning,
            "tool_access": self.tool_access,
            "context_budget_tokens": self.context_budget_tokens,
            "preferred_group": self.preferred_group,
            "fallbacks": list(self.fallbacks),
            "max_attempts": self.max_attempts,
            "require_tools": self.require_tools,
            "require_json": self.require_json,
        }


#: 默认角色表。execution/planner/title/spatial 的输出预算与 model_config.py
#: 的 _ROLE_MAX_TOKENS 一致（title=512 / spatial=4096）—— 单一语义两处引用
#: 以 profile 为准收敛（Wave 4 起 model_config 的 resolve 继续工作，路由器
#: 输出与之兼容的 LLMConfig）。
DEFAULT_ROLE_PROFILES: Dict[str, ModelRoleProfile] = {
    "execution": ModelRoleProfile(role="execution", require_tools=True),
    "planner": ModelRoleProfile(
        role="planner", max_output_tokens=4096, require_tools=False, require_json=True,
        temperature=0.2,
    ),
    "title": ModelRoleProfile(
        role="title", max_output_tokens=512, require_tools=False, temperature=0.3,
        timeout_s=30.0, max_attempts=1,
    ),
    "spatial": ModelRoleProfile(
        role="spatial", max_output_tokens=4096, require_tools=False, require_json=True,
    ),
    "subagent_worker": ModelRoleProfile(
        role="subagent_worker", max_output_tokens=4096, require_tools=True,
        temperature=0.3, max_attempts=2,
    ),
    "subagent_reviewer": ModelRoleProfile(
        role="subagent_reviewer", max_output_tokens=2048, require_tools=False,
        temperature=0.0, max_attempts=1,
    ),
    "structured_extraction": ModelRoleProfile(
        role="structured_extraction", max_output_tokens=2048, require_tools=False,
        require_json=True, temperature=0.0, max_attempts=1,
    ),
}

_lock = threading.Lock()
_overrides: Dict[str, ModelRoleProfile] = {}


def _load_env_overrides() -> None:
    raw = os.getenv("MODEL_ROLE_PROFILES")
    if not raw:
        return
    try:
        data = json.loads(raw)
        for role, patch in data.items():
            base = DEFAULT_ROLE_PROFILES.get(role, ModelRoleProfile(role=role))
            known = set(ModelRoleProfile.__dataclass_fields__) - {"role"}
            bad = set(patch) - known
            if bad:
                raise ValueError(f"角色 {role} 的未知 profile 字段: {sorted(bad)}")
            _overrides[role] = replace(base, **patch)
    except Exception:  # noqa: BLE001 — 配置损坏降级为默认
        logger.exception("MODEL_ROLE_PROFILES 解析失败，忽略")


_load_env_overrides()


def get_role_profile(role: str) -> ModelRoleProfile:
    """取角色 profile：显式覆盖 > 默认表 > 全零约束兜底。"""
    with _lock:
        if role in _overrides:
            return _overrides[role]
    return DEFAULT_ROLE_PROFILES.get(role) or ModelRoleProfile(role=role)


def set_role_profile_override(profile: ModelRoleProfile) -> None:
    with _lock:
        _overrides[profile.role] = profile


def clear_role_profile_overrides() -> None:
    with _lock:
        _overrides.clear()


def all_role_profiles() -> Dict[str, ModelRoleProfile]:
    merged = dict(DEFAULT_ROLE_PROFILES)
    with _lock:
        merged.update(_overrides)
    return merged
