"""Harness V5 Subagent accounting 契约（ADR-0118 D6）。

验收锚点（Epic V5）：
- 子代理 token/usage 记账：引擎既有 usage 通道（ContextVar 继承）落到
  子专属 TurnEvidence → 预算 llm_usage → 结果 budget_usage；
- 父 evidence roll-up：父 turn 的 usage 汇总包含子代理消费（一次，幂等）；
- lineage：结果携带 parent_turn_id / depth / role；
- 预算纪律：usage() 恒含 llm_usage 键（无 provider 上报时 reports=0
  诚实缺席，不虚构 token 数）。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.subagent import SubagentDispatcher
from app.services.subagent_roles import SubagentBudget


# ---------------------------------------------------------------- 预算单元

def test_budget_usage_always_has_llm_usage():
    b = SubagentBudget(max_tool_calls=5, max_heavy_tool_calls=2,
                       max_wall_time_s=30)
    u = b.usage()
    assert u["llm_usage"] == {
        "prompt_tokens": 0, "completion_tokens": 0,
        "total_tokens": 0, "reports": 0,
    }


def test_budget_add_llm_usage_accumulates():
    b = SubagentBudget(max_tool_calls=5, max_heavy_tool_calls=2,
                       max_wall_time_s=30)
    b.add_llm_usage({"prompt_tokens": 100, "completion_tokens": 20})
    b.add_llm_usage({"prompt_tokens": 50, "completion_tokens": 30,
                     "total_tokens": 80})
    b.add_llm_usage(None)  # 安全跳过
    b.add_llm_usage({"prompt_tokens": "bad"})  # 类型异常跳过
    u = b.usage()["llm_usage"]
    assert u["prompt_tokens"] == 150
    assert u["completion_tokens"] == 50
    assert u["total_tokens"] == 200  # 显式 total 优先
    assert u["reports"] == 2


def test_budget_join_turn_evidence_idempotent():
    from app.lib.runtime.evidence import TurnEvidence

    ev = TurnEvidence(request_id=None, session_id="s", turn_id="t", run_id=None)
    ev.add_llm_usage({"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15})
    b = SubagentBudget(max_tool_calls=5, max_heavy_tool_calls=2,
                       max_wall_time_s=30)
    assert b.join_turn_evidence(ev) is True
    assert b.join_turn_evidence(ev) is False  # 幂等守卫
    assert b.usage()["llm_usage"]["total_tokens"] == 15
    assert b.usage()["llm_usage"]["reports"] == 1


# ---------------------------------------------------------------- 运行时接线

class _UsageAwareEngine:
    """桩引擎：chat 内经既有 current_turn_evidence 通道回报 usage。"""

    def __init__(self, total_calls: int = 2):
        self.total_calls = total_calls
        self.dispatch_service = None

    async def chat(self, *, message: str, session_id: str):
        from app.lib.runtime.evidence import current_turn_evidence

        ev = current_turn_evidence()
        assert ev is not None, "子代理运行必须绑定专属 evidence（ContextVar 继承）"
        for _ in range(self.total_calls):
            ev.add_llm_usage({"prompt_tokens": 10, "completion_tokens": 5,
                              "total_tokens": 15})
        return {"content": "done", "reasoning": ""}


@pytest.fixture
def registry():
    from app.tools.registry import ToolRegistry

    return ToolRegistry()


@pytest.mark.asyncio
async def test_subagent_usage_rollup_and_lineage(registry):
    """run() 结果携带 llm_usage 与 lineage；父 evidence 汇总包含子消费。"""
    from app.lib.runtime.evidence import (
        TurnEvidence,
        bind_turn_evidence,
    )

    parent_ev = TurnEvidence(request_id=None, session_id="s-parent",
                             turn_id="turn-parent", run_id=None)
    dispatcher = SubagentDispatcher(registry, "s-parent")
    engine = _UsageAwareEngine(total_calls=2)

    def _fake_build(tool_subset, max_rounds):
        return engine

    dispatcher._build_sub_engine = _fake_build  # type: ignore[method-assign]

    with bind_turn_evidence(parent_ev):
        result = await dispatcher.run(task="统计要点", max_rounds=2)

    assert result.success is True
    llm = result.budget_usage["llm_usage"]
    assert llm["total_tokens"] == 30, "子代理 token 未汇入预算"
    assert llm["reports"] == 2
    assert result.lineage["parent_turn_id"] == "turn-parent"
    assert result.lineage["parent_session_id"] == "s-parent"
    assert result.lineage["depth"] == 0
    # 父 evidence roll-up：一次、幂等
    assert parent_ev.total_tokens == 30
    assert parent_ev.prompt_tokens == 20


@pytest.mark.asyncio
async def test_subagent_usage_zero_when_provider_silent(registry):
    """provider 不回报 usage → reports=0 诚实缺席（token 恒 0，不虚构）。"""
    from app.lib.runtime.evidence import TurnEvidence, bind_turn_evidence

    class _SilentEngine(_UsageAwareEngine):
        async def chat(self, *, message: str, session_id: str):
            return {"content": "ok", "reasoning": ""}

    parent_ev = TurnEvidence(request_id=None, session_id="s2",
                             turn_id="turn-2", run_id=None)
    dispatcher = SubagentDispatcher(registry, "s2")
    dispatcher._build_sub_engine = (
        lambda tool_subset, max_rounds: _SilentEngine()
    )  # type: ignore[method-assign]

    with bind_turn_evidence(parent_ev):
        result = await dispatcher.run(task="x", max_rounds=1)

    llm = result.budget_usage["llm_usage"]
    assert llm == {"prompt_tokens": 0, "completion_tokens": 0,
                   "total_tokens": 0, "reports": 0}
    assert parent_ev.total_tokens == 0


@pytest.mark.asyncio
async def test_wall_time_exceeded_result_still_carries_usage(registry):
    """墙钟超时早退路径同样携带预算快照与 lineage（记账不丢）。"""
    from app.lib.runtime.evidence import TurnEvidence, bind_turn_evidence

    class _SlowEngine(_UsageAwareEngine):
        async def chat(self, *, message: str, session_id: str):
            await asyncio.sleep(30)
            return {"content": "never", "reasoning": ""}

    parent_ev = TurnEvidence(request_id=None, session_id="s3",
                             turn_id="turn-3", run_id=None)
    dispatcher = SubagentDispatcher(registry, "s3")
    dispatcher._build_sub_engine = (
        lambda tool_subset, max_rounds: _SlowEngine()
    )  # type: ignore[method-assign]
    from app.services.subagent_roles import SubagentRole

    fast_role = SubagentRole(
        name="v5_fast", title="V5 fast", model_role="default",
        max_rounds=1, max_wall_time_s=1.0, max_tool_calls=2,
        max_heavy_tool_calls=0,
    )

    with bind_turn_evidence(parent_ev):
        result = await dispatcher.run(task="x", role=fast_role)

    assert result.success is False
    assert result.error == "budget_exceeded:wall_time"
    assert "llm_usage" in result.budget_usage
    assert result.lineage["parent_turn_id"] == "turn-3"
