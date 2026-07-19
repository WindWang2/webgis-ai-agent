"""认证路由：/auth/register、/auth/login、/auth/me。

最小可用实现，闭合审计 A1 (无 /login 端点)。
- 密码用 scrypt 哈希（无新依赖）
- JWT 走现有 app/core/auth.py 已有的 HS256
- 用户表已经存在 (app/models/db_model.User)，直接复用
- 不实现 /refresh：现在 token 7 天有效，到期重登；可作为后续优化
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.core.config import settings
from app.core.database import get_async_db
from app.core.rate_limiter import get_rate_limiter
from app.models.db_model import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["认证"])

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]{3,40}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# 注册端点默认关闭 —— 防止任何匿名用户铸造合法 JWT 后访问所有 admin 端点
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
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒
    user: dict


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role,
    }


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> TokenResponse:
    """新建用户并返回 JWT。

    默认关闭（ALLOW_PUBLIC_REGISTER 未设为 true 时返回 503）——
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
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(
        data={"sub": user.id, "username": user.username, "role": user.role},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return TokenResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_user_to_dict(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> TokenResponse:
    """用户名或邮箱 + 密码登录，返回 JWT。"""
    # 限速：每 (IP, identifier) 5 分钟最多 5 次失败 —— 防 password spraying。
    # 用 (ip, identifier) 组合 key 是为了让单账号被多 IP 撞库时仍能限速，
    # 同时不误伤单 IP 下多个正常用户。
    client_ip = request.client.host if request.client else "unknown"
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"auth_login:{client_ip}:{req.identifier}",
        max_requests=5,
        window_seconds=300,
    ):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 5 分钟后再试")

    result = await db.execute(
        select(User).where((User.username == req.identifier) | (User.email == req.identifier))
    )
    user = result.scalar_one_or_none()
    # 即使用户不存在也跑一遍假 verify 以防止时序侧信道泄漏用户存在性
    valid = verify_password(req.password, user.password_hash if user else "scrypt$1$1$1$00$00")
    if not user or not valid:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")

    token = create_access_token(
        data={"sub": user.id, "username": user.username, "role": user.role},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return TokenResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_user_to_dict(user),
    )


@router.get("/me")
async def me(current: dict = Depends(get_current_user)) -> dict:
    """返回当前 JWT 所属用户的核心信息（payload 已校验）。"""
    # JWT payload 已包含 sub/username/role；不必每次回 DB
    return {
        "user_id": current.get("user_id"),
    }
