"""专家注册表一致性与 BaseSpecialistAgent 基座纪律（ADR-0188 D1/D2/D7）。

- registry：四专家 ↔ 四角色档 ↔ SwarmSpecialistRole 词表的交叉一致；
  ``get_specialist`` 未知专家 fail-closed；同名异档冲突显式失败；
- base：RBAC 白名单（越权 typed 拦截 / 构造期腐烂名单 fail-closed /
  dispatch 边界包装）、心跳与墙钟熔断（自最近心跳起算，超限 typed
  失败，绝不静默续跑）。
"""
from __future__ import annotations

import logging
import time

import pytest

from app.services.agent_swarm.base import (
    BaseSpecialistAgent,
    SpecialistTimeoutError,
    SpecialistToolDeniedError,
    _registry_has_tool,
)
from app.services.agent_swarm.delegation_contracts import SwarmSpecialistRole
from app.services.agent_swarm.registry import (
    AUDIT_JUDGE_ROLE,
    CARTOGRAPHY_SPECIALIST_ROLE,
    DATA_SCOUT_ROLE,
    GEOCOMPUTE_ROLE,
    SPECIALIST_REGISTRY,
    SPECIALIST_ROLE_DEFINITIONS,
    ensure_subagent_roles_registered,
    get_specialist,
)
from app.services.subagent import AllowlistDispatchRegistry
from app.services.subagent_roles import SUBAGENT_ROLES, get_subagent_role


class FakeClock:
    """手动推进的时钟（心跳/熔断确定性）。"""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class StubToolRegistry:
    """最小 ToolRegistry 替身（descriptor 可解析即存在；dispatch 同步）。"""

    def __init__(self, tools) -> None:
        self._tools = set(tools)
        self.dispatched: list[str] = []

    def descriptor(self, name: str):
        if name not in self._tools:
            raise KeyError(name)
        return {"name": name}

    async def dispatch(self, name: str, **kwargs):
        self.dispatched.append(name)
        return {"ok": True}


class _DemoSpecialist(BaseSpecialistAgent):
    """白名单/提示词可配置的演示专家（RBAC 与熔断面）。"""

    name = "demo_specialist"
    role_name = "demo_role"
    TOOL_ALLOWLIST = frozenset({"tool_a", "tool_b"})
    SPECIALIST_PROMPT = "演示专家提示词"


class TestRegistryConsistency:
    """注册表 ↔ 角色档 ↔ 词表的三向一致。"""

    def test_registry_has_four_specialists(self):
        assert sorted(SPECIALIST_REGISTRY) == [
            "cartographer",
            "critic_auditor",
            "data_scout",
            "geocompute",
        ]

    def test_registry_values_are_specialist_agents(self):
        for cls in SPECIALIST_REGISTRY.values():
            assert issubclass(cls, BaseSpecialistAgent)

    def test_role_definitions_match_registry_count(self):
        assert sorted(SPECIALIST_ROLE_DEFINITIONS) == [
            "audit_judge",
            "cartography_specialist",
            "data_scout",
            "geocompute",
        ]

    def test_specialist_role_names_map_to_role_definitions(self):
        for cls in SPECIALIST_REGISTRY.values():
            assert cls.role_name in SPECIALIST_ROLE_DEFINITIONS

    def test_swarm_role_enum_covers_cartography_and_audit(self):
        values = {r.value for r in SwarmSpecialistRole}
        assert {"cartography_specialist", "audit_judge"} <= values
        assert CARTOGRAPHY_SPECIALIST_ROLE.name == "cartography_specialist"
        assert AUDIT_JUDGE_ROLE.name == "audit_judge"

    def test_ensure_registration_is_idempotent(self):
        ensure_subagent_roles_registered()
        snapshot = dict(SUBAGENT_ROLES)
        ensure_subagent_roles_registered()
        assert SUBAGENT_ROLES == snapshot

    def test_specialist_roles_injected_into_global_registry(self):
        ensure_subagent_roles_registered()
        for name in SPECIALIST_ROLE_DEFINITIONS:
            assert get_subagent_role(name).name == name

    def test_audit_judge_role_is_readonly(self):
        assert AUDIT_JUDGE_ROLE.allow_mutation is False
        assert AUDIT_JUDGE_ROLE.failure_behavior == "fail_closed"

    def test_cartography_role_allows_mutation(self):
        assert CARTOGRAPHY_SPECIALIST_ROLE.allow_mutation is True

    def test_data_scout_and_geocompute_are_readonly(self):
        assert DATA_SCOUT_ROLE.allow_mutation is False
        assert GEOCOMPUTE_ROLE.allow_mutation is False

    def test_conflicting_role_definition_fails_loud(self, monkeypatch):
        """同名异档：拒绝静默覆盖（冲突须先显式迁移）。"""
        from dataclasses import replace

        from app.services.agent_swarm import registry as reg_mod

        monkeypatch.setattr(reg_mod, "_roles_registered", False)
        monkeypatch.setitem(
            SUBAGENT_ROLES, "data_scout", replace(DATA_SCOUT_ROLE, max_rounds=99)
        )
        with pytest.raises(ValueError, match="拒绝静默覆盖"):
            ensure_subagent_roles_registered()


