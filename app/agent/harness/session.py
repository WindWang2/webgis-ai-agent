"""Session harness — session persistence helpers for the new Agent system.

Provides thin wrappers around app/services/history_service_async.py and
app/services/session_data.py, maintaining the single-direction dependency:
app/agent/harness/ → app/services/

Usage:
    from app.agent.harness.session import load_messages, save_message, clear_session
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def load_messages(session_id: str, user_id: Optional[str] = None) -> list[dict]:
    """Load conversation history from DB.

    Returns list of LLM-compatible message dicts.
    Inserts system prompt if not present.
    """
    messages: list[dict] = []
    try:
        from app.tools._utils import async_db_session
        from app.services.history_service_async import AsyncHistoryService
        from app.agent.harness.system_prompt import SYSTEM_PROMPT
        async with async_db_session() as db:
            conv = await AsyncHistoryService(db).get_or_create_conversation(session_id, user_id=user_id)
            if conv and conv.messages:
                sorted_msgs = sorted(conv.messages, key=lambda x: x.id)
                for m in sorted_msgs:
                    d = {"role": m.role, "content": m.content or ""}
                    if m.reasoning_content:
                        d["reasoning_content"] = m.reasoning_content
                    if m.tool_calls:
                        try:
                            d["tool_calls"] = m.tool_calls if isinstance(m.tool_calls, list) else json.loads(m.tool_calls)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if m.tool_call_id:
                        d["tool_call_id"] = m.tool_call_id
                    messages.append(d)
    except Exception as e:
        logger.warning(f"[harness.session] Failed to load conversation {session_id}: {e}")

    has_system = any(m.get("role") == "system" for m in messages)
    if not has_system:
        from app.agent.harness.system_prompt import SYSTEM_PROMPT
        from app.tools.skills import list_md_skills
        skills = list_md_skills()
        if skills:
            skill_text = "\n".join(f"- **{s['name']}**: {s['description']}" for s in skills)
        else:
            skill_text = "（暂无预置技能）"
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT.format(skill_list=skill_text)})
    return messages


async def save_message(
    session_id: str,
    role: str,
    content: str,
    tool_calls: Optional[list] = None,
    tool_result: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    reasoning_content: Optional[str] = None,
) -> None:
    """Save a message to DB asynchronously."""
    try:
        if tool_result is not None and isinstance(tool_result, str) and len(tool_result) > 100000:
            tool_result = tool_result[:100000] + "...[truncated]"
        from app.tools._utils import async_db_session
        from app.services.history_service_async import AsyncHistoryService
        async with async_db_session() as db:
            await AsyncHistoryService(db).save_message(
                session_id, role, content, tool_calls, tool_result, tool_call_id, reasoning_content
            )
    except Exception as e:
        logger.error(f"[harness.session] Failed to save message: {e}")


async def clear_session(session_id: str, user_id: Optional[str] = None) -> bool:
    """Delete session from DB. Returns True if deleted."""
    deleted = False
    try:
        from app.tools._utils import async_db_session
        from app.services.history_service_async import AsyncHistoryService
        async with async_db_session() as db:
            deleted = await AsyncHistoryService(db).delete_session(session_id, user_id=user_id)
    except Exception as e:
        logger.warning(f"[harness.session] Failed to delete session {session_id}: {e}")
    return deleted
