"""认证模块

S41 (token refresh + logout) 引入两类 JWT：
- **access token** (默认 30min): `{"sub","username","role","type":"access","ver":<int>,"exp","iat"}`
- **refresh token** (默认 7d): `{"sub","username","role","type":"refresh","ver":<int>,"jti":<hex>,"exp","iat"}`

`ver` (token_version) 与 `User.token_version` 列对应；bump 后所有携带旧 ver
的 access / refresh token 立即失效 (logout-everywhere 语义)。

**Back-compat window**: 部署后最长 7 天内，部署前签发的旧 access token (无
`type`/`ver` claim) 仍被接受为 `type=access, ver=0`。7 天后所有旧 token 自然
过期，可改为严格拒绝无 `type` claim 的 token。
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_db
from app.models.db_model import Conversation, User

security = HTTPBearer(auto_error=False)

# Sentinels returned by get_current_user_optional for unauthenticated callers.
# Never persist these as owner_id / user_id (no matching users row).
_ANONYMOUS_USER_IDS = frozenset({"anonymous", "anon"})

# ── 测试阶段免登录（AUTH_DISABLED=true）─────────────────────────────────
# 所有受保护依赖退化为固定 admin 身份：不校验 Bearer token，无需登录。
# bypass 身份是一个真实 User 行（test-admin, role=admin），使会话归属、
# ver 校验契约（"user" ORM 键）与正常登录路径一致；密码为随机值——登录
# 通道不因 bypass 开启而多出一个可爆破账号。
AUTH_BYPASS_USER_ID = "test-admin"
AUTH_BYPASS_PROFILE = {
    "user_id": AUTH_BYPASS_USER_ID,
    "role": "admin",
    "org_id": None,
}


def auth_bypass_enabled() -> bool:
    """测试阶段免登录开关（settings.AUTH_DISABLED）。"""
    return bool(getattr(settings, "AUTH_DISABLED", False))


async def _get_or_create_bypass_user(db: AsyncSession) -> Optional[User]:
    """惰性确保 test-admin 用户行存在（with_version 路径的 ver/ORM 契约）。

    每请求一次 indexed PK lookup（与正常 ver 校验同量级）；创建只发生在
    首次。DB 不可用时返回 None——依赖仍返回 bypass dict，仅缺 ORM 键。
    """
    try:
        result = await db.execute(
            select(User).where(User.id == AUTH_BYPASS_USER_ID)
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                id=AUTH_BYPASS_USER_ID,
                username=AUTH_BYPASS_USER_ID,
                email="test-admin@local.test",
                # 随机密码：/auth/login 无法登录该账号（bypass 才是入口）。
                password_hash=hash_password(secrets.token_urlsafe(32)),
                role="admin",
                is_active=True,
                email_verified=True,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user
    except Exception as exc:  # noqa: BLE001 - bypass 必须比 DB 更可用（测试阶段）
        import logging

        logging.getLogger(__name__).warning(
            "[auth-bypass] test-admin row unavailable: %s", exc
        )
        return None


def actor_ids(user: Optional[dict]) -> tuple[Optional[str], Optional[object]]:
    """Normalize the auth-dependency dict to (user_id, org_id).

    JWT helpers return ``user_id``, never ``id``. Routes that read ``user.get("id")``
    silently skip every owner check. Accept ``user_id`` / ``id`` / ``sub`` and
    collapse anonymous sentinels to ``None``.
    """
    if not user:
        return None, None
    uid = user.get("user_id") or user.get("id") or user.get("sub")
    if uid is None or str(uid) in _ANONYMOUS_USER_IDS:
        uid = None
    else:
        uid = str(uid)
    return uid, user.get("org_id")


def authorize_session_write(
    conv: Optional[object],
    user_id: Optional[str],
    owner_token: Optional[str] = None,
) -> bool:
    """Same ownership rules as ``AsyncHistoryService.get_session``.

    * ``conv is None`` — first-turn write; allowed (caller will create keys).
    * Bound ``user_id`` — only that user.
    * Anonymous + ``owner_token`` set — caller must present the matching token
      (SEC-08). Session-id-only writes are the original IDOR.
    * Anonymous + ``owner_token`` NULL — grandfather; session_id is capability.
    """
    if conv is None:
        return True
    conv_uid = getattr(conv, "user_id", None)
    expected = getattr(conv, "owner_token", None)
    if conv_uid is None:
        if expected is not None:
            if not owner_token:
                return False
            return hmac.compare_digest(str(owner_token), str(expected))
        return True
    if user_id is None or str(user_id) in _ANONYMOUS_USER_IDS:
        return False
    return str(conv_uid) == str(user_id)

# JWT 配置
SECRET_KEY = settings.JWT_SECRET_KEY
ALGORITHM = "HS256"

# S41: access token 30min (was 7d); refresh token 7d。
# 短 access TTL 让权限变更 (role 改动 / logout) 在 ~30min 内对大多数请求生效；
# refresh token 让用户无需每 30min 重输密码。
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
REFRESH_TOKEN_EXPIRE_MINUTES = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60  # 10080

# JWT claim 常量
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"

# scrypt 参数（OWASP Memory-Hard Hash 推荐）
# N=2**14 在普通服务器约 50ms / hash，足以挡住字典攻击但不阻塞登录
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_KEY_LEN = 32

# 审计 P1：登录时序侧信道防护。模块加载时生成一个随机 dummy hash，
# 使 "用户不存在" 和 "密码错误" 两条路径都走完整 scrypt (N=2^14)，
# 消除固定 dummy（n=1）导致的时序差异。
_DUMMY_SALT = os.urandom(16)
_DUMMY_HASH = hashlib.scrypt(
    b"dummy-password-for-timing",
    salt=_DUMMY_SALT,
    n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
    dklen=_SCRYPT_KEY_LEN,
).hex()
_DUMMY_STORED = f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_DUMMY_SALT.hex()}${_DUMMY_HASH}"


def hash_password(plain: str) -> str:
    """生成密码哈希。格式：scrypt$N$r$p$salt_hex$hash_hex。

    用 stdlib hashlib.scrypt 避免引入新依赖。
    """
    if not isinstance(plain, str) or not plain:
        raise ValueError("password must be non-empty string")
    salt = os.urandom(16)
    key = hashlib.scrypt(
        plain.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_SCRYPT_KEY_LEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${key.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    """常量时间比较；任意解析失败一律返回 False（不泄漏哪步出错）。"""
    if not stored or not isinstance(stored, str):
        return False
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        # 限制参数避免 DoS：超过预期 N 的存量直接拒绝
        if n > _SCRYPT_N * 4 or r > 32 or p > 4:
            return False
        derived = hashlib.scrypt(
            plain.encode("utf-8"),
            salt=salt,
            n=n, r=r, p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(derived, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    token_version: int = 0,
) -> str:
    """创建 access token (默认 30min)。

    `data` 应含 `sub`/`username`/`role`；本函数补 `exp`/`iat`/`type`/`ver`。
    `token_version` 来自 `User.token_version`；bump 它即让旧 token 失效。

    back-compat: 调用方传 `token_version=0` 时仍写 `ver=0` claim
    (与默认值一致)，避免新旧 token 行为分歧。
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({
        "exp": expire,
        "iat": now,
        "type": TOKEN_TYPE_ACCESS,
        "ver": int(token_version),
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    token_version: int = 0,
) -> str:
    """创建 refresh token (默认 7d)。

    refresh token 只用于换取新的 access token，不能直接访问受保护资源
    (`get_current_user_with_version` 会拒绝 `type != access` 的 token)。
    `jti` 是 token 的唯一 id；目前不服务端存储 (soft rotation)，将来若要
    实现 per-device logout，可改用 refresh_tokens 表存 jti。
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES))
    to_encode.update({
        "exp": expire,
        "iat": now,
        "type": TOKEN_TYPE_REFRESH,
        "ver": int(token_version),
        "jti": secrets.token_hex(16),  # 32-char hex，碰撞概率可忽略
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """验证 JWT 签名 + exp；返回 payload 或 None。

    注意：本函数只做密码学校验，**不检查 `ver` 是否与 DB 一致**。
    需要 ver 校验的路径用 `get_current_user_with_version` 依赖。

    #473：校验失败（签名/exp/格式错误）会递增
    auth_jwt_validation_errors_total —— Auth_JWT_Errors 告警的真实数据源；
    之前该指标从未被暴露，告警是永久静默的。
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        from app.core.auth_metrics import inc_jwt_validation_error

        inc_jwt_validation_error()
        return None


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """获取当前用户 - 需要 Bearer token (无 ver 校验)。

    返回 dict 含 user_id 和（如 JWT 中有）role。下游如需 admin 校验，
    使用 `require_admin` 依赖；不要直接在本函数返回值上做 role 判断。

    **不查 DB，不校验 token_version** -- 仅校验签名 + exp。
    用于性能敏感或非关键路径；要求 logout 即时生效的路径用
    `get_current_user_with_version`。

    back-compat: 无 `type` claim 的旧 token 视为 access token (ver=0)。
    """
    if auth_bypass_enabled():
        return dict(AUTH_BYPASS_PROFILE)

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = verify_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 拒绝 refresh token 被当 access 用 (新增 type claim 的 token 强校验)
    # 旧 token 无 type claim，按 back-compat 视为 access。
    tok_type = payload.get("type")
    if tok_type is not None and tok_type != TOKEN_TYPE_ACCESS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type; use an access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # role 来自 register/login 时写入的 JWT claim；未带 role 的旧 token 视为 viewer
    return {
        "user_id": user_id,
        "role": payload.get("role") or "viewer",
        "org_id": payload.get("org_id"),
    }


async def get_current_user_optional(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """获取当前用户 - 可选认证 (用于公开接口)。

    不查 DB，不校验 ver。用于性能敏感的公开端点 (e.g. /sessions 列表)。
    若要 ver 校验，用 `Depends(get_current_user_with_version)` + 兜底逻辑。
    """
    if auth_bypass_enabled():
        # bypass 身份而非匿名哨兵：会话归属/所有权守卫与受保护端点一致，
        # 避免"同一请求在 A 端点是 test-admin、在 B 端点是 anonymous"。
        return dict(AUTH_BYPASS_PROFILE)

    if credentials is None:
        return {"user_id": "anonymous", "role": "anonymous"}

    token = credentials.credentials
    payload = verify_token(token)

    if payload is None:
        return {"user_id": "anonymous", "role": "anonymous"}

    user_id = payload.get("sub")
    if not user_id:
        return {"user_id": "anonymous", "role": "anonymous"}

    # 拒绝 refresh token 被当 access 用 (新 token)
    tok_type = payload.get("type")
    if tok_type is not None and tok_type != TOKEN_TYPE_ACCESS:
        return {"user_id": "anonymous", "role": "anonymous"}

    return {
        "user_id": user_id,
        "role": payload.get("role") or "viewer",
        "org_id": payload.get("org_id"),
    }


async def get_current_user_with_version(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """获取当前用户 - 需要 Bearer access token **且** ver 与 DB 一致。

    返回 dict 含 `user_id`/`role`/`user` (User ORM 对象，供下游避免二次查库)。

    **这是受保护资源的推荐依赖** -- 它在每次请求时做一次 indexed PK lookup
    (User.id)，~1ms 量级，可接受。Bumping `User.token_version` (logout) 会让
    所有携带旧 ver 的 token 立即 401。

    back-compat: 无 `ver` claim 的旧 token 视为 ver=0；只要用户的
    `token_version` 还是 0 (即未 logout 过)，旧 token 仍可通过。
    """
    if auth_bypass_enabled():
        profile = dict(AUTH_BYPASS_PROFILE)
        user = await _get_or_create_bypass_user(db)
        if user is not None:
            profile["user"] = user
        return profile

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 拒绝 refresh token 被当 access 用
    tok_type = payload.get("type")
    if tok_type is not None and tok_type != TOKEN_TYPE_ACCESS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type; use an access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 查 DB 拿 token_version；User.id 是 PK，走 indexed lookup。
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        # 用户已删除 -- token 应当失效
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ver 校验：旧 token 无 ver claim 视为 0。
    token_ver = int(payload.get("ver", 0))
    if token_ver != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token revoked, please re-login",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已停用",
        )

    return {
        "user_id": user_id,
        "role": payload.get("role") or user.role or "viewer",
        "org_id": user.org_id,
        "user": user,
    }


async def require_admin(_user: dict = Depends(get_current_user_with_version)) -> dict:
    """要求当前用户具有 admin 角色（且 token_version 与 DB 一致）。

    审计 SEC-05：原先依赖 `get_current_user`（不查 DB），logout 后
    `User.token_version` bump，但旧 admin token 在 30min TTL 内仍能访问
    所有 admin 端点。现改为 `get_current_user_with_version`，使 logout
    对 admin 端点也立即生效。

    role 取值优先读 DB 中的 user 对象（实时），fallback 到 JWT claim。
    这样即使管理员被降级 (admin→viewer) 也能在下一次请求时生效，而非
    等 token 过期。
    """
    user_obj = _user.get("user")
    if user_obj is not None and getattr(user_obj, "role", None):
        role = user_obj.role
    else:
        role = _user.get("role")
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return _user


async def get_owner_token(
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Optional[str]:
    """提取 X-Session-Token 请求头 (SEC-08 匿名会话所有权校验)。"""
    return x_session_token


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
    from app.services.history_service_async import AsyncHistoryService

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

