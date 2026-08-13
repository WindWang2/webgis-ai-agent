"""Runtime chaos tests for plan_mode.execute_plan_async（WP-PLAN / F6）。

缺陷 F6：execute_plan_async 用 asyncio.create_task 派发整波工具任务，但没有
try/finally 兜底。若外层 task 被取消（客户端断连）：
- pending 的波次任务继续跑到完成 —— GIS 工具在 turn 结束后仍产生副作用；
- __status__ 停留在 'running'，之后所有 execute_plan 都被
  「plan 已在执行中」拒绝（永久卡死）。

测试完全确定性：工具协程用 asyncio.Event 门控，无任何 wall-clock sleep。
"""
import asyncio

import pytest

from app.services import plan_mode as svc
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    return ToolRegistry()


def _register_gated_tool(
    registry: ToolRegistry,
    name: str,
    release: asyncio.Event,
    started_counter: dict,
    all_started: asyncio.Event,
    total: int,
    state: dict,
) -> None:
    """注册一个门控 async 工具：等待 release 事件；记录 cancelled/completed。

    async 函数在 registry 里默认 ASYNC policy（事件循环内直接 await），
    因此取消能真实传播进 ``release.wait()``。
    """

    @registry.tool(name=name, description=f"gated tool {name}")
    async def _gated() -> dict:
        started_counter["n"] += 1
        if started_counter["n"] == total:
            all_started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        state["completed"] = True
        return {"success": True, "data": {"tool": name}}


@pytest.mark.asyncio
async def test_f6_cancel_mid_wave_cancels_pending_tasks_and_converges_status(registry):
    """波次执行中取消外层 task：兄弟任务必须全部被取消回收，状态收敛到终态。"""
    sid = "sess-chaos-f6-cancel"
    release = asyncio.Event()
    all_started = asyncio.Event()
    counter = {"n": 0}
    states = {}
    for name in ("g1", "g2", "g3"):
        states[name] = {"cancelled": False, "completed": False}
        _register_gated_tool(registry, name, release, counter, all_started, 3, states[name])

    plan = svc.PlanProposal(
        title="chaos-cancel",
        steps=[svc.PlanStep(id=n, tool=n) for n in ("g1", "g2", "g3")],
    )
    plan_id = await svc.store_plan(sid, plan)

    baseline = asyncio.all_tasks()
    exec_task = asyncio.create_task(svc.execute_plan_async(sid, plan_id, registry))
    await asyncio.wait_for(all_started.wait(), timeout=10)

    exec_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await exec_task

    # (a) 所有兄弟任务收到取消；没有任何一个在取消后跑到完成
    for name, st in states.items():
        assert st["cancelled"], f"{name} 未收到取消（任务泄漏，仍在运行）"
        assert not st["completed"], f"{name} 在取消后仍跑完（副作用窗口）"

    # (b) plan 状态收敛到非 running 终态
    plan_data = await svc.load_plan(sid, plan_id)
    assert plan_data["__status__"] == "cancelled"

    # (d) 任务集合回到基线（无泄漏的 pending task）
    assert asyncio.all_tasks() - baseline == set()

    # (c) 后续 execute_plan 不再被「已在执行中」拒绝。
    # 347 P1-C: cancelled 是终态，resume 必须拒绝（不是卡在 running）。
    release.set()
    result = await svc.execute_plan_async(sid, plan_id, registry)
    assert "已在执行中" not in str(result.get("error", ""))
    assert result["success"] is False
    assert result.get("status") == "cancelled" or "已取消" in str(result.get("error", ""))


