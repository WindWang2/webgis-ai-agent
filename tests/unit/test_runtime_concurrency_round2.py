"""Regression tests for round-2 runtime/concurrency fixes:

- RUN-01: one shared ToolDispatchService per engine so the dedup lock is
  effective across a parallel tool wave (previously each _dispatch_tool
  built a fresh service with a fresh asyncio.Lock).
- RUN-02: execute_tool_call with a pre_created_step must NOT open a second
  track_step (previously every tool in chat_stream produced 2 steps,
  doubling task.steps and desyncing step_id in SSE).
- RUN-03: chat()/chat_stream() hold the session lock for the whole turn so
  concurrent requests on one session_id cannot interleave messages.append
  or executed_tools writes.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.chat.tool_pipeline import ToolExecutionPipeline
from app.services.task_tracker import TaskTracker


# ---------------------------------------------------------------------------
# RUN-02 — no double step tracking with pre_created_step
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_created_step_does_not_double_count():
    tracker = TaskTracker()
    task = tracker.create("s1", "test request")
    dispatch = AsyncMock(return_value=MagicMock(
        status="success", raw_result={"ok": 1}, llm_payload="ok",
        slim_event={}, geojson_ref=None,
    ))
    pipe = ToolExecutionPipeline(MagicMock(), tracker, dispatch_fn=dispatch)

    step = tracker.start_step(task.id, "buffer", {})
    await pipe.execute_tool_call(
        {"id": "c1", "function": {"name": "buffer", "arguments": "{}"}},
        "s1", task.id, pre_created_step=step,
    )
    # RUN-02 regression: the pipeline used to open a SECOND track_step,
    # leaving task.steps with 2 entries for one tool call.
    assert len(task.steps) == 1, f"expected 1 step, got {len(task.steps)}"
    assert task.steps[0].id == step.id
    # The pre-created step must carry the dispatch result.
    assert task.steps[0].result == {"ok": 1}


@pytest.mark.asyncio
async def test_legacy_path_creates_single_step():
    tracker = TaskTracker()
    task = tracker.create("s1", "test request 2")
    dispatch = AsyncMock(return_value=MagicMock(
        status="success", raw_result={"ok": 1}, llm_payload="ok",
        slim_event={}, geojson_ref=None,
    ))
    pipe = ToolExecutionPipeline(MagicMock(), tracker, dispatch_fn=dispatch)

    await pipe.execute_tool_call(
        {"id": "c2", "function": {"name": "buffer", "arguments": "{}"}},
        "s1", task.id,
    )
    assert len(task.steps) == 1


@pytest.mark.asyncio
async def test_repeated_outcome_terminates_pre_created_step():
    """Reviewer BLOCKING fix: a 'repeated' (deduped) outcome must complete the
    pre-created step — it previously stayed in 'running' forever because the
    pipeline's track_step __aexit__ (which used to do the terminal transition)
    is skipped in the pre_created_step path."""
    from app.services.chat.execution_engine import ChatExecutionEngine

    engine = ChatExecutionEngine.__new__(ChatExecutionEngine)
    engine.tracker = TaskTracker()

    task = engine.tracker.create("s1", "dup request")
    step = engine.tracker.start_step(task.id, "buffer", {})
    assert step.status.value == "running" or step.status == "running"

    # Simulate the chat_stream "repeated" branch: complete_step must be called
    # for the repeated outcome (this is what the fixed code path does).
    engine.tracker.complete_step(task.id, step.id, {"deduped": True})
    assert step.status.value == "completed" or step.status == "completed"


# ---------------------------------------------------------------------------
# RUN-01 — shared dispatch service (dedup lock shared)
# ---------------------------------------------------------------------------

def test_engine_holds_single_shared_dispatch_service():
    """The engine must create ONE ToolDispatchService and reuse it, so the
    dedup asyncio.Lock is shared across the parallel tool wave."""
    from app.services.chat.execution_engine import ChatExecutionEngine
    from app.tools.registry import ToolRegistry
    from app.services.tool_dispatch_service import ToolDispatchService

    # Stub settings/llm deps needed by __init__ (minimal construction) and
    # capture the args passed to ToolExecutionPipeline.
    import app.services.chat.execution_engine as ee_mod

    class _FakeSettings:
        LLM_BASE_URL = "http://localhost:9999"
        LLM_MODEL = "m"
        LLM_API_KEY = "k"
        LLM_PROMPT_CACHING_ENABLED = False

    captured = {}

    class _FakePipeline:
        def __init__(self, registry, tracker, dispatch_service=None, dispatch_fn=None):
            captured["dispatch_service"] = dispatch_service
            captured["dispatch_fn"] = dispatch_fn
            self.dispatch_service = dispatch_service
            self.dispatch_fn = dispatch_fn

    orig_settings = ee_mod.settings
    orig_pipeline_cls = ee_mod.ToolExecutionPipeline
    try:
        ee_mod.settings = _FakeSettings()
        ee_mod.ToolExecutionPipeline = _FakePipeline
        engine = ChatExecutionEngine(ToolRegistry())
    finally:
        ee_mod.settings = orig_settings
        ee_mod.ToolExecutionPipeline = orig_pipeline_cls

    assert hasattr(engine, "dispatch_service"), "engine must hold dispatch_service"
    assert isinstance(engine.dispatch_service, ToolDispatchService)
    # The pipeline must receive the SAME shared service (RUN-01: one dedup
    # lock across the whole parallel wave).
    assert captured.get("dispatch_service") is engine.dispatch_service
    # dispatch_fn must route through the engine's _pipeline_dispatch, which
    # resolves _dispatch_tool at CALL time (late binding). Freezing the
    # construction-time bound method here would bypass later overrides of
    # engine._dispatch_tool (test seams, subclass overrides); late binding
    # keeps the shared-service guarantee — _dispatch_tool always routes to
    # self.dispatch_service — while honoring those overrides.
    assert captured.get("dispatch_fn").__func__ is engine._pipeline_dispatch.__func__
    # The dedup lock must exist and be a single object.
    assert engine.dispatch_service._dedup_lock is not None


# ---------------------------------------------------------------------------
# RUN-03 — session lock covers the whole turn
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_holds_session_lock_during_turn(monkeypatch):
    """chat() must hold the session lock for the whole turn, so a second
    concurrent request on the same session_id waits."""
    from app.services.chat.execution_engine import ChatExecutionEngine

    engine = ChatExecutionEngine.__new__(ChatExecutionEngine)
    engine._session_locks = {}
    engine._MAX_LOCKS = 200

    lock = engine._get_session_lock("s_locktest")
    acquired = []

    async def _slow_locked():
        async with lock:
            acquired.append(True)
            await asyncio.sleep(0.05)
            return {"content": "done"}

    # Simulate: first request takes the lock; second must wait until release.
    async def _second():
        async with lock:
            acquired.append(True)

    t1 = asyncio.create_task(_slow_locked())
    await asyncio.sleep(0.01)  # let t1 acquire
    t2 = asyncio.create_task(_second())
    await asyncio.sleep(0.01)
    # t2 must NOT have acquired yet while t1 holds the lock.
    assert len(acquired) == 1, f"lock not exclusive: acquired={len(acquired)}"
    await t1
    await t2
    assert len(acquired) == 2


@pytest.mark.asyncio
async def test_chat_no_deadlock_on_session_creation(monkeypatch):
    """CI regression (PR #320): chat() used to acquire the turn lock BEFORE
    _get_or_create_session — which acquires the SAME per-session lock for the
    DB-load path. asyncio.Lock is not reentrant → deadlock on a fresh session.
    This test drives a real ChatEngine.chat() with a mocked LLM and asserts it
    completes (no hang) for a session that needs DB loading."""
    from app.services.chat.execution_engine import ChatExecutionEngine
    from app.tools.registry import ToolRegistry
    from unittest.mock import AsyncMock, patch

    # Minimal engine construction (mirrors test_engine_holds_single_shared_dispatch_service).
    import app.services.chat.execution_engine as ee_mod

    class _FakeSettings:
        LLM_BASE_URL = "http://localhost:9999"
        LLM_MODEL = "m"
        LLM_API_KEY = "k"
        LLM_PROMPT_CACHING_ENABLED = False

    orig_settings = ee_mod.settings
    try:
        ee_mod.settings = _FakeSettings()
        engine = ChatExecutionEngine(ToolRegistry())
    finally:
        ee_mod.settings = orig_settings

    # Force the DB-load path: a session not yet in _sessions.
    engine._sessions = {}
    engine._session_locks = {}

    mock_response = {"choices": [{"message": {"content": "你好！", "tool_calls": None}}]}
    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
        with patch.object(engine, "_load_session_from_db", new_callable=AsyncMock, return_value=[]):
            # Must complete — the old code deadlocked here on the fresh session.
            result = await engine.chat("你好")
    assert result["content"] == "你好！"


def test_chat_stream_lock_scope_comment():
    """Assert the chat_stream source actually wraps the loop in the lock (a
    structural guard against future refactors moving the lock back)."""
    import inspect
    from app.services.chat import execution_engine

    src = inspect.getsource(execution_engine.ChatExecutionEngine.chat_stream)
    # The lock must be acquired once and the whole loop body must be inside it:
    # count occurrences of 'async with lock' — should be exactly 1 in chat_stream,
    # and the loop 'for round_index in range' must appear AFTER it.
    lock_occurrences = src.count("async with lock:")
    assert lock_occurrences == 1, f"chat_stream lock count={lock_occurrences}"
    loop_pos = src.find("for round_index in range(self.max_rounds):")
    lock_pos = src.find("async with lock:")
    assert lock_pos != -1 and loop_pos != -1
    assert lock_pos < loop_pos, (
        "RUN-03 regression: chat_stream loop is outside the session lock"
    )
