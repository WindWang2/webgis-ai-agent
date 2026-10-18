"""#1646: chat_stream 锁等待 keepalive 轮询的授予窗口竞态回归。

根因（master c8c66119，app/services/chat/execution_engine.py 锁等待循环）：
``await asyncio.wait_for(lock.acquire(), timeout=_PLANNER_KEEPALIVE_S)`` 的
超时取消的是「正在 acquire 的任务」。前一个 turn 放锁与 10s 轮询超时同刻
时，锁已被授予给该（随即被取消的）任务而无人释放 —— in-process 模式下同
session 后续每个 turn 都排在这把永不释放的锁后（30s acquire 预算耗尽 →
LockContentionError 误报 → 再排队，直至进程重启）；Redis 模式下已 nx
写入的 key 随属主取消失去 renewer，TTL 内同 session 楔死。

修复（issue 选项 A）：acquire 改为单个 shield 任务、跨轮询复用、绝不被
wait_for 取消；生成器退出时诚实回收 —— 授予了但没进入 turn 主体就释放，
仍在途则挂完成回调（授予即刻释放），绝不 cancel。

断言策略（为何是结构断言而非模拟 CPython 窗口）：该授予窗口是 µs 级
（issue 自报概率 ≈ 1e-5/次轮询），本机 Python 3.13.9 上 6000 次对抗计时
试验（放锁时刻刻意贴近超时时刻）零泄漏 —— 单次测试运行无法确定性地逼迫
真实 asyncio 落入该窗口。因此回归测试钉死修复的不变量本身：轮询超时绝不
取消/重启 acquire（旧实现每轮询一次新 acquire 调用），以及用户可见契约
（放锁后 turn 正常开始收尾、锁不泄漏、断连不楔死后续 turn）。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import distributed_lock as dl
from app.services.chat import execution_engine as ee
from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry
from app.utils.sse import sse_event_type


def _fast_stream(*args, **kwargs):
    async def _gen():
        yield ("token", {"content": "a"})
        yield ("done", {"message": {"content": "a", "tool_calls": None},
                        "usage": {"total_tokens": 1}})

    return _gen()


async def _collect(agen) -> list[str]:
    out: list[str] = []
    async for item in agen:
        out.append(item)
    return out


@pytest.fixture
def engine(monkeypatch):
    """真实 ChatEngine，stub DB/planner/title 副作用（同
    tests/test_sse_planner_keepalive_435.py 的 fixture 模式）。"""
    eng = ChatEngine(ToolRegistry())

    async def fake_get_or_create_session(session_id, user_id=None):
        return []

    async def fake_maybe_plan(*a, **kw):
        return None

    async def fake_generate_title(*a, **kw):
        return None

    monkeypatch.setattr(eng, "_get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(eng, "_maybe_plan", fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", fake_generate_title)
    monkeypatch.setattr(eng, "_save_msg_async", AsyncMock())
    return eng


@pytest.fixture
def inprocess_locks(monkeypatch):
    """钉死 in-process 回退路径（与 USE_REDIS 环境无关）：无 Redis client
    → ``_ResilientSessionLock`` 走 ``_InProcessLock`` 回退，acquire 调用
    可被 spy 计数。"""
    monkeypatch.setattr(dl.session_lock_registry, "_get_client", lambda: None)


def _spy_acquire_calls(monkeypatch) -> list:
    """spy ``_InProcessLock.acquire``：记录每次调用（含 timeout 参数），
    行为原样委派。"""
    calls: list = []
    orig_acquire = dl._InProcessLock.acquire

    async def _counting_acquire(self, timeout_s=None):
        calls.append(timeout_s)
        return await orig_acquire(self, timeout_s=timeout_s)

    monkeypatch.setattr(dl._InProcessLock, "acquire", _counting_acquire)
    return calls


# ─── 结构性回归：轮询超时绝不取消/重启 acquire ───────────────────────────


@pytest.mark.asyncio
async def test_lock_wait_poll_never_restarts_acquire(
    engine, monkeypatch, inprocess_locks
):
    """两次 keepalive 轮询超时后，in-process fallback acquire 仍只有一次
    调用（holder 一次 + 第二请求那一个被 shield 保护的 acquire）—— 轮询
    超时绝不取消 acquire 并以新的 acquire 重试（旧 #1646 形态：每轮询一次
    新 acquire，放锁与超时同刻时锁被死任务持有）。放锁后 turn 正常开始并
    收尾，且会话锁不泄漏（新的 acquire 立即成功）。"""
    sid = "s-1646-poll"
    acquire_calls = _spy_acquire_calls(monkeypatch)
    monkeypatch.setattr(ee, "_PLANNER_KEEPALIVE_S", 0.02)

    holder = ee.session_lock(sid)
    await holder.acquire()  # 前一个 turn 持锁（fallback acquire #1）
    assert len(acquire_calls) == 1

    engine._call_llm_stream = _fast_stream
    gen = engine.chat_stream("hi", session_id=sid)
    events: list[str] = []
    try:
        for _ in range(2):  # 两轮轮询超时 → 两次 keep_alive
            block = await gen.__anext__()
            assert sse_event_type(block) == "keep_alive"
            events.append(block)
        assert len(acquire_calls) == 2, (
            f"keepalive 轮询取消了 acquire 并重启（{len(acquire_calls)} 次调用）"
            " —— 放锁与 10s 超时同刻时锁会被死任务持有（#1646）"
        )

        holder.release()  # 放锁：授予给同一个（shield 保护的）acquire 任务
        events.extend(await _collect(gen))
    finally:
        await gen.aclose()

    types = [sse_event_type(e) for e in events]
    assert types.count("keep_alive") == 2, types
    assert "task_start" in types and types[-1] == "done", types
    assert "task_error" not in types, types

    # 无泄漏：turn 结束后同 session 立即可锁（旧形态下永久楔死在此）
    probe = ee.session_lock(sid)
    await asyncio.wait_for(probe.acquire(), timeout=1.0)
    probe.release()


# ─── 回收回归：断连发生在锁等待期，授予后的孤儿 acquire 被诚实释放 ──────


@pytest.mark.asyncio
async def test_disconnect_during_lock_wait_reclaims_granted_lock(
    engine, monkeypatch, inprocess_locks
):
    """客户端在锁等待期断连（生成器在 keepalive yield 处被关闭）：acquire
    任务仍在途且不被取消；前一个 turn 放锁后授予给它时，回收路径必须释放
    这把锁 —— 否则同 session 的后续 turn 全部排死（#1646 的用户可见症状：
    SSE 流永远 keep_alive、无任何进展）。"""
    sid = "s-1646-disc"
    monkeypatch.setattr(ee, "_PLANNER_KEEPALIVE_S", 0.02)

    holder = ee.session_lock(sid)
    await holder.acquire()

    engine._call_llm_stream = _fast_stream
    gen = engine.chat_stream("hi", session_id=sid)
    block = await gen.__anext__()
    assert sse_event_type(block) == "keep_alive"
    await gen.aclose()  # 断连：acquire 任务仍在途（shield 保护，未被取消）

    holder.release()  # 前一个 turn 放锁 → 授予给被回收的 acquire 任务

    # 有界轮询：孤儿 acquire 完成后锁应立即可取（回收路径释放了它）
    deadline = asyncio.get_running_loop().time() + 5.0
    reclaimed = False
    while asyncio.get_running_loop().time() < deadline:
        probe = ee.session_lock(sid)
        try:
            await asyncio.wait_for(probe.acquire(), timeout=0.05)
        except (asyncio.TimeoutError, dl.LockContentionError):
            await asyncio.sleep(0.01)
            continue
        probe.release()
        reclaimed = True
        break
    assert reclaimed, (
        "断连后孤儿 acquire 持锁不释放 —— 同 session 后续 turn 将永久悬挂"
    )


# ─── 端到端：同 session 双请求（issue 验收场景 3）────────────────────────


@pytest.mark.asyncio
async def test_concurrent_second_stream_heartbeats_then_completes(
    engine, monkeypatch
):
    """同 session 双请求：首个 turn 持锁期间，第二请求持续收到 keep_alive
    （字节上线，#554 语义不回归）；首 turn 结束后第二请求正常开始并收尾，
    全程无 task_error / 无 LockContentionError 误报、无悬挂；两个 turn 都
    结束后会话锁立即可锁（RUN-03 全 turn 持锁的释放面 + #1646 无泄漏）。"""
    sid = "s-1646-dual"
    monkeypatch.setattr(ee, "_PLANNER_KEEPALIVE_S", 0.02)

    async def slow_stream(*args, **kwargs):
        await asyncio.sleep(0.15)  # 首 turn 持锁足够久，覆盖数次轮询
        yield ("token", {"content": "a"})
        yield ("done", {"message": {"content": "a", "tool_calls": None},
                        "usage": {"total_tokens": 1}})

    engine._call_llm_stream = slow_stream

    turn_a = asyncio.ensure_future(
        _collect(engine.chat_stream("first", session_id=sid))
    )
    await asyncio.sleep(0.03)  # 让 A 先拿到锁并进入 LLM 等待
    b_events = await _collect(engine.chat_stream("second", session_id=sid))
    a_events = await turn_a

    b_types = [sse_event_type(e) for e in b_events]
    heartbeats = [i for i, t in enumerate(b_types) if t == "keep_alive"]
    assert heartbeats, f"second stream got no heartbeats while waiting: {b_types}"
    assert b_types.index("task_start") > heartbeats[0], (
        "heartbeats must precede the second turn's start"
    )
    assert b_types[-1] == "done", b_types
    assert "task_error" not in b_types, b_types
    assert [sse_event_type(e) for e in a_events][-1] == "done"

    # 两个 turn 都结束后锁立即可取（无泄漏、无楔死）
    probe = ee.session_lock(sid)
    await asyncio.wait_for(probe.acquire(), timeout=1.0)
    probe.release()
