"""认证子系统契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/auth.py` 内联迁出；路由文件只 import。
命名规范：<Resource><Action>Request/Response。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class UserInfo(BaseModel):
    """JWT 用户核心信息（/auth/me 与 TokenResponse.user 共用）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "username": "kevin",
                    "email": "kevin@example.com",
                    "full_name": "Kevin Wang",
                    "role": "viewer",
                }
            ]
        }
    )

    id: str
    username: str
    email: str
    full_name: Optional[str] = None
    role: str = "viewer"


class RegisterRequest(BaseModel):
    """POST /auth/register 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "username": "kevin",
                    "email": "kevin@example.com",
                    "password": "s3cret-passphrase",
                    "full_name": "Kevin Wang",
                }
            ]
        }
    )

    username: str = Field(..., min_length=3, max_length=40)
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(None, max_length=255)


class LoginRequest(BaseModel):
    """POST /auth/login 请求体（identifier 支持用户名或邮箱）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"identifier": "kevin", "password": "s3cret-passphrase"}]
        }
    )

    identifier: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """登录/注册/refresh 的返回。

    S41 起新增 `refresh_token` 字段；旧客户端忽略它不会破坏。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "token_type": "bearer",
                    "expires_in": 1800,
                    "user": {
                        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                        "username": "kevin",
                        "email": "kevin@example.com",
                        "full_name": None,
                        "role": "viewer",
                    },
                }
            ]
        }
    )

    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int  # 秒 (access token TTL)
    user: UserInfo


class RefreshRequest(BaseModel):
    """POST /auth/refresh 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}]
        }
    )

    refresh_token: str = Field(..., min_length=10, max_length=4096)


class LogoutResponse(BaseModel):
    """POST /auth/logout 返回（logout-everywhere 语义）。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"ok": True, "message": "已登出"}]}
    )

    ok: bool
    message: str


class MeResponse(BaseModel):
    """GET /auth/me 返回。

    user 降级路径（DB 记录缺失）只保证 `user_id`，其余字段可空。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "username": "kevin",
                    "email": "kevin@example.com",
                    "full_name": "Kevin Wang",
                    "role": "viewer",
                }
            ]
        }
    )

    user_id: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
