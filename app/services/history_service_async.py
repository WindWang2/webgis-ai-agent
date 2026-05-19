"""Async chat conversation persistence service.

资源所有权（A2）规约：
- `user_id` 在 Conversation 上 nullable。新建会话时，如果调用方提供 user_id 就写入，
  匿名访问保持 NULL（与现有数据兼容）。
- `list_sessions(user_id)` 仅返回该用户的会话；user_id=None 视为匿名调用方，
  返回空（匿名用户没有列表入口，会话只能凭 session_id 直访）。
- `get_session(session_id, user_id)` / `delete_session(session_id, user_id)`：
  - 会话 user_id 为 NULL → 允许（旧匿名记录，知道 session_id 即能力令牌）
  - 会话 user_id == 调用方 user_id → 允许
  - 否则返回 None / 静默忽略，**统一 404 风格**避免泄露存在性。
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.db_model import Conversation, Message

logger = logging.getLogger(__name__)

MAX_SESSIONS = 1000


def _is_anonymous(user_id: Optional[str]) -> bool:
    """统一对外的『匿名调用方』判定。None 或字符串 'anonymous' 都视为匿名。"""
    return not user_id or user_id == "anonymous"


class AsyncHistoryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_conversation(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Conversation:
        import anyio
        owner = None if _is_anonymous(user_id) else user_id
        for attempt in range(3):
            try:
                stmt = select(Conversation).where(Conversation.id == session_id).options(selectinload(Conversation.messages))
                result = await self.db.execute(stmt)
                conv = result.scalar_one_or_none()
                if conv:
                    return conv

                conv = Conversation(
                    id=session_id,
                    user_id=owner,
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

    async def list_sessions(
        self,
        limit: int = MAX_SESSIONS,
        user_id: Optional[str] = None,
    ) -> list[Conversation]:
        """匿名调用方拿不到列表；认证用户只看自己的会话。"""
        if _is_anonymous(user_id):
            return []
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .options(selectinload(Conversation.messages))
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Conversation]:
        """带所有权检查的会话取回。

        - 会话 user_id IS NULL：视为旧匿名记录，知道 session_id 即可访问
        - 会话 user_id 已绑定：仅原用户可见
        - 不存在或越权：均返回 None（统一处理为 404，避免存在性泄露）
        """
        stmt = select(Conversation).where(Conversation.id == session_id).options(selectinload(Conversation.messages))
        result = await self.db.execute(stmt)
        conv = result.scalar_one_or_none()
        if conv is None:
            return None
        if conv.user_id is None:
            return conv
        if _is_anonymous(user_id):
            return None
        if conv.user_id != user_id:
            return None
        return conv

    async def delete_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> bool:
        """所有权检查后删除；返回是否真正删除（用于路由层决定 404 vs 204）。"""
        conv = await self.db.get(Conversation, session_id)
        if conv is None:
            return False
        if conv.user_id is not None:
            if _is_anonymous(user_id) or conv.user_id != user_id:
                return False
        await self.db.delete(conv)
        await self.db.commit()
        return True

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
