"""Protocol and dataclass definition for HistoryStore deepen seam."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class HistoryContext:
    session_id: str
    owner_token: Optional[str] = None
    user_id: Optional[str] = None
    llm_messages: list[dict[str, Any]] = field(default_factory=list)
    raw_conversation: Any = None


@runtime_checkable
class HistoryStoreProtocol(Protocol):
    """Protocol defining the deep seam interface for conversation persistence."""

    async def load_context(
        self,
        session_id: str,
        owner_token: Optional[str] = None,
        user_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> HistoryContext:
        """Load conversation context, validate owner token (SEC-08), and return converted LLM messages."""
        ...

    async def commit_interaction(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        metadata: Optional[dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Atomically record user query and assistant response."""
        ...

    async def delete_history(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        owner_token: Optional[str] = None,
    ) -> bool:
        """Delete conversation history for session."""
        ...

    async def summarize_session_title(
        self,
        session_id: str,
    ) -> Optional[str]:
        """Summarize and update session title."""
        ...
