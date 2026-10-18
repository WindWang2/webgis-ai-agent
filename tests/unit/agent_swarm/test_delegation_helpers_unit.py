"""Delegation 协议纯函数层直测（#1552 测试债清偿）。

test_delegation_protocol.py 覆盖了状态机/网关/公平调度的对抗面；本文件锁定
其中零引用或仅经网关间接覆盖的**纯函数与模型约束**（并发/调度语义优先）：

- ``is_terminal_phase`` / ``to_swarm_error_code``：终态词表与错误码映射；
- ``DelegationBudget``：ge=0 约束 + extra=forbid（无走私入口）；
- ``DelegationOutcome.is_terminal_success`` / ``runner_extras`` 有界化
  （>12 键截断、dict/list/标量逐类裁剪）；
- ``verify_outcome`` 三分支直测（空摘要拒绝 / 预期产出未达降级 /
  未授权能力拒绝 / 干净透传同一对象）；
- ``FairSlotScheduler.snapshot``：metrics 投影 + 有界背压拒绝计数；
- ``DelegationLedger``：代际记忆上限（最老出账 → late）、显式 generation
  的 terminal/phase_of、request_digest 80 字裁剪。
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.services.agent_swarm.delegation import (
    DelegationBudget,
    DelegationContractError,
    DelegationFailureReason,
    DelegationLedger,
    DelegationOutcome,
    DelegationParent,
    DelegationRequest,
    DelegationStatus,
    FairSlotScheduler,
    is_terminal_phase,
    to_swarm_error_code,
    verify_outcome,
)


def _req(session: str = "s1", **kw: Any) -> DelegationRequest:
    return DelegationRequest(
        delegation_id=f"dg-{uuid.uuid4().hex[:10]}",
        goal=kw.pop("goal", "测试委派"),
        parent=DelegationParent(session_id=session),
        **kw,
    )


def _out(status: str, **kw: Any) -> DelegationOutcome:
    return DelegationOutcome(
        delegation_id=kw.pop("delegation_id", "dg-u1"),
        status=status,
        **kw,
    )


class TestVocabularyAndMapping:
    def test_is_terminal_phase_vocabulary(self):
        assert all(is_terminal_phase(p) for p in (
            "succeeded", "degraded", "failed", "cancelled", "expired",
        ))
        assert not any(is_terminal_phase(p) for p in (
            "created", "acquiring", "running", "nope",
        ))

    def test_to_swarm_error_code_mapping(self):
        assert to_swarm_error_code(_out("failed",
            failure_reason=DelegationFailureReason.TIMEOUT)) == "timeout"
        assert to_swarm_error_code(_out("cancelled",
            failure_reason=DelegationFailureReason.CANCELLED)) == "cancelled"
        for reason in (
            DelegationFailureReason.CHILD_CRASH,
            DelegationFailureReason.BUDGET_EXCEEDED,
            DelegationFailureReason.ACQUIRE_CRASH,
        ):
            assert to_swarm_error_code(_out("failed", failure_reason=reason)) \
                == "non_retryable"


class TestBudgetAndOutcomeBounds:
    def test_delegation_budget_constraints(self):
        b = DelegationBudget()
        assert b.max_tool_calls is None and b.max_total_tokens is None
        with pytest.raises(ValidationError):
            DelegationBudget(max_tool_calls=-1)
        with pytest.raises(ValidationError):
            DelegationBudget(max_total_tokens=-5)
        # extra=forbid：预算面没有自由 payload 入口
        with pytest.raises(ValidationError):
            DelegationBudget(max_tool_calls=3, surprise=1)

    def test_delegation_outcome_is_terminal_success(self):
        assert _out(DelegationStatus.SUCCEEDED).is_terminal_success()
        assert _out(DelegationStatus.DEGRADED).is_terminal_success()
        for status in (
            DelegationStatus.FAILED,
            DelegationStatus.CANCELLED,
            DelegationStatus.EXPIRED,
        ):
            assert not _out(status).is_terminal_success()

    def test_runner_extras_bounding(self):
        # 标量值：>12 键截断、单值 200 字裁剪
        out = _out("failed", runner_extras={f"k{i}": "v" * 500 for i in range(20)})
        assert len(out.runner_extras) == 12
        assert all(len(v) <= 200 for v in out.runner_extras.values())
        # dict 值 8 键 / 120 字；list 值 8 项 / 120 字
        nested = _out("failed", runner_extras={
            "d": {f"kk{i}": "y" * 300 for i in range(20)},
            "l": [f"z{i}" * 100 for i in range(20)],
        })
        assert len(nested.runner_extras["d"]) == 8
        assert all(len(v) <= 120 for v in nested.runner_extras["d"].values())
        assert len(nested.runner_extras["l"]) == 8


class TestVerifyOutcome:
    def test_rejects_empty_summary(self):
        v = verify_outcome(
            _req(), _out(DelegationStatus.SUCCEEDED, summary="   "),
        )
        assert v.status == DelegationStatus.FAILED
        assert "empty_summary" in v.verdict_reasons
        assert v.failure_reason == DelegationFailureReason.RECEIPT_INVALID
        assert v.verdict == "rejected"
        assert "empty_summary" in v.error

    def test_degrades_unmet_expected_outputs(self):
        v = verify_outcome(
            _req(expected_outputs=["ref:map"]),
            _out(DelegationStatus.SUCCEEDED, summary="ok"),
        )
        assert v.status == DelegationStatus.DEGRADED
        assert v.verdict_reasons == ["expected_outputs_unmet"]
        assert v.verdict == "degraded"

    def test_rejects_alien_capability(self):
        v = verify_outcome(
            _req(allowed_capabilities=["map.query"]),
            _out(DelegationStatus.SUCCEEDED, summary="ok",
                 capabilities_used=["map.query", "shell.exec"]),
        )
        assert v.status == DelegationStatus.FAILED
        assert v.verdict_reasons == ["capabilities_not_granted:shell.exec"]

    def test_clean_passthrough_is_same_object(self):
        out = _out(DelegationStatus.SUCCEEDED, summary="ok",
                   produced_refs=["ref:out-1"])
        assert verify_outcome(_req(), out) is out


class TestSchedulerSnapshot:
    async def test_snapshot_counters_and_bounded_backpressure(self):
        sched = FairSlotScheduler(global_max=1, per_session_max=1, max_queue=2)
        lease = await sched.acquire(_req(session="s1"))
        snap = sched.snapshot()
        assert snap["global_max"] == 1 and snap["active"] == 1
        assert snap["active_by_session"] == {"s1": 1}
        assert snap["waiting"] == 0 and snap["queue_rejected"] == 0

        # 两个等待者占满 max_queue=2，第三个诚实拒绝（有界背压）
        waiters = [
            asyncio.ensure_future(sched.acquire(_req(session=f"s{i}")))
            for i in (2, 3)
        ]
        await asyncio.sleep(0.05)
        assert sched.snapshot()["waiting"] == 2
        with pytest.raises(DelegationContractError, match="queue full"):
            await sched.acquire(_req(session="s9"))
        assert sched.snapshot()["queue_rejected"] == 1

        sched.release(lease)  # 唤醒队首等待者
        second = await asyncio.wait_for(waiters[0], timeout=2.0)
        assert isinstance(second.lease_id, str) and second.lease_id
        for w in waiters[1:]:
            w.cancel()
        assert sched.snapshot()["wait_total"] == 2


class TestLedgerGenerations:
    def test_generation_cap_evicts_oldest(self):
        # MAX_GENERATIONS_REMEMBERED=4：第 5 代登记后 gen 1 出账
        ledger = DelegationLedger()
        for _ in range(5):
            ledger.register("dg-cap", request_digest="d" * 100)
        stale = _out("succeeded", summary="s")
        assert ledger.offer("dg-cap", stale, generation=1) == "late"
        assert ledger.late_discarded == 1
        assert ledger.terminal("dg-cap", generation=1) is None

    def test_terminal_and_phase_of_explicit_generation(self):
        ledger = DelegationLedger()
        assert ledger.register("dg-gen") == 1
        assert ledger.offer("dg-gen", _out("succeeded", summary="s")) \
            == "accepted"
        assert ledger.register("dg-gen") == 2  # 换代
        assert ledger.terminal("dg-gen") is None  # 当前代（2）无终态
        assert ledger.terminal("dg-gen", generation=1) is not None
        assert ledger.phase_of("dg-gen", generation=1) == "succeeded"
        assert ledger.phase_of("dg-gen") == "created"  # gen 2 刚登记

    def test_register_digest_clipped_to_80(self):
        ledger = DelegationLedger()
        ledger.register("dg-d", request_digest="x" * 200)
        assert ledger._entries["dg-d"]["request_digest"] == "x" * 80
