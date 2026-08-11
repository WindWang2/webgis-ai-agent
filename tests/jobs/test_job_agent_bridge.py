"""Agent Turn → Tool Step → Durable Job 关联，以及取消贯穿工具管道（ADR-0052）。

覆盖规范 §21（agent 与后台 job 不是两条无关 UI 条目）、§11（取消贯穿到工具实现）、
Scenario 1（短工具正常完成）、Scenario 2（重工具创建 durable job）、
Scenario 3（取消真正停止计算）。
"""
import asyncio

import pytest

from app.services.chat.tool_pipeline import ToolExecutionPipeline
from app.services.jobs.cancellation import checkpoint, current_token
from app.services.jobs.context import JobOrigin, current_origin, use_origin
from app.services.task_tracker import TaskTracker
from app.services.tool_dispatch_service import ToolDispatchResult


def _tc(name: str, args: str = "{}") -> dict:
    return {"id": f"call-{name}", "function": {"name": name, "arguments": args}}


def _ok(payload=None) -> ToolDispatchResult:
    return ToolDispatchResult(
        status="ok",
        llm_payload="done",
        slim_event={"ok": True},
        geojson_ref=None,
        raw_result=payload or {"ok": True},
        error_msg=None,
    )


def _pipeline(dispatch_fn, tracker=None) -> ToolExecutionPipeline:
    class _Registry:
        pass

    return ToolExecutionPipeline(
        registry=_Registry(), tracker=tracker or TaskTracker(), dispatch_fn=dispatch_fn
    )


# ── Scenario 1：短工具正常完成 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_short_tool_completes_normally():
    tracker = TaskTracker()
    task = tracker.create("sess-1", "帮我算个缓冲区")

    async def dispatch(tc, session_id, sentinels):
        return _ok({"features": 3})

    pipeline = _pipeline(dispatch, tracker)
    result = await pipeline.execute_tool_call(_tc("buffer"), "sess-1", task.id)

    assert result.is_error is False
    assert result.cancelled is False
    assert result.background_job_ids == []
    assert len(task.steps) == 1
    assert task.steps[0].status.value == "completed"


# ── 取消 token 贯穿 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_token_is_bound_during_dispatch():
    """规范 §11：token 必须一路传到工具实现，而不是只在 step 之间检查。"""
    tracker = TaskTracker()
    task = tracker.create("sess-1", "req")
    seen: list[object] = []

    async def dispatch(tc, session_id, sentinels):
        seen.append(current_token())
        return _ok()

    await _pipeline(dispatch, tracker).execute_tool_call(_tc("t"), "sess-1", task.id)
    assert seen[0] is task.cancel_token
    assert current_token() is None, "dispatch 结束后必须还原上下文"


@pytest.mark.asyncio
async def test_cancel_stops_tool_at_checkpoint():
    """Scenario 3：取消后工具在 checkpoint 处退出，剩余 chunk 不再执行。"""
    tracker = TaskTracker()
    task = tracker.create("sess-1", "重计算")
    executed: list[int] = []

    async def dispatch(tc, session_id, sentinels):
        # 模拟一个分块 GIS 循环
        for i in range(100):
            checkpoint()
            executed.append(i)
            if i == 3:
                tracker.cancel(task.id)
        return _ok()

    result = await _pipeline(dispatch, tracker).execute_tool_call(_tc("heavy"), "sess-1", task.id)

    assert len(executed) <= 5, f"取消后仍执行了 {len(executed)}/100 个 chunk"
    assert result.cancelled is True
    assert result.is_error is True
    assert result.raw_result["cancelled"] is True


@pytest.mark.asyncio
async def test_cancellation_is_not_reported_as_tool_failure_payload():
    """取消给 LLM 的说明必须是「已取消」而不是一个异常堆栈。"""
    tracker = TaskTracker()
    task = tracker.create("sess-1", "req")
    tracker.cancel(task.id)

    async def dispatch(tc, session_id, sentinels):
        checkpoint()
        return _ok()

    result = await _pipeline(dispatch, tracker).execute_tool_call(_tc("t"), "sess-1", task.id)
    assert result.cancelled is True
    assert "取消" in result.llm_payload
    assert "Traceback" not in result.llm_payload


@pytest.mark.asyncio
async def test_ordinary_tool_error_is_not_marked_cancelled():
    """普通工具异常必须与取消区分开 —— 取消绝不能触发 retry（规范 §17）。"""
    tracker = TaskTracker()
    task = tracker.create("sess-1", "req")

    async def dispatch(tc, session_id, sentinels):
        raise ValueError("bad crs")

    result = await _pipeline(dispatch, tracker).execute_tool_call(_tc("t"), "sess-1", task.id)
    assert result.is_error is True
    assert result.cancelled is False


@pytest.mark.asyncio
async def test_cancel_token_is_shared_across_steps_of_same_task():
    tracker = TaskTracker()
    task = tracker.create("sess-1", "req")
    assert tracker.cancel_token_for(task.id) is task.cancel_token
    tracker.cancel(task.id)
    assert task.cancel_token.cancelled is True
    assert tracker.is_cancelled(task.id) is True


@pytest.mark.asyncio
async def test_cancel_is_idempotent_at_tracker_level():
    tracker = TaskTracker()
    task = tracker.create("sess-1", "req")
    assert tracker.cancel(task.id) is True
    assert tracker.cancel(task.id) is True  # 幂等：仍返回 True，不重复点燃
    assert tracker.cancel("task-unknown") is False


