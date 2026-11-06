"""One primary session writer across Pi workers and specialist Swarm runs.

This admission lock is separate from short session-data mutation locks: tools
and child receipts must still acquire those while their parent writer runs.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol


class _Lock(Protocol):
    @property
    def lost(self) -> bool: ...
    async def acquire(self) -> object: ...
    def release(self) -> None: ...


class WriterLease:
    def __init__(self, lock: _Lock, owner: asyncio.Task[object] | None, on_lost: Callable[[], None] | None = None) -> None:
        self._lock = lock
        self._released = False
        self._watcher = asyncio.create_task(self._watch(owner, on_lost))

    async def _watch(self, owner: asyncio.Task[object] | None, on_lost: Callable[[], None] | None) -> None:
        try:
            while not self._released:
                await asyncio.sleep(0.05)
                if self._lock.lost:
                    if on_lost is not None:
                        on_lost()
                    if owner is not None and not owner.done():
                        owner.cancel("session writer ownership lost")
                    return
        except asyncio.CancelledError:
            return

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._watcher.cancel()
            self._lock.release()


async def acquire_writer(
    session_id: str, *, timeout_s: float,
    owner: asyncio.Task[object] | None = None,
    on_lost: Callable[[], None] | None = None,
) -> WriterLease:
    from app.services.distributed_lock import session_lock_registry

    lock = session_lock_registry.lock(
        f"pi-writer:{session_id}", acquire_timeout_s=timeout_s,
        fail_on_degraded=True, fail_on_lost=True,
    )
    await lock.acquire()
    return WriterLease(lock, owner or asyncio.current_task(), on_lost)
