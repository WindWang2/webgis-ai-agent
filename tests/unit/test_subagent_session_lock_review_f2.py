"""Review F2: spawn_subagent inside a legacy turn must not self-deadlock on
the parent's non-reentrant session lock (real lock, no engine mocks)."""
import functools

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
async def test_subagent_runs_under_parent_held_lock(short_lock, monkeypatch):
    """F2 门控生效时，子引擎跑在父 turn 已持有的会话锁下，绝不自己再取锁。

    断言度量的是**锁获取次数**而非墙钟（#1647）：无 Redis 开发机上
    ``session_data_manager``（RedisSessionStore）的 list_refs /
    get_map_state / get_event_log / get_started_at 逐个撞连接超时后降级，
    会把场景总耗时拉到数秒——那是环境 IO 噪声，与「子引擎是否等锁」无关。
    结构断言：spy ``_InProcessLock.acquire``，整个场景内 fallback acquire
    只被父锁调用一次（子引擎零次）。子引擎若自锁等待，1.0s acquire 预算
    耗尽 → LockContentionError → ``session_lock_contention``，由 error
    断言与第二次 acquire 调用共同检出（反向验证见 issue 验收标准 2）。
    """
    from app.services.subagent import SubagentDispatcher

    sid = "sess-review-f2-held"
    acquire_calls: list = []
    orig_acquire = dl._InProcessLock.acquire

    async def _counting_acquire(self, timeout_s=None):
        acquire_calls.append(timeout_s)
        return await orig_acquire(self, timeout_s=timeout_s)

    monkeypatch.setattr(dl._InProcessLock, "acquire", _counting_acquire)

    lock = ee.session_lock(sid)
    async with lock:
        assert len(acquire_calls) == 1, "父锁自身应经 fallback acquire 获取"
        tok = bind_engine_lock(sid, lock)
        try:
            res = await SubagentDispatcher(ToolRegistry(), sid).run(
                task="count features", max_rounds=2
            )
        finally:
            unbind_engine_lock(tok)
    assert res.error not in ("budget_exceeded:wall_time", "session_lock_contention"), res
    # 结构性断言：整个场景只有父锁那一次 fallback acquire —— 子引擎在父已
    # 持有的锁下运行，没有（也不必）再取会话锁。
    assert len(acquire_calls) == 1, (
        f"sub-engine acquired the session lock itself ({len(acquire_calls)} calls) "
        "— it must run under the parent's held lock (F2 gate)"
    )


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
