"""Delegation Protocol（H07/ADR-0216）协议级测试。

覆盖面（对应 DoD 验证清单）：
- 状态机：合法/非法转移、终态封闭；
- 请求契约：refs-only、无 payload 走私入口、资源类词表；
- 公平调度器：全局/每会话/heavy 上限、FIFO 公平、排队 deadline 过期；
- 台账 fencing：首终态 accepted、重复 duplicate、旧代际迟到 late；
- Gateway：acquire 崩溃 / runner 崩溃 / 超时 / 取消传播 + 迟到结果隔离 /
  receipt 验证（缺证据不得当成功）/ 槽位零泄漏 / CancelledError 纪律；
- FakeSubagentRunner：确定性离线（零 LLM/DB/网络）；
- 生产接线 parity：spawn_subagent 单发结果 dict 逐字节同形、harness
  degraded 诚实失败、orchestrator 台账投影。
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, List

import pytest

from app.services.agent_swarm.delegation import (
    DelegationContractError,
    DelegationFailureReason,
    DelegationGateway,
    DelegationLedger,
    DelegationOutcome,
    DelegationParent,
    DelegationPhase,
    DelegationRequest,
    DelegationStatus,
    FakeSubagentRunner,
    FairSlotScheduler,
    check_delegation_transition,
)
from app.services.agent_swarm.delegation_adapters import (
    outcome_from_subagent_result,
)
from app.services.agent_swarm.delegation_contracts import SwarmContractError


def _req(delegation_id: str = "", *, session: str = "s1", **kw: Any) -> DelegationRequest:
    return DelegationRequest(
        delegation_id=delegation_id or f"dg-{uuid.uuid4().hex[:10]}",
        goal=kw.pop("goal", "测试委派目标"),
        parent=DelegationParent(session_id=session),
        **kw,
    )


def _out(delegation_id: str, status: str, **kw: Any) -> DelegationOutcome:
    return DelegationOutcome(delegation_id=delegation_id, status=status, **kw)


# ─────────────────────────── 状态机 ───────────────────────────


class TestPhaseMachine:
    def test_happy_path_transitions_legal(self):
        # 合法转移静默通过（返回 None）；非法由 fail-loud 用例负向覆盖
        assert check_delegation_transition(
            DelegationPhase.CREATED, DelegationPhase.ACQUIRING
        ) is None
        assert check_delegation_transition(
            DelegationPhase.ACQUIRING, DelegationPhase.RUNNING
        ) is None
        assert check_delegation_transition(
            DelegationPhase.RUNNING, DelegationPhase.SUCCEEDED
        ) is None

    def test_acquire_phase_can_reach_every_terminal(self):
        for terminal in (
            DelegationPhase.FAILED,
            DelegationPhase.CANCELLED,
            DelegationPhase.EXPIRED,
            DelegationPhase.DEGRADED,
        ):
            assert check_delegation_transition(DelegationPhase.ACQUIRING, terminal) is None

    def test_illegal_transition_fail_closed(self):
        for bad in (
            (DelegationPhase.RUNNING, DelegationPhase.ACQUIRING),
            (DelegationPhase.CREATED, DelegationPhase.RUNNING),
            (DelegationPhase.SUCCEEDED, DelegationPhase.FAILED),
            (DelegationPhase.CANCELLED, DelegationPhase.RUNNING),
        ):
            with pytest.raises(SwarmContractError):
                check_delegation_transition(*bad)


# ─────────────────────────── 请求契约 ───────────────────────────


class TestRequestContract:
    def test_context_refs_reject_non_ref_payload(self):
        # pydantic v2 会把 validator 异常包装为 ValidationError（swarm 套件同口径）
        with pytest.raises(Exception, match="ref:"):
            _req(context_refs=["{‘geojson’: ‘…巨大的地图数据…’}"])

    def test_no_payload_smuggling_field(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DelegationRequest(
                delegation_id="dg-x", goal="g",
                parent=DelegationParent(session_id="s"),
                payload={"secret": "token"},  # type: ignore[call-arg]
            )

    def test_unknown_resource_class_rejected(self):
        with pytest.raises(Exception, match="资源类"):
            _req(resource_class="gpu_hog")

    def test_goal_clipped(self):
        r = _req(goal="g" * 5000)
        assert len(r.goal) == 800


# ─────────────────────────── 公平调度器 ───────────────────────────


class TestFairSlotScheduler:
    async def test_global_cap_and_session_cap(self):
        sched = FairSlotScheduler(global_max=2, per_session_max=1)
        r1 = _req(session="a")
        r2 = _req(session="b")
        r3 = _req(session="c")
        l1 = await sched.acquire(r1)
        l2 = await sched.acquire(r2)
        assert sched.snapshot()["active"] == 2
        # 全局 2 已满 → r3 排队
        t3 = asyncio.ensure_future(sched.acquire(r3))
        await asyncio.sleep(0.01)
        assert not t3.done()
        sched.release(l2)  # 释放后容量可行 → FIFO 唤醒 c
        l3 = await asyncio.wait_for(t3, timeout=1)
        assert sched.snapshot()["active"] == 2
        sched.release(l1)
        sched.release(l3)
        assert sched.snapshot()["active"] == 0

    async def test_per_session_cap_queues_same_session(self):
        sched = FairSlotScheduler(global_max=4, per_session_max=1)
        l1 = await sched.acquire(_req(session="a"))
        t2 = asyncio.ensure_future(sched.acquire(_req(session="a")))
        await asyncio.sleep(0.01)
        assert not t2.done()  # 同会话达上限 → 排队
        l_other = await sched.acquire(_req(session="b"))
        assert l_other is not None  # 其它会话不受影响
        sched.release(l1)
        l2 = await asyncio.wait_for(t2, timeout=1)
        sched.release(l_other)
        sched.release(l2)
        assert sched.snapshot()["active"] == 0

    async def test_light_not_blocked_behind_unready_heavy_head(self):
        """队头 heavy 取不到 heavy 槽位时，可就绪的 light 不被队头阻塞。"""
        sched = FairSlotScheduler(global_max=4, heavy_global_max=1)
        h1 = await sched.acquire(_req(session="a", resource_class="heavy"))
        t2 = asyncio.ensure_future(
            sched.acquire(_req(session="b", resource_class="heavy"))
        )
        await asyncio.sleep(0.01)
        assert not t2.done()
        light = await asyncio.wait_for(
            sched.acquire(_req(session="c", resource_class="light")), timeout=1
        )
        assert light is not None  # 未被 heavy 队头卡死
        sched.release(h1)
        l2 = await asyncio.wait_for(t2, timeout=1)
        sched.release(light)
        sched.release(l2)

    async def test_fifo_fairness_across_sessions(self):
        sched = FairSlotScheduler(global_max=1, per_session_max=1)
        holder = await sched.acquire(_req(session="holder"))
        order: List[str] = []

        async def _waiter(sid: str) -> None:
            lease = await sched.acquire(_req(session=sid))
            order.append(sid)
            sched.release(lease)

        t_b = asyncio.ensure_future(_waiter("b"))
        await asyncio.sleep(0.005)
        t_c = asyncio.ensure_future(_waiter("c"))
        await asyncio.sleep(0.01)
        sched.release(holder)
        await asyncio.wait_for(asyncio.gather(t_b, t_c), timeout=2)
        assert order == ["b", "c"]  # FIFO：先排队先获得

    async def test_heavy_cap_bounded(self):
        sched = FairSlotScheduler(global_max=4, heavy_global_max=1)
        h1 = await sched.acquire(_req(session="a", resource_class="heavy"))
        t2 = asyncio.ensure_future(
            sched.acquire(_req(session="b", resource_class="heavy"))
        )
        await asyncio.sleep(0.01)
        assert not t2.done()  # heavy 上限封住第二个 heavy
        sched.release(h1)
        l2 = await asyncio.wait_for(t2, timeout=1)
        sched.release(l2)

    async def test_queue_deadline_expiry(self):
        sched = FairSlotScheduler(global_max=1, per_session_max=1)
        holder = await sched.acquire(_req(session="x"))
        loop = asyncio.get_running_loop()
        with pytest.raises(asyncio.TimeoutError):
            await sched.acquire(
                _req(session="x"), deadline_ts=loop.time() + 0.05
            )
        sched.release(holder)
        # 超时 waiter 不残留（等待队列清空、槽位可再取）
        assert sched.snapshot()["waiting"] == 0
        lease = await sched.acquire(_req(session="x"))
        sched.release(lease)

    async def test_double_release_is_honest_noop(self):
        sched = FairSlotScheduler(global_max=1)
        lease = await sched.acquire(_req(session="a"))
        sched.release(lease)
        sched.release(lease)  # 双释放：诚实忽略，不超发
        assert sched.snapshot()["active"] == 0
        lease2 = await sched.acquire(_req(session="a"))
        assert lease2 is not None

    async def test_stale_loop_ledger_is_rebuilt_not_poisoned(self):
        """跨 loop 防护（Governor 先例）：loop 更换清陈旧 waiter/lease，
        新 loop 不被旧 loop 残留占位毒化。"""
        sched = FairSlotScheduler(global_max=1)
        await sched.acquire(_req(session="old"))
        assert sched.snapshot()["active"] == 1
        sched._loop_key = 0  # 模拟旧 loop 消亡后共享实例落入新 loop
        lease2 = await sched.acquire(_req(session="new"))
        assert lease2 is not None  # 陈旧 active 已清，不卡死
        assert sched.snapshot()["active"] == 1
        sched.release(lease2)

    async def test_queue_depth_bounded_backpressure(self):
        """等待队列有界：满员诚实拒绝（不无界积压），metrics 披露。"""
        from app.services.agent_swarm.delegation import DelegationContractError

        sched = FairSlotScheduler(global_max=1, per_session_max=1, max_queue=2)
        holder = await sched.acquire(_req(session="h"))
        w1 = asyncio.ensure_future(sched.acquire(_req(session="w1")))
        w2 = asyncio.ensure_future(sched.acquire(_req(session="w2")))
        await asyncio.sleep(0.01)
        assert len(sched._waiters) == 2  # 队列已满
        with pytest.raises(DelegationContractError, match="queue full"):
            await sched.acquire(_req(session="w3"))
        assert sched.snapshot()["queue_rejected"] == 1
        sched.release(holder)

        async def _drain(task) -> None:
            lease = await asyncio.wait_for(task, timeout=2)
            sched.release(lease)

        await asyncio.gather(_drain(w1), _drain(w2))
        assert sched.snapshot()["active"] == 0

    async def test_gateway_queue_full_is_honest_failure(self):
        """gateway 侧：队列满员折算 FAILED(queue_full)，绝不挂起。"""
        sched = FairSlotScheduler(global_max=1, per_session_max=1, max_queue=1)
        gw = DelegationGateway(scheduler=sched)
        blocker = asyncio.ensure_future(
            gw.execute(_req(session="busy"), FakeSubagentRunner(["sleep:1"]))
        )
        await asyncio.sleep(0.01)
        waiter = asyncio.ensure_future(
            gw.execute(_req(session="w"), FakeSubagentRunner())
        )
        await asyncio.sleep(0.01)
        outcome = await gw.execute(_req(session="w2"), FakeSubagentRunner())
        assert outcome.status == DelegationStatus.FAILED
        assert outcome.failure_reason == "queue_full"
        blocker.cancel()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert sched.snapshot()["active"] == 0


# ─────────────────────────── 吞吐冒烟（宽松阈值，防意外 O(n²)） ───────────────────────────


class TestThroughputSmoke:
    async def test_two_hundred_sequential_fake_delegations(self):
        """200 次确定性假委派串行完成（阈值宽松 100x：防意外结构性退化，
        不做机器速度断言）。"""
        gw = DelegationGateway()
        runner = FakeSubagentRunner()
        ok = 0
        for _ in range(200):
            outcome = await gw.execute(_req(session="perf"), runner)
            assert outcome.status == DelegationStatus.SUCCEEDED
            ok += 1
        assert ok == 200
        snap = gw.ledger.snapshot()
        assert snap["delegations"] <= 64  # 台账有界（FIFO 出账）
        assert snap["outcomes"].get(DelegationStatus.SUCCEEDED) == snap["delegations"]
        assert gw.scheduler.snapshot()["active"] == 0

    async def test_concurrent_fan_in_through_queue(self):
        """并发 fan-in（review P3-4）：50 并发挤 4 槽 + 队列，全部诚实
        终态、队列有界、槽位归零 —— 锻炼 pump/背压并发路径。
        （队列深度上限的行为面由 test_queue_depth_bounded_backpressure
        单独钉住；此处调大 max_queue 让 50 并发全部可排队。）"""
        gw = DelegationGateway(
            scheduler=FairSlotScheduler(
                global_max=4, per_session_max=4, max_queue=64
            )
        )
        runner = FakeSubagentRunner()

        async def _one(i: int) -> str:
            outcome = await gw.execute(_req(session=f"fan-{i % 7}"), runner)
            return outcome.status

        results = await asyncio.wait_for(
            asyncio.gather(*(_one(i) for i in range(50))), timeout=30
        )
        assert results.count(DelegationStatus.SUCCEEDED) == 50
        snap = gw.scheduler.snapshot()
        assert snap["active"] == 0
        assert snap["waiting"] == 0
        assert snap["wait_total"] > 0  # 确实经过队列/唤醒路径


# ─────────────────────────── 台账 fencing ───────────────────────────


class TestLedgerFencing:
    def test_first_register_generation_is_one(self):
        led = DelegationLedger()
        assert led.register("dg-a") == 1
        assert led.register("dg-a") == 2

    def test_duplicate_result_disclosed_not_merged(self):
        led = DelegationLedger()
        led.register("dg-a")
        assert led.offer("dg-a", _out("dg-a", DelegationStatus.SUCCEEDED, summary="1")) == "accepted"
        assert led.offer("dg-a", _out("dg-a", DelegationStatus.SUCCEEDED, summary="2")) == "duplicate"
        assert led.duplicate_discarded == 1
        assert led.terminal("dg-a").summary == "1"

    def test_late_result_from_old_generation_isolated(self):
        led = DelegationLedger()
        led.register("dg-a")
        led.register("dg-a")  # 换代（harness 重试语义）
        # 旧代际（gen 1）结果在 gen 2 之后到达 → 隔离
        verdict = led.offer(
            "dg-a",
            _out("dg-a", DelegationStatus.SUCCEEDED, generation=1, summary="stale"),
            generation=1,
        )
        assert verdict == "late"
        assert led.late_discarded == 1
        assert led.terminal("dg-a") is None  # 新代际未被污染
        assert led.quarantine and led.quarantine[-1]["kind"] == "late"

    def test_phase_history_recorded_and_bounded(self):
        led = DelegationLedger()
        led.register("dg-a")
        led.transition("dg-a", DelegationPhase.ACQUIRING)
        led.transition("dg-a", DelegationPhase.RUNNING)
        led.offer("dg-a", _out("dg-a", DelegationStatus.SUCCEEDED, summary="s"))
        snap = led.snapshot()
        assert snap["phases"].get(DelegationPhase.SUCCEEDED) == 1
        assert snap["outcomes"].get(DelegationStatus.SUCCEEDED) == 1

    def test_unknown_delegation_offer_is_late(self):
        led = DelegationLedger()
        assert led.offer("dg-ghost", _out("dg-ghost", DelegationStatus.FAILED)) == "late"

    def test_illegal_transition_fail_loud(self):
        led = DelegationLedger()
        led.register("dg-a")
        with pytest.raises(DelegationContractError):
            led.transition("dg-a", DelegationPhase.RUNNING)  # CREATED→RUNNING 非法


# ─────────────────────────── Gateway 对抗面 ───────────────────────────


class TestGatewayAdversarial:
    async def test_happy_path_carries_causal_ids(self):
        gw = DelegationGateway()
        req = _req(session="sess-1")
        outcome = await gw.execute(req, FakeSubagentRunner())
        assert outcome.status == DelegationStatus.SUCCEEDED
        assert outcome.verdict == "ok"
        assert outcome.session_id == "sess-1"
        assert outcome.lease_id.startswith("lease-")
        assert outcome.generation == 1
        assert gw.ledger.phase_of(req.delegation_id) == DelegationPhase.SUCCEEDED
        assert gw.scheduler.snapshot()["active"] == 0

    async def test_runner_crash_is_honest_failure_never_raises(self):
        gw = DelegationGateway()
        req = _req()
        outcome = await gw.execute(req, FakeSubagentRunner([RuntimeError("boom")]))
        assert outcome.status == DelegationStatus.FAILED
        assert outcome.failure_reason == DelegationFailureReason.CHILD_CRASH
        assert "boom" in outcome.error
        assert gw.scheduler.snapshot()["active"] == 0

    async def test_acquire_crash_is_fail_closed(self):
        class ExplodingScheduler(FairSlotScheduler):
            async def acquire(self, request, **kw):
                raise RuntimeError("scheduler broken")

        gw = DelegationGateway(scheduler=ExplodingScheduler())
        outcome = await gw.execute(_req(), FakeSubagentRunner())
        assert outcome.status == DelegationStatus.FAILED
        assert outcome.failure_reason == DelegationFailureReason.ACQUIRE_CRASH
        assert gw.ledger.phase_of(outcome.delegation_id) == DelegationPhase.FAILED

    async def test_run_deadline_timeout_cancels_child(self):
        gw = DelegationGateway(scheduler=FairSlotScheduler(global_max=4, per_session_max=4))
        req = _req(deadline_s=0.05)
        outcome = await gw.execute(req, FakeSubagentRunner(["sleep:2"]))
        assert outcome.status == DelegationStatus.FAILED
        assert outcome.failure_reason == DelegationFailureReason.TIMEOUT
        assert gw.scheduler.snapshot()["active"] == 0

    async def test_queue_expiry_is_expired_not_timeout(self):
        sched = FairSlotScheduler(global_max=1, per_session_max=1)
        gw = DelegationGateway(scheduler=sched)
        blocker = asyncio.ensure_future(
            gw.execute(_req(session="busy"), FakeSubagentRunner(["sleep:1"]))
        )
        await asyncio.sleep(0.02)
        outcome = await gw.execute(
            _req(session="busy", deadline_s=0.05), FakeSubagentRunner()
        )
        assert outcome.status == DelegationStatus.EXPIRED
        assert outcome.failure_reason == DelegationFailureReason.EXPIRED
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        assert sched.snapshot()["active"] == 0

    async def test_cancel_propagates_and_late_result_is_isolated(self):
        gw = DelegationGateway(scheduler=FairSlotScheduler(global_max=4, per_session_max=4))
        req = _req()
        runner = FakeSubagentRunner(
            [("park", 0.08)],
            late_deliver=lambda lease, out: gw.ledger.offer(
                lease.delegation_id, out, generation=lease.generation
            ),
        )
        task = asyncio.ensure_future(gw.execute(req, runner))
        await asyncio.sleep(0.02)  # runner 已挂起
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # 取消已落账、槽位已归还
        assert gw.ledger.phase_of(req.delegation_id) == DelegationPhase.CANCELLED
        assert gw.ledger.terminal(req.delegation_id).status == DelegationStatus.CANCELLED
        assert gw.scheduler.snapshot()["active"] == 0
        # 迟到的子结果被 fencing 隔离（同代已终态 → duplicate）——
        # 不覆盖 CANCELLED 终态；跨代际 late 已由台账用例单独覆盖
        await asyncio.sleep(0.12)
        assert gw.ledger.duplicate_discarded >= 1
        assert gw.ledger.terminal(req.delegation_id).status == DelegationStatus.CANCELLED

    async def test_expected_outputs_unmet_degrades_not_succeeds(self):
        gw = DelegationGateway()
        req = _req(expected_outputs=["审计结论"])
        outcome = await gw.execute(req, FakeSubagentRunner(["no_refs"]))
        assert outcome.status == DelegationStatus.DEGRADED
        assert outcome.verdict == "degraded"
        assert outcome.verdict_reasons == ["expected_outputs_unmet"]

    async def test_empty_summary_rejected_as_receipt_invalid(self):
        gw = DelegationGateway()
        outcome = await gw.execute(_req(), FakeSubagentRunner(["no_summary"]))
        assert outcome.status == DelegationStatus.FAILED
        assert outcome.failure_reason == DelegationFailureReason.RECEIPT_INVALID
        assert outcome.verdict == "rejected"

    async def test_ungranted_capability_rejected(self):
        gw = DelegationGateway()
        req = _req(allowed_capabilities=["cap.granted"])
        outcome = await gw.execute(req, FakeSubagentRunner(["alien_cap"]))
        assert outcome.status == DelegationStatus.FAILED
        assert "capabilities_not_granted" in "".join(outcome.verdict_reasons)

    async def test_runner_non_outcome_return_is_honest_failure(self):
        class BadRunner:
            async def run(self, lease):
                return {"status": "succeeded"}  # 非法返回

        gw = DelegationGateway()
        outcome = await gw.execute(_req(), BadRunner())
        assert outcome.status == DelegationStatus.FAILED
        assert outcome.failure_reason == DelegationFailureReason.CHILD_CRASH

    async def test_runner_absorbed_cancellation_folds_to_cancelled(self):
        """取消吸收面（3.11+ 防御性加固）：runner 吞 CancelledError 并正常
        返回成功 —— 委派按取消语义诚实折算，绝不让已取消的父拿到 success。"""
        from app.services.agent_swarm.delegation import DelegationLease

        class AbsorbingRunner:
            async def run(self, lease: DelegationLease) -> DelegationOutcome:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return DelegationOutcome(
                        delegation_id=lease.delegation_id,
                        generation=lease.generation,
                        lease_id=lease.lease_id,
                        status=DelegationStatus.SUCCEEDED,
                        summary="吞掉取消假装成功",
                        session_id=lease.session_id,
                    )

        gw = DelegationGateway(scheduler=FairSlotScheduler(global_max=4, per_session_max=4))
        req = _req()
        task = asyncio.ensure_future(gw.execute(req, AbsorbingRunner()))
        await asyncio.sleep(0.02)
        task.cancel()
        outcome = await asyncio.wait_for(task, timeout=5)
        assert outcome.status == DelegationStatus.CANCELLED
        assert outcome.failure_reason == DelegationFailureReason.CANCELLED
        assert gw.ledger.terminal(req.delegation_id).status == DelegationStatus.CANCELLED
        assert gw.scheduler.snapshot()["active"] == 0

    async def test_ledger_snapshot_metrics_shape(self):
        gw = DelegationGateway()
        await gw.execute(_req(), FakeSubagentRunner())
        snap = gw.ledger.snapshot()
        assert snap["delegations"] == 1
        assert snap["duplicate_discarded"] == 0
        assert snap["late_discarded"] == 0
        assert snap["quarantine"] == []

    async def test_ledger_evict_during_queue_is_honest_failure_no_leak(self):
        """共享台账出账竞面（review P1-1 回归）：排队期间 entry 被 evict →
        execute 返回诚实 FAILED(ledger_crash) 而非违约 raise，槽位归还。"""
        gw = DelegationGateway(scheduler=FairSlotScheduler(global_max=1, per_session_max=1))
        blocker = asyncio.ensure_future(
            gw.execute(_req(session="busy"), FakeSubagentRunner(["sleep:0.4"]))
        )
        await asyncio.sleep(0.02)
        victim = asyncio.ensure_future(
            gw.execute(_req(session="busy"), FakeSubagentRunner())
        )
        await asyncio.sleep(0.01)  # victim 已在队列等待
        # 模拟共享台账被其它委派挤出账（64 FIFO 出账的真实竞面）
        for i in range(70):
            gw.ledger.register(f"dg-evict-{i}")
        outcome = await asyncio.wait_for(victim, timeout=2)
        await blocker
        assert outcome.status == DelegationStatus.FAILED
        assert outcome.failure_reason == "ledger_crash"  # 不向调用方 raise
        assert gw.scheduler.snapshot()["active"] == 0  # 槽位零泄漏


# ─────────────────────────── 生产接线 parity ───────────────────────────


class TestAdapterParity:
    def test_outcome_mapping_from_subagent_result(self):
        from app.services.subagent import SubagentResult
        from app.services.agent_swarm.delegation import DelegationLease

        lease = DelegationLease(
            lease_id="lease-x", delegation_id="dg-p", generation=1, session_id="s"
        )
        ok = outcome_from_subagent_result(
            lease,
            SubagentResult(success=True, summary="done", refs=["ref:a", "bad-ref"],
                           reasoning="why", budget_usage={"tool_calls": 3},
                           lineage={"role": "r"}),
            started=0.0,
        )
        assert ok.status == DelegationStatus.SUCCEEDED
        assert ok.produced_refs == ["ref:a"]  # 非 ref 产出剔除
        assert ok.runner_extras["budget_usage"]["tool_calls"] == "3"
        assert ok.runner_extras["lineage"]["role"] == "r"

        cancelled = outcome_from_subagent_result(
            lease, SubagentResult(success=False, error="cancelled: parent"), 0.0
        )
        assert cancelled.status == DelegationStatus.CANCELLED

        budget = outcome_from_subagent_result(
            lease, SubagentResult(success=False, error="budget_exceeded:wall_time"), 0.0
        )
        assert budget.failure_reason == DelegationFailureReason.BUDGET_EXCEEDED

    async def test_run_single_delegation_result_dict_shape(self, monkeypatch):
        """spawn_subagent 单发路径 parity：结果 dict 与 legacy 同形且值型保真。"""
        from app.services import subagent as subagent_mod
        from app.services.agent_swarm.delegation_adapters import run_single_delegation

        class StubDispatcher:
            def __init__(self, registry, session_id):
                self.session_id = session_id

            async def run(self, *, task, domains=None, extra_tools=None,
                          max_rounds=10, role=None, budget_overlay=None):
                return subagent_mod.SubagentResult(
                    success=True, summary="ok", refs=["ref:z"],
                    reasoning="r", budget_usage={"tool_calls": 1, "wall_time_s": 2.5},
                    lineage={"role": role or "", "depth": 0},
                )

        monkeypatch.setattr(subagent_mod, "SubagentDispatcher", StubDispatcher)
        result, snapshot = await run_single_delegation(object(), "parity-sess",
                                                       task="做点事", role="r1")
        d = result.to_dict()
        assert set(d) == {
            "success", "summary", "refs", "reasoning", "error",
            "budget_usage", "lineage",
        }
        assert result.success is True
        assert result.refs == ["ref:z"]
        # 值型保真：raw SubagentResult 直传（不经 runner_extras 标量化）
        assert result.budget_usage["tool_calls"] == 1
        assert isinstance(result.budget_usage["tool_calls"], int)
        assert isinstance(result.budget_usage["wall_time_s"], float)
        assert result.lineage["depth"] == 0
        # 委派因果 id 增量进 lineage（既有字段零破坏）
        assert result.lineage["delegation_id"].startswith("dg-")
        assert result.lineage["delegation_status"] == DelegationStatus.SUCCEEDED
        assert snapshot["delegations"] >= 1

    async def test_spawn_tool_single_path_dict_compat(self, monkeypatch):
        """注册的 spawn_subagent 工具：单发结果 dict 键与 legacy 逐字节同形。"""
        from app.tools.registry import ToolRegistry
        from app.tools.subagent import register_subagent_tools
        from app.services import subagent as subagent_mod

        class StubDispatcher:
            def __init__(self, registry, session_id):
                pass

            async def run(self, *, task, domains=None, extra_tools=None,
                          max_rounds=10, role=None, budget_overlay=None):
                return subagent_mod.SubagentResult(
                    success=True, summary="done", refs=["ref:k"],
                    budget_usage={"tool_calls": 2},
                )

        monkeypatch.setattr(subagent_mod, "SubagentDispatcher", StubDispatcher)
        registry = ToolRegistry()
        register_subagent_tools(registry)
        tool = registry.all_metadata()["spawn_subagent"]
        assert tool is not None
        schemas = registry.get_schemas_subset({"spawn_subagent"})
        assert schemas, "spawn_subagent schema missing"
        # 经 registry dispatch 走工具闭包（含 gateway 接线路径）
        out = await registry.dispatch(
            "spawn_subagent",
            {"task": "子任务", "role": "data_scout", "session_id": "tool-sess"},
            session_id="tool-sess",
        )
        assert out["success"] is True
        assert set(out) == {
            "success", "summary", "refs", "reasoning", "error",
            "budget_usage", "lineage",
        }
        assert out["lineage"]["delegation_id"].startswith("dg-")
        assert out["budget_usage"]["tool_calls"] == 2  # 值型保真（非字符串化）
        assert isinstance(out["budget_usage"]["tool_calls"], int)

    async def test_harness_delegate_degraded_is_honest_failure(self):
        """receipt 降级（声明产出但零证据）→ 台账 failed，绝不 completed。"""
        from app.services.gis_harness.delegation import (
            DelegationSpec,
            STATUS_FAILED,
            delegate,
        )
        from app.services.session_plan import SessionPlan, save_session_plan
        from app.services.subagent import SubagentResult

        sid = f"dlg-deg-{uuid.uuid4().hex[:8]}"
        await save_session_plan(SessionPlan(
            envelope_id="env-deg", session_id=sid, user_goal="测试目标",
            gis_chapter={"plan_id": "p", "query": "测试目标"},
        ))
        try:

            class Fake:
                async def run(self, *, task, max_rounds=10, role=None, **kw):
                    return SubagentResult(success=True, summary="看起来做完了", refs=[])

            rec = await delegate(
                sid,
                DelegationSpec(role="cartography_reviewer", task="t",
                               required_outputs=["复核结论"]),
                dispatcher=Fake(),
            )
            assert rec.status == STATUS_FAILED
            assert "receipt degraded" in rec.error
        finally:
            from app.services.session_data import session_data_manager

            await session_data_manager.clear_session(sid)


# ─────────────────────────── 编排器台账投影 ───────────────────────────


class TestOrchestratorLedgerProjection:
    async def test_status_carries_delegation_snapshot(self):
        from app.services.agent_swarm.aggregator import NullSink, SwarmAggregator
        from app.services.agent_swarm.delegation_contracts import (
            SwarmSpecialistRole,
            SwarmTaskDescriptor,
        )
        from app.services.agent_swarm.dispatcher import SpecialistDispatcher
        from app.services.agent_swarm.orchestrator import SwarmOrchestrator

        class _Fixed:
            def decompose(self, root_goal, projection):
                return [
                    SwarmTaskDescriptor(
                        task_id="t1", goal="g",
                        role=SwarmSpecialistRole.DATA_HUNTER,
                    )
                ]

        class _RT:
            async def execute(self, assignment, *, on_heartbeat=None):
                from app.services.agent_swarm.delegation_contracts import (
                    SubagentReceipt,
                    SwarmReceiptStatus,
                )

                return SubagentReceipt(
                    assignment_id=assignment.assignment_id,
                    task_id=assignment.task.task_id,
                    role=assignment.task.role,
                    status=SwarmReceiptStatus.SUCCEEDED,
                    produced_refs=["ref:o1"],
                    summary="ok",
                )

        orch = SwarmOrchestrator(
            f"s-{uuid.uuid4().hex[:6]}",
            decomposer=_Fixed(),
            dispatcher=SpecialistDispatcher(_RT()),
            aggregator=SwarmAggregator(sink=NullSink()),
        )
        status = await orch.run_swarm("台账投影")
        snap = status.delegation_snapshot
        assert snap["delegations"] == 1
        assert snap["outcomes"].get(DelegationStatus.SUCCEEDED) == 1
        assert snap["phases"].get(DelegationPhase.SUCCEEDED) == 1
