"""Periodic authorization watchdog, independent of inbound WebSocket frames."""
from __future__ import annotations
import asyncio
import logging
from collections.abc import Awaitable, Callable
from starlette.websockets import WebSocket

_logger = logging.getLogger(__name__)


async def watch_authorization(websocket: WebSocket, check: Callable[[], Awaitable[bool]], *, interval: float = 60,
                              on_invalid: Callable[[], None] | None = None,
                              closing: asyncio.Event | None = None,
                              stopped: asyncio.Event | None = None) -> None:
    while True:
        delay = max(0.01, interval)
        if stopped is None:
            await asyncio.sleep(delay)
        else:
            try:
                await asyncio.wait_for(stopped.wait(), delay)
                return
            except asyncio.TimeoutError:
                _logger.debug("WebSocket authority refresh interval elapsed")
        try:
            valid = await check()
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.warning("WebSocket authority refresh failed; closing connection", exc_info=True)
            valid = False
        if stopped is not None and stopped.is_set():
            return
        if valid:
            continue
        if closing is not None:
            closing.set()
        if on_invalid is not None:
            try:
                on_invalid()
            except Exception:
                _logger.warning("WebSocket detach callback failed", exc_info=True)
        try:
            await websocket.close(code=4401, reason="Connection authority revoked")
        except Exception:
            _logger.debug("WebSocket already closed during authority refresh", exc_info=True)
        return


async def stop_watchdog(task: asyncio.Task[None], *, closing: asyncio.Event | None = None,
                        stopped: asyncio.Event | None = None) -> None:
    if stopped is not None:
        stopped.set()
    elif closing is None or not closing.is_set():
        task.cancel()
    try:
        # Permit an in-flight DB check/close frame to finish normally. Cancelling
        # SQLAlchemy connection teardown mid-check can leak shielded cleanup.
        await asyncio.wait_for(asyncio.shield(task), 2)
    except asyncio.CancelledError:
        _logger.debug("WebSocket authority watchdog cancelled")
    except asyncio.TimeoutError:
        task.cancel()
        _logger.warning("WebSocket authority watchdog teardown timed out")
    except Exception:
        _logger.warning("WebSocket authority watchdog failed", exc_info=True)
