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
        {"role": m.role, "content": m.content or ""}
        for m in result.scalars().all()
    ]
    # G3D05 v1.1：会话 mapspec 有 raster-dem 源才投影叙事场景档
    #（fail-closed —— 无高程证据不虚构立体叙事）。
    scene_enabled = False
    try:
        from app.services.mapspec.store import mapspec_store_instance

        stored = await mapspec_store_instance.get_mapspec(str(conv.id))
        sources = stored.get("sources") if isinstance(stored, dict) else None
        scene_enabled = isinstance(sources, dict) and any(
            isinstance(src, dict) and src.get("type") == "raster-dem"
            for src in sources.values()
        )
    except Exception:  # noqa: BLE001 — 证据探测失败按无证据处理
        scene_enabled = False
    return compile_story_map(
        messages=messages, session_id=str(conv.id), scene_enabled=scene_enabled)


__all__ = ["compile_for_session"]
