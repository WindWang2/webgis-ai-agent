"""Async chat conversation persistence service."""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.db_model import Conversation, Message

logger = logging.getLogger(__name__)

MAX_SESSIONS = 1000


class AsyncHistoryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_conversation(self, session_id: str) -> Conversation:
        import anyio
        for attempt in range(3):
            try:
                stmt = select(Conversation).where(Conversation.id == session_id).options(selectinload(Conversation.messages))
                result = await self.db.execute(stmt)
                conv = result.scalar_one_or_none()
                if conv:
                    return conv

                conv = Conversation(
                    id=session_id,
                    title="新对话",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                self.db.add(conv)
                await self.db.flush()
                await self._enforce_cap()
                await self.db.commit()
                # 重新查询以确保加载了关系
                result = await self.db.execute(stmt)
                return result.scalar_one()
            except Exception as e:
                if "locked" in str(e).lower() and attempt < 2:
                    await anyio.sleep(0.1 * (attempt + 1))
                    await self.db.rollback()
                    continue
                raise

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls=None,
        tool_result=None,
        tool_call_id=None,
        reasoning_content: Optional[str] = None,
    ) -> None:
        import anyio
        for attempt in range(3):
            try:
                msg = Message(
                    conversation_id=session_id,
                    role=role,
                    content=content,
                    reasoning_content=reasoning_content,
                    tool_calls=tool_calls,
                    tool_call_id=tool_call_id,
                    tool_result=tool_result,
                    created_at=datetime.now(timezone.utc),
                )
                self.db.add(msg)
                conv = await self.db.get(Conversation, session_id)
                if conv:
                    conv.updated_at = datetime.now(timezone.utc)
                await self.db.commit()
                return
            except Exception as e:
                if "locked" in str(e).lower() and attempt < 2:
                    await anyio.sleep(0.1 * (attempt + 1))
                    await self.db.rollback()
                    continue
                raise

    async def update_title(self, session_id: str, title: str) -> None:
        conv = await self.db.get(Conversation, session_id)
        if conv:
            conv.title = title[:200]
            await self.db.commit()

    async def list_sessions(self, limit: int = MAX_SESSIONS) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .order_by(Conversation.updated_at.desc())
            .options(selectinload(Conversation.messages))
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_session(self, session_id: str) -> Optional[Conversation]:
        stmt = select(Conversation).where(Conversation.id == session_id).options(selectinload(Conversation.messages))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_session(self, session_id: str) -> None:
        conv = await self.db.get(Conversation, session_id)
        if conv:
            await self.db.delete(conv)
            await self.db.commit()

    async def _enforce_cap(self) -> None:
        total_result = await self.db.execute(select(func.count()).select_from(Conversation))
        total = total_result.scalar_one()
        if total <= MAX_SESSIONS:
            return
        overflow = total - MAX_SESSIONS
        stmt = (
            select(Conversation)
            .order_by(Conversation.updated_at.asc())
            .limit(overflow)
        )
        result = await self.db.execute(stmt)
        oldest = result.scalars().all()
        for conv in oldest:
            await self.db.delete(conv)
        # No commit here — caller commits
