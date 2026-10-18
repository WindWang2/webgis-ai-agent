"""Deep-review carto-platform CP-14：runtime validator 并发上限 + 同会话串行。"""

from __future__ import annotations

import asyncio

from app.services import runtime_validator as rv


def _tracking(monkeypatch, validator):
    state = {"now": 0, "peak": 0, "per": {}, "per_peak": {}}

    async def fake(self, session_id, probes_path=None):
        state["now"] += 1
        state["per"][session_id] = state["per"].get(session_id, 0) + 1
        state["peak"] = max(state["peak"], state["now"])
        state["per_peak"][session_id] = max(
            state["per_peak"].get(session_id, 0), state["per"][session_id]
        )
        await asyncio.sleep(0.05)
        state["now"] -= 1
        state["per"][session_id] -= 1
        return {"ok": True}

    monkeypatch.setattr(rv.RuntimeValidator, "_validate_runtime_unlocked", fake)
    return state


async def test_cp14_global_concurrency_capped(monkeypatch):
    v = rv.RuntimeValidator()
    state = _tracking(monkeypatch, v)
    await asyncio.gather(*(v.validate_runtime(f"s{i}") for i in range(6)))
    assert state["peak"] == rv.MAX_CONCURRENT_RUNTIME_VALIDATIONS


async def test_cp14_same_session_serialized(monkeypatch):
    v = rv.RuntimeValidator()
    state = _tracking(monkeypatch, v)
    await asyncio.gather(*(v.validate_runtime("same") for _ in range(3)))
    assert state["per_peak"]["same"] == 1
