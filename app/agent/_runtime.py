"""AgentRuntime — manages ChatAgent instances + HTTP request handling.

Responsibilities:
- Create/cache/destroy ChatAgent instances per session_id
- Handle HTTP requests (streaming and non-streaming) via ChatAgent
- Wire up EventBus listeners for decision logging, SSE, etc.
- Feature flag: USE_NEW_AGENT environment variable
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from app.agent._event import EventBus
from app.agent._types import AgentState, ModelInfo
from app.agent.chat import ChatAgent

logger = logging.getLogger(__name__)

# Feature flag: enable new Agent system
USE_NEW_AGENT = os.getenv("USE_NEW_AGENT", "").lower() in ("true", "1", "yes")


class AgentRuntime:
    """Manages ChatAgent instances with LRU cache and per-session locks.

    Wraps existing ChatEngine, delegating actual conversation to ChatAgent
    while preserving all existing GIS-specific behavior (dedup, WS broadcast,
    GeoJSON ref, planner, decision log).
    """

    def __init__(
        self,
        chat_engine: Any,  # ChatEngine
        max_sessions: int = 200,
        max_locks: int = 200,
    ) -> None:
        self._chat_engine = chat_engine
        self._max_sessions = max_sessions
        self._max_locks = max_locks
        self._sessions: dict[str, ChatAgent] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ── Agent lifecycle ─────────────────────────────────────────

    async def get_or_create_agent(self, session_id: str) -> ChatAgent:
        """Get or create a ChatAgent for the given session_id."""
        if session_id in self._sessions:
            return self._sessions[session_id]

        # Evict old locks if at capacity
        if len(self._locks) > self._max_locks:
            evict_count = self._max_locks // 4
            for sid in list(self._locks.keys())[:evict_count]:
                lock = self._locks[sid]
                if not lock.locked():
                    self._locks.pop(sid, None)

        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            if session_id in self._sessions:
                return self._sessions[session_id]

            agent = ChatAgent(engine=self._chat_engine)
            agent._session_id = session_id
            agent._max_rounds = 60

            # Register default listeners
            self._register_default_listeners(agent, session_id)

            self._sessions[session_id] = agent
            return agent

    async def clear_session(self, session_id: str) -> bool:
        """Remove an agent and clean up session data."""
        if session_id in self._sessions:
            agent = self._sessions.pop(session_id)
            agent.reset()
        self._locks.pop(session_id, None)
        await self._chat_engine.clear_session(session_id)
        return True

    # ── HTTP request handlers ───────────────────────────────────

    async def handle_stream_request(self, message: str, session_id: Optional[str] = None,
                                     map_state: Optional[dict] = None,
                                     skill_name: Optional[str] = None,
                                     user_id: Optional[str] = None):
        """Handle streaming chat request via ChatAgent.

        Returns async generator of SSE event strings.
        """
        agent = await self.get_or_create_agent(session_id or "")
        async for event in agent.chat_stream(
            message=message,
            session_id=session_id,
            map_state=map_state,
            skill_name=skill_name,
            user_id=user_id,
        ):
            yield event

    async def handle_request(self, message: str, session_id: Optional[str] = None,
                              map_state: Optional[dict] = None,
                              skill_name: Optional[str] = None,
                              user_id: Optional[str] = None) -> dict:
        """Handle non-streaming chat request via ChatAgent.

        Returns dict with session_id and content.
        """
        agent = await self.get_or_create_agent(session_id or "")
        # For non-streaming, we collect all content from the stream
        content_parts = []
        async for event in agent.chat_stream(
            message=message,
            session_id=session_id,
            map_state=map_state,
            skill_name=skill_name,
            user_id=user_id,
        ):
            # Parse SSE events to extract content
            if event.startswith("event: token\ndata:"):
                try:
                    import json
                    data_line = event.split("data:", 1)[1].split("\n")[0].strip()
                    data = json.loads(data_line)
                    content_parts.append(data.get("content", ""))
                except Exception:
                    pass
            elif event.startswith("event: content\ndata:"):
                try:
                    import json
                    data_line = event.split("data:", 1)[1].split("\n")[0].strip()
                    data = json.loads(data_line)
                    if data.get("streaming_done"):
                        break
                except Exception:
                    pass

        return {
            "session_id": session_id or agent._session_id,
            "content": "".join(content_parts),
        }

    # ── Internal helpers ────────────────────────────────────────

    def _register_default_listeners(self, agent: ChatAgent, session_id: str) -> None:
        """Register default EventBus listeners for this agent."""
        async def persistence_listener(event: Any, signal: Optional[asyncio.Event]) -> None:
            try:
                event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")
                summary = {}
                if isinstance(event, dict):
                    if event.get("toolName"):
                        summary = {"tool": event.get("toolName"), "args": event.get("args", {})}
                    elif event.get("message"):
                        msg = event["message"]
                        summary = {"role": msg.get("role", "") if isinstance(msg, dict) else ""}
                if summary:
                    from app.services.session_data import session_data_manager
                    await session_data_manager.append_event(session_id, event_type, summary)
            except Exception:
                pass
        agent.subscribe(persistence_listener)
