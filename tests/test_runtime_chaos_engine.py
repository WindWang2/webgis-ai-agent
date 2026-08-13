"""WP-ENGINE runtime chaos / recovery regression tests.

Covers the Agent Runtime hardening defects:
  F1  clear_session 在 turn 持锁期间丢弃 session 锁/状态（锁逐出 check-then-pop 竞态）
  F3  终态覆盖：complete_task/fail_task 无条件改写 cancelled
  F7  cancel/disconnect 后 step 永远停在 running
  F8  取消与失败不可区分（step/task 两级）
  F9  DB 中遗留孤儿 assistant tool_calls，下一轮 LLM 调用被拒
  F15 _fire_and_forget 任务无跟踪， lifespan 无法 drain
  F17 durable job id 未注册进取消 registry，cancel 级联是空操作
  F18 running 任务被静默逐出（不可见也不可取消）
  F21 clear_session 进程内清理部分失败 → 500 + 跳过其余清理
  F23 DB 加载失败 stub 被永久缓存
  F28 非流式 _chat_locked 无 cancel_watch，取消不能抢占在飞工具

模式遵循 tests/test_chat_engine_tracking.py / tests/test_sse_resume.py /
tests/jobs/test_job_agent_bridge.py：asyncio.Event 屏障 + monkeypatch 引擎
接缝，无 wall-clock 依赖。
"""
import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.chat_engine import ChatEngine
from app.services.chat import execution_engine as ee_mod
from app.services.chat.tool_pipeline import ToolExecutionPipeline
from app.services.jobs.cancellation import registry as cancellation_registry
from app.services.task_tracker import StepStatus, TaskStatus, TaskTracker
from app.services.tool_dispatch_service import ToolDispatchResult
from app.tools.registry import ToolRegistry


# ─── helpers / fixtures ──────────────────────────────────────────────


def _fake_stream(response_msg):
    """Single-round fake _call_llm_stream yielding one 'done' event."""

    async def stream(*args, **kwargs):
        yield ("done", {"message": response_msg})

    return stream


def _ok_dispatch_result(name: str = "tool") -> ToolDispatchResult:
    return ToolDispatchResult(
        status="ok",
        llm_payload=f"{name} ok",
        slim_event={"result": f"{name} ok"},
        geojson_ref=None,
        raw_result={"result": f"{name} ok"},
        error_msg=None,
    )


@pytest.fixture
def engine(monkeypatch):
    """ChatEngine with DB/planner/title side effects stubbed (mirrors the
    fixture pattern in test_chat_engine_tracking.py)."""
    eng = ChatEngine(ToolRegistry())

    async def fake_get_or_create_session(session_id, user_id=None):
        return []

    async def fake_maybe_plan(*a, **kw):
        return None

    monkeypatch.setattr(eng, "_get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(eng, "_maybe_plan", fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", AsyncMock())
    monkeypatch.setattr(eng, "_save_msg_async", AsyncMock())
    return eng


def _mock_history_delete(deleted=True):
    """Patch the DB delete path of engine.clear_session."""
    mock_history = AsyncMock()
    mock_history.delete_session = AsyncMock(return_value=deleted)
    db_ctx = patch("app.services.chat.execution_engine.async_db_session")
    svc = patch(
        "app.services.chat.execution_engine.AsyncHistoryService",
        return_value=mock_history,
    )
    return db_ctx, svc


def _enter_db_ctx(mock_db_ctx):
    mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)


def _stream_task_id(events):
    for e in events:
        if "task_start" in e:
            return json.loads(e.split("data: ")[1])["task_id"]
    return None


# ─── F3: terminal states are immutable (first terminal wins) ─────────


class TestF3TerminalImmutability:
    def test_complete_task_does_not_overwrite_cancelled(self):
        tracker = TaskTracker()
        task = tracker.create("s", "r")
        assert tracker.cancel(task.id) is True
        tracker.complete_task(task.id)
        assert tracker.get(task.id).status == TaskStatus.cancelled, (
            "complete_task overwrote a cancelled task (F3)"
        )
        tracker.fail_task(task.id, "late failure")
        assert tracker.get(task.id).status == TaskStatus.cancelled

    def test_fail_task_does_not_overwrite_completed(self):
        tracker = TaskTracker()
        task = tracker.create("s", "r")
        tracker.complete_task(task.id)
        tracker.fail_task(task.id, "late failure")
        assert tracker.get(task.id).status == TaskStatus.completed

    @pytest.mark.asyncio
    async def test_streaming_cancel_then_llm_complete_stays_cancelled(self, engine, monkeypatch):
        """Drive cancel-vs-complete deterministically: cancel lands while the
        FINAL LLM round is streaming (after the is_cancelled poll), so the
        content branch reaches tracker.complete_task (execution_engine
        :1002). First terminal (cancelled) must win."""
        sid = "sess-f3-race"
        round2_started = asyncio.Event()

        async def fake_dispatch(tc, session_id, executed_tools):
            return _ok_dispatch_result(tc["function"]["name"])

        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)

        msg1 = {
            "content": None,
            "tool_calls": [
                {"id": "call_1", "function": {"name": "geocode", "arguments": "{}"}}
            ],
        }

        async def round2_stream(*args, **kwargs):
            # Cancel AFTER the round-2 is_cancelled check but BEFORE
            # complete_task — the exact F3 race window.
            task = next(t for t in engine.tracker.list_all() if t.session_id == sid)
            engine.tracker.cancel(task.id)
            round2_started.set()
            yield ("done", {"message": {"content": "final answer", "tool_calls": None}})

        with patch.object(
            engine, "_call_llm_stream",
            side_effect=[_fake_stream(msg1)(), round2_stream()],
        ):
            events = []
            async for event in engine.chat_stream("测试", session_id=sid):
                events.append(event)

        assert round2_started.is_set()
        task_id = _stream_task_id(events)
        assert engine.tracker.get(task_id).status == TaskStatus.cancelled, (
            "complete_task at end of stream overwrote the cancelled terminal state (F3)"
        )


