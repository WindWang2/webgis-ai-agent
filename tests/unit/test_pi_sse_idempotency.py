"""PI-04（ADR-0286 D2）：SSE 事件幂等去重 + 纯文字任务负面断言。

覆盖 M0 审计缺口 G1（Pi 重复回传 ``tool_execution_end`` → dispatch cache 单次
消费后第二份走 cache-miss 仍发 ``step_result``，无去重无测试）与 G2（纯文字
turn 不产生 map completed 无负面测试，回归无锁）。

行为契约：
- 同一 ``toolCallId`` 的终结事件（``tool_execution_end``）turn 内首见放行、
  重复丢弃（客户端只见一条 ``step_result``）；重复被计数
  （pi_surface_metrics.duplicate_tool_end_dropped + TurnEvidence warning）。
- 重复 ``agent_settled`` 只产生一个 ``task_complete``、一次 kernel 结算。
- 纯文字 turn 的 ``task_complete`` 不含 ``map_product`` 键、无 ``map_finalization``
  事件；有地图产物的 turn 照发（防去过火）。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge

import app.api.routes.chat  # noqa: F401  # stream_prompt 冷导入预热


def _make_rpc():
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    rpc.process_died_event = asyncio.Event()
    return rpc


@pytest.fixture
def _env(monkeypatch):
    """与 settlement seam 车道同款的桩环境 + 摘要/效果捕获。"""
    summaries = []
    map_products = []  # 每个用例可注入 run_settle_effects 返回的 map product

    class FakeTrackerTask:
        def __init__(self, tid):
            self.id = tid

    class FakeTracker:
        def create(self, sid, message):
            return FakeTrackerTask(f"{sid}-task")

        def list_by_session(self, sid):
            return []

        def cancel(self, tid):
            pass

        def fail_task(self, tid, reason):
            pass

        def complete_task(self, tid):
            pass

    import app.services.chat.engine_instance as ei
    monkeypatch.setattr(ei, "try_get_chat_engine", lambda: SimpleNamespace(tracker=FakeTracker()))

    import app.services.harness_kernel.settle as hks
    from app.services.harness_kernel.settle_claim import SettlementClaim
    monkeypatch.setattr(
        "app.services.harness_kernel.settle_claim.acquire_settlement_claim",
        AsyncMock(return_value=SettlementClaim("claimed", "fixture-claim")),
    )
    monkeypatch.setattr(
        "app.services.harness_kernel.settle_claim.complete_settlement",
        AsyncMock(return_value=True),
    )

    async def _settle_effects(sid, turn_id, settle_class, *, reason="turn_settled",
                              state_trigger="execution_settled"):
        mp = map_products[-1] if map_products else None
        return [], mp

    monkeypatch.setattr(hks, "run_settle_effects", _settle_effects)
    # 事件循环内的 clean 投影经包级 re-export 解析 —— 两个绑定点都打桩
    # （包级打桩用保真恢复，防物化污染 PEP562 惰性委托）。
    _pkg_restore = _patch_pkg_settle_effects(monkeypatch, _settle_effects)
    monkeypatch.setattr(hks, "resolve_turn_refusal", AsyncMock(return_value=None))

    import app.services.harness_kernel as hk
    import app.services.harness_kernel.runtime as hk_runtime

    kernel_ends = []

    class FakeKernelRuntime:
        async def begin_turn(self, turn_id, *, host, message, lock=None):
            pass

        async def end_turn(self, turn_id, *, host, status, checkpoint=True, lock=None, settle_token=""):
            kernel_ends.append((turn_id, status))
            return []

        async def lifecycle_parity_snapshot(self, turn_id):
            return {"turn_id": turn_id, "phase": "verifying", "status": "running"}

        async def turn_refusal_candidate(self, turn_id):
            return None

    monkeypatch.setattr(hk, "get_runtime", lambda sid: FakeKernelRuntime())
    monkeypatch.setattr(hk_runtime, "get_runtime", lambda sid: FakeKernelRuntime())
    monkeypatch.setattr(bridge_mod, "_clear_dispatch_cache", lambda sid=None: None)
    monkeypatch.setattr(bridge_mod, "emit_turn_summary", lambda ev: summaries.append(ev) or {})
    hks.reset_for_tests()

    import app.services.chat.pi_surface_metrics as psm
    psm.reset_for_tests()
    yield SimpleNamespace(summaries=summaries, map_products=map_products,
                          kernel_ends=kernel_ends)
    _pkg_restore.teardown()
    hks.reset_for_tests()
    psm.reset_for_tests()



def _patch_pkg_settle_effects(monkeypatch, fake):
    """打桩包级 run_settle_effects 且保真恢复 PEP562 惰性委托。

    直接 monkeypatch.setattr 包属性会物化 __dict__ 中的普通函数，revert 后
    惰性委托（__getattr__ → settle.py）永久丢失——污染同会话后续只 patch
    settle.py 的存量测试。此处物化前先记录、恢复时按需删除。"""
    import app.services.harness_kernel as _hk_pkg
    had = "run_settle_effects" in _hk_pkg.__dict__
    saved = getattr(_hk_pkg, "run_settle_effects")
    # 不经 monkeypatch：其恢复机制会把物化函数写回 __dict__，重新污染。
    setattr(_hk_pkg, "run_settle_effects", fake)

    class _Restore:
        def __init__(self):
            self._done = False
        def teardown(self):
            if self._done:
                return
            self._done = True
            if had:
                setattr(_hk_pkg, "run_settle_effects", saved)
            else:
                _hk_pkg.__dict__.pop("run_settle_effects", None)
    return _Restore()

def _sse_events_of(raw_events: list, name: str) -> list[dict]:
    """从原始 SSE 块（每个元素可含多行）提取指定事件名的 data 负载。"""
    out = []
    block_name = None
    for chunk in raw_events:
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                block_name = line[len("event: "):].strip()
            elif line.startswith("data: ") and block_name == name:
                try:
                    out.append(json.loads(line[len("data: "):]))
                except json.JSONDecodeError:
                    out.append({})
    return out


async def _consume(bridge, sid, message="hi"):
    return [ev async for ev in bridge.stream_prompt(message, session_id=sid)]


# ── W2-1/W2-3/W2-6：重复 tool_execution_end 去重 ──────────────────────────

@pytest.mark.asyncio
async def test_duplicate_tool_end_suppressed(_env):
    """同一 toolCallId 的 tool_execution_end 只发一条 step_result；
    重复被丢弃并计数（metrics + TurnEvidence warning）。"""
    import app.services.chat.pi_surface_metrics as psm

    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({"type": "tool_execution_start", "toolCallId": "call-1"})
            await rpc.events.put({"type": "tool_execution_end", "toolCallId": "call-1"})
            # 重复回传（vendor 重试/协议重发）
            await rpc.events.put({"type": "tool_execution_end", "toolCallId": "call-1"})
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    events = await _consume(bridge, "sess-dup")
    steps = _sse_events_of(events, "step_result")
    # 首见放行、重复丢弃：恰好一条 step_result（重复那条不产生降级补发）
    assert len(steps) == 1, f"expected exactly 1 step_result, got {len(steps)}"
    snap = psm.snapshot()
    assert snap["duplicate_tool_end_dropped"] == 1
    summary = _env.summaries[-1].to_summary()
    assert any(w["code"] == "duplicate_tool_execution_end" for w in summary["warnings"])


@pytest.mark.asyncio
async def test_distinct_tool_ends_not_deduped(_env):
    """不同 toolCallId 互不去重（W2-6）。"""
    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            for cid in ("call-a", "call-b"):
                await rpc.events.put({"type": "tool_execution_start", "toolCallId": cid})
                await rpc.events.put({"type": "tool_execution_end", "toolCallId": cid})
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    events = await _consume(bridge, "sess-distinct")
    assert len(_sse_events_of(events, "step_result")) == 2


# ── W2-2：重复 agent_settled ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_agent_settled_single_terminal(_env):
    """重复 agent_settled 只产生一个 task_complete、一次 kernel 结算。"""
    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    events = await _consume(bridge, "sess-dupsettle")
    assert len(_sse_events_of(events, "task_complete")) == 1
    assert len(_env.kernel_ends) == 1
    assert any(ev.startswith("event: done") for ev in events)


# ── W2-4/W2-5：纯文字负面 + 有图正向对照 ──────────────────────────────────

@pytest.mark.asyncio
async def test_text_only_turn_no_map_completion(_env):
    """纯文字 turn：task_complete 无 map_product 键、无 map_finalization 事件。"""
    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "文字"},
            })
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    events = await _consume(bridge, "sess-text", message="只要文字回答")
    # 不注入 map product（fixture 默认 None）
    completions = _sse_events_of(events, "task_complete")
    assert len(completions) == 1
    assert "map_product" not in completions[0]
    assert not _sse_events_of(events, "map_finalization")
    assert not any(ev.startswith("event: map_finalization") for ev in events)


@pytest.mark.asyncio
async def test_map_turn_still_finalizes(_env):
    """有地图产物的 turn 照发 map_finalization + task_complete.map_product（正向对照）。"""
    _env.map_products.append({
        "status": "ready",
        "summary": "热力图已生成并通过视口终验",
        "kind": "map",
    })
    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    events = await _consume(bridge, "sess-map", message="画一张图")
    finalizations = _sse_events_of(events, "map_finalization")
    assert len(finalizations) == 1
    completions = _sse_events_of(events, "task_complete")
    mp = completions[0].get("map_product")
    assert mp == {
        "status": "ready",
        "summary": "热力图已生成并通过视口终验",
    }
