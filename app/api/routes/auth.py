"""认证路由：/auth/register、/auth/login、/auth/refresh、/auth/logout、/auth/me。

S41: token refresh + logout (backend-only)。
- access token 30min, refresh token 7d
- logout = bump User.token_version -> 所有旧 access/refresh token 失效
- /auth/refresh 用 refresh token 换取新的 access + refresh token 对
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_MINUTES,
    TOKEN_TYPE_REFRESH,
    _DUMMY_STORED,
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_current_user_with_version,
    hash_password,
    verify_password,
    verify_token,
)
from app.core.database import get_async_db
from app.core.rate_limiter import get_rate_limiter
from app.models.db_model import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["认证"])

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]{3,40}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# 注册端点默认关闭 -- 防止任何匿名用户铸造合法 JWT 后访问所有 admin 端点
# （审计 S28：原本完全开放，加上 require_admin 后所有 admin 端点仍可被注册账号访问，
# 关闭公开注册是真正切断攻击面的方式）。运维如需自助注册，设置环境变量
# ALLOW_PUBLIC_REGISTER=true，但生产环境强烈推荐通过 manage.py create_admin
# CLI 显式创建账号而非开放注册。
# 注意：lazy 读取，便于测试在每个 case 重置环境变量。
def _allow_public_register() -> bool:
    return os.getenv("ALLOW_PUBLIC_REGISTER", "").lower() == "true"


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(None, max_length=255)


class LoginRequest(BaseModel):
    # 支持用户名或邮箱登录
    identifier: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """登录/注册/refresh 的返回。

    S41 起新增 `refresh_token` 字段；旧客户端忽略它不会破坏。
    """
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int  # 秒 (access token TTL)
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10, max_length=4096)


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role,
    }


def _issue_token_pair(user: User) -> TokenResponse:
    """为给定 user 签发 access + refresh token 对。"""
    token_data = {"sub": user.id, "username": user.username, "role": user.role}
    access = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        token_version=user.token_version,
    )
    refresh = create_refresh_token(
        data=token_data,
        expires_delta=timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES),
        token_version=user.token_version,
    )
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_user_to_dict(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> TokenResponse:
    """新建用户并返回 JWT。

    默认关闭（ALLOW_PUBLIC_REGISTER 未设为 true 时返回 503）--
    防止任何匿名用户铸造合法 JWT 后访问所有 admin 端点（审计 S28）。
    生产环境用 `manage.py create_admin` CLI 创建账号。
    """
    if not _allow_public_register():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="公开注册已禁用；联系运维创建账号（manage.py create_admin）",
        )

    # 限速：每 IP 每小时最多 5 次注册（防账号农场 + 减少攻击面）
    client_ip = request.client.host if request.client else "unknown"
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(f"auth_register:{client_ip}", max_requests=5, window_seconds=3600):
        raise HTTPException(status_code=429, detail="注册过于频繁，请稍后再试")

    if not _USERNAME_RE.match(req.username):
        raise HTTPException(
            status_code=400,
            detail="username 只能包含字母/数字/下划线/点/连字符，长度 3-40",
        )
    if not _EMAIL_RE.match(req.email):
        raise HTTPException(status_code=400, detail="email 格式非法")

    # 唯一性预检（DB 也有 unique 约束兜底）
    existing = await db.execute(
        select(User).where((User.username == req.username) | (User.email == req.email))
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="username 或 email 已被占用")

    user = User(
        id=str(uuid.uuid4()),
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        role="viewer",
        is_active=True,
        token_version=0,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return _issue_token_pair(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> TokenResponse:
    """用户名或邮箱 + 密码登录，返回 access + refresh token。"""
    # 限速：每 IP 5 分钟最多 5 次失败 -- 防 password spraying + 避免 NAT 下锁正常用户。
    # 审计 P1：之前 key 包含 identifier（用户名），攻击者可在同一 NAT 下用
    # 受害者的用户名发起失败登录，导致受害者被锁。改为纯 IP 限速。
    client_ip = request.client.host if request.client else "unknown"
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"auth_login:{client_ip}",
        max_requests=5,
        window_seconds=300,
    ):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 5 分钟后再试")

    result = await db.execute(
        select(User).where((User.username == req.identifier) | (User.email == req.identifier))
    )
    user = result.scalar_one_or_none()
    # 即使用户不存在也跑一遍假 verify 以防止时序侧信道泄漏用户存在性
    # 审计 P1：使用模块级随机 dummy hash，避免固定 dummy 导致的时序差异。
    valid = verify_password(req.password, user.password_hash if user else _DUMMY_STORED)
    if not user or not valid:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")

    # S41 bonus: 维护 last_login / login_count (审计中标记为 TODO)
    user.last_login = datetime.now(timezone.utc)
    user.login_count = (user.login_count or 0) + 1
    await db.commit()
    await db.refresh(user)

    return _issue_token_pair(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    req: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> TokenResponse:
    """用 refresh token 换取新的 access + refresh token 对。

    校验：
    1. token 签名 + exp
    2. `type == "refresh"` (拒绝 access token 当 refresh 用)
    3. user 存在且 `is_active`
    4. `ver` claim == `User.token_version` (logout 后旧 refresh token 失效)

    Rate limit: 30 req / 5min per user_id -- 防止 misbehaving client 死循环刷新。
    """
    payload = verify_token(req.refresh_token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if payload.get("type") != TOKEN_TYPE_REFRESH:
        # 用 access token 来 refresh 是常见误用；明确报错便于排错
        raise HTTPException(
            status_code=401,
            detail="Wrong token type; provide a refresh token",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid refresh token payload")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")

    # ver 校验：logout 后 token_version bump，旧 refresh token 立即失效
    token_ver = int(payload.get("ver", 0))
    if token_ver != user.token_version:
        raise HTTPException(
            status_code=401,
            detail="Refresh token revoked, please re-login",
        )

    # 限速：30/5min/user -- 前端正常 30min 一次 refresh，30 次 = 2.5h 不间断
    # 刷新才触发；足够宽松，又能挡住死循环。
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"auth_refresh:{user_id}",
        max_requests=30,
        window_seconds=300,
    ):
        raise HTTPException(status_code=429, detail="刷新过于频繁，请稍后再试")

    # Soft rotation: 发新 refresh token (新 jti)；旧 refresh token 仍签名有效，
    # 但前端应当丢弃它 (recommended: refresh 后立即用新 token 替换旧 token)。
    # 真正的 rotation 需要 refresh_tokens 表存 jti，本迭代不做 (logout =
    # bump ver 已经覆盖主要威胁)。
    return _issue_token_pair(user)


@router.post("/logout")
async def logout(
    current: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """登出 - bump `User.token_version` 让所有 access/refresh token 失效。

    语义：logout-everywhere (单设备 logout 需要 refresh_tokens 表跟踪 jti，
    本迭代不做)。

    需要 access token 认证 (避免陌生人 trigger logout)。
    """
    user_id = current["user_id"]
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        # 已删除的用户 -- 视为已登出
        return {"ok": True, "message": "已登出"}

    user.token_version = (user.token_version or 0) + 1
    await db.commit()
    return {"ok": True, "message": "已登出"}


@router.get("/me")
async def me(current: dict = Depends(get_current_user_with_version)) -> dict:
    """返回当前 JWT 所属用户的核心信息。

    S41: 改用 `get_current_user_with_version`，让 logout (ver bump) 立即生效。
    代价：每请求一次 indexed PK lookup (~1ms)。
    """
    user = current.get("user")
    if user is not None:
        # 全量信息 (从 DB 取)
        return {
            "user_id": current["user_id"],
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
        }
    # fallback (理论上不会触发，因为 with_version 总会带 user)
    return {"user_id": current.get("user_id")}