# ─── F7: no step stays running on a terminal task ────────────────────


class TestF7StepsTerminalized:
    def test_cancel_terminalizes_running_steps(self):
        tracker = TaskTracker()
        task = tracker.create("s", "r")
        s1 = tracker.start_step(task.id, "tool_a", {})
        s2 = tracker.start_step(task.id, "tool_b", {})
        tracker.complete_step(task.id, s1.id, {"ok": 1})
        tracker.cancel(task.id)

        steps = {s.id: s for s in tracker.get(task.id).steps}
        assert steps[s1.id].status == StepStatus.completed
        assert steps[s2.id].status == StepStatus.cancelled, (
            "cancelled task still has a running step (F7)"
        )

    def test_fail_task_terminalizes_running_steps(self):
        tracker = TaskTracker()
        task = tracker.create("s", "r")
        step = tracker.start_step(task.id, "tool_a", {})
        tracker.fail_task(task.id, "boom")
        assert tracker.get(task.id).steps[0].status != StepStatus.running, (
            "failed task still shows a running step (F7)"
        )
        assert tracker.get(task.id).steps[0].id == step.id

    def test_complete_task_terminalizes_running_steps(self):
        tracker = TaskTracker()
        task = tracker.create("s", "r")
        tracker.start_step(task.id, "tool_a", {})
        tracker.complete_task(task.id)
        assert all(
            s.status != StepStatus.running for s in tracker.get(task.id).steps
        ), "terminal task must not expose running steps via GET /tasks/{id} (F7)"


# ─── F8: cancellation observable as cancellation ─────────────────────


class TestF8CancelNotFailure:
    @pytest.mark.asyncio
    async def test_pipeline_marks_step_cancelled_not_failed(self):
        """Cancelled tool must terminalize its step as cancelled, not failed."""
        from app.services.jobs.cancellation import checkpoint
        from app.services.tool_dispatch_service import ToolDispatchService

        registry = ToolRegistry()

        def cancellable_tool() -> dict:
            checkpoint()
            return {"unreachable": True}

        registry.register(
            name="cancellable_tool",
            description="test tool",
            func=cancellable_tool,
            parameters={"type": "object", "properties": {}},
        )

        tracker = TaskTracker()
        task = tracker.create("sess-1", "req")
        tracker.cancel(task.id)

        pipeline = ToolExecutionPipeline(
            registry=registry,
            tracker=tracker,
            dispatch_service=ToolDispatchService(registry=registry),
        )
        tc = {"id": "c1", "function": {"name": "cancellable_tool", "arguments": "{}"}}
        result = await pipeline.execute_tool_call(tc, "sess-1", task.id)

        assert result.cancelled is True
        step = tracker.get(task.id).steps[0]
        assert step.status == StepStatus.cancelled, (
            f"cancelled tool step recorded as {step.status} — indistinguishable "
            "from a failure (F8)"
        )

    @pytest.mark.asyncio
    async def test_streaming_preemptive_cancel_marks_step_and_task_cancelled(
        self, engine, monkeypatch
    ):
        """Cancel preempts an in-flight tool (cancel_watch): the step must end
        cancelled, the task must end cancelled, and SSE must carry
        step_cancelled + task_cancelled — never step_error/failed."""
        sid = "sess-f8"
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()

        async def fake_dispatch(tc, session_id, executed_tools):
            tool_started.set()
            await release_tool.wait()
            return _ok_dispatch_result(tc["function"]["name"])

        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)

        msg1 = {
            "content": None,
            "tool_calls": [
                {"id": "call_1", "function": {"name": "slow_tool", "arguments": "{}"}}
            ],
        }
        msg2 = {"content": "done", "tool_calls": None}

        with patch.object(
            engine, "_call_llm_stream",
            side_effect=[_fake_stream(msg1)(), _fake_stream(msg2)()],
        ):
            events = []

            async def _consume():
                async for event in engine.chat_stream("测试", session_id=sid):
                    events.append(event)

            consumer = asyncio.create_task(_consume())
            await asyncio.wait_for(tool_started.wait(), timeout=5)
            task = next(t for t in engine.tracker.list_all() if t.session_id == sid)
            engine.tracker.cancel(task.id)
            await asyncio.wait_for(consumer, timeout=5)

        assert any("task_cancelled" in e for e in events)
        assert any("step_cancelled" in e for e in events), (
            "preempted tool step did not surface as cancelled in SSE (F8)"
        )
        task = engine.tracker.get(task.id)
        assert task.status == TaskStatus.cancelled
        assert all(s.status != StepStatus.running for s in task.steps), (
            "cancelled task still has running steps (F7)"
        )
        assert task.steps[0].status == StepStatus.cancelled, (
            f"preempted step recorded as {task.steps[0].status} (F8)"
        )

    @pytest.mark.asyncio
    async def test_disconnect_marks_task_cancelled_not_failed(self, engine, monkeypatch):
        """Client disconnect mid-tool: task must terminalize as cancelled
        (today fail_task(..., 'cancelled') records status=failed)."""
        sid = "sess-f8-disconnect"
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()

        async def fake_dispatch(tc, session_id, executed_tools):
            tool_started.set()
            await release_tool.wait()
            return _ok_dispatch_result(tc["function"]["name"])

        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)

        msg1 = {
            "content": None,
            "tool_calls": [
                {"id": "call_1", "function": {"name": "slow_tool", "arguments": "{}"}}
            ],
        }

        with patch.object(
            engine, "_call_llm_stream", return_value=_fake_stream(msg1)()
        ):
            events = []

            async def _consume():
                async for event in engine.chat_stream("测试", session_id=sid):
                    events.append(event)

            consumer = asyncio.create_task(_consume())
            await asyncio.wait_for(tool_started.wait(), timeout=5)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer

        task = next(t for t in engine.tracker.list_all() if t.session_id == sid)
        assert task.status == TaskStatus.cancelled, (
            f"disconnect recorded task as {task.status} — cancel must be "
            "distinguishable from failure (F8)"
        )
        assert all(s.status != StepStatus.running for s in task.steps), (
            "disconnect left a step running (F7)"
        )


