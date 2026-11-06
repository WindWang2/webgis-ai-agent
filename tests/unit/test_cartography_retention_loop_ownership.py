"""Retention tasks, throttles and observations belong to their event loop."""
import asyncio
import gc
import weakref

from app.services import cartography_metrics_store as store


def _close_loop(loop):
    async def drain():
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    loop.run_until_complete(drain())
    loop.close()


def test_foreign_loop_pending_sweep_neither_blocks_nor_joins_current_loop(monkeypatch):
    first, second = asyncio.new_event_loop(), asyncio.new_event_loop()
    started = []
    releases = {}

    async def sweep():
        loop = asyncio.get_running_loop()
        release = releases.setdefault(loop, asyncio.Event())
        started.append(loop)
        await release.wait()
        return {"expired_runs": len(started), "overflow_runs": 0}

    monkeypatch.setattr(store, "maybe_enforce_retention", sweep)

    async def start():
        store.schedule_retention()
        await asyncio.sleep(0)

    async def current_loop():
        assert await store.flush_retention() is None
        await start()
        assert started == [first, second]
        releases[second].set()
        assert await store.flush_retention() == {"expired_runs": 2, "overflow_runs": 0}
        assert not releases[first].is_set()

    try:
        first.run_until_complete(start())
        second.run_until_complete(current_loop())
    finally:
        _close_loop(first)
        _close_loop(second)


def test_completed_loop_throttle_and_result_do_not_leak_to_new_loop(monkeypatch):
    calls = []

    async def sweep():
        calls.append(asyncio.get_running_loop())
        return {"expired_runs": len(calls), "overflow_runs": 0}

    monkeypatch.setattr(store, "maybe_enforce_retention", sweep)

    async def run(expected):
        assert await store.flush_retention() is None
        store.schedule_retention()
        assert await store.flush_retention() == {"expired_runs": expected, "overflow_runs": 0}
        store.schedule_retention()
        assert len(calls) == expected  # the same loop still observes its throttle

    for expected in (1, 2):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run(expected))
        finally:
            _close_loop(loop)


def test_completed_sweeps_do_not_retain_closed_loops_or_tasks(monkeypatch):
    references = []

    async def sweep():
        await asyncio.sleep(0)
        return {"expired_runs": 0, "overflow_runs": 0}

    monkeypatch.setattr(store, "maybe_enforce_retention", sweep)

    async def run():
        store.schedule_retention()
        task = next(task for task in asyncio.all_tasks() if task is not asyncio.current_task())
        references.append((weakref.ref(asyncio.get_running_loop()), weakref.ref(task)))
        await store.flush_retention()

    for _ in range(8):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            _close_loop(loop)
    del loop
    gc.collect()
    assert all(loop_ref() is None and task_ref() is None for loop_ref, task_ref in references)
