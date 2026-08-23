"""会话所有权查询服务（E-2 / #893 分层收口）。

`lookup_session_owner_token` 原先定义在 api/routes/raster.py，被
services/tools 层反向 import（api→services→api 隐式环）。所有权查询是
纯 DB 读取，属 service 层职责，下沉至此；路由层保留 re-export 兼容。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from app.models.db_model import Conversation
from app.tools._utils import async_db_session


async def lookup_session_owner_token(session_id: str) -> Optional[str]:
  """The session's anonymous owner_token, if any (for URL minting in tools).

  Authenticated sessions have no owner_token (cookie/bearer auth covers them);
  token-less legacy anonymous sessions need none. Only token-bearing anonymous
  sessions require the query-parameter form on the URLs they hand to MapLibre.
  """
  async with async_db_session() as db:
    row = (
        await db.execute(select(Conversation.owner_token).where(Conversation.id == session_id))
    ).scalar_one_or_none()
    return row if isinstance(row, str) else None
