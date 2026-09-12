"""认证路由：/auth/register、/auth/login、/auth/refresh、/auth/logout、/auth/me。

S41: token refresh + logout (backend-only)。
- access token 30min, refresh token 7d
- logout = bump User.token_version -> 所有旧 access/refresh token 失效
- /auth/refresh 用 refresh token 换取新的 access + refresh token 对
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.password_policy import (
    PasswordPolicyError,
    record_login_failure,
    reset_login_failures,
    validate_password_strength,
)
from app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
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
from app.models.api_response import ApiResponse
from app.schemas.auth_schema import (
    LoginRequest,
    LogoutResponse,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserInfo,
)

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


def _get_client_ip(request: Request) -> str:
    """Client IP for auth rate limits — shared extraction (SEC-F4)."""
    from app.core.client_ip import client_ip_from

    return client_ip_from(request)


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role,
    }


def _issue_token_pair(
    user: User,
    *,
    family_id: Optional[str] = None,
    refresh_jti: Optional[str] = None,
) -> TokenResponse:
    """为给定 user 签发 access + refresh token 对。

    ADR-0139 P3：写入 ``scopes`` claim（角色默认集的线格式）。refresh
    时按**当前**角色重算（角色降级在下次刷新即收敛，不等 access 过期）。
    ADR-0139 P6：``family_id`` 提供时写入 ``fam`` claim（家族轮换语义）；
    ``refresh_jti`` 提供时作为新 refresh 的 jti（家族行先行写入，保证
    行与 token 一致）。
    """
    from app.core.scopes import scopes_for_role

    token_data = {"sub": user.id, "username": user.username, "role": user.role}
    if getattr(user, "org_id", None) is not None:
        token_data["org_id"] = user.org_id
    token_data["scopes"] = " ".join(sorted(scopes_for_role(user.role)))
    if family_id:
        token_data["fam"] = str(family_id)
    access = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        token_version=user.token_version,
    )
    refresh = create_refresh_token(
        data=token_data,
        expires_delta=timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES),
        token_version=user.token_version,
        jti=refresh_jti,
        family_id=family_id,
    )
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserInfo(**_user_to_dict(user)),
    )


async def _new_pair_with_family(db: AsyncSession, user: User) -> TokenResponse:
    """带家族的 token 对（登录/注册路径）：建家族 + 首对 token。

    先定 jti → 写家族行（current_jti=jti）→ 以同一 jti 签 refresh，
    保证「行与 token 一致」不变量。
    """
    from datetime import datetime, timedelta, timezone

    from app.models.db_model import RefreshTokenFamily

    family_id = secrets.token_hex(16)
    first_jti = secrets.token_hex(16)
    db.add(RefreshTokenFamily(
        user_id=str(user.id),
        family_id=family_id,
        current_jti=first_jti,
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS + 1),
    ))
    await db.commit()
    return _issue_token_pair(user, family_id=family_id, refresh_jti=first_jti)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "username/email 格式非法", "model": ApiResponse},
        409: {"description": "username 或 email 已被占用", "model": ApiResponse},
        429: {"description": "注册限流", "model": ApiResponse},
        503: {"description": "公开注册未开放", "model": ApiResponse},
    },
)
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

    # ADR-0139 P6：密码策略（长度/字符类/常见密码表）；422 与
    # Pydantic 校验失败同一形态（诚实拒绝，不落入弱口令落库再修）。
    try:
        validate_password_strength(req.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 限速：每 IP 每小时最多 5 次注册（防账号农场 + 减少攻击面）
    client_ip = _get_client_ip(request)
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

    # 计算隔离不变式 1：scrypt (N=2^14, ~50-150ms) 是 CPU 密集 KDF ——
    # 在 async 路由上直接算会阻塞事件循环（#386）。注册/登录各只此一处。
    password_hash = await asyncio.to_thread(hash_password, req.password)
    user = User(
        id=str(uuid.uuid4()),
        username=req.username,
        email=req.email,
        password_hash=password_hash,
        full_name=req.full_name,
        role="viewer",
        is_active=True,
        token_version=0,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return await _new_pair_with_family(db, user)


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        400: {"description": "请求体解析失败", "model": ApiResponse},
        401: {"description": "凭证无效", "model": ApiResponse},
        403: {"description": "账号停用", "model": ApiResponse},
        429: {"description": "限流", "model": ApiResponse},
    },
)
async def login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> TokenResponse:
    """用户名或邮箱 + 密码登录，返回 access + refresh token。"""
    # 限速：每 IP 5 分钟最多 5 次**失败** -- 防 password spraying。
    # 审计 P1：之前 key 包含 identifier（用户名），攻击者可在同一 NAT 下用
    # 受害者的用户名发起失败登录，导致受害者被锁。改为纯 IP 限速。
    # audit #839：此前 is_allowed 在凭证校验前无条件消耗配额且成功不返还 ——
    # 同一 NAT 出口 5 次成功登录就会锁死第 6 个正常用户（与注释语义相悖）。
    # 现在：失败才记入 fail 台账（count 只读探测不消耗）；另设宽的 attempts
    # 上限（30/5min）保留防爆破/DoS 的前置闸门。
    client_ip = _get_client_ip(request)
    limiter = await get_rate_limiter()
    if await limiter.count(f"auth_login_fail:{client_ip}", 300) >= 5:
        raise HTTPException(status_code=429, detail="登录失败次数过多，请 5 分钟后再试")
    if not await limiter.is_allowed(
        f"auth_login_attempt:{client_ip}",
        max_requests=30,
        window_seconds=300,
    ):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 5 分钟后再试")

    result = await db.execute(
        select(User).where((User.username == req.identifier) | (User.email == req.identifier))
    )
    user = result.scalar_one_or_none()
    # 即使用户不存在也跑一遍假 verify 以防止时序侧信道泄漏用户存在性
    # 审计 P1：使用模块级随机 dummy hash，避免固定 dummy 导致的时序差异。
    # 计算隔离不变式 1：scrypt verify 是 CPU 密集 KDF，offload 到线程（#386）。
    stored_hash = user.password_hash if user else _DUMMY_STORED
    valid = await asyncio.to_thread(verify_password, req.password, stored_hash)
    if not user or not valid:
        # audit #839: 失败记入 fail 台账（窗口 5 分钟，计数 5 次锁出）
        await limiter.record(f"auth_login_fail:{client_ip}", 300)
        # ADR-0139 P6：渐进延迟（进程内 best-effort；跨进程硬上界仍由
        # 既有限流承担）。延迟在响应前施加 —— 不泄漏「失败但已延迟」
        # 之外的任何信息，且 dummy-hash 路径同样走完整 scrypt。
        delay_s = record_login_failure(req.identifier)
        if delay_s > 0:
            await asyncio.sleep(delay_s)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")

    user.last_login = datetime.now(timezone.utc)
    user.login_count = (user.login_count or 0) + 1
    await db.commit()
    await db.refresh(user)
    reset_login_failures(req.identifier)

    return await _new_pair_with_family(db, user)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses={
        400: {"description": "请求体解析失败", "model": ApiResponse},
        401: {"description": "refresh token 无效/类型错误/已吊销", "model": ApiResponse},
        403: {"description": "账号停用", "model": ApiResponse},
        429: {"description": "刷新限流", "model": ApiResponse},
    },
)
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

    # ── ADR-0139 P6：家族轮换 + 重用检测 ─────────────────────────────
    # 带 ``fam`` claim 的 token 走严格 rotation：jti 必须等于家族行的
    # current_jti（最新签发）；旧 jti 再次出现 = 重放 → 整个家族失效
    # （合法用户与攻击者的并存被一次性终结，防互踢循环）。
    fam = payload.get("fam")
    tok_jti = payload.get("jti")
    if fam:
        from datetime import datetime, timezone

        from app.models.db_model import RefreshTokenFamily

        row = (await db.execute(
            select(RefreshTokenFamily).where(
                RefreshTokenFamily.family_id == str(fam),
                RefreshTokenFamily.user_id == user_id,
            )
        )).scalar_one_or_none()
        if row is None or row.reuse_detected:
            raise HTTPException(
                status_code=401,
                detail="Refresh token family revoked, please re-login",
            )
        if not tok_jti or str(tok_jti) != row.current_jti:
            # 重放（或陈旧副本）：家族全失效 + 审计事件（fail-open）
            row.reuse_detected = True
            await db.commit()
            try:
                from app.services.audit import (
                    ACTION_AUTH_REFRESH_REUSE, record_audit)

                await record_audit(
                    db, action=ACTION_AUTH_REFRESH_REUSE,
                    actor_id=user_id, org_id=user.org_id,
                    target_type="refresh_family",
                    target_id=str(fam),
                )
            except Exception:  # noqa: BLE001 - 审计失败不改变拒绝语义
                pass
            raise HTTPException(
                status_code=401,
                detail="Refresh token replayed; family revoked, please re-login",
            )
        # 严格 rotation：新 jti 前移家族行，再签新 token 对（行先行，一致）
        new_jti = secrets.token_hex(16)
        row.current_jti = new_jti
        row.rotated_at = datetime.now(timezone.utc)
        await db.commit()
        return _issue_token_pair(user, family_id=str(fam), refresh_jti=new_jti)

    # Legacy soft rotation：无 ``fam`` claim 的存量 token（部署窗口 ≤7d，
    # 与迁移 back-compat 同纪律）；过期后该路径自然不再触达。
    return _issue_token_pair(user)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    current: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> LogoutResponse:
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
        return LogoutResponse(ok=True, message="已登出")

    user.token_version = (user.token_version or 0) + 1
    await db.commit()
    return LogoutResponse(ok=True, message="已登出")


@router.get("/me", response_model=MeResponse)
async def me(current: dict = Depends(get_current_user_with_version)) -> MeResponse:
    """返回当前 JWT 所属用户的核心信息。

    S41: 改用 `get_current_user_with_version`，让 logout (ver bump) 立即生效。
    代价：每请求一次 indexed PK lookup (~1ms)。
    """
    user = current.get("user")
    if user is not None:
        # 全量信息 (从 DB 取)
        return MeResponse(
            user_id=current["user_id"],
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
        )
    # fallback (理论上不会触发，因为 with_version 总会带 user)
    return MeResponse(user_id=current.get("user_id"))
