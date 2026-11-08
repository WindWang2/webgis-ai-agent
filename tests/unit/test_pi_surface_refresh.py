"""PI-03/PI-08（ADR-0286 D4）：registry 热更新可见性 + 时延度量聚合。

覆盖 M0 审计缺口 G5（registry 代内变化对已运行 Pi 子进程不可见——dump 仅
spawn 一次，动态注册的工具需进程重启才进模型面）与 G7（TTFT/冷启动无聚合
度量，只进日志 summary）。

行为契约：
- spawn 时记录 dump 的 ``registry_generation``；代漂移（registry 注入/重编译
  manifest）在 **turn 边界** 被检测：无在飞 turn 时回收 worker（stop+start，
  下一次 spawn 重建 dump，新工具对下一 turn 可见）；有在飞 turn 时延迟（本
  turn 用旧面跑完，不杀活 turn）。
- ``PI_SURFACE_HOT_RECYCLE=0`` 为 kill switch。
- spawn→ready 墙钟与 turn TTFT 进 ``pi_surface_metrics`` 有界聚合（百分位）。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest



def _patch_pkg_settle_effects(monkeypatch, fake):
    """打桩包级 run_settle_effects 且保真恢复 PEP562 惰性委托（防物化污染）。"""
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

def _make_rpc(*, spawn_generation: str = "gen-1"):
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    rpc.process_died_event = asyncio.Event()
    rpc.spawn_surface_generation = spawn_generation
    return rpc


@pytest.fixture(autouse=True)
def _metrics(monkeypatch):
    import app.services.chat.pi_surface_metrics as psm

    monkeypatch.delenv("PI_SURFACE_HOT_RECYCLE", raising=False)
    psm.reset_for_tests()
    yield psm
    psm.reset_for_tests()


# ── W4-1/W4-2：代漂移回收 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generation_drift_recycles_worker(_metrics, monkeypatch):
    """registry 世代变化 → 无在飞 turn 时 stop+start 回收，计数器+1。"""
    from app.agent_pi_bridge import PiBridge

    monkeypatch.setattr(
        "app.services.chat.pi_tool_spec.current_registry_generation",
        lambda: "gen-2",
    )
    rpc = _make_rpc(spawn_generation="gen-1")
    bridge = PiBridge(rpc=rpc)
    bridge._active_turn_sid = None
    assert bridge.surface_generation_stale() is True
    recycled = await bridge.recycle_if_surface_stale()
    assert recycled is True
    rpc.stop.assert_awaited()
    rpc.start.assert_awaited()
    assert _metrics.snapshot()["surface_recycles"] == 1


@pytest.mark.asyncio
async def test_same_generation_no_recycle(_metrics, monkeypatch):
    from app.agent_pi_bridge import PiBridge

    monkeypatch.setattr(
        "app.services.chat.pi_tool_spec.current_registry_generation",
        lambda: "gen-1",
    )
    rpc = _make_rpc(spawn_generation="gen-1")
    bridge = PiBridge(rpc=rpc)
    assert bridge.surface_generation_stale() is False
    recycled = await bridge.recycle_if_surface_stale()
    assert recycled is False
    rpc.stop.assert_not_awaited()
    assert _metrics.snapshot()["surface_recycles"] == 0


@pytest.mark.asyncio
async def test_turn_in_flight_defers_recycle(_metrics, monkeypatch):
    """有在飞 turn：不杀活 worker（延迟到 turn 后的下一边界）。"""
    from app.agent_pi_bridge import PiBridge

    monkeypatch.setattr(
        "app.services.chat.pi_tool_spec.current_registry_generation",
        lambda: "gen-2",
    )
    rpc = _make_rpc(spawn_generation="gen-1")
    bridge = PiBridge(rpc=rpc)
    bridge._active_turn_sid = "sess-live"
    assert bridge.surface_generation_stale() is True
    recycled = await bridge.recycle_if_surface_stale()
    # 延迟：本 turn 继续用旧面（诚实可用），未 stop
    assert recycled is False
    rpc.stop.assert_not_awaited()
    assert _metrics.snapshot()["surface_recycles"] == 0


@pytest.mark.asyncio
async def test_hot_recycle_kill_switch(_metrics, monkeypatch):
    from app.agent_pi_bridge import PiBridge

    monkeypatch.setenv("PI_SURFACE_HOT_RECYCLE", "0")
    monkeypatch.setattr(
        "app.services.chat.pi_tool_spec.current_registry_generation",
        lambda: "gen-2",
    )
    rpc = _make_rpc(spawn_generation="gen-1")
    bridge = PiBridge(rpc=rpc)
    assert bridge.surface_generation_stale() is False  # kill switch 关闭判定


@pytest.mark.asyncio
async def test_no_generation_fact_never_recycles(_metrics):
    """v2 dump / 无世代事实（None/空）→ 永不回收（fail-open）。"""
    from app.agent_pi_bridge import PiBridge

    rpc = _make_rpc(spawn_generation="")
    bridge = PiBridge(rpc=rpc)
    assert bridge.surface_generation_stale() is False


# ── W4-3：时延度量聚合 ─────────────────────────────────────────────────────

def test_timing_aggregation_percentiles(_metrics):
    """record_timing → snapshot 带 p50/p95（有界窗口）。"""
    import statistics

    samples = [float(i) for i in range(1, 101)]  # 1..100 ms
    for v in samples:
        _metrics.record_timing("ttft_ms", v)
    snap = _metrics.snapshot()
    t = snap["timings"]
    assert t["ttft_samples"] == 100
    # 最近邻秩百分位（非插值）：与精确中位数的差 ≤ 1 个样本粒度。
    assert abs(t["ttft_p50_ms"] - statistics.median(samples)) <= 1.0
    assert t["ttft_p95_ms"] >= t["ttft_p50_ms"]
    assert abs(t["ttft_p95_ms"] - 95.0) <= 1.0


def test_spawn_ready_timing_recorded(_metrics):
    """spawn→ready 计时 helper（rpc 客户端在 start() 成功路径调用）。"""
    _metrics.record_timing("spawn_ready_ms", 321.5)
    snap = _metrics.snapshot()
    assert snap["timings"]["spawn_ready_samples"] == 1
    assert snap["timings"]["spawn_ready_p50_ms"] == 321.5


def test_timing_window_bounded(_metrics):
    """窗口有界：超过容量的样本只保留最近 N 条。"""
    for i in range(500):
        _metrics.record_timing("ttft_ms", float(i))
    snap = _metrics.snapshot()
    assert snap["timings"]["ttft_samples"] <= 256


@pytest.mark.asyncio
async def test_bridge_turn_records_ttft(_metrics, monkeypatch):
    """真实 stream turn 结束时 TTFT 进聚合（TurnEvidence first_event 投影）。"""
    from types import SimpleNamespace
    import app.agent_pi_bridge as bridge_mod
    from app.agent_pi_bridge import PiBridge
    import app.api.routes.chat  # noqa: F401

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

    async def _se(*a, **k):
        return [], None

    monkeypatch.setattr(hks, "run_settle_effects", _se)
    _pkg_restore = _patch_pkg_settle_effects(monkeypatch, _se)
    monkeypatch.setattr(hks, "resolve_turn_refusal", AsyncMock(return_value=None))

    import app.services.harness_kernel as hk
    import app.services.harness_kernel.runtime as hkr

    class FK:
        async def begin_turn(self, *a, **k):
            pass

        async def end_turn(self, *a, **k):
            return []

        async def lifecycle_parity_snapshot(self, t):
            return {}

        async def turn_refusal_candidate(self, t):
            return None

    monkeypatch.setattr(hk, "get_runtime", lambda sid: FK())
    monkeypatch.setattr(hkr, "get_runtime", lambda sid: FK())
    monkeypatch.setattr(bridge_mod, "_clear_dispatch_cache", lambda sid=None: None)
    hks.reset_for_tests()

    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    try:
        _ = [ev async for ev in bridge.stream_prompt("hi", session_id="sess-ttft")]
    finally:
        _pkg_restore.teardown()
        hks.reset_for_tests()
    snap = _metrics.snapshot()
    assert snap["timings"]["ttft_samples"] == 1
    assert snap["timings"]["ttft_p50_ms"] > 0


# ── review P1-1/P1-2：回收路径的回归锁 ────────────────────────────────────

@pytest.mark.asyncio
async def test_recycle_failure_keeps_worker_alive_checked(_metrics, monkeypatch):
    """P1-1：回收 stop 成功但 start 失败 → worker 死；调用方（chat 准入）必须
    复检活性而非沿用回收前的 True。本测试锁 bridge 侧行为：recycle 返回
    False 且 worker 处于 stopped 状态（不复活）。"""
    from app.agent_pi_bridge import PiBridge

    monkeypatch.setattr(
        "app.services.chat.pi_tool_spec.current_registry_generation",
        lambda: "gen-2",
    )
    rpc = _make_rpc(spawn_generation="gen-1")

    async def _start_boom():
        raise RuntimeError("spawn failed")

    rpc.start = AsyncMock(side_effect=_start_boom)
    bridge = PiBridge(rpc=rpc)
    recycled = await bridge.recycle_if_surface_stale()
    assert recycled is False
    rpc.stop.assert_awaited()  # stop 已发生


@pytest.mark.asyncio
async def test_recycle_defers_when_turn_lock_held(_metrics, monkeypatch):
    """P1-2：turn 锁被持（并发准入的 turn 在飞/刚起步）→ 有界等待超时 →
    延迟回收，绝不 stop 在飞 turn 的 worker。"""
    from app.agent_pi_bridge import PiBridge

    monkeypatch.setattr(
        "app.services.chat.pi_tool_spec.current_registry_generation",
        lambda: "gen-2",
    )
    rpc = _make_rpc(spawn_generation="gen-1")
    bridge = PiBridge(rpc=rpc)
    bridge._active_turn_sid = None
    async with bridge._lock:
        # 持锁模拟在飞 turn；_active_turn_sid 置空以证明守卫来自锁本身
        recycled = await bridge.recycle_if_surface_stale()
    assert recycled is False
    rpc.stop.assert_not_awaited()
