"""#1108 — CancelledError must not permanently leak PiBridge._lock.

Disconnect storms can re-deliver ``CancelledError`` while ``stream_prompt`` /
``prompt`` finally awaits Redis unregister (or while register runs after
acquire but before the main try). A bare await lets CancelledError skip
``self._lock.release()``, hanging the singleton bridge for every session.

This module installs process-wide guards:

1. ``unregister_active_pi_turn`` is wrapped with ``asyncio.shield`` +
   ``wait_for`` (same discipline as abort-on-disconnect in agent_pi_bridge).
2. ``stream_prompt`` / ``prompt`` are wrapped so that if the lock is still
   held when the turn exits for any reason (including cancel during register
   outside the main try), it is released exactly once.

Import this module once at app startup (see ``app.main``). Tests may call
``install()`` explicitly; it is idempotent.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Optional

logger = logging.getLogger(__name__)

_installed = False


def install() -> None:
    """Idempotently wrap PiBridge turn entrypoints + unregister for #1108."""
    global _installed
    if _installed:
        return
    import app.agent_pi_bridge as bridge

    _wrap_unregister(bridge)
    _wrap_stream_prompt(bridge)
    _wrap_prompt(bridge)
    _installed = True
    logger.info("[PiBridge#1108] cancel-safety guards installed")


def _wrap_unregister(bridge: Any) -> None:
    orig = bridge.unregister_active_pi_turn

    async def _shielded(session_id: str, turn_id: str) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(orig(session_id, turn_id)), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[PiBridge#1108] unregister-on-cleanup failed (session=%s): %s",
                session_id,
                e,
            )

    bridge.unregister_active_pi_turn = _shielded


def _release_if_held(lock: asyncio.Lock) -> None:
    if lock.locked():
        try:
            lock.release()
        except RuntimeError:
            # Already released by the original finally — benign race.
            pass


def _wrap_stream_prompt(bridge: Any) -> None:
    orig = bridge.PiBridge.stream_prompt

    async def _guarded(
        self: Any,
        message: str,
        session_id: Optional[str] = None,
        cartography_context: Optional[str] = None,
        on_turn_result: Any = None,
        env_block: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        try:
            async for item in orig(
                self,
                message,
                session_id=session_id,
                cartography_context=cartography_context,
                on_turn_result=on_turn_result,
                env_block=env_block,
            ):
                yield item
        finally:
            # If CancelledError skipped the original finally's release (register
            # outside try, or bare unregister await), recover the invariant.
            _release_if_held(self._lock)

    bridge.PiBridge.stream_prompt = _guarded


def _wrap_prompt(bridge: Any) -> None:
    orig = bridge.PiBridge.prompt

    async def _guarded(
        self: Any,
        message: str,
        session_id: Optional[str] = None,
        cartography_context: Optional[str] = None,
        env_block: Optional[str] = None,
    ) -> dict:
        try:
            return await orig(
                self,
                message,
                session_id=session_id,
                cartography_context=cartography_context,
                env_block=env_block,
            )
        finally:
            _release_if_held(self._lock)

    bridge.PiBridge.prompt = _guarded
