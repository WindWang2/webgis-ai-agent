"""Delegation 生产适配器的共享单例与崩溃面（#1552 测试债清偿）。

既有测试全部显式构造本地 scheduler/gateway 或注入参数，进程级共享单例层
与 runner 崩溃路径零覆盖。本文件锁定：

- ``get_shared_scheduler``：身份稳定、默认上限（全局 8 / 每会话 4）、
  ``GIS_DELEGATION_SESSION_CONCURRENCY`` env 解析（含垃圾值回落 / 0/-N 钳 1）、
  reset 语义；
- ``get_shared_gateway``：绑定共享调度器（同一进程同一公平域）；
- ``SubagentDispatcherRunner.run``：legacy dispatcher 崩溃 → FAILED/
  CHILD_CRASH 诚实折算（不上抛、``last_raw_result`` 保持 None）；
- ``run_single_delegation``：崩溃 → 按 outcome 合成诚实失败 SubagentResult
  + 台账快照（failed 计数）；
- ``SwarmConcurrencyGovernor``：陈旧 event loop 换环 → 信号量重建 +
  陈旧台账清空（function-scoped loop 测试场景），未知 release 诚实 no-op。

全部用例零 LLM/DB/网络（SubagentDispatcher 以 stub 替换，沿用
test_delegation_protocol.py 的 monkeypatch 模式）。
"""
from __future__ import annotations

import pytest

from app.services.agent_swarm import delegation_adapters as da
from app.services.agent_swarm.delegation import (
    DelegationFailureReason,
    DelegationGateway,
    DelegationLease,
    DelegationStatus,
    FairSlotScheduler,
)
from app.services.agent_swarm.orchestrator import SwarmConcurrencyGovernor


@pytest.fixture(autouse=True)
def _isolate_shared_singletons():
    """每个用例前后复位共享单例（既有测试从不触碰它们，须保持纯净）。"""
    da.reset_shared_scheduler_for_tests()
    da.reset_shared_gateway_for_tests()
    yield
    da.reset_shared_scheduler_for_tests()
    da.reset_shared_gateway_for_tests()


class BoomDispatcher:
    """崩溃型 SubagentDispatcher stub（legacy dispatcher 面）。"""

    def __init__(self, registry, session_id):
        pass

    async def run(self, **kw):
        raise RuntimeError("boom")


class TestSharedScheduler:
    def test_identity_defaults_and_reset(self):
        a = da.get_shared_scheduler()
        assert da.get_shared_scheduler() is a
        snap = a.snapshot()
        assert snap["global_max"] == 8
        assert snap["per_session_max"] == 4
        da.reset_shared_scheduler_for_tests()
        assert da.get_shared_scheduler() is not a

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("2", 2), ("garbage", 4), ("0", 1), ("-3", 1)],
        ids=["tightened", "garbage-fallback", "zero-clamped", "neg-clamped"],
    )
    def test_env_session_cap_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv("GIS_DELEGATION_SESSION_CONCURRENCY", raw)
        assert da.get_shared_scheduler().snapshot()["per_session_max"] \
            == expected

    def test_env_unset_defaults(self, monkeypatch):
        monkeypatch.delenv("GIS_DELEGATION_SESSION_CONCURRENCY", raising=False)
        assert da.get_shared_scheduler().snapshot()["per_session_max"] == 4


class TestSharedGateway:
    def test_gateway_binds_shared_scheduler(self):
        gw = da.get_shared_gateway()
        assert da.get_shared_gateway() is gw
        assert gw.scheduler is da.get_shared_scheduler()


class TestRunnerCrashPath:
    async def test_runner_crash_folded_to_child_crash(self, monkeypatch):
        import app.services.subagent as subagent_mod

        monkeypatch.setattr(subagent_mod, "SubagentDispatcher", BoomDispatcher)
        runner = da.SubagentDispatcherRunner(object(), "s1", task="t")
        lease = DelegationLease(
            lease_id="lease-x", delegation_id="dg-x",
            generation=1, session_id="s1",
        )
        outcome = await runner.run(lease)
        assert outcome.status == DelegationStatus.FAILED
        assert outcome.failure_reason == DelegationFailureReason.CHILD_CRASH
        assert outcome.error.startswith("subagent runtime error:")
        assert runner.last_raw_result is None  # 崩溃时无 raw 出口

    async def test_run_single_delegation_crash_synthetic_result(self,
                                                                monkeypatch):
        import app.services.subagent as subagent_mod

        monkeypatch.setattr(subagent_mod, "SubagentDispatcher", BoomDispatcher)
        gateway = DelegationGateway(scheduler=FairSlotScheduler())
        result, snapshot = await da.run_single_delegation(
            object(), "s1", task="t", gateway=gateway,
        )
        # 合成诚实失败 result（非 raw SubagentResult 直传）
        assert result.success is False
        assert (result.error or "").startswith("subagent runtime error")
        assert result.lineage["delegation_status"] == "failed"
        assert snapshot["outcomes"].get("failed") == 1
        assert snapshot["active"] == []  # 已落账终态，不在飞


class TestGovernorStaleLoop:
    async def test_stale_loop_rebuild_and_unknown_release(self):
        """陈旧 loop 占位后换环：信号量重建 + 台账清空（同
        test_stale_loop_ledger_is_rebuilt_not_poisoned 的 _loop_key 置 0 习语；
        真换环会撞 CPython id() 复用，非本用例要锁定的面）。"""
        gov = SwarmConcurrencyGovernor(max_concurrency=2)
        await gov.acquire("a1", "task-a1")
        assert gov.snapshot()["active_count"] == 1
        gov._sem_loop_key = 0  # 模拟旧 loop 消亡后落入新 loop
        await gov.acquire("a2", "task-a2")
        # 陈旧台账已清：新环不被旧占位毒化（未清会数成 2）
        assert gov.snapshot()["active_count"] == 1
        assert gov.snapshot()["active_task_ids"] == ["task-a2"]
        gov.release("no-such-assignment")  # 诚实 no-op，不抛
        assert gov.snapshot()["active_count"] == 1
