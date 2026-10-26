"""G3D05 —— StoryMap 会话资产服务（保存/装载，ADR-0275 D6）。

薄 IO 壳：校验/序列化在 lib（spec 版本门 + 载荷有界性），本模块只做
会话作用域读写与 revision 单调递增。所有权由路由层 require_owned_session
守卫（与会话消息同生命周期）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.lib.storymap.spec import StoryMapSpec
from app.models.db_model import Conversation
from app.models.story_map_asset import StoryMapAsset

#: spec 序列化上限（防 5MB 叙事 payload 挤库；正常 spec <<64KB）。
MAX_STORY_SPEC_BYTES = 512 * 1024


async def save_story_spec(
    db: AsyncSession, conv: Conversation, spec: StoryMapSpec
) -> Dict[str, Any]:
    """保存/覆盖会话 StoryMap 资产（revision = 旧值+1）。

    返回 {"revision", "title"}；spec 非法或超界 ValueError（路由映射 422）。
    """
    payload = spec.model_dump(mode="json", exclude_none=False)
    text = json.dumps(payload, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_STORY_SPEC_BYTES:
        raise ValueError(
            f"story spec exceeds {MAX_STORY_SPEC_BYTES} bytes budget")
    # R1-P1c（评审修复）：双标签并发首存 —— SELECT-then-INSERT 竞态窗口
    # 内两笔都走 INSERT，后者撞 conversation 唯一约束炸 IntegrityError →
    # 裸 500。捕获后回滚并按"行已存在"重走 UPDATE+1 路径一次（SQLite/
    # PostgreSQL 同语义；不加锁不长事务；每次保存仍单调 +1）。
    from sqlalchemy.exc import IntegrityError

    title = (spec.metadata.title or "StoryMap 专报")[:255]
    row = await db.scalar(
        select(StoryMapAsset).where(StoryMapAsset.conversation_id == conv.id)
    )
    if row is not None:
        row.title = title
        row.spec = text
        row.revision = int(row.revision or 1) + 1
        await db.flush()
        return {"revision": int(row.revision), "title": row.title}
    row = StoryMapAsset(
        conversation_id=conv.id, title=title, spec=text, revision=1)
    db.add(row)
    try:
        await db.flush()
        return {"revision": 1, "title": row.title}
    except IntegrityError:
        await db.rollback()
    # 竞态输家：行已被并发首存 —— 重读并按保存语义 +1。
    row = await db.scalar(
        select(StoryMapAsset).where(StoryMapAsset.conversation_id == conv.id)
    )
    if row is None:
        raise
    row.title = title
    row.spec = text
    row.revision = int(row.revision or 1) + 1
    await db.flush()
    return {"revision": int(row.revision), "title": row.title}


async def load_story_spec(
    db: AsyncSession, conv: Conversation
) -> Optional[Dict[str, Any]]:
    """读会话最新 StoryMap 资产；无资产 → None（调用方映射 404）。"""
    row = await db.scalar(
        select(StoryMapAsset).where(StoryMapAsset.conversation_id == conv.id)
    )
    if row is None:
        return None
    try:
        spec = json.loads(row.spec)
    except json.JSONDecodeError:
        # 存量损坏：不伪造默认 —— 返回带 typed 错误标记的载荷（调用方
        # 显式告知"资产损坏"，可重新 compile 覆盖恢复）。
        return {
            "schema_version": "corrupt",
            "asset_revision": int(row.revision or 0),
            "error": "story_asset_corrupt",
        }
    if isinstance(spec, dict):
        spec.setdefault("asset_revision", int(row.revision or 0))
    return spec


__all__ = ["save_story_spec", "load_story_spec", "MAX_STORY_SPEC_BYTES"]
