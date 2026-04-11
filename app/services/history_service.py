"""Chat conversation persistence service."""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.db_model import Conversation, Message

logger = logging.getLogger(__name__)

MAX_SESSIONS = 1000


class HistoryService:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_conversation(self, session_id: str) -> Conversation:
        conv = self.db.get(Conversation, session_id)
        if conv:
            return conv
        conv = Conversation(
            id=session_id,
            title="新对话",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(conv)
        self.db.flush()       # assign PK without committing
        self._enforce_cap()   # prune within same transaction
        self.db.commit()
        self.db.refresh(conv)
        return conv

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_result=None,
        tool_call_id=None,
    ) -> None:
        msg = Message(
            conversation_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            tool_result=tool_result,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(msg)
        conv = self.db.get(Conversation, session_id)
        if conv:
            conv.updated_at = datetime.now(timezone.utc)
        self.db.commit()

    def update_title(self, session_id: str, title: str) -> None:
        conv = self.db.get(Conversation, session_id)
        if conv:
            conv.title = title[:200]
            self.db.commit()

    def list_sessions(self, limit: int = MAX_SESSIONS) -> list[Conversation]:
        return (
            self.db.query(Conversation)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .all()
        )

    def get_session(self, session_id: str) -> Optional[Conversation]:
        return self.db.get(Conversation, session_id)

    def delete_session(self, session_id: str) -> None:
        conv = self.db.get(Conversation, session_id)
        if conv:
            self.db.delete(conv)
            self.db.commit()

    def _enforce_cap(self) -> None:
        total = self.db.query(Conversation).count()
        if total <= MAX_SESSIONS:
            return
        overflow = total - MAX_SESSIONS
        oldest = (
            self.db.query(Conversation)
            .order_by(Conversation.updated_at.asc())
            .limit(overflow)
            .all()
        )
        for conv in oldest:
            self.db.delete(conv)
        # No commit here — caller commits