@pytest.mark.asyncio
async def test_f6_cancel_during_failure_drain_also_converges(registry):
    """兄弟失败后的 drain（asyncio.wait 无超时）阶段被取消，同样必须收敛。

    f_fail 等两个门控兄弟都启动后立即失败 → execute_plan 进入 failure-drain
    （等待 g1/g2 收尾）。此刻取消外层 task：g1/g2 必须被取消回收，
    __status__ 收敛，后续 execute_plan 不被拒绝。
    """
    sid = "sess-chaos-f6-drain"
    release = asyncio.Event()
    both_started = asyncio.Event()
    failing_returned = asyncio.Event()
    counter = {"n": 0}
    states = {n: {"cancelled": False, "completed": False} for n in ("g1", "g2")}
    for name in ("g1", "g2"):
        _register_gated_tool(registry, name, release, counter, both_started, 2, states[name])

    @registry.tool(name="f_fail", description="等兄弟启动后立刻失败")
    async def _f_fail() -> dict:
        await asyncio.wait_for(both_started.wait(), timeout=10)
        failing_returned.set()
        return {"success": False, "code": "TOOL_ERROR", "message": "boom"}

    plan = svc.PlanProposal(
        title="chaos-drain",
        steps=[
            svc.PlanStep(id="g1", tool="g1"),
            svc.PlanStep(id="g2", tool="g2"),
            svc.PlanStep(id="f1", tool="f_fail"),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)

    baseline = asyncio.all_tasks()
    exec_task = asyncio.create_task(svc.execute_plan_async(sid, plan_id, registry))
    await asyncio.wait_for(failing_returned.wait(), timeout=10)
    # 让执行循环消费掉 f_fail 的 done 事件并进入 drain（纯调度 yield，非 wall-clock）。
    # 注意：无论取消落在 FIRST_COMPLETED 循环还是 drain 的 asyncio.wait，
    # 修复后的行为都必须一致，因此此处的调度位置不影响断言正确性。
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    exec_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await exec_task

    for name, st in states.items():
        assert st["cancelled"], f"{name} 在 drain 取消时未被回收"
        assert not st["completed"], f"{name} 在取消后仍跑完"

    plan_data = await svc.load_plan(sid, plan_id)
    assert plan_data["__status__"] in ("cancelled", "failed")
    assert plan_data["__status__"] != "running"

    assert asyncio.all_tasks() - baseline == set()

    # 后续 execute_plan 不被「已在执行中」拒绝。
    # drain 取消后状态是 cancelled（347 拒绝 resume）或 failed（首达终态 /
    # livelock 守卫会再次失败），两种都证明没有卡在 running。
    release.set()
    result = await svc.execute_plan_async(sid, plan_id, registry)
    assert "已在执行中" not in str(result.get("error", ""))
    assert result["success"] is False
    if result.get("failed_step") is not None:
        assert result["failed_step"] == "f1"
    else:
        assert result.get("status") == "cancelled" or "已取消" in str(result.get("error", ""))


# ─── P2: 计划状态首达终态获胜 + 收敛写失败不替换原始异常 ──────────


@pytest.mark.asyncio
async def test_plan_status_first_terminal_wins():
    """P2: update_plan_status 首达终态获胜 —— 一旦 completed/failed/cancelled，
    后到的终态/非终态写入都不能覆盖（失败分支写 failed 后，并发取消不能把它
    翻成 cancelled，反之亦然；终态也不能被 running 复活）。"""
    sid = "sess-chaos-terminal-wins"
    plan = svc.PlanProposal(
        title="first-terminal",
        steps=[svc.PlanStep(id="s1", tool="g1")],
    )

    # failed 先到达，cancelled 后到 —— 不能翻转
    plan_id = await svc.store_plan(sid, plan)
    await svc.update_plan_status(sid, plan_id, __status__="failed", __error__="boom")
    await svc.update_plan_status(sid, plan_id, __status__="cancelled", __error__="cancelled")
    data = await svc.load_plan(sid, plan_id)
    assert data["__status__"] == "failed", "cancelled overwrote failed (P2 first-terminal-wins)"
    assert data["__error__"] == "boom"

    # cancelled 先到达，failed 后到不翻转；running 也不能复活终态
    plan_id2 = await svc.store_plan(sid, plan)
    await svc.update_plan_status(sid, plan_id2, __status__="cancelled")
    await svc.update_plan_status(sid, plan_id2, __status__="failed", __error__="late")
    await svc.update_plan_status(sid, plan_id2, __status__="running")
    data2 = await svc.load_plan(sid, plan_id2)
    assert data2["__status__"] == "cancelled", "failed/running overwrote cancelled (P2)"
    assert "__error__" not in data2, "companion fields of a losing write leaked"

    # 非终态更新不受影响（pending -> running）
    plan_id3 = await svc.store_plan(sid, plan)
    await svc.update_plan_status(sid, plan_id3, __status__="running")
    assert (await svc.load_plan(sid, plan_id3))["__status__"] == "running"


@pytest.mark.asyncio
async def test_cancel_handler_convergence_write_failure_reraises_cancelled(
    registry, monkeypatch, caplog
):
    """P2: cancel handler 里 update_plan_status 抛错（Redis 抖动）不能替换
    CancelledError —— 原异常必须原样传播（旧实现把 RuntimeError 抛给调用方，
    cancel 语义丢失），收敛写失败只记 warning；波次任务照常回收。

    注意：收敛写本身失败时状态停在 running 是存储宕机的真实结果（写没落库）；
    本修复保证的是「能收敛时收敛、不能时也不吞原始异常」。
    """
    import logging as _logging

    sid = "sess-chaos-failwrite"
    release = asyncio.Event()
    started = asyncio.Event()
    counter = {"n": 0}
    state = {}
    _register_gated_tool(registry, "g1", release, counter, started, 1, state)

    plan = svc.PlanProposal(
        title="chaos-failwrite",
        steps=[svc.PlanStep(id="g1", tool="g1")],
    )
    plan_id = await svc.store_plan(sid, plan)

    real_update = svc.update_plan_status

    async def flaky_update(sid_, pid_, **updates):
        if updates.get("__status__") in ("cancelled", "failed"):
            raise RuntimeError("redis down")
        await real_update(sid_, pid_, **updates)

    monkeypatch.setattr(svc, "update_plan_status", flaky_update)

    baseline = asyncio.all_tasks()
    exec_task = asyncio.create_task(svc.execute_plan_async(sid, plan_id, registry))
    await asyncio.wait_for(started.wait(), timeout=10)

    exec_task.cancel()
    with caplog.at_level(_logging.WARNING, logger="app.services.plan_mode"):
        with pytest.raises(asyncio.CancelledError):
            await exec_task

    # 波次任务全部回收（无泄漏）
    assert asyncio.all_tasks() - baseline == set()
    # 收敛写失败被降级为 warning，而不是替换原始 CancelledError
    assert any("收敛写失败" in r.message for r in caplog.records), (
        "convergence write failure was not logged as a warning"
    )
