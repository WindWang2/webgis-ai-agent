"""StoryMap 会话编译服务壳（ADR-0196 §5.1）——装载会话消息后委托 lib 编译。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.lib.storymap.story_compiler import compile_story_map
from app.models.db_model import Conversation, Message

# 与会话详情路由同款页幅：默认覆盖典型会话的完整历史。
_SESSION_MESSAGE_LIMIT = 200


async def compile_for_session(db: AsyncSession, conv: Conversation) -> object:
    """会话消息 → StoryMapSpec（消息降级路径；trace 编排由调用方直供）。"""
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id,
               Message.role.in_(("user", "assistant")))
        .order_by(Message.created_at.asc(), Message.id.asc())
        .limit(_SESSION_MESSAGE_LIMIT)
    )
    messages = [
        {"role": m.role, "content": m.content}
        for m in result.scalars().all()
    ]
    return compile_story_map(messages=messages, session_id=str(conv.id))


__all__ = ["compile_for_session"]