class TestGetSpecialist:
    def test_known_specialists_construct(self):
        for name in SPECIALIST_REGISTRY:
            agent = get_specialist(name)
            assert isinstance(agent, BaseSpecialistAgent)
            assert agent.name == name

    def test_unknown_specialist_fails_closed(self):
        with pytest.raises(ValueError, match="未知专家"):
            get_specialist("wizard_king")

    def test_construction_accepts_injected_registry(self):
        agent = get_specialist("data_scout", registry=None)
        assert agent._registry is None


class TestRBAC:
    """fail-closed 工具白名单（ADR-0188 D2）。"""

    def test_authorized_tool_passes(self):
        agent = _DemoSpecialist()
        assert agent.authorize_tool("tool_a") is None  # 白名单内静默放行
        assert "tool_a" in agent.TOOL_ALLOWLIST

    def test_unauthorized_tool_denied_typed(self):
        agent = _DemoSpecialist()
        with pytest.raises(SpecialistToolDeniedError):
            agent.authorize_tool("tool_z")
        assert issubclass(SpecialistToolDeniedError, PermissionError)

    def test_rotten_allowlist_fails_closed_at_construction(self):
        registry = StubToolRegistry({"tool_a"})  # tool_b 缺席
        with pytest.raises(ValueError, match="工具白名单引用了注册表中不存在的工具"):
            _DemoSpecialist(registry=registry)

    def test_complete_allowlist_constructs(self):
        registry = StubToolRegistry({"tool_a", "tool_b"})
        agent = _DemoSpecialist(registry=registry)
        assert agent.TOOL_ALLOWLIST == frozenset({"tool_a", "tool_b"})

    def test_empty_allowlist_skips_registry_validation(self):
        class _NoTools(_DemoSpecialist):
            TOOL_ALLOWLIST = frozenset()

        class _HostileRegistry:
            """descriptor 必抛 —— 若跑了存在性校验构造即失败。"""

            def descriptor(self, name: str):
                raise RuntimeError("registry down")

        # 空白名单不触碰 registry（校验被跳过而非无可缺项）
        agent = _NoTools(registry=_HostileRegistry())
        assert agent.TOOL_ALLOWLIST == frozenset()

    def test_guarded_registry_without_binding_raises(self):
        agent = _DemoSpecialist()
        with pytest.raises(ValueError, match="未绑定 ToolRegistry"):
            agent.guarded_registry()

    def test_guarded_registry_wraps_allowlist(self):
        registry = StubToolRegistry({"tool_a", "tool_b"})
        agent = _DemoSpecialist(registry=registry)
        guarded = agent.guarded_registry()
        assert isinstance(guarded, AllowlistDispatchRegistry)
        assert set(guarded._allowed) == {"tool_a", "tool_b"}

    async def test_guarded_registry_rejects_outside_tools_structurally(self):
        """dispatch 边界：白名单外结构化拒绝（不执行），白名单内透传。

        stub 的 dispatch 为 async，与生产 ToolRegistry 协议一致（Allowlist
        边界只做名单裁决，不改变返回协议）。
        """
        registry = StubToolRegistry({"tool_a", "tool_b"})
        agent = _DemoSpecialist(registry=registry)
        guarded = agent.guarded_registry()
        denied = guarded.dispatch("tool_z")
        assert denied["code"] == "TOOL_NOT_ALLOWLISTED"
        assert registry.dispatched == []  # 未执行
        allowed = await guarded.dispatch("tool_a")
        assert allowed == {"ok": True}
        assert registry.dispatched == ["tool_a"]

    def test_registry_has_tool_swallows_errors(self):
        class _BrokenRegistry:
            def descriptor(self, name: str):
                raise RuntimeError("registry down")

        assert _registry_has_tool(_BrokenRegistry(), "any") is False
        assert _registry_has_tool(StubToolRegistry({"x"}), "x") is True
        assert _registry_has_tool(StubToolRegistry({"x"}), "y") is False