def test_dropped_task_releases_its_token():
    """token 必须随 task 一起回收，否则 registry 随进程寿命单调增长。"""
    from app.services.jobs.cancellation import registry as cancellation_registry

    tracker = TaskTracker()
    task = tracker.create("sess-1", "req")
    assert cancellation_registry.get(task.id) is not None
    tracker._drop_task(task.id)  # noqa: SLF001 —— 泄漏断言需要触发内部回收
    assert cancellation_registry.get(task.id) is None


# ── Scenario 2：agent ↔ durable job 关联 ───────────────────────────


@pytest.mark.asyncio
async def test_origin_is_bound_with_agent_linkage():
    """工具执行期间 JobOrigin 必须带齐 session/owner/agent step 关联。"""
    tracker = TaskTracker()
    task = tracker.create("sess-1", "算 NDVI")
    step = tracker.start_step(task.id, "analyze_vegetation_index", {})
    captured: list[JobOrigin] = []

    async def dispatch(tc, session_id, sentinels):
        captured.append(current_origin())
        return _ok()

    await _pipeline(dispatch, tracker).execute_tool_call(
        _tc("analyze_vegetation_index"),
        "sess-1",
        task.id,
        pre_created_step=step,
        owner_id="user-a",
        owner_token="tok-a",
    )

    origin = captured[0]
    assert origin.session_id == "sess-1"
    assert origin.owner_id == "user-a"
    assert origin.owner_token == "tok-a"
    assert origin.agent_task_id == task.id
    assert origin.agent_step_id == step.id
    assert origin.tool_call_id == "call-analyze_vegetation_index"
    assert origin.tool_name == "analyze_vegetation_index"


@pytest.mark.asyncio
async def test_background_job_ids_flow_to_step_and_result():
    """规范 §21：工具创建的 durable job 挂到该 tool step，并出现在执行结果里。"""
    tracker = TaskTracker()
    task = tracker.create("sess-1", "算 NDVI")
    step = tracker.start_step(task.id, "analyze_vegetation_index", {})

    async def dispatch(tc, session_id, sentinels):
        # 模拟工具内部提交 durable job（submit_durable_job 会做同样的事）
        current_origin().record_job(4242)
        return _ok({"job_id": "4242"})

    result = await _pipeline(dispatch, tracker).execute_tool_call(
        _tc("analyze_vegetation_index"), "sess-1", task.id, pre_created_step=step
    )

    assert result.background_job_ids == ["4242"]
    assert step.background_job_ids == ["4242"]
    assert task.background_job_ids == ["4242"]


@pytest.mark.asyncio
async def test_background_job_ids_recorded_without_pre_created_step():
    tracker = TaskTracker()
    task = tracker.create("sess-1", "req")

    async def dispatch(tc, session_id, sentinels):
        current_origin().record_job("7")
        return _ok()

    result = await _pipeline(dispatch, tracker).execute_tool_call(_tc("t"), "sess-1", task.id)
    assert result.background_job_ids == ["7"]
    assert task.steps[0].background_job_ids == ["7"]


def test_task_background_job_ids_are_deduped_in_order():
    tracker = TaskTracker()
    task = tracker.create("sess-1", "req")
    s1 = tracker.start_step(task.id, "a", {})
    s2 = tracker.start_step(task.id, "b", {})
    s1.background_job_ids = ["1", "2"]
    s2.background_job_ids = ["2", "3"]
    assert task.background_job_ids == ["1", "2", "3"]


def test_origin_record_job_is_idempotent():
    origin = JobOrigin()
    origin.record_job(1)
    origin.record_job("1")
    assert origin.created_job_ids == ["1"]


def test_origin_child_inherits_owner_but_not_collector():
    parent = JobOrigin(session_id="s", owner_id="u", agent_task_id="task-1")
    parent.record_job("9")
    child = parent.child(tool_name="other")
    assert child.session_id == "s"
    assert child.owner_id == "u"
    assert child.agent_task_id == "task-1"
    assert child.tool_name == "other"
    assert child.created_job_ids == []


def test_use_origin_restores_previous():
    assert current_origin() is None
    outer = JobOrigin(session_id="outer")
    with use_origin(outer):
        assert current_origin() is outer
        with use_origin(JobOrigin(session_id="inner")):
            assert current_origin().session_id == "inner"
        assert current_origin() is outer
    assert current_origin() is None


# ── 并行 wave 中的取消隔离 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_tools_share_task_token():
    """同一 turn 的并行工具共享 token：取消一次全部停。"""
    tracker = TaskTracker()
    task = tracker.create("sess-1", "req")
    started = asyncio.Event()
    executed: dict[str, int] = {"a": 0, "b": 0}

    async def dispatch(tc, session_id, sentinels):
        name = tc["function"]["name"]
        for _ in range(100):
            checkpoint()
            executed[name] += 1
            started.set()
            await asyncio.sleep(0)
        return _ok()

    pipeline = _pipeline(dispatch, tracker)
    tasks = [
        asyncio.create_task(pipeline.execute_tool_call(_tc("a"), "sess-1", task.id)),
        asyncio.create_task(pipeline.execute_tool_call(_tc("b"), "sess-1", task.id)),
    ]
    await started.wait()
    tracker.cancel(task.id)
    results = await asyncio.gather(*tasks)

    assert all(r.cancelled for r in results)
    assert executed["a"] < 100 and executed["b"] < 100
