"""Deep-review carto-platform CP-14：runtime validator 并发上限 + 同会话串行。"""

from __future__ import annotations

import asyncio

import pytest

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


async def test_cp14_cancel_holds_guard_until_orphan_finishes(monkeypatch, tmp_path):
    """CP-14 取消穿透（#1630）：await 方被 cancel 时锁/信号量不得提前归还。

    真正干活的 worker 线程（subprocess npx tsx）不可取消 —— 取消后它仍在
    写同一 runtime 证据目录。若守卫随取消释放，同会话第二次 validate 会与
    孤儿并发覆写 report.json / map.png / trace.zip，全局 2 并发上限也被击穿。
    """
    import time as _time

    from app.services.mapspec_store import mapspec_store
    from app.services.runtime_validator import _runtime_guards, runtime_validator

    def _slow_headless(input_dir, out_dir, probes_path=None):
        _time.sleep(1.0)  # 孤儿 worker：线程内阻塞，不可取消
        return {
            "mapLoaded": True, "mapIdle": True, "pageErrors": [],
            "consoleErrors": [], "failedRequests": [], "fatalError": None,
            "canvas": {"luminanceStdDev": 20.0, "dominantRatio": 0.5,
                       "transparentRatio": 0.1, "blank": False},
            "controls": {"overflow": [], "collisions": []},
        }

    async def _fake_mapspec(session_id):
        return {"sources": {"s1": {}}, "layers": [{"id": "l1"}]}

    async def _fake_compile(session_id):
        return {"success": True, "out_dir": str(tmp_path / "out"),
                "report": {"stats": {}}}

    monkeypatch.setattr(runtime_validator, "_run_headless_validator", _slow_headless)
    monkeypatch.setattr(mapspec_store, "get_mapspec", _fake_mapspec)
    monkeypatch.setattr(mapspec_store, "compile_mapspec_cli", _fake_compile)

    sid = "cp14-cancel-1"
    task = asyncio.create_task(runtime_validator.validate_runtime(sid))
    await asyncio.sleep(0.3)  # 进入 headless（孤儿 worker 在跑）
    task.cancel()
    await asyncio.sleep(0.1)  # 让取消真正投递（旧实现在此刻已归还锁）
    # 孤儿线程仍在写 runtime 目录 —— 会话锁不得提前归还
    _sem, lock = _runtime_guards(sid)
    assert lock.locked(), (
        "cancel released the session guard while the orphan worker still runs"
    )
    # 同会话第二次 validate 在孤儿结束前无法开始（串行锁仍被孤儿持有）
    second = asyncio.create_task(runtime_validator.validate_runtime(sid))
    await asyncio.sleep(0.2)
    assert not second.done(), (
        "second validate started while the orphan still writes the same "
        "runtime evidence dir"
    )
    with pytest.raises(asyncio.CancelledError):
        await task  # 取消最多延迟到孤儿结束（≤ RUNTIME_TIMEOUT_S 有界）
    # 孤儿结束、锁释放后：第二次 validate 正常跑完（不再与孤儿并发）。
    # wait_for 超时即锁从未释放（fail-fast，不挂死）。
    res = await asyncio.wait_for(second, timeout=10.0)
    assert res["valid"] is True
