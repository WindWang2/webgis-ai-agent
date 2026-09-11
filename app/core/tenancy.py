"""多租户查询级隔离（ADR-0139，foundation/security-tenancy-v9 P2）。

三层语义：

1. **effective org**：JWT 用户的 org 是权威值（DB ``User.org_id``，随
   claim 传播）；无 org 的用户、匿名 owner_token 会话、AUTH_DISABLED
   bypass 统一归入 **default 组织隔离桶**（``organizations.slug='default'``，
   幂等 ensure）。default org id 进程内缓存一次——organizations.slug
   唯一且行不可变，缓存失真面为零（迁移回填保证存量行全有 org）。
2. **scoped query**：租户数据面表的全部 REST 读路径必须叠加
   ``org_id == effective_org`` 谓词（additive——与既有 owner/session
   过滤叠加，只收紧不放宽；匿名桶内的会话级隔离由既有 owner 过滤承担）。
3. **控制面信任域**：coordinator 调度/worker 注册/资源账本等路径不参与
   org 过滤（行服务全部租户），边界论证见
   docs/dev/security-tenancy-recon.md §3.2 与 ADR-0139。

fail 方向：org 谓词缺失是 **fail-closed 的反面（fail-open）**，因此
``scoped_query`` 对没有 ``org_id`` 列的模型直接抛 TypeError（编译期
放不进就别编译进去），宁可炸测试也不静默跨租户。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional
from app.core.database import get_async_db

logger = logging.getLogger(__name__)

#: 默认组织 slug（迁移 0036 首次 ensure；运行期幂等补建）。
DEFAULT_ORG_SLUG = "default"

# 进程内缓存（organizations.slug 唯一、行不可变 → 可缓存）。
_default_org_id: Optional[str] = None


async def get_or_create_default_org_id(db: AsyncSession) -> str:
    """default 组织 id（字符串原文，与 V8 表 org_id 列同一词表）。

    幂等：slug 唯一约束兜底并发创建；已迁移库由 0036 保证存在。
    """
    global _default_org_id
    if _default_org_id is not None:
        return _default_org_id
    from app.models.db_model import Organization

    result = await db.execute(
        select(Organization.id).where(Organization.slug == DEFAULT_ORG_SLUG)
    )
    org_id = result.scalar_one_or_none()
    if org_id is None:
        org = Organization(name="Default Organization", slug=DEFAULT_ORG_SLUG,
                           is_active=True)
        db.add(org)
        try:
            await db.commit()
        except Exception:  # noqa: BLE001 - 并发创建时唯一约束兜底
            await db.rollback()
            result = await db.execute(
                select(Organization.id).where(Organization.slug == DEFAULT_ORG_SLUG)
            )
            org_id = result.scalar_one()
        else:
            org_id = org.id
    _default_org_id = str(org_id)
    return _default_org_id


def get_or_create_default_org_id_sync(db) -> str:
    """同步会话变体（geocompute 同步线程路由 / store 侧使用）。"""
    global _default_org_id
    if _default_org_id is not None:
        return _default_org_id
    from sqlalchemy import select as _select

    from app.models.db_model import Organization

    org_id = db.execute(
        _select(Organization.id).where(Organization.slug == DEFAULT_ORG_SLUG)
    ).scalar_one_or_none()
    if org_id is None:
        org = Organization(name="Default Organization", slug=DEFAULT_ORG_SLUG,
                           is_active=True)
        db.add(org)
        try:
            db.commit()
        except Exception:  # noqa: BLE001 - 并发创建时唯一约束兜底
            db.rollback()
            org_id = db.execute(
                _select(Organization.id).where(Organization.slug == DEFAULT_ORG_SLUG)
            ).scalar_one()
        else:
            org_id = org.id
    _default_org_id = str(org_id)
    return _default_org_id


def reset_default_org_cache() -> None:
    """测试隔离用（切换 DB 后清缓存）。"""
    global _default_org_id
    _default_org_id = None


async def effective_org_id(
    user: Optional[dict],
    db: AsyncSession,
    *,
    owner_token: Optional[str] = None,
) -> str:
    """请求的 effective org（字符串原文）。

    - JWT 用户带 org → ``str(org_id)``（既有行为不变：geocompute runs
      0033 起就写这个值）；
    - 无 org 用户 / 匿名 owner_token 会话 → default 桶。匿名桶内的
      会话级隔离仍由既有 owner_scope / owner_token 过滤承担——org 谓词
      是**叠加的硬边界**，不是共享授权。

    ``owner_token`` 参数目前不参与解析（保留以明确调用点语义：匿名请求
    归 default 桶），避免未来误把 token 当 org 身份。
    """
    del owner_token
    if user:
        uid = user.get("user_id") or user.get("id") or user.get("sub")
        anonymous = uid is None or str(uid).lower() in {"anonymous", "anon"}
        if not anonymous:
            org = user.get("org_id")
            if org is not None:
                return str(org)
    return await get_or_create_default_org_id(db)


class OrgContext:
    """请求级租户上下文（require_org_context 依赖的产出）。

    ``org_id``：effective org（字符串原文）；``is_anonymous``：匿名
    owner_token 会话（default 桶内由既有 owner/session 过滤收口）；
    ``user_id``：已归一化的身份（None = 匿名/系统）。
    """

    __slots__ = ("org_id", "user_id", "is_anonymous")

    def __init__(self, org_id: str, user_id: Optional[str],
                 is_anonymous: bool):
        self.org_id = org_id
        self.user_id = user_id
        self.is_anonymous = is_anonymous

    def __repr__(self) -> str:  # 调试投影；绝不含明文 token
        return (f"OrgContext(org_id={self.org_id!r}, "
                f"user_id={self.user_id!r}, anonymous={self.is_anonymous})")


async def require_org_context(
    user: Optional[dict] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_async_db),
) -> "OrgContext":
    """FastAPI 依赖：解析请求的租户上下文（ADR-0139 §D2）。

    用法：``ctx: OrgContext = Depends(require_org_context)`` 后以
    ``ctx.org_id`` 传入 scoped_query / store 调用。异步路由推荐本依赖；
    同步线程闭包用 ``effective_org_in_thread``。
    """
    uid = None
    is_anon = True
    if user:
        raw = user.get("user_id") or user.get("id") or user.get("sub")
        is_anon = raw is None or str(raw).lower() in {"anonymous", "anon"}
        if not is_anon:
            uid = str(raw)
    org = await effective_org_id(user, db)
    return OrgContext(org, uid, is_anon)


def scoped_query(stmt, model, org_id: str):
    """给 SELECT 叠加租户谓词（additive，只收紧不放宽）。

    ``model`` 必须声明 ``org_id`` 列——控制面/无租户列的模型走本函数是
    编程错误，直接 TypeError（fail-loud）。
    """
    col = getattr(model, "org_id", None)
    if col is None:
        raise TypeError(
            f"scoped_query: {getattr(model, '__tablename__', model)} 无 org_id 列；"
            "控制面表不参与租户过滤，请显式走信任域路径（ADR-0139 §3.2）"
        )
    return stmt.where(col == org_id)


def effective_org_id_sync(user: Optional[dict], db) -> str:
    """同步会话变体（见 ``effective_org_id`` 语义）。"""
    if user:
        uid = user.get("user_id") or user.get("id") or user.get("sub")
        anonymous = uid is None or str(uid).lower() in {"anonymous", "anon"}
        if not anonymous:
            org = user.get("org_id")
            if org is not None:
                return str(org)
    return get_or_create_default_org_id_sync(db)


def effective_org_in_thread(user: Optional[dict]) -> str:
    """路由同步线程（asyncio.to_thread 闭包）内解析 effective org。

    打开短命 SessionLocal——仅首次解析会触库（default org id 进程内缓存）。

    fail 方向：解析失败（DB 不可用/表缺席）→ 返回空串，与任何 org 都不
    相等 ⇒ 租户读路径全部「空集/404」（fail-closed，绝不因解析故障放行
    跨租户读）；写路径不在本函数兜底（store 层 fail-open 纪律自会丢弃）。
    """
    from app.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            return effective_org_id_sync(user, db)
    except Exception:  # noqa: BLE001 - fail-closed：解析失败即拒绝可见性
        logger.warning("[tenancy] effective org resolution failed; "
                       "denying org-scoped visibility", exc_info=True)
        return ""
