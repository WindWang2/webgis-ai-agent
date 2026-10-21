"""#1711 请求面 loop-affinity 回归：health 刷新守卫 + spatial_events webhook 限流锁。

失败形态（issue #1711 §1）：两处模块级 ``asyncio.Lock`` 在首次「有竞争」的
await 时绑定所在事件循环（CPython ``Lock.acquire`` 的快路径不绑定，等待者路径
才 ``_get_loop()``）。同一 ASGI app 若被第二个事件循环驱动（测试客户端 + 多
worker 同进程），竞争路径直接 ``RuntimeError: ... is bound to a different event
loop`` → ``/api/v1/status/detailed`` 与 ``/api/v1/spatial-events/webhook`` 500。

锁的回归形态对齐存量 loop-affinity 测试（#1703 的 ``_run_in_new_loop`` /
#1681 的 per-loop 修法）：loop A 竞争 → loop B（另一线程的新 loop）再竞争，
必须不抛，且单 loop 内仍是同一把锁互斥（行为不变）。
"""
import asyncio
import threading
from unittest.mock import patch

from app.api.routes import health as health_mod
from app.api.routes import spatial_events as se


def _run_in_new_loop(coro_factory):
    """在独立线程的新 event loop 里跑协程工厂；异常装进 box 回主线程断言。"""
    box: dict = {}

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            box["result"] = loop.run_until_complete(coro_factory())
        except BaseException as exc:  # noqa: BLE001 — 回主线程断言，不吞
            box["error"] = exc
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    t = threading.Thread(target=runner)
    t.start()
    t.join()
    return box


async def _yield_until(flag) -> None:
    """把控制权交够轮数，直到 flag 被置位（有界，防挂死）。"""
    for _ in range(500):
        if flag.is_set():
            return
        await asyncio.sleep(0)
    raise AssertionError("flag never set — 协程未按预期推进")


# ── health.py：SRE 快照刷新守卫 ────────────────────────────────────────────


async def test_refresh_guard_same_loop_identity_and_mutual_exclusion():
    """单 loop 内行为不变：同一把锁 + waiter 等 holder 释放后才进临界区。"""
    guard = health_mod._refresh_guard()
    assert guard is health_mod._refresh_guard(), "同一 loop 必须持同一把守卫锁"

    order: list[str] = []

    async def _holder() -> None:
        async with health_mod._refresh_guard():
            order.append("holder-in")
            await asyncio.sleep(0.05)
            order.append("holder-out")

    async def _waiter() -> None:
        await asyncio.sleep(0.01)
        async with health_mod._refresh_guard():
            order.append("waiter-in")

    await asyncio.gather(_holder(), _waiter())
    assert order == ["holder-in", "holder-out", "waiter-in"]


async def test_refresh_guard_survives_second_event_loop_contention():
    """#1711-1：loop B 的竞争路径不得 RuntimeError（旧代码在此 500）。"""
    async def _contended() -> list[str]:
        order: list[str] = []

        async def _holder() -> None:
            async with health_mod._refresh_guard():
                order.append("holder-in")
                await asyncio.sleep(0.05)
                order.append("holder-out")

        async def _waiter() -> None:
            await asyncio.sleep(0.01)
            async with health_mod._refresh_guard():
                order.append("waiter-in")

        await asyncio.gather(_holder(), _waiter())
        return order

    # loop A（本测试 loop）：竞争发生即绑定 loop A
    assert await _contended() == ["holder-in", "holder-out", "waiter-in"]

    # loop B（另一线程的新 loop）再竞争
    box = _run_in_new_loop(_contended)
    assert "error" not in box, (
        f"#1711: 跨事件循环竞争刷新守卫报错: {box.get('error')!r}"
    )
    assert box["result"] == ["holder-in", "holder-out", "waiter-in"]


async def test_status_detailed_serves_from_second_event_loop():
    """/api/v1/status/detailed 端到端：第二个事件循环的并发刷新不得 500。"""
    entered = asyncio.Event()
    release = asyncio.Event()

    def _fake_snapshot() -> dict:
        return {"components": {}, "stuck_jobs": None, "overall": "ok"}

    async def _slow_stuck_jobs():
        # 持锁期间的可控 await —— 让第二个请求真实排在锁上
        entered.set()
        await release.wait()
        return 0

    async def _contended_status_call():
        # 强制走刷新路径（TTL 未过期会整体跳过锁）
        health_mod._cache_snapshot = None
        health_mod._cache_written_at = 0.0
        t1 = asyncio.create_task(health_mod.sre_status_detailed(_current_user={}))
        await _yield_until(entered)  # t1 已在临界区内
        t2 = asyncio.create_task(health_mod.sre_status_detailed(_current_user={}))
        await asyncio.sleep(0)  # t2 推进到锁上排队
        release.set()
        return await asyncio.gather(t1, t2)

    with patch.object(health_mod, "_collect_sre_snapshot", _fake_snapshot), \
            patch.object(health_mod, "_count_stuck_jobs", _slow_stuck_jobs):
        r1, r2 = await _contended_status_call()
        assert r1.status_code == 200 and r2.status_code == 200

        box = _run_in_new_loop(_contended_status_call)
        assert "error" not in box, (
            f"#1711: /status/detailed 在第二个事件循环 500: {box.get('error')!r}"
        )
        for resp in box["result"]:
            assert resp.status_code == 200


# ── spatial_events.py：webhook per-org 限流锁 ──────────────────────────────


async def test_rate_limit_lock_survives_second_event_loop_contention():
    """#1711-1：loop B 的限流竞争路径不得 RuntimeError（旧代码在此 500）。"""
    async def _contended():
        # 真竞争：手动持锁再打一次 _rate_limit，让它在锁上排队
        # （_rate_limit 的临界区内没有 await，不持锁就永远走快路径、
        # 根本不绑定 loop —— 那样测不出 loop-affinity）。
        lock = se._rl_lock()
        await lock.acquire()
        waiter = asyncio.create_task(se._rate_limit("org-1711"))
        await asyncio.sleep(0)  # waiter 推进到锁上排队
        lock.release()
        return await waiter

    assert await _contended() is True

    box = _run_in_new_loop(_contended)
    assert "error" not in box, (
        f"#1711: 跨事件循环竞争 webhook 限流锁报错: {box.get('error')!r}"
    )
    assert box["result"] is True


async def test_rate_limit_still_enforces_window_cap():
    """行为不变：窗口内第 _WEBHOOK_MAX_PER_WINDOW+1 次仍被拒（False）。"""
    org = "org-1711-cap"
    results = [await se._rate_limit(org) for _ in range(se._WEBHOOK_MAX_PER_WINDOW)]
    assert all(results)
    assert await se._rate_limit(org) is False
