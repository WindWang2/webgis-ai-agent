"""Review F2: spawn_subagent inside a legacy turn must not self-deadlock on
the parent's non-reentrant session lock (real lock, no engine mocks)."""
import functools
import time

import pytest

from app.services import distributed_lock as dl
import app.services.chat.execution_engine as ee
from app.services.harness_kernel.legacy_adapter import (
    bind_engine_lock,
    unbind_engine_lock,
)
from app.tools.registry import ToolRegistry


async def _fake_llm(self, messages, tools=None):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}
        ]
    }


@pytest.fixture
def short_lock(monkeypatch):
    monkeypatch.setattr(
        ee, "session_lock", functools.partial(dl.session_lock, acquire_timeout_s=1.0)
    )
    monkeypatch.setattr(ee.ChatExecutionEngine, "_call_llm", _fake_llm, raising=True)


@pytest.mark.asyncio
async def test_subagent_runs_under_parent_held_lock(short_lock):
    from app.services.subagent import SubagentDispatcher

    sid = "sess-review-f2-held"
    lock = ee.session_lock(sid)
    async with lock:
        tok = bind_engine_lock(sid, lock)
        try:
            t0 = time.monotonic()
            res = await SubagentDispatcher(ToolRegistry(), sid).run(
                task="count features", max_rounds=2
            )
            elapsed = time.monotonic() - t0
        finally:
            unbind_engine_lock(tok)
    assert res.error not in ("budget_exceeded:wall_time", "session_lock_contention"), res
    assert elapsed < 1.0, f"sub-engine waited on the parent's lock ({elapsed:.1f}s)"


@pytest.mark.asyncio
async def test_lock_contention_not_mislabelled_as_wall_time(short_lock):
    from app.services.subagent import SubagentDispatcher

    sid = "sess-review-f2-foreign"
    # Lock held by someone else (no parent binding in this context).
    async with ee.session_lock(sid):
        res = await SubagentDispatcher(ToolRegistry(), sid).run(
            task="count features", max_rounds=2
        )
    assert res.success is False
    assert res.error == "session_lock_contention", res.error
