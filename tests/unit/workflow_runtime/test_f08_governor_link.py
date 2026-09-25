"""F08 / ADR-0214 D2：NodeGovernorLink 生命周期（预算不泄漏）。

验收面：
- pass-through：kill-switch（GIS_WORKFLOW_GOVERNOR=0）/ 无估算 /
  governor 异常 → 直通且 close 无副作用；
- admit→execute→complete：成功/失败/取消路径恰好一次归还（close 幂等）；
- enforce 拒绝 → rejected + typed error_code（预算类不可重试、排队超时
  可重试、会话取消 → CANCELLED），observe 拒绝照常持有预留并执行；
- attempt>1 → RetryClass.WORKFLOW 实扣（RetryBudget.charge 被调）；
- 校准：completed 状态回填 CalibrationStore（bounded 键空间）。
"""
from __future__ import annotations

from typing import List, Tuple

import pytest

from app.services.governor.contract import (
    AdmissionDecision,
    Dimension,
    ResourceDecision,
    ResourceDemand,
    ResourceReservation,
    RetryClass,
    Subsystem,
)
from app.services.workflow_runtime.governor_link import (
    NodeGovernorLink,
    _classify_rejection,
    get_node_governor_link,
    node_queue_max_wait_s,
    reset_node_governor_link_for_tests,
    workflow_surface_enabled,
)


class FakeLedger:
    def __init__(self):
        self.reserved = []
        self.released = []

    def reserve(self, reservation, adjudged):
        self.reserved.append(reservation.reservation_id)

    def release(self, reservation, actual=None):
        self.released.append(reservation.reservation_id)


class FakeBp:
    def __init__(self):
        self.acquired = 0
        self.released = 0

    async def acquire(self, **kw):
        self.acquired += 1

        class T:
            channel = "heavy"
            session_id = kw.get("session_id", "")
            heavy = True
            small = False

        return T()

    async def release(self, ticket, *, actual_cost=1.0):
        self.released += 1


class FakeRetries:
    def __init__(self):
        self.charged: List[Tuple[str, str]] = []
        self.allowed = True

    def retry_allowed(self, session_id, retry_class, operation=""):
        return self.allowed, ""

    def charge(self, session_id, retry_class):
        self.charged.append((session_id, retry_class.value))


class FakeGovernor:
    def __init__(self, *, decision=AdmissionDecision.ACCEPT,
                 enforce=True, fail=False):
        from app.services.governor.config import GovernorConfig, GovernorMode

        self.config = GovernorConfig()
        self.config.mode = (GovernorMode.ENFORCE if enforce
                            else GovernorMode.OBSERVE)
        self._enforce = enforce
        self.decision = decision
        self.fail = fail
        self.ledger = FakeLedger()
        self.bp = FakeBp()
        self.retries = FakeRetries()
        self.admited: List[ResourceDemand] = []
        self.completed = 0

    async def admit_and_reserve(self, demand, **kw):
        if self.fail:
            raise RuntimeError("governor boom")
        self.admited.append(demand)
        allowed = self.decision in (AdmissionDecision.ACCEPT,
                                    AdmissionDecision.ACCEPT_WITH_LIMITS,
                                    AdmissionDecision.DEFER)
        if allowed or not self._enforce:
            # 镜像真实 facade：observe 模式下即使决策为 reject 也建
            # 预留 + ticket（回滚语义 —— 必须执行且必须 complete）。
            res = ResourceReservation(
                session_id=demand.session_id,
                subsystem=demand.subsystem,
                charged={Dimension.MEMORY_BYTES: 1.0})
            return self._decide(), res, object()
        return self._decide(), None, None

    def _decide(self):
        return ResourceDecision(decision=self.decision,
                                mode=self.config.mode.value)

    async def complete(self, reservation, ticket, **kw):
        # 镜像真实 facade：认领到预留即归还 ledger + 背压槽位
        self.completed += 1
        if reservation is not None:
            self.ledger.release(reservation)
        if ticket is not None:
            await self.bp.release(ticket)


