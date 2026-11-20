"""G22-P7 — A24 长会话 soak：队列 / receipt / 上下文结构有界。

不是绝对值断言，而是**平坦性**断言：N=20 与 N=120 个 turn 压力后，
各 capped 结构的尺寸不随 N 增长（有界逐出生效、无泄漏累积）。
结构清单（与 PI_RUNTIME_CONTRACT §9 预算表对齐）：
- ``_TURN_ABORT_SOURCES``（≤64，bridge）
- ``surface_receipt_store._receipts``（≤128，逐出）
- ``TurnResumeRegistry``（LRU max_sessions）
- bridge ``_inflight_control``（G22-P1 新增：done-callback 逐出）
- 重建块文本（域 cap 1600 / 段预算 1540）
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, record_turn_abort_source


class _AutoRpc:
    """auto 完成 prompt 的探针 RPC（含控制面写相位）。"""

    def __init__(self) -> None:
        self.events: asyncio.Queue = asyncio.Queue()
        self._seq = 0

    async def request(self, command, data=None):
        if command == "prompt":
            await self.events.put({"type": "agent_end", "willRetry": False})
            await self.events.put({"type": "agent_settled"})
            return {"status": "ok"}
        return {}

    async def issue(self, command, data=None):
        self._seq += 1
        fut = asyncio.get_running_loop().create_future()
        fut.set_result({"status": "ok"})
        return SimpleNamespace(id=f"i{self._seq}", command=command, future=fut)

    async def collect(self, issued, command=None):
        return await issued.future

    def pending_request_ids(self):
        return set()

    def fail_pending_ids(self, ids, reason):
        pass


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    import app.services.chat.engine_instance as engines

    monkeypatch.setattr(
        engines, "try_get_chat_engine",
        lambda: SimpleNamespace(tracker=SimpleNamespace(
            create=lambda sid, message: SimpleNamespace(id=f"t-{sid}"),
            list_by_session=lambda sid: [],
            cancel=lambda tid: None,
            fail_task=lambda tid, reason: None,
            complete_task=lambda tid, message=None, map_product=None: None,
        )))
    bridge_mod._TURN_ABORT_SOURCES.clear()


def _snapshot() -> dict:
    from app.services.chat.event_resume import TurnResumeRegistry  # noqa: F401
    from app.services.chat.pi_surface_receipt import surface_receipt_store

    inflight_sizes = [
        len(getattr(b, "_inflight_control", None) or ())
        for b in {id(v): v for e in list(bridge_mod._active_turns.values())
                  for v in [getattr(e, "bridge", None)] if v is not None}.values()
    ]
    return {
        "abort_sources": len(bridge_mod._TURN_ABORT_SOURCES),
        "receipts": len(surface_receipt_store._receipts),
        "inflight_control_max": max(inflight_sizes) if inflight_sizes else 0,
    }


@pytest.mark.asyncio
async def test_a24_soak_structures_bounded_flat():
    from app.services.chat.event_resume import RESUME_MAX_SESSIONS, TurnResumeRegistry
    from app.services.chat.pi_surface_receipt import (
        SurfaceReceipt,
        surface_receipt_store,
    )

    rpc = _AutoRpc()
    bridge = PiBridge(rpc=rpc)
    resume_registry = TurnResumeRegistry()

    async def _one_turn(i: int) -> None:
        sid = f"soak-{i % 7}"  # 少数会话反复用（长会话形态）
        await bridge.prompt(f"msg-{i}", session_id=sid)
        # 每 turn 铸 receipt（不逐出就会线性增长 → 逐出必须生效）
        surface_receipt_store.record(SurfaceReceipt(
            session_id=sid, turn_id=f"turn-soak-{i}",
            selected=("webgis_map_intent",), surface_fingerprint=f"fp{i}",
        ))
        # 死 turn 的 abort 来源残留（有界台账）
        record_turn_abort_source(f"turn-soak-{i}", "user")
        # resume 注册表：每会话只留最近 buffer（LRU 上限）
        buf = resume_registry.find(sid, f"msg-{i}", last_event_id="e1")
        if buf is None:
            from app.services.chat.event_resume import TurnEventBuffer

            new_buf = TurnEventBuffer(f"msg-{i}")
            new_buf.record("e1", "step", {"i": i})
            resume_registry.register(sid, new_buf)

    for i in range(20):
        await _one_turn(i)
    snap20 = _snapshot()
    sess20 = len(resume_registry._sessions) if hasattr(
        resume_registry, "_sessions") else None

    for i in range(20, 160):  # 160 receipts > cap 128 → 逐出必须真实生效
        await _one_turn(i)
    snap120 = _snapshot()
    sess120 = len(resume_registry._sessions) if hasattr(
        resume_registry, "_sessions") else None

    # 平坦性：cap 生效，N 增长 6× 而结构尺寸不增长
    assert snap120["abort_sources"] <= 64, snap120
    assert snap120["abort_sources"] <= snap20["abort_sources"] + 64
    assert snap120["receipts"] <= 128, snap120  # 160 投递后 ≤128（逐出生效）
    assert snap120["inflight_control_max"] <= 8, snap120
    if sess20 is not None and sess120 is not None:
        assert sess120 <= RESUME_MAX_SESSIONS
        assert sess120 <= max(sess20, 7)  # 7 个会话键为上限，不随 turn 数增长

    # 重建块在极大 mapspec 下仍受域 cap 约束（文本有界）
    from app.services.context_assembly.providers import (
        render_gis_state_rebuild_block,
    )

    big = {
        "revision": 999,
        "layers": [
            {"id": f"l{i}", "layout": {"visibility": "visible"},
             "label": {"authoring": {"overrides": {f"f{i}": {"pinned": True}}}}}
            for i in range(200)
        ],
        "workbench": {"lockedLayerIds": [f"l{i}" for i in range(30)]},
        "frame_product": {
            "product_kind": "k", "active_frame": "f1",
            "frames": [{"frame_id": f"f{j}", "title": f"T{j}"} for j in range(24)],
        },
    }
    text = render_gis_state_rebuild_block(big, None)
    assert len(text) <= 1600
    # 约束仍在（预算挤压不吞用户约束）
    assert "保持隐藏" in text or "锁定" in text
