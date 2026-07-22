"""Context compaction utilities for the new Agent system.

Provides token-aware conversation history compression to prevent
context window overflow during long agent loops.

Usage:
    from app.agent.harness.compaction import compact_context
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


async def compact_context(
    messages: list[dict],
    *,
    model_context_window: int = 65536,
    max_messages: int = 50,
    summarize_fn: Optional[Callable[[list[dict]], Awaitable[str]]] = None,
) -> list[dict]:
    """Compact conversation history if it exceeds limits.

    Strategy:
    1. If message count > max_messages, keep system + last N messages
    2. Optionally call summarize_fn to compress dropped messages into
       a summary message inserted after system prompt

    Args:
        messages: Full conversation history
        model_context_window: Token budget for context (informational)
        max_messages: Maximum messages to keep before compaction
        summarize_fn: Optional async function (old_messages) -> summary_string
            If provided, creates a summary of dropped messages

    Returns:
        Compacted message list
    """
    if len(messages) <= max_messages:
        return messages

    logger.info(f"[compaction] Compacting {len(messages)} messages to {max_messages}")

    # Keep system message + last N messages
    system_msg = None
    non_system = []
    for m in messages:
        if m.get("role") == "system" and system_msg is None:
            system_msg = m
        else:
            non_system.append(m)

    keep_count = max_messages - (1 if system_msg else 0)
    dropped = non_system[:-keep_count] if keep_count > 0 else non_system
    kept = non_system[-keep_count:] if keep_count > 0 else []

    result = []
    if system_msg:
        result.append(system_msg)

    # Optionally add summary of dropped messages
    if dropped and summarize_fn:
        try:
            summary = await summarize_fn(dropped)
            if summary:
                result.append({
                    "role": "system",
                    "content": f"[对话历史摘要]\n{summary}",
                })
        except Exception as e:
            logger.warning(f"[compaction] Summarization failed: {e}")

    result.extend(kept)
    logger.info(f"[compaction] Compacted to {len(result)} messages")
    return result