def _estimate():
    from app.services.governor.estimation import estimate_for_tool

    return estimate_for_tool("wf_node", tool_class="light",
                             subsystem=Subsystem.WORKFLOW)


@pytest.mark.asyncio
async def test_pass_through_when_surface_disabled(monkeypatch):
    monkeypatch.setenv("GIS_WORKFLOW_GOVERNOR", "0")
    assert workflow_surface_enabled() is False
    link = NodeGovernorLink(governor=FakeGovernor())
    sess = await link.begin(node={"kind": "transform"}, node_id="n",
                            session_id="s1", estimate=_estimate())
    assert sess.pass_through is True
    await sess.close(ok=True)


@pytest.mark.asyncio
async def test_pass_through_without_estimate():
    link = NodeGovernorLink(governor=FakeGovernor())
    sess = await link.begin(node={"kind": "transform"}, node_id="n",
                            session_id="s1", estimate=None)
    assert sess.pass_through is True
    await sess.close(ok=True)


@pytest.mark.asyncio
async def test_fail_open_on_governor_exception():
    link = NodeGovernorLink(governor=FakeGovernor(fail=True))
    sess = await link.begin(node={"kind": "transform"}, node_id="n",
                            session_id="s1", estimate=_estimate())
    assert sess.pass_through is True
    assert sess.rejected is False
    await sess.close(ok=True)


@pytest.mark.asyncio
async def test_admit_complete_releases_exactly_once():
    g = FakeGovernor()
    link = NodeGovernorLink(governor=g)
    sess = await link.begin(node={"kind": "transform", "capability": "t"},
                            node_id="n", session_id="s1", attempt=1,
                            estimate=_estimate())
    assert sess.rejected is False
    assert sess._reservation is not None
    await sess.close(ok=True, duration_ms=5)
    await sess.close(ok=True)  # 幂等：第二次 no-op
    assert g.completed == 1
    assert len(g.ledger.released) == 1
    assert g.bp.released == 1


@pytest.mark.asyncio
async def test_demand_shape_workflow_subsystem_and_priority():
    g = FakeGovernor()
    link = NodeGovernorLink(governor=g)
    await link.begin(node={"kind": "transform", "priority": 9},
                     node_id="n", session_id="s1", estimate=_estimate())
    demand = g.admited[0]
    assert demand.subsystem is Subsystem.WORKFLOW
    assert demand.priority == 0  # INTERACTIVE
    assert demand.retry_class is None  # attempt=1 无重试类
    assert demand.max_wait_s == node_queue_max_wait_s()


@pytest.mark.asyncio
async def test_retry_attempt_charges_workflow_budget():
    g = FakeGovernor()
    link = NodeGovernorLink(governor=g)
    sess = await link.begin(node={"kind": "transform"}, node_id="n",
                            session_id="s1", attempt=2,
                            estimate=_estimate())
    assert sess.rejected is False
    demand = g.admited[0]
    assert demand.attempt == 2
    assert demand.retry_class is RetryClass.WORKFLOW
    assert g.retries.charged == [("s1", "workflow")]


@pytest.mark.asyncio
async def test_first_attempt_never_charges_retry_budget():
    g = FakeGovernor()
    link = NodeGovernorLink(governor=g)
    await link.begin(node={"kind": "transform"}, node_id="n",
                     session_id="s1", attempt=1, estimate=_estimate())
    assert g.retries.charged == []


@pytest.mark.asyncio
async def test_enforce_reject_budget_exceeded_not_retryable():
    g = FakeGovernor(decision=AdmissionDecision.REJECT, enforce=True)
    link = NodeGovernorLink(governor=g)
    sess = await link.begin(node={"kind": "transform"}, node_id="n",
                            session_id="s1", estimate=_estimate())
    assert sess.rejected is True
    assert sess.error_code == "RESOURCE_BUDGET_EXCEEDED"
    assert sess.retryable is False


