"""G37：会话模型 preference 的持久化与 CAS（WP03）。

复用既有 Conversation 持久体系（键 = conversation id，不引入第三个
session id 空间）。owner 归属校验在路由层完成（require_owned_session 同
款）；本模块只做 preference 语义：

- ``inherit``（默认）：继承管理员基础配置（model_config 解析的主模型）；
- ``explicit``：显式引用目录项 identity + 可选思考档；
- ``revision`` CAS：并发选择（双标签）以 expected_revision 竞争，后到者
  拿到明确冲突（RevisionConflict），绝不静默丢更新。

跨 session 修改（conversation 不存在）与 preference 目标不在当前目录时
由调用层（service/route）拒绝或重验证 —— 本模块不猜默认。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_model import ChatSessionModelPref

logger = logging.getLogger(__name__)

THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


class RevisionConflict(Exception):
    """CAS 竞争失败：expected_revision 与库中当前值不一致。"""


class PreferenceNotFoundError(Exception):
    """目标会话的 preference 行不存在（新会话尚未写过）。"""


@dataclass(frozen=True)
class SessionModelPref:
    """preference 的值语义快照（DB 行的无 ORM 投影）。"""

    mode: str                    # inherit | explicit
    catalog_item_id: Optional[str]
    thinking_level: Optional[str]
    session_token_cap: Optional[int]
    revision: int

    @property
    def is_explicit(self) -> bool:
        return self.mode == "explicit"


_INHERIT = SessionModelPref(
    mode="inherit", catalog_item_id=None, thinking_level=None,
    session_token_cap=None, revision=0,
)


def inherit_pref() -> SessionModelPref:
    """「未写过 preference」的语义等价物：继承默认（revision=0 表示无行）。"""
    return _INHERIT


async def get_pref(db: AsyncSession, conversation_id: str) -> SessionModelPref:
    """读取 preference（无行 = inherit 语义，revision 0）。"""
    row = await db.get(ChatSessionModelPref, conversation_id)
    if row is None:
        return _INHERIT
    return SessionModelPref(
        mode=row.mode or "inherit",
        catalog_item_id=row.catalog_item_id,
        thinking_level=row.thinking_level,
        session_token_cap=row.session_token_cap,
        revision=int(row.revision or 1),
    )


async def set_pref(
    db: AsyncSession,
    conversation_id: str,
    *,
    mode: str,
    catalog_item_id: Optional[str] = None,
    thinking_level: Optional[str] = None,
    session_token_cap: Optional[int] = None,
    expected_revision: int,
) -> SessionModelPref:
    """写入 preference（CAS）。

    - 无行 + expected_revision=0 → INSERT（首写）；
    - 有行 + expected_revision=当前 revision → UPDATE ... revision+1；
    - 不匹配 → RevisionConflict（携带当前值供 UI 刷新后重试）。

    thinking_level 只接受已知档位词（实际生效由 turn 期 get_state 回读决
    定，requested/effective 分开保存）。
    """
    if mode not in ("inherit", "explicit"):
        raise ValueError(f"invalid mode: {mode!r}")
    if mode == "explicit" and not catalog_item_id:
        raise ValueError("explicit preference requires catalog_item_id")
    if thinking_level is not None and thinking_level not in THINKING_LEVELS:
        raise ValueError(f"invalid thinking level: {thinking_level!r}")

    current = await get_pref(db, conversation_id)
    if current.revision != expected_revision:
        raise RevisionConflict(
            f"preference revision moved: expected={expected_revision} "
            f"current={current.revision}"
        )

    if current.revision == 0:
        # 首写：INSERT ... ON CONFLICT 语义（并发首写二选一，另一人以
        # IntegrityError 失败 → 调用方转 RevisionConflict）。
        stmt = insert(ChatSessionModelPref).values(
            conversation_id=conversation_id,
            mode=mode,
            catalog_item_id=catalog_item_id,
            thinking_level=thinking_level,
            session_token_cap=session_token_cap,
            revision=1,
        )
        try:
            await db.execute(stmt)
        except Exception:
            await db.rollback()
            raise RevisionConflict("concurrent first write lost the race")
        await db.commit()
        return SessionModelPref(
            mode=mode, catalog_item_id=catalog_item_id,
            thinking_level=thinking_level, session_token_cap=session_token_cap,
            revision=1,
        )

    new_rev = current.revision + 1
    stmt = (
        update(ChatSessionModelPref)
        .where(
            ChatSessionModelPref.conversation_id == conversation_id,
            ChatSessionModelPref.revision == current.revision,
        )
        .values(
            mode=mode,
            catalog_item_id=catalog_item_id,
            thinking_level=thinking_level,
            session_token_cap=session_token_cap,
            revision=new_rev,
        )
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:  # pragma: no cover - 并发窗口
        await db.rollback()
        raise RevisionConflict("preference revision moved during update")
    await db.commit()
    return SessionModelPref(
        mode=mode, catalog_item_id=catalog_item_id,
        thinking_level=thinking_level, session_token_cap=session_token_cap,
        revision=new_rev,
    )


async def effective_session_token_cap(db: AsyncSession, conversation_id: str) -> Optional[int]:
    """会话生效的 token 上限：用户自设 > admin 默认；无 = None（不限）。"""
    from app.core.config import settings

    pref = await get_pref(db, conversation_id)
    if pref.session_token_cap is not None:
        return int(pref.session_token_cap)
    default_cap = int(settings.SESSION_MODEL_TOKEN_CAP_DEFAULT or 0)
    return default_cap if default_cap > 0 else None


__all__ = [
    "PreferenceNotFoundError",
    "RevisionConflict",
    "SessionModelPref",
    "THINKING_LEVELS",
    "effective_session_token_cap",
    "get_pref",
    "inherit_pref",
    "set_pref",
]