# ─── F9: orphaned assistant tool_calls repaired on abort ─────────────


class TestF9OrphanedToolCalls:
    @pytest.mark.asyncio
    async def test_cancel_repairs_orphaned_tool_calls(self, engine, monkeypatch):
        """After a cancel mid-tool, the in-memory history AND the persisted
        history must contain a tool message for every assistant tool_call —
        otherwise the next turn's LLM call rejects the orphaned tool_calls."""
        sid = "sess-f9"
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()
        captured_messages: list[dict] = []

        async def fake_get_or_create_session(session_id, user_id=None):
            return captured_messages

        async def fake_dispatch(tc, session_id, executed_tools):
            tool_started.set()
            await release_tool.wait()
            return _ok_dispatch_result(tc["function"]["name"])

        monkeypatch.setattr(engine, "_get_or_create_session", fake_get_or_create_session)
        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)

        msg1 = {
            "content": None,
            "tool_calls": [
                {"id": "call_1", "function": {"name": "slow_tool", "arguments": "{}"}}
            ],
        }

        with patch.object(
            engine, "_call_llm_stream", return_value=_fake_stream(msg1)()
        ):
            events = []

            async def _consume():
                async for event in engine.chat_stream("测试", session_id=sid):
                    events.append(event)

            consumer = asyncio.create_task(_consume())
            await asyncio.wait_for(tool_started.wait(), timeout=5)
            task = next(t for t in engine.tracker.list_all() if t.session_id == sid)
            engine.tracker.cancel(task.id)
            await asyncio.wait_for(consumer, timeout=5)

        # in-memory repair
        answered = {
            m.get("tool_call_id") for m in captured_messages if m.get("role") == "tool"
        }
        orphans = [
            tc["id"]
            for m in captured_messages
            if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
            if tc.get("id") and tc["id"] not in answered
        ]
        assert not orphans, (
            f"assistant tool_calls left orphaned in history after cancel: {orphans} (F9)"
        )

        # persisted repair: a tool message must have been saved for call_1
        tool_saves = [
            c for c in engine._save_msg_async.call_args_list
            if len(c.args) >= 6 and c.args[1] == "tool" and c.args[5] == "call_1"
        ]
        assert tool_saves, (
            "no tool response message persisted for the orphaned tool_call — "
            "next turn's LLM call would be rejected (F9)"
        )

    @pytest.mark.asyncio
    async def test_disconnect_repairs_orphaned_tool_calls(self, engine, monkeypatch):
        sid = "sess-f9b"
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()
        captured_messages: list[dict] = []

        async def fake_get_or_create_session(session_id, user_id=None):
            return captured_messages

        async def fake_dispatch(tc, session_id, executed_tools):
            tool_started.set()
            await release_tool.wait()
            return _ok_dispatch_result(tc["function"]["name"])

        monkeypatch.setattr(engine, "_get_or_create_session", fake_get_or_create_session)
        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)

        msg1 = {
            "content": None,
            "tool_calls": [
                {"id": "call_9", "function": {"name": "slow_tool", "arguments": "{}"}}
            ],
        }

        with patch.object(
            engine, "_call_llm_stream", return_value=_fake_stream(msg1)()
        ):
            async def _consume():
                async for _ in engine.chat_stream("测试", session_id=sid):
                    pass

            consumer = asyncio.create_task(_consume())
            await asyncio.wait_for(tool_started.wait(), timeout=5)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer

        answered = {
            m.get("tool_call_id") for m in captured_messages if m.get("role") == "tool"
        }
        assert "call_9" in answered, (
            "disconnect left an orphaned assistant tool_call in history (F9)"
        )

    @pytest.mark.asyncio
    async def test_cancel_same_batch_keeps_completed_tool_success(self, engine, monkeypatch):
        """F9 (cancel-vs-success batch): cancel and the fast tool's completion
        land in the SAME ``asyncio.wait`` done set. The completed tool must be
        harvested as a success — step completed, real tool message persisted —
        NOT marked cancelled / given the '工具执行已被用户取消' placeholder;
        the genuinely in-flight tool is preempted and its orphaned tool_call
        is still repaired."""
        sid = "sess-f9-batch"
        fast_gate = asyncio.Event()
        slow_started = asyncio.Event()
        slow_release = asyncio.Event()
        slow_cancelled = {"ok": False}

        async def fake_dispatch(tc, session_id, executed_tools):
            name = tc["function"]["name"]
            if name == "fast_tool":
                await fast_gate.wait()
                return _ok_dispatch_result("fast")
            slow_started.set()
            try:
                await slow_release.wait()
            except asyncio.CancelledError:
                slow_cancelled["ok"] = True
                raise
            return _ok_dispatch_result("slow")

        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)

        msg1 = {
            "content": None,
            "tool_calls": [
                {"id": "call_fast", "function": {"name": "fast_tool", "arguments": "{}"}},
                {"id": "call_slow", "function": {"name": "slow_tool", "arguments": "{}"}},
            ],
        }

        with patch.object(
            engine, "_call_llm_stream", return_value=_fake_stream(msg1)()
        ):
            events = []

            async def _consume():
                async for e in engine.chat_stream("测试", session_id=sid):
                    events.append(e)

            consumer = asyncio.create_task(_consume())
            await asyncio.wait_for(slow_started.wait(), timeout=5)
            task = next(t for t in engine.tracker.list_all() if t.session_id == sid)
            # 取消与 fast 完成落在同一个事件循环调度批 → 二者同批进入 wave 的
            # asyncio.wait done 集合。经 token 直接点燃（不经 tracker.cancel 的
            # terminalize_running_steps），隔离验证 wave 自身的「已完成 ≠ 已取消」
            # 收割逻辑。
            task.cancel_token.cancel("test")
            fast_gate.set()
            try:
                await asyncio.wait_for(consumer, timeout=5)
            finally:
                slow_release.set()  # 任何路径下都不让 gated 工具泄漏

        task = engine.tracker.get(task.id)
        fast_step = next(s for s in task.steps if s.tool == "fast_tool")
        slow_step = next(s for s in task.steps if s.tool == "slow_tool")
        assert fast_step.status == StepStatus.completed, (
            f"completed tool recorded as {fast_step.status} — success lost to cancel (F9)"
        )
        assert slow_step.status == StepStatus.cancelled, (
            f"in-flight tool recorded as {slow_step.status} — must be cancelled (F7/F9)"
        )
        assert slow_cancelled["ok"] is True, "in-flight tool was not preempted"
        assert any("task_cancelled" in e for e in events)

        # 已完成工具的 tool 消息以真实结果落库，而不是「已取消」占位
        fast_saves = [
            c for c in engine._save_msg_async.call_args_list
            if len(c.args) >= 6 and c.args[1] == "tool" and c.args[5] == "call_fast"
        ]
        assert fast_saves, "completed tool's tool message was not persisted (F9)"
        assert fast_saves[-1].args[4] == "fast ok", (
            f"completed tool's message is {fast_saves[-1].args[4]!r} — placeholder for a success (F9)"
        )
        # 真正在飞的 slow 的孤儿 tool_call 仍被补齐（幂等 repair 只补孤儿）
        assert any(
            len(c.args) >= 6 and c.args[1] == "tool" and c.args[5] == "call_slow"
            and c.args[4] == "工具执行已被用户取消"
            for c in engine._save_msg_async.call_args_list
        ), "orphaned in-flight tool_call was not repaired (F9)"