@pytest.mark.asyncio
async def test_queue_timeout_rejection_is_retryable():
    g = FakeGovernor(decision=AdmissionDecision.DEGRADE, enforce=True)
    link = NodeGovernorLink(governor=g)

    async def degrade(*a, **kw):
        decision = ResourceDecision(
            decision=AdmissionDecision.DEGRADE,
            reasons=["queue_timeout:heavy:10.0s"],
            mode="enforce")
        return decision, None, None

    g.admit_and_reserve = degrade
    sess = await link.begin(node={"kind": "transform"}, node_id="n",
                            session_id="s1", estimate=_estimate())
    assert sess.rejected is True
    assert sess.error_code == "RESOURCE_EXHAUSTED"
    assert sess.retryable is True


@pytest.mark.asyncio
async def test_observe_rejection_still_executes_with_reservation():
    """observe 模式：拒绝决策留痕但放行 —— 必须执行且必须 complete。"""
    g = FakeGovernor(decision=AdmissionDecision.REJECT, enforce=False)
    link = NodeGovernorLink(governor=g)
    sess = await link.begin(node={"kind": "transform"}, node_id="n",
                            session_id="s1", estimate=_estimate())
    assert sess.rejected is False  # observe 不拒绝
    assert sess._reservation is not None
    await sess.close(ok=True)
    assert g.completed == 1
    assert g.bp.released == 1  # 观察语义完整：槽位不泄漏


def test_rejection_classification_table():
    assert _classify_rejection(["queue_timeout:heavy:1.0s"]) == \
        ("RESOURCE_EXHAUSTED", True)
    assert _classify_rejection(["session_cancelled_while_queued"]) == \
        ("CANCELLED", False)
    assert _classify_rejection(["global_memory_pressure:5g>4g"]) == \
        ("RESOURCE_BUDGET_EXCEEDED", False)
    assert _classify_rejection(["retry_budget:exhausted"]) == \
        ("RESOURCE_BUDGET_EXCEEDED", False)


@pytest.mark.asyncio
async def test_close_records_calibration_on_completed(monkeypatch):
    from app.services.governor import calibration as CAL

    store = CAL.CalibrationStore()
    monkeypatch.setattr(CAL, "get_calibration_store", lambda: store)
    g = FakeGovernor()
    link = NodeGovernorLink(governor=g)
    sess = await link.begin(
        node={"kind": "transform", "capability": "buffer"},
        node_id="n", session_id="s1", estimate=_estimate())
    await sess.close(ok=True, duration_ms=12)
    stats = store.stats()
    assert any(k.startswith("workflow:transform:") for k in stats)


@pytest.mark.asyncio
async def test_close_failed_records_no_calibration(monkeypatch):
    from app.services.governor import calibration as CAL

    store = CAL.CalibrationStore()
    monkeypatch.setattr(CAL, "get_calibration_store", lambda: store)
    link = NodeGovernorLink(governor=FakeGovernor())
    sess = await link.begin(node={"kind": "transform"}, node_id="n",
                            session_id="s1", estimate=_estimate())
    await sess.close(ok=False, error_code="NODE_TIMEOUT")
    assert store.stats() == {}


def test_process_link_singleton_and_reset():
    a = get_node_governor_link()
    b = get_node_governor_link()
    assert a is b
    custom = NodeGovernorLink(governor=FakeGovernor())
    assert reset_node_governor_link_for_tests(custom) is custom
    assert get_node_governor_link() is custom
    reset_node_governor_link_for_tests()


@pytest.mark.asyncio
async def test_cancelled_close_does_not_raise():
    """取消路径（deadline abandon）close 必须安静完成（绝不外泄异常）。"""
    g = FakeGovernor()

    async def boom_complete(*a, **kw):
        raise RuntimeError("complete boom")

    g.complete = boom_complete
    link = NodeGovernorLink(governor=g)
    sess = await link.begin(node={"kind": "transform"}, node_id="n",
                            session_id="s1", estimate=_estimate())
    await sess.close(ok=False, cancelled=True)  # 绝不抛
    assert sess._closed is True
