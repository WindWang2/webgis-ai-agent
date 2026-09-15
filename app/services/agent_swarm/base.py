"""BaseSpecialistAgent — 专家子智能体基类（ADR-0188 D1/D2/D7）。

专家 = 角色档（SubagentRole）+ RBAC 工具白名单 + 专属系统提示词 +
确定性领域编排方法。执行宿主仍是 SubagentDispatcher + 独立 ChatEngine
（项目不变式：子代理不是第二 agent 框架）；基类只统一三件事：

- **RBAC**（fail-closed）：``authorize_tool`` 名单校验（越权 typed 拦截）、
  ``guarded_registry`` dispatch 边界包装（复用 AllowlistDispatchRegistry，
  结构化 TOOL_NOT_ALLOWLISTED 不执行）、构造期白名单存在性校验（防白
  名单腐烂成静默放行）；
- **心跳**：``heartbeat`` / ``heartbeat_age_s``（长任务逐波续期的僵死
  观测面）；
- **超时熔断**：``check_deadline``（自最近心跳起算的墙钟闸；超限 typed
  SpecialistTimeoutError，绝不静默续跑）。
"""
from __future__ import annotations

import logging
import time
from abc import ABC
from typing import Any, ClassVar, FrozenSet, Optional

from app.services.subagent import AllowlistDispatchRegistry
from app.services.subagent_roles import get_subagent_role


class SpecialistToolDeniedError(PermissionError):
    """专家越权访问白名单外工具（fail-closed；ADR-0188 D2）。"""


class SpecialistTimeoutError(TimeoutError):
    """专家墙钟熔断（自最近心跳超限；ADR-0188 D7）。"""


def _registry_has_tool(registry: Any, tool_name: str) -> bool:
    """registry 是否认识该工具（descriptor 可解析即认为存在）。"""
    try:
        descriptor = registry.descriptor(tool_name)
    except Exception:  # noqa: BLE001 — KeyError/未实现等一律视为缺席
        return False
    return descriptor is not None


class BaseSpecialistAgent(ABC):
    """专家基类：子类声明 name / role_name / TOOL_ALLOWLIST / SPECIALIST_PROMPT。"""

    name: ClassVar[str]
    role_name: ClassVar[str]
    #: 允许调用的工具名单（真实工具名；构造期对 registry 校验存在性）。
    TOOL_ALLOWLIST: ClassVar[FrozenSet[str]] = frozenset()
    #: 专属系统提示词边界（委派路径注入任务头；ADR-0188 D1）。
    SPECIALIST_PROMPT: ClassVar[str] = ""

    #: 默认墙钟（角色档缺席时的保守兜底；与 BUDGET_CLASSES.standard 同量级）。
    _FALLBACK_DEADLINE_S = 300.0

    def __init__(
        self,
        *,
        registry: Optional[Any] = None,
        clock: Any = time.monotonic,
        deadline_s: Optional[float] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._clock = clock
        self._log = logger or logging.getLogger(f"agent_swarm.{self.name}")
        self._registry = registry
        self._started_at = clock()
        self._last_heartbeat = self._started_at
        self._heartbeat_stage = "init"
        if registry is not None and self.TOOL_ALLOWLIST:
            missing = sorted(
                name for name in self.TOOL_ALLOWLIST
                if not _registry_has_tool(registry, name)
            )
            if missing:
                raise ValueError(
                    f"{self.name} 工具白名单引用了注册表中不存在的工具: {missing}"
                    "（fail-closed：拒绝带腐烂名单的专家上线，见 ADR-0188 D2）"
                )
        self._deadline_s = (
            float(deadline_s) if deadline_s is not None else self._default_deadline_s()
        )

    # ── RBAC（D2）────────────────────────────────────────────

    def authorize_tool(self, tool_name: str) -> None:
        """白名单校验；越权抛 SpecialistToolDeniedError（不执行、不降级）。"""
        if tool_name not in self.TOOL_ALLOWLIST:
            raise SpecialistToolDeniedError(
                f"专家 {self.name} 无权访问工具 {tool_name!r}"
                f"（授权面仅 {sorted(self.TOOL_ALLOWLIST)[:8]}…，ADR-0188 D2 RBAC）"
            )

    def guarded_registry(self) -> AllowlistDispatchRegistry:
        """dispatch 边界包装：白名单外结构化拒绝（TOOL_NOT_ALLOWLISTED）。"""
        if self._registry is None:
            raise ValueError(f"{self.name} 未绑定 ToolRegistry，无法构造 dispatch 守卫")
        return AllowlistDispatchRegistry(self._registry, set(self.TOOL_ALLOWLIST))

    # ── 心跳 / 熔断（D7）─────────────────────────────────────

    def heartbeat(self, stage: str = "") -> float:
        """记录一次心跳（结构化日志）；返回心跳间隔（秒）。"""
        now = self._clock()
        gap = now - self._last_heartbeat
        self._last_heartbeat = now
        if stage:
            self._heartbeat_stage = stage
        self._log.info(
            "[Specialist:%s] heartbeat stage=%s gap=%.1fs",
            self.name, self._heartbeat_stage, gap,
        )
        return gap

    def heartbeat_age_s(self) -> float:
        """距最近心跳的秒数（僵死检测观测面）。"""
        return self._clock() - self._last_heartbeat

    def check_deadline(self) -> None:
        """墙钟熔断：自最近心跳超限即 typed 失败（长任务须逐波 heartbeat 续期）。"""
        age = self.heartbeat_age_s()
        if age > self._deadline_s:
            raise SpecialistTimeoutError(
                f"{self.name} exceeded wall-clock deadline: {age:.1f}s since last "
                f"heartbeat > {self._deadline_s:.1f}s budget (stage={self._heartbeat_stage})"
            )

    def _default_deadline_s(self) -> float:
        """角色档墙钟预算；角色未注册时的保守兜底（不虚构精度）。"""
        try:
            return float(get_subagent_role(self.role_name).max_wall_time_s)
        except (ValueError, KeyError, TypeError):
            return self._FALLBACK_DEADLINE_S

    # ── 委派（D1）────────────────────────────────────────────

    async def delegate(self, dispatcher: Any, *, task: str, **kwargs: Any):
        """生产 LLM 委派路径：注入专属提示词边界 + 角色档后交派遣器。

        预算/域/只读约束由角色档承载（role∩caller 语义，SubagentDispatcher
        既有逻辑）；本方法不复制这些策略，只保证专家身份与提示词边界。
        """
        wrapped = (
            f"[{self.name}] {self.SPECIALIST_PROMPT}\n\n# 子任务\n{task}"
        )
        return await dispatcher.run(task=wrapped, role=self.role_name, **kwargs)
