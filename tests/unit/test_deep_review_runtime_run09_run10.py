"""RUN-09 / RUN-10: hard-cancel resource release.

* RUN-09 — ``_MultiSlotAcquire.__aenter__`` waits slot-by-slot; a
  ``BaseException`` (e.g. CancelledError) raised while waiting for the 2nd
  slot must release the already-held 1st slot (``async with`` never calls
  ``__aexit__`` when ``__aenter__`` raises).
* RUN-10 — ``GovernorDispatchAdapter.run`` only caught ``Exception``: a hard
  cancel (``CancelledError``) escaped with a live governor reservation and
  leaked the heavy channel. ``governor.complete`` must always run.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.governor.config import GovernorMode
from app.services.governor.dispatch_adapter import GovernorDispatchAdapter
from app.services.tool_dispatch_service import _MultiSlotAcquire


# ── RUN-09 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multislot_cancel_while_waiting_releases_held_slots():
    sem = asyncio.Semaphore(1)
    acquire = _MultiSlotAcquire(sem, slots=2)

    task = asyncio.create_task(acquire.__aenter__())
    await asyncio.sleep(0)  # let the 1st acquire land; 2nd blocks
    assert sem.locked() is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The held permit must be back — semaphore is usable again.
    assert sem.locked() is False
    await asyncio.wait_for(sem.acquire(), timeout=0.5)
    sem.release()


@pytest.mark.asyncio
async def test_multislot_normal_exit_releases_every_slot():
    sem = asyncio.Semaphore(2)
    async with _MultiSlotAcquire(sem, slots=2):
        assert sem.locked() is True

    assert sem.locked() is False
    await asyncio.wait_for(sem.acquire(), timeout=0.5)
    await asyncio.wait_for(sem.acquire(), timeout=0.5)
    sem.release()
    sem.release()


# ── RUN-10 ───────────────────────────────────────────────────────────────


class _Decision:
    allowed = True
    reasons: list = []
    suggestions: list = []
    degrade_hint = {}


class _Config:
    mode = GovernorMode.ENFORCE


class _FakeGovernor:
    def __init__(self):
        self.config = _Config()
        self.completed: list = []

    async def admit_and_reserve(self, demand):
        return _Decision(), object(), object()

    async def complete(self, reservation, ticket, *, usage=None, actual=None, estimate=None):
        self.completed.append({"usage": usage, "actual": actual})


@pytest.mark.asyncio
async def test_hard_cancel_completes_reservation_as_cancelled():
    governor = _FakeGovernor()
    adapter = GovernorDispatchAdapter(governor, metadata_fn=lambda _n: {"cost": "light"})

    async def cancelled_inner():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await adapter.run(
            tool_name="raster_analysis",
            tool_args={},
            session_id="sess-cancel",
            dispatch_inner=cancelled_inner,
        )

    assert len(governor.completed) == 1
    assert governor.completed[0]["usage"].status == "cancelled"


@pytest.mark.asyncio
async def test_exception_still_completes_reservation_as_failed():
    governor = _FakeGovernor()
    adapter = GovernorDispatchAdapter(governor, metadata_fn=lambda _n: {"cost": "light"})

    async def failing_inner():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await adapter.run(
            tool_name="raster_analysis",
            tool_args={},
            session_id="sess-fail",
            dispatch_inner=failing_inner,
        )

    assert len(governor.completed) == 1
    assert governor.completed[0]["usage"].status == "failed"


@pytest.mark.asyncio
async def test_success_still_completes_reservation():
    governor = _FakeGovernor()
    adapter = GovernorDispatchAdapter(governor, metadata_fn=lambda _n: {"cost": "light"})

    async def ok_inner():
        return {"success": True}

    result = await adapter.run(
        tool_name="buffer_layer",
        tool_args={},
        session_id="sess-ok",
        dispatch_inner=ok_inner,
    )

    assert result == {"success": True}
    assert len(governor.completed) == 1
    assert governor.completed[0]["usage"].status == "completed"
