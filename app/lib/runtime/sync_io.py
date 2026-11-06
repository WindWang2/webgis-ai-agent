"""Diagnostic boundary for synchronous storage entrypoints."""
from __future__ import annotations
import asyncio
import logging
_logger = logging.getLogger(__name__)


def warn_if_event_loop(operation: str) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    _logger.warning("%s performs synchronous database I/O; call through asyncio.to_thread", operation)