# ─── F1: clear_session vs in-flight turn + lock eviction race ────────


class TestF1ClearSessionLockSafety:
    @pytest.mark.asyncio
    async def test_clear_session_keeps_in_use_lock(self):
        """A lock held by an in-flight turn (or with waiters) must NOT be
        dropped — a new lock would re-enable concurrent turns on the same
        session while the old turn still runs."""
        eng = ChatEngine(ToolRegistry())
        sid = "sess-busy"
        lock = eng._get_session_lock(sid)
        await lock.acquire()
        eng._sessions[sid] = [{"role": "system", "content": "x"}]

        db_ctx, svc = _mock_history_delete(deleted=True)
        try:
            with db_ctx as mock_db_ctx, svc, patch(
                "app.services.chat.execution_engine.session_data_manager"
            ) as mock_sdm:
                _enter_db_ctx(mock_db_ctx)
                mock_sdm.clear_session = AsyncMock()
                result = await eng.clear_session(sid)
        finally:
            lock.release()

        assert result is True
        assert sid not in eng._sessions
        assert eng._session_locks.get(sid) is lock, (
            "clear_session dropped a session lock that an in-flight turn holds (F1)"
        )

    @pytest.mark.asyncio
    async def test_clear_session_cancels_inflight_tasks(self):
        """clear_session must cancel the session's in-flight turns so they
        terminalize instead of resurrecting messages into a deleted session."""
        eng = ChatEngine(ToolRegistry())
        sid = "sess-running"
        task = eng.tracker.create(sid, "req")

        db_ctx, svc = _mock_history_delete(deleted=True)
        with db_ctx as mock_db_ctx, svc, patch(
            "app.services.chat.execution_engine.session_data_manager"
        ) as mock_sdm:
            _enter_db_ctx(mock_db_ctx)
            mock_sdm.clear_session = AsyncMock()
            result = await eng.clear_session(sid)

        assert result is True
        assert eng.tracker.get(task.id).status == TaskStatus.cancelled, (
            "clear_session left an in-flight task running — the turn keeps "
            "appending into a deleted session (F1)"
        )

    @pytest.mark.asyncio
    async def test_lock_eviction_keeps_lock_with_waiters(self):
        """`if not lock.locked(): pop` evicts a lock whose waiters have been
        woken but not yet re-acquired — a check-then-pop race (F1)."""
        eng = ChatEngine(ToolRegistry())
        eng._MAX_LOCKS = 4  # evict_count = _MAX_LOCKS // 4 = 1 (oldest entry)

        contested = asyncio.Lock()
        await contested.acquire()
        waiter = asyncio.create_task(contested.acquire())
        await asyncio.sleep(0)  # waiter is queued
        contested.release()
        # No yield here: the waiter is woken (call_soon) but has NOT
        # re-acquired yet — locked() is False while a waiter exists.

        eng._session_locks["old-contended"] = contested
        for i in range(4):
            eng._session_locks[f"old-idle-{i}"] = asyncio.Lock()
        try:
            eng._get_session_lock("new-session")  # triggers eviction pass
            assert eng._session_locks.get("old-contended") is contested, (
                "eviction dropped a lock with a pending waiter (F1)"
            )
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            if contested.locked():
                contested.release()


# ─── F15: background task tracking + drain ───────────────────────────