class TestHeartbeatAndDeadline:
    """心跳续期与墙钟熔断（ADR-0188 D7）。"""

    def test_heartbeat_returns_gap_and_updates_age(self):
        clock = FakeClock()
        agent = _DemoSpecialist(clock=clock)
        clock.advance(5.0)
        gap = agent.heartbeat("stage-a")
        assert gap == 5.0
        clock.advance(2.0)
        assert agent.heartbeat_age_s() == 2.0

    def test_heartbeat_without_stage_keeps_previous_stage(self):
        agent = _DemoSpecialist(clock=FakeClock())
        agent.heartbeat("stage-a")
        agent.heartbeat()
        assert agent._heartbeat_stage == "stage-a"

    def test_deadline_exceeded_raises_typed_timeout(self):
        clock = FakeClock()
        agent = _DemoSpecialist(clock=clock, deadline_s=10.0)
        clock.advance(11.0)
        with pytest.raises(SpecialistTimeoutError, match="exceeded wall-clock deadline"):
            agent.check_deadline()
        assert issubclass(SpecialistTimeoutError, TimeoutError)

    def test_heartbeat_renews_deadline(self):
        clock = FakeClock()
        agent = _DemoSpecialist(clock=clock, deadline_s=10.0)
        clock.advance(8.0)
        gap = agent.heartbeat("renew")  # 续期：age 归零
        assert gap == 8.0
        assert agent.heartbeat_age_s() == 0.0
        clock.advance(8.0)
        assert agent.check_deadline() is None  # 距续期 8s < 10s 预算，不抛
        assert agent.heartbeat_age_s() == 8.0

    def test_deadline_boundary_is_inclusive_ok(self):
        clock = FakeClock()
        agent = _DemoSpecialist(clock=clock, deadline_s=10.0)
        clock.advance(10.0)
        assert agent.heartbeat_age_s() == 10.0  # age == budget 恰好不超限
        assert agent.check_deadline() is None

    def test_default_deadline_from_registered_role(self):
        agent = get_specialist("data_scout")
        assert agent._deadline_s == DATA_SCOUT_ROLE.max_wall_time_s == 240.0

    def test_default_deadline_fallback_for_unknown_role(self):
        agent = _DemoSpecialist()  # demo_role 未注册
        assert agent._deadline_s == BaseSpecialistAgent._FALLBACK_DEADLINE_S

    def test_explicit_deadline_overrides_role_default(self):
        agent = get_specialist("data_scout", deadline_s=42.0)
        assert agent._deadline_s == 42.0

    def test_default_logger_namespaced_by_specialist(self):
        agent = _DemoSpecialist()
        assert agent._log.name == f"agent_swarm.{_DemoSpecialist.name}"

    def test_injected_logger_used(self):
        custom = logging.getLogger("custom.specialist")
        agent = _DemoSpecialist(logger=custom)
        assert agent._log is custom

    def test_started_at_from_injected_clock(self):
        clock = FakeClock(now=500.0)
        agent = _DemoSpecialist(clock=clock)
        assert agent._started_at == 500.0
        assert agent.heartbeat_age_s() == 0.0

    def test_real_clock_default_is_monotonic(self):
        agent = _DemoSpecialist()
        before = time.monotonic()
        assert agent._started_at <= before + 1e-6


class TestDelegateBoundary:
    """delegate：专属提示词边界 + 角色档透传（D1）。"""

    async def test_delegate_wraps_prompt_and_role(self):
        seen: dict = {}

        class _RecordingDispatcher:
            async def run(self, *, task: str, role=None, **kwargs):
                seen["task"] = task
                seen["role"] = role
                seen["kwargs"] = kwargs
                return "subagent-result"

        agent = _DemoSpecialist()
        result = await agent.delegate(_RecordingDispatcher(), task="子任务内容")
        assert result == "subagent-result"
        assert seen["role"] == "demo_role"
        assert seen["task"].startswith("[demo_specialist] 演示专家提示词")
        assert "子任务内容" in seen["task"]

    async def test_delegate_forwards_kwargs(self):
        seen: dict = {}

        class _RecordingDispatcher:
            async def run(self, *, task: str, role=None, **kwargs):
                seen.update(kwargs)
                return None

        agent = _DemoSpecialist()
        await agent.delegate(_RecordingDispatcher(), task="x", session_id="s1")
        assert seen["session_id"] == "s1"
