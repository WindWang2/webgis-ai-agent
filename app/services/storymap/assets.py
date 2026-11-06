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
    from sqlalchemy import update, func
    from sqlalchemy.exc import IntegrityError
    title = (spec.metadata.title or "StoryMap 专报")[:255]
    # UPDATE obtains the row/write lock and increments at the database. It also
    # begins SQLite's physical write transaction before the insert savepoint.
    for attempt in range(2):
        result = await db.execute(update(StoryMapAsset).where(
            StoryMapAsset.conversation_id == conv.id,
        ).values(title=title, spec=text, revision=func.coalesce(StoryMapAsset.revision, 0) + 1)
          .execution_options(synchronize_session=False))
        if result.rowcount:
            row = (await db.execute(select(StoryMapAsset.revision, StoryMapAsset.title).where(
                StoryMapAsset.conversation_id == conv.id,
            ))).one()
            return {"revision": int(row[0]), "title": row[1]}
        try:
            async with db.begin_nested():
                db.add(StoryMapAsset(conversation_id=conv.id, title=title, spec=text, revision=1))
                await db.flush()
            return {"revision": 1, "title": title}
        except IntegrityError:
            if attempt:
                raise
            # Only the insert savepoint rolled back. Caller-owned changes survive.
    raise RuntimeError("story asset revision could not be persisted")



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