class TestF15BackgroundTaskDrain:
    @pytest.mark.asyncio
    async def test_fire_and_forget_is_tracked_and_drained(self):
        eng = ChatEngine(ToolRegistry())
        done = asyncio.Event()

        async def bg():
            done.set()

        eng._fire_and_forget(bg)
        tracked = [t for t in ee_mod._background_tasks if not t.done()]
        assert tracked, "fire-and-forget task is untracked (F15)"

        await ee_mod.drain_background_tasks(timeout=5.0)
        assert done.is_set()
        assert not [t for t in ee_mod._background_tasks if not t.done()]

    @pytest.mark.asyncio
    async def test_drain_background_tasks_cancels_pending_on_timeout(self, caplog):
        """P1 (drain): a background task still pending at the drain deadline
        must be CANCELLED (bounded best-effort gather), not left to outlive
        lifespan shutdown and write into closed resources — the old contract
        (log + return, task left pending) let a slow _generate_title keep
        writing to dead shared clients."""
        async def never():
            await asyncio.Event().wait()

        task = asyncio.create_task(never())
        ee_mod._background_tasks.add(task)
        task.add_done_callback(ee_mod._background_tasks.discard)
        try:
            with caplog.at_level(logging.WARNING):
                await ee_mod.drain_background_tasks(timeout=0.05)  # must not raise
            assert task.done(), "drain returned while the task was still pending"
            assert task.cancelled(), "drain must cancel pending tasks on timeout (P1)"
            assert any(
                "cancelling" in r.message for r in caplog.records
            ), "drain did not log the cancel warning"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_drain_drops_stale_tasks_bound_to_other_loop(self, caplog):
        """P1 (drain): the module-global registry is cross-loop sticky — a
        task bound to a closed/other event loop never completes, so every
        later drain includes it and eats the full timeout. drain must filter
        to tasks of the CURRENT running loop and drop stale ones (with a
        log) instead of waiting them out."""
        other_loop = asyncio.new_event_loop()

        class _StaleTask:
            """Minimal stand-in for a task bound to a closed/different loop:
            never done, get_loop() != the running loop (its loop can't be run
            from this thread while the pytest loop is running)."""

            def done(self):
                return False

            def get_loop(self):
                return other_loop

        stale = _StaleTask()
        ee_mod._background_tasks.add(stale)
        try:
            with caplog.at_level(logging.WARNING):
                # Must return promptly — the stale task must not consume the
                # whole drain budget (30s here would hang the old code).
                await asyncio.wait_for(
                    ee_mod.drain_background_tasks(timeout=30.0), timeout=1.0
                )
            assert stale not in ee_mod._background_tasks, (
                "stale cross-loop task left in the registry (P1)"
            )
            assert any(
                "stale" in r.message for r in caplog.records
            ), "stale drop was not logged"
        finally:
            other_loop.close()

    @pytest.mark.asyncio
    async def test_drain_signature(self):
        import inspect

        sig = inspect.signature(ee_mod.drain_background_tasks)
        assert list(sig.parameters) == ["timeout"]
        assert sig.parameters["timeout"].default == 5.0


# ─── F17: durable job ids registered in the cancellation registry ────


