"""Retained, observed policy abort tasks and bounded bridge pool settings."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Coroutine

logger = logging.getLogger(__name__)
_pending_aborts: set[asyncio.Task[object]] = set()


def schedule_abort(coro: Coroutine[object, object, object]) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        raise
    task = loop.create_task(coro)
    _pending_aborts.add(task)
    task.add_done_callback(_abort_done)


def _abort_done(task: asyncio.Task[object]) -> None:
    _pending_aborts.discard(task)
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.warning("Pi policy abort failed", exc_info=(type(error), error, error.__traceback__))


def bridge_pool_size() -> int:
    raw = os.getenv("PI_BRIDGE_POOL_SIZE", "1")
    try:
        size = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid PI_BRIDGE_POOL_SIZE; using1")
        return 1
    if not 1 <= size <= 64:
        logger.warning("PI_BRIDGE_POOL_SIZE outside1..64; clamping")
    return min(64, max(1, size))
