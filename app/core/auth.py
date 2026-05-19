"""认证模块"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

from app.core.config import settings

security = HTTPBearer(auto_error=False)

# JWT 配置
SECRET_KEY = settings.JWT_SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 天

# scrypt 参数（OWASP Memory-Hard Hash 推荐）
# N=2**14 在普通服务器约 50ms / hash，足以挡住字典攻击但不阻塞登录
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_KEY_LEN = 32


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


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """创建 JWT token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> dict:
    """验证 JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """获取当前用户 - 需要 Bearer token"""
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
    
    return {"user_id": user_id}


async def get_current_user_optional(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """获取当前用户 - 可选认证 (用于公开接口)"""
    if credentials is None:
        return {"user_id": "anonymous"}
    
    token = credentials.credentials
    payload = verify_token(token)
    
    if payload is None:
        return {"user_id": "anonymous"}
    
    user_id = payload.get("sub")
    return {"user_id": user_id or "anonymous"}
