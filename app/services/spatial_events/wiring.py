"""app 装配缝：session→org 解析器 + worker 生命周期辅助。

被 main.py lifespan 调用（一次）；测试可直接调用 reset。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _resolve_org_for_session(session_id: str) -> str:
    """session_id → org_id（Conversation.user → User.org，organizations.id 字符串）。

    解析失败/匿名会话 → ""（调用方按"无 org 印章不入账"处理——租户红线）。
    """
    if not session_id:
        return ""
    try:
        from sqlalchemy import select

        from app.models.db_model import Conversation, User
        from app.tools._utils import async_db_session

        async with async_db_session() as db:
            org = (
                await db.execute(
                    select(User.org_id)
                    .join(Conversation, Conversation.user_id == User.id)
                    .where(Conversation.id == str(session_id))
                )
            ).scalar_one_or_none()
        return str(org) if org is not None else ""
    except Exception as e:  # noqa: BLE001 — 解析失败诚实返回空
        logger.debug("[spatial_events] org resolve failed: %s", e)
        return ""


def install_session_org_resolver() -> None:
    from app.services.spatial_events import adapters

    adapters.set_org_resolver(_resolve_org_for_session)


def reset_session_org_resolver() -> None:
    from app.services.spatial_events import adapters

    adapters.set_org_resolver(None)
