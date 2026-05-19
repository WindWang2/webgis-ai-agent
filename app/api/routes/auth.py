"""认证路由：/auth/register、/auth/login、/auth/me。

最小可用实现，闭合审计 A1 (无 /login 端点)。
- 密码用 scrypt 哈希（无新依赖）
- JWT 走现有 app/core/auth.py 已有的 HS256
- 用户表已经存在 (app/models/db_model.User)，直接复用
- 不实现 /refresh：现在 token 7 天有效，到期重登；可作为后续优化
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
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
from app.core.database import get_async_db
from app.models.db_model import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["认证"])

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]{3,40}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


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
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_async_db)) -> TokenResponse:
    """新建用户并返回 JWT。"""
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
async def login(req: LoginRequest, db: AsyncSession = Depends(get_async_db)) -> TokenResponse:
    """用户名或邮箱 + 密码登录，返回 JWT。"""
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