class TestF17CancelCascadeRegistration:
    def test_submit_registers_job_cancellation_token(self, tmp_path, monkeypatch):
        """submit_durable_job must register the job id in the in-process
        cancellation registry — otherwise TaskTracker.cancel()'s cascade
        (cancellation_registry.cancel(job_id)) is a no-op."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.models.db_model import Base
        from app.services.jobs.submit import submit_durable_job

        engine = create_engine(f"sqlite:///{tmp_path / 'f17.db'}")
        Base.metadata.create_all(engine)
        Sess = sessionmaker(bind=engine)

        import contextlib

        @contextlib.contextmanager
        def fake_db_session():
            db = Sess()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        monkeypatch.setattr("app.services.jobs.submit.db_session", fake_db_session)

        class _FakeTask:
            name = "tests.fake_task"

            def apply_async(self, args=None, kwargs=None, **_):
                class _R:
                    id = "celery-f17"

                return _R()

        try:
            result = submit_durable_job(
                celery_task=_FakeTask(),
                task_type="ndvi",
                display_name="NDVI 分析",
                params={"raster_path": "/data/a.tif"},
                task_args=("/data/a.tif",),
                session_id="sess-f17",
            )
            job_id = result["job_id"]

            token = cancellation_registry.get(job_id)
            assert token is not None, (
                "durable job id never registered — the agent-task cancel "
                "cascade cannot reach it (F17)"
            )

            # The full cascade: agent task cancel must light the job's token
            # WITHOUT the hand-registration done in test_job_agent_bridge.py:396.
            tracker = TaskTracker()
            task = tracker.create("sess-f17", "req")
            step = tracker.start_step(task.id, "analyze", {})
            step.background_job_ids = [job_id]
            tracker.cancel(task.id)
            assert token.cancelled is True, "cancel cascade did not fire (F17)"
        finally:
            cancellation_registry.discard(str(result["job_id"]) if "result" in dir() else "")
            engine.dispose()


# ─── F18: running-task eviction ──────────────────────────────────────


class TestF18Eviction:
    def test_evicted_running_task_is_cancelled_not_orphaned(self):
        """Evicting a RUNNING task without lighting its cancel token leaves an
        invisible, uncancellable execution running in the background."""
        tracker = TaskTracker()
        victim = tracker.create("s-victim", "r")
        for i in range(TaskTracker.MAX_TOTAL_TASKS + 60):
            tracker.create(f"s-{i}", f"r-{i}")

        assert victim.id not in tracker._tasks, "precondition: victim was evicted"
        assert victim.cancel_token is not None and victim.cancel_token.cancelled, (
            "running task evicted while still executing — its in-flight tools "
            "never observe cancellation (F18)"
        )

    def test_per_session_eviction_cancels_running_task(self):
        tracker = TaskTracker()
        first = tracker.create("s", "r0")
        for i in range(TaskTracker.MAX_TASKS_PER_SESSION):
            tracker.create("s", f"r{i + 1}")

        assert first.id not in tracker._tasks
        assert first.cancel_token is not None and first.cancel_token.cancelled, (
            "per-session eviction orphaned a running task (F18)"
        )


# ─── F21: clear_session partial failure isolation ────────────────────


class TestF21ClearSessionIsolation:
    @pytest.mark.asyncio
    async def test_inprocess_cleanup_failure_is_isolated(self):
        """session_data_manager.clear_session raising must not 500 the route
        nor skip the remaining in-process cleanup."""
        eng = ChatEngine(ToolRegistry())
        sid = "sess-f21"
        eng._sessions[sid] = [{"role": "system", "content": "x"}]
        eng._session_locks[sid] = asyncio.Lock()

        from app.services.chat import planner

        clear_plan = MagicMock()
        db_ctx, svc = _mock_history_delete(deleted=True)
        with db_ctx as mock_db_ctx, svc, patch(
            "app.services.chat.execution_engine.session_data_manager"
        ) as mock_sdm, patch.object(planner, "clear_plan", clear_plan):
            _enter_db_ctx(mock_db_ctx)
            mock_sdm.clear_session = AsyncMock(side_effect=RuntimeError("redis down"))
            result = await eng.clear_session(sid)  # currently raises → 500

        assert result is True, (
            "in-process cleanup failure must not change route semantics (F21)"
        )
        assert sid not in eng._sessions
        assert sid not in eng._session_locks
        clear_plan.assert_called_once_with(sid)


# ─── F23: failed DB load must not be cached ──────────────────────────


class TestF23LoadFailureNotCached:
    @pytest.mark.asyncio
    async def test_failed_load_not_cached(self):
        """A post-DB-outage stub must not poison the session cache — the next
        turn must retry the DB load."""
        eng = ChatEngine(ToolRegistry())
        sid = "sess-f23"

        mock_svc = AsyncMock()
        mock_svc.load_context = AsyncMock(side_effect=RuntimeError("db down"))
        with patch(
            "app.services.chat.execution_engine.AsyncHistoryService",
            return_value=mock_svc,
        ):
            msgs = await eng._get_or_create_session(sid)

        # Current turn degrades to system-prompt-only (kept behavior) ...
        assert msgs[0]["role"] == "system"
        # ... but the failure stub must NOT be cached.
        assert sid not in eng._sessions, (
            "failed DB load cached an empty stub — every later turn runs with "
            "permanently empty history (F23)"
        )

        # DB "recovers": the next turn loads the real history.
        mock_svc.load_context = AsyncMock(return_value=SimpleNamespace(
            owner_token=None,
            llm_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "real history"},
            ],
        ))
        with patch(
            "app.services.chat.execution_engine.AsyncHistoryService",
            return_value=mock_svc,
        ):
            msgs2 = await eng._get_or_create_session(sid)
        assert msgs2[-1]["content"] == "real history"
        assert eng._sessions[sid] is msgs2


# ─── F28: non-streaming chat() preemptive cancel ─────────────────────


class TestF28NonStreamingCancelWatch:
    @pytest.mark.asyncio
    async def test_cancel_preempts_inflight_tool(self, engine, monkeypatch):
        """User cancel must preempt an in-flight tool in the non-streaming
        path (today chat() waits for the tool to finish naturally)."""
        sid = "sess-f28"
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()

        async def fake_dispatch(tc, session_id, executed_tools):
            tool_started.set()
            await release_tool.wait()
            return _ok_dispatch_result(tc["function"]["name"])

        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)

        async def fake_call_llm(*a, **k):
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "call_1", "function": {"name": "slow_tool", "arguments": "{}"}}
                        ],
                    },
                }]
            }

        monkeypatch.setattr(engine, "_call_llm", fake_call_llm)

        chat_task = asyncio.create_task(engine.chat("hi", session_id=sid))
        await asyncio.wait_for(tool_started.wait(), timeout=5)
        task = next(t for t in engine.tracker.list_all() if t.session_id == sid)
        engine.tracker.cancel(task.id)

        result = await asyncio.wait_for(chat_task, timeout=5)
        assert result["content"] == "任务已取消", (
            f"non-streaming cancel did not preempt the in-flight tool (F28): {result}"
        )
        assert not release_tool.is_set(), "tool ran to completion despite cancel"
        assert engine.tracker.get(task.id).status == TaskStatus.cancelled

    @pytest.mark.asyncio
    async def test_cancel_preempts_slow_tool_and_keeps_fast_result(self, engine, monkeypatch):
        """F28 hole (multi-tool wave): with TWO tools of unequal duration and
        cancel firing AFTER the fast tool completes, the slow tool must be
        preempted (its task cancelled) while the fast tool's result is still
        recorded. The single-shot cancel_watch wait (old code) fell into
        ``await asyncio.gather(*tool_tasks)`` after the fast tool completed and
        never re-checked the token until the WHOLE wave finished naturally."""
        sid = "sess-f28-multi"
        fast_gate = asyncio.Event()
        slow_started = asyncio.Event()
        slow_release = asyncio.Event()
        slow_cancelled = {"ok": False}

        async def fake_dispatch(tc, session_id, executed_tools):
            name = tc["function"]["name"]
            if name == "fast_tool":
                await fast_gate.wait()
                return _ok_dispatch_result("fast")
            slow_started.set()
            try:
                await slow_release.wait()
            except asyncio.CancelledError:
                slow_cancelled["ok"] = True
                raise
            return _ok_dispatch_result("slow")

        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)

        async def fake_call_llm(*a, **k):
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "call_fast", "function": {"name": "fast_tool", "arguments": "{}"}},
                            {"id": "call_slow", "function": {"name": "slow_tool", "arguments": "{}"}},
                        ],
                    },
                }]
            }

        monkeypatch.setattr(engine, "_call_llm", fake_call_llm)

        chat_task = asyncio.create_task(engine.chat("hi", session_id=sid))
        await asyncio.wait_for(slow_started.wait(), timeout=5)
        task = next(t for t in engine.tracker.list_all() if t.session_id == sid)

        # 先让 fast 完成并被 wave 处理，再点燃取消 —— 复现 F28 hole：旧实现
        # 第一次 wait 返回 fast 后直接 gather 等整波自然结束，cancel 永不重查，
        # slow 会一直跑（此处 slow_release 永不 set → chat 挂死）。
        fast_gate.set()
        await asyncio.sleep(0)  # 纯调度 yield（非 wall-clock）：让 wave 处理 fast
        await asyncio.sleep(0)
        engine.tracker.cancel(task.id)
        try:
            result = await asyncio.wait_for(chat_task, timeout=5)
        finally:
            slow_release.set()  # 任何路径下都释放 gated 工具，避免测试泄漏

        assert result["content"] == "任务已取消", (
            f"cancel did not preempt the slow tool after fast completed (F28): {result}"
        )
        assert slow_cancelled["ok"] is True, "slow tool was not preempted (F28)"
        assert engine.tracker.get(task.id).status == TaskStatus.cancelled

        # fast 的结果必须真实落库，而不是「已取消」占位
        fast_saves = [
            c for c in engine._save_msg_async.call_args_list
            if len(c.args) >= 6 and c.args[1] == "tool" and c.args[5] == "call_fast"
        ]
        assert fast_saves, "fast tool's result was not recorded after cancel (F9)"
        assert fast_saves[-1].args[4] == "fast ok", (
            f"fast tool's recorded message is {fast_saves[-1].args[4]!r} — success lost (F9)"
        )
        assert not any(c.args[4] == "工具执行已被用户取消" for c in fast_saves), (
            "a completed tool must not be marked cancelled (F9)"
        )


# ─── P1 round-2: clear_session must not resurrect deleted history ─────


class TestC1ClearSessionNoResurrect:
    @pytest.mark.asyncio
    async def test_clear_session_suppresses_history_persistence_during_turn(
        self, engine, monkeypatch
    ):
        """P1: clear_session during a streaming turn must not let the
        cancelled turn's cleanup resurrect the deleted conversation — the DB
        write is suppressed (real _save_msg_async funnels into a recorded
        save_message) while the in-memory orphan repair still runs."""
        import types as _types
        sid = "sess-p1-resurrect"
        captured_messages: list[dict] = []
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()
        save_calls: list[tuple] = []

        async def fake_get_or_create_session(session_id, user_id=None):
            return captured_messages

        async def fake_dispatch(tc, session_id, executed_tools):
            tool_started.set()
            await release_tool.wait()
            return _ok_dispatch_result(tc["function"]["name"])

        monkeypatch.setattr(engine, "_get_or_create_session", fake_get_or_create_session)
        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)
        # Re-bind the REAL _save_msg_async (the fixture stubs it) so the
        # clearing-suppression is exercised; the DB write lands in the mock.
        engine._save_msg_async = _types.MethodType(
            ee_mod.ChatExecutionEngine._save_msg_async, engine
        )

        async def _recording_save(*args, **kwargs):
            save_calls.append(args)

        mock_history = AsyncMock()
        mock_history.delete_session = AsyncMock(return_value=True)
        mock_history.save_message = AsyncMock(side_effect=_recording_save)
        db_ctx = patch("app.services.chat.execution_engine.async_db_session")
        svc = patch(
            "app.services.chat.execution_engine.AsyncHistoryService",
            return_value=mock_history,
        )
        msg1 = {
            "content": None,
            "tool_calls": [
                {"id": "call_p1", "function": {"name": "slow_tool", "arguments": "{}"}}
            ],
        }
        with db_ctx as mock_db_ctx, svc, patch(
            "app.services.chat.execution_engine.session_data_manager"
        ) as mock_sdm, patch.object(
            engine, "_call_llm_stream", return_value=_fake_stream(msg1)()
        ):
            _enter_db_ctx(mock_db_ctx)
            mock_sdm.clear_session = AsyncMock()

            async def _consume():
                async for _ in engine.chat_stream("hi", session_id=sid):
                    pass

            consumer = asyncio.create_task(_consume())
            await asyncio.wait_for(tool_started.wait(), timeout=5)
            before = len(save_calls)
            try:
                ok = await engine.clear_session(sid)
            finally:
                release_tool.set()  # 任何路径下都不让 gated 工具泄漏
            assert ok is True
            await asyncio.wait_for(consumer, timeout=5)

        # No history write for the deleted conversation after the clear began
        new_calls = save_calls[before:]
        assert not any(call[0] == sid for call in new_calls), (
            f"cancelled turn's cleanup persisted into the deleted conversation: "
            f"{[c[:2] for c in new_calls]} (P1)"
        )
        # ...while the in-memory orphan repair still ran
        assert any(
            m.get("role") == "tool" and m.get("tool_call_id") == "call_p1"
            for m in captured_messages
        ), "in-memory orphan repair did not run during clear (P1)"

    @pytest.mark.asyncio
    async def test_new_turn_refused_while_session_clearing(self, engine, monkeypatch):
        """P1: a NEW turn POSTed while clear_session is mid-flight must be
        refused cleanly (SessionClearingError), not raced into the delete."""
        sid = "sess-p1-refuse"
        clear_entered = asyncio.Event()
        release_clear = asyncio.Event()

        async def blocking_sdm_clear(_sid):
            clear_entered.set()
            await release_clear.wait()

        async def should_not_run(*a, **k):
            raise AssertionError("a new turn started during clear_session (P1)")

        monkeypatch.setattr(engine, "_call_llm", should_not_run)
        monkeypatch.setattr(engine, "_call_llm_stream", should_not_run)

        db_ctx, svc = _mock_history_delete(deleted=True)
        with db_ctx as mock_db_ctx, svc, patch(
            "app.services.chat.execution_engine.session_data_manager"
        ) as mock_sdm:
            _enter_db_ctx(mock_db_ctx)
            mock_sdm.clear_session = AsyncMock(side_effect=blocking_sdm_clear)

            clear_task = asyncio.create_task(engine.clear_session(sid))
            await asyncio.wait_for(clear_entered.wait(), timeout=5)

            with pytest.raises(ee_mod.SessionClearingError):
                await engine.chat("new turn", session_id=sid)
            with pytest.raises(ee_mod.SessionClearingError):
                async for _ in engine.chat_stream("new turn", session_id=sid):
                    pass

            release_clear.set()
            await asyncio.wait_for(clear_task, timeout=5)
        assert sid not in engine._clearing_sessions


# ─── P1 round-2: bounded cleanup on cancel (no unbounded gather) ──────


class TestC1BoundedCancelCleanup:
    # NOTE: the straggler tool below deliberately IGNORES cancellation (the
    # while-loop re-waits on CancelledError). That models a tool whose worker
    # genuinely cannot be interrupted — a thread parked in a long GIS compute
    # on runtimes where cancelling an asyncio.to_thread task does not retire
    # the task (older/newer CPython differ). The unbounded
    # `await asyncio.gather(...)` then blocks the cancel/finally path for the
    # worker's full duration; the bounded wait must return promptly and the
    # session lock must be released regardless.

    @pytest.mark.asyncio
    async def test_cancel_returns_promptly_with_stubborn_straggler_tool(
        self, engine, monkeypatch
    ):
        """P1: cancel mid-tool where the in-flight tool task cannot be
        interrupted must return promptly (bounded cancel wait) and release
        the session lock — the unbounded gather held the lock until the
        straggler finished."""
        sid = "sess-p1-cancel-stubborn"
        gate = asyncio.Event()      # never set -> the tool parks forever
        die = asyncio.Event()       # test teardown: let the straggler exit
        tool_started = asyncio.Event()

        async def fake_dispatch(tc, session_id, executed_tools):
            tool_started.set()
            while not die.is_set():
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    continue  # a straggler that can't be cancelled
            return _ok_dispatch_result(tc["function"]["name"])

        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)
        engine._cancel_wait_timeout = 0.05

        async def fake_call_llm(*a, **k):
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "call_t", "function": {"name": "slow_tool", "arguments": "{}"}}
                        ],
                    },
                }]
            }

        monkeypatch.setattr(engine, "_call_llm", fake_call_llm)

        chat_task = asyncio.create_task(engine.chat("hi", session_id=sid))
        await asyncio.wait_for(tool_started.wait(), timeout=5)
        task = next(t for t in engine.tracker.list_all() if t.session_id == sid)
        try:
            engine.tracker.cancel(task.id)
            result = await asyncio.wait_for(chat_task, timeout=2.0)
        finally:
            die.set()
            gate.set()  # 让 straggler 任务退出，避免测试泄漏
        assert result["content"] == "任务已取消", (
            f"cancel did not return promptly with a stubborn straggler (P1): {result}"
        )
        # session lock is released even though the straggler tool is still alive
        lock = engine._get_session_lock(sid)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.5)
        finally:
            if lock.locked():
                lock.release()

    @pytest.mark.asyncio
    async def test_disconnect_returns_promptly_with_stubborn_straggler_tool(
        self, engine, monkeypatch
    ):
        """P1: client disconnect mid-tool (streaming finally path) with an
        in-flight tool task that cannot be interrupted must return promptly
        and release the session lock."""
        sid = "sess-p1-disc-stubborn"
        gate = asyncio.Event()      # never set -> the tool parks forever
        die = asyncio.Event()       # test teardown: let the straggler exit
        tool_started = asyncio.Event()

        async def fake_dispatch(tc, session_id, executed_tools):
            tool_started.set()
            while not die.is_set():
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    continue  # a straggler that can't be cancelled
            return _ok_dispatch_result(tc["function"]["name"])

        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)
        engine._cancel_wait_timeout = 0.05

        msg1 = {
            "content": None,
            "tool_calls": [
                {"id": "call_t", "function": {"name": "slow_tool", "arguments": "{}"}}
            ],
        }
        with patch.object(engine, "_call_llm_stream", return_value=_fake_stream(msg1)()):
            async def _consume():
                async for _ in engine.chat_stream("hi", session_id=sid):
                    pass

            consumer = asyncio.create_task(_consume())
            await asyncio.wait_for(tool_started.wait(), timeout=5)
            try:
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(consumer, timeout=2.0)
            finally:
                die.set()
                gate.set()  # 让 straggler 任务退出，避免测试泄漏

        lock = engine._get_session_lock(sid)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.5)
        finally:
            if lock.locked():
                lock.release()


# ─── P1 round-2: second cancel must not truncate the orphan repair ────


class TestC1ShieldedRepair:
    @pytest.mark.asyncio
    async def test_second_cancel_does_not_truncate_orphan_repair(
        self, engine, monkeypatch
    ):
        """P1: a second cancellation arriving while the
        (CancelledError/GeneratorExit) handler runs _repair_orphaned_tool_calls
        must not truncate the repair mid-write — the shielded repair's DB
        write still completes."""
        sid = "sess-p1-repair"
        captured_messages: list[dict] = []
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()
        repair_started = asyncio.Event()
        release_repair = asyncio.Event()

        async def fake_get_or_create_session(session_id, user_id=None):
            return captured_messages

        async def fake_dispatch(tc, session_id, executed_tools):
            tool_started.set()
            await release_tool.wait()
            return _ok_dispatch_result(tc["function"]["name"])

        monkeypatch.setattr(engine, "_get_or_create_session", fake_get_or_create_session)
        monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch)

        orig_repair = engine._repair_orphaned_tool_calls

        async def gated_repair(session_id, messages):
            repair_started.set()
            await release_repair.wait()
            await orig_repair(session_id, messages)

        monkeypatch.setattr(engine, "_repair_orphaned_tool_calls", gated_repair)

        msg1 = {
            "content": None,
            "tool_calls": [
                {"id": "call_rep", "function": {"name": "slow_tool", "arguments": "{}"}}
            ],
        }
        with patch.object(engine, "_call_llm_stream", return_value=_fake_stream(msg1)()):
            async def _consume():
                async for _ in engine.chat_stream("hi", session_id=sid):
                    pass

            consumer = asyncio.create_task(_consume())
            await asyncio.wait_for(tool_started.wait(), timeout=5)
            consumer.cancel()  # first cancel -> except handler -> gated repair
            await asyncio.wait_for(repair_started.wait(), timeout=5)

            consumer.cancel()  # second cancel while the repair is mid-flight
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, timeout=5)

            release_repair.set()
            for _ in range(10):  # 纯调度 yield —— 让 shield 的 repair 跑完
                await asyncio.sleep(0)
            release_tool.set()

        assert any(
            len(c.args) >= 6
            and c.args[0] == sid
            and c.args[1] == "tool"
            and c.args[5] == "call_rep"
            for c in engine._save_msg_async.call_args_list
        ), "second cancellation truncated the orphan repair (P1)"
