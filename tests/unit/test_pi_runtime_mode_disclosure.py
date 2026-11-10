"""PI-06（ADR-0286 D3）：运行时披露 native / unavailable（已移除 ChatEngine fallback）。

覆盖 M0 审计缺口 G6：/health 只报 runtime 名不报降级原因；per-request 静默
降级客户端不可见。单一定义点 ``pi_compat.classify_runtime_mode``，四个披露
出口（/health 字段、task_start 键、SSE 响应头、pi_doctor 汇总行）共用同一
词表与分类逻辑。
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


# ── W3-1/W3-4：分类器词表与矩阵（纯函数）─────────────────────────────────

def test_mode_vocabulary_is_closed():
    from app.services.chat import pi_compat

    assert pi_compat.RUNTIME_MODES == ("native", "unavailable")


def test_classify_unavailable_when_disabled():
    from app.services.chat.pi_compat import classify_runtime_mode

    # 产品已无关闭开关；enabled=False 与桥未初始化同义（兼容旧调用方）。
    mode, reason = classify_runtime_mode(
        enabled=False, bridge_present=True, alive=True, entry_built=True,
    )
    assert (mode, reason) == ("unavailable", "bridge_not_initialized")


def test_classify_unavailable_when_no_bridge():
    from app.services.chat.pi_compat import classify_runtime_mode

    mode, reason = classify_runtime_mode(
        enabled=True, bridge_present=False, alive=False, entry_built=True,
    )
    assert (mode, reason) == ("unavailable", "bridge_not_initialized")


def test_classify_unavailable_when_process_down():
    from app.services.chat.pi_compat import classify_runtime_mode

    # vendor 未构建 → unavailable（原因码：vendor_entry_unbuilt）
    assert classify_runtime_mode(
        enabled=True, bridge_present=True, alive=False, entry_built=False,
    ) == ("unavailable", "vendor_entry_unbuilt")
    # 已构建但进程不活 → unavailable（原因码：pi_process_down）
    assert classify_runtime_mode(
        enabled=True, bridge_present=True, alive=False, entry_built=True,
    ) == ("unavailable", "pi_process_down")


def test_classify_native():
    from app.services.chat.pi_compat import classify_runtime_mode

    assert classify_runtime_mode(
        enabled=True, bridge_present=True, alive=True, entry_built=True,
    ) == ("native", "")


# ── W3-2：/health 披露 ─────────────────────────────────────────────────────

def _health_client(monkeypatch, facts: dict):
    """按存量 test_health_api 模式构建仅含 health router 的无鉴权 app。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import health as health_mod

    monkeypatch.setattr(health_mod, "_pi_mode_facts", lambda: dict(facts))
    app = FastAPI()
    app.include_router(health_mod.router, prefix="/api/v1")
    return TestClient(app)


def test_health_reports_mode_and_reason(monkeypatch):
    """agent_runtime_mode + agent_runtime_reason 有界披露（降级不冒充）。"""
    client = _health_client(monkeypatch, dict(
        enabled=True, bridge_present=True, alive=False, entry_built=True,
    ))
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_runtime_mode"] == "unavailable"
    assert body["agent_runtime_reason"] == "pi_process_down"
    # agent_runtime 名与 mode 一致（不冒充）：unavailable 时不得报 "pi"
    assert body["agent_runtime"] != "pi"


def test_health_native_mode(monkeypatch):
    client = _health_client(monkeypatch, dict(
        enabled=True, bridge_present=True, alive=True, entry_built=True,
    ))
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_runtime_mode"] == "native"
    assert body["agent_runtime_reason"] == ""


# ── W3-3：task_start 携带 runtime_mode ─────────────────────────────────────


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
    yield
    _pkg_restore.teardown()
    hks.reset_for_tests()


@pytest.mark.asyncio
async def test_task_start_carries_native_mode(_env):
    """Pi 路径 task_start 负载含 runtime_mode=native（统一披露契约）。"""
    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    events = [ev async for ev in bridge.stream_prompt("hi", session_id="sess-mode")]
    starts = []
    for chunk in events:
        for line in chunk.split("\n"):
            if line.startswith("event: task_start"):
                starts.append("pending")
            elif line.startswith("data: ") and starts and starts[-1] == "pending":
                starts[-1] = json.loads(line[len("data: "):])
    assert len(starts) == 1
    assert starts[0]["runtime_mode"] == "native"


# ── W3-5：pi_doctor 同词表 ─────────────────────────────────────────────────

def test_pi_doctor_reports_runtime_mode():
    """pi_doctor --offline --json 输出与运行时同词表的 runtime_mode 字段。"""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    res = subprocess.run(
        [sys.executable, str(repo / "scripts" / "pi_doctor.py"), "--offline", "--json"],
        capture_output=True, text=True, timeout=120,
        cwd=str(repo),
    )
    assert res.returncode in (0, 1), res.stderr[-400:]
    # --json 在域报告之后仍打印人读 epilogue 行 —— 只解析首个 JSON 对象。
    payload, _end = json.JSONDecoder().raw_decode(res.stdout)
    mode = payload["runtime_mode"]["mode"]
    from app.services.chat.pi_compat import RUNTIME_MODES

    assert mode in RUNTIME_MODES


def test_health_probe_failed_reason(monkeypatch):
    """nP2-1：探测自身失败 → probe_failed 原因码（有界词表内，不误报
    native）。"""
    client = _health_client(monkeypatch, dict(
        enabled=True, bridge_present=True, alive=True, entry_built=True,
        probe_ok=False,
    ))
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_runtime_mode"] == "unavailable"
    assert body["agent_runtime_reason"] == "probe_failed"


def test_probe_failed_reason_in_bounded_vocabulary():
    from app.services.chat.pi_compat import RUNTIME_MODE_REASONS

    assert "probe_failed" in RUNTIME_MODE_REASONS
