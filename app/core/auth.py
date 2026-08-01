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
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
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
    return {"user_id": user_id, "role": payload.get("role") or "viewer"}


async def get_current_user_optional(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """获取当前用户 - 可选认证 (用于公开接口)。

    不查 DB，不校验 ver。用于性能敏感的公开端点 (e.g. /sessions 列表)。
    若要 ver 校验，用 `Depends(get_current_user_with_version)` + 兜底逻辑。
    """
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

    conv = await AsyncHistoryService(db).get_session(
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

