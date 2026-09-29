"""Session-ownership guard (SEC-08) — services-side home for the
``verify_session_owner`` / ``require_owned_session`` pair.

#1542：这两个守卫原先住在 ``app/core/auth.py``，而它们对外部的唯一依赖是
``AsyncHistoryService.get_session_meta``（Conversation 元数据行的所有权判定，
#525 降载查询）。core → services 是层级倒置，且污染 mypy ratchet 区
（files=app/core）的类型边界。本模块把守卫搬到 services 侧（services → core
方向合法），core/auth.py 不再出现任何 ``app.services`` import。

迁移契约（无行为变更）：

- 函数签名、FastAPI ``Depends`` 接线（``get_async_db`` /
  ``get_current_user_optional`` / ``get_owner_token``）、404 语义逐字保留；
- 调用方只改 import 来源（app.core.auth → 本模块）；
- SEC-08 manifest 锚点（app/lib/quality/security_manifest.py）随实现同步
  迁移；monkeypatch 目标相应变为 ``app.services.auth_history_bridge.
  verify_session_owner``（经 require_owned_session 的调用在运行期从本模块
  全局命名空间解析，语义与原先经 app.core.auth 解析一致）。
"""
from typing import Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional, get_owner_token
from app.core.database import get_async_db
from app.models.db_model import Conversation
from app.services.history_service_async import AsyncHistoryService

__all__ = ["verify_session_owner", "require_owned_session"]


async def verify_session_owner(
    db: AsyncSession,
    session_id: str,
    user_id: Optional[str] = None,
    owner_token: Optional[str] = None,
) -> Conversation:
    """跨租户隔离守卫 (S31/S32/SEC-08): 验证 session_id 是否存在且属于 user_id / owner_token。

    若不存在或无权访问，统一抛出 HTTPException(404, "Session not found")。
    返回 Conversation ORM 实例。
    """
    # #525: guard uses the metadata-only query — the ~30 guard call sites
    # (incl. the 3s task-center poll) must not pay O(messages) full-row loads.
    conv = await AsyncHistoryService(db).get_session_meta(
        session_id, user_id=user_id, owner_token=owner_token
    )
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return conv


async def require_owned_session(
    session_id: str,
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Conversation:
    """FastAPI 依赖注入：要求当前请求的 session_id 属于当前用户 (或匹配 owner_token)。

    校验成功后直接注入并返回 `Conversation` 对象。
    """
    user_id = _user.get("user_id") if isinstance(_user, dict) else None
    return await verify_session_owner(
        db=db,
        session_id=session_id,
        user_id=user_id,
        owner_token=owner_token,
    )
