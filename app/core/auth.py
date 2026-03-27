"""
JWT 认证模块 - 修复版
支持用户注册、登录、JWT令牌签发与校验
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from functools import wraps

from fastapi import HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.user_model import User

logger = logging.getLogger(__name__)
settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"

# ============== Password Utils =============

def hash_password(password: str) -> str:
    """BCrypt password hashing"""
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    """Verify password"""
    return pwd_context.verify(plain, hashed)

# ============== JWT Utils =============

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=24)
    
    to_encode.update({
        "exp": expire,
        "iat": datetime.utcnow()
    })
    
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_token(token: str) -> Dict[str, Any]:
    """Decode and verify JWT token"""
    try:
        payload = jwt.decode(
            token, 
            settings.SECRET_KEY, 
            algorithms=[ALGORITHM]
        )
        return payload
    except JWTError as e:
        logger.warning(f"JWT verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")

# ============== Dependency Injection =============

async def get_current_user(
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: DbSession = Depends(get_db)
) -> User:
    """
    Get current authenticated user
    
    Usage:
    @app.get("/protected")
    def protected_route(user: User = Depends(get_current_user)):
        return {"user_id": user.id}
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    payload = decode_token(authorization.credential)
    user_id = payload.get("sub") or payload.get("user_id")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid payload")
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or disabled")
    
    return user

def get_optional_user(
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: DbSession = Depends(get_db)
) -> Optional[User]:
    """Get current user (optional, for unauthenticated requests)"""
    if not authorization:
        return None
    
    try:
        payload = decode_token(authorization.credential)
        user_id = payload.get("sub") or payload.get("user_id")
        if user_id:
            return db.query(User).filter(User.id == int(user_id)).first()
    except:
        pass
    
    return None

# ============== Role Permissions =============

class Role:
    """Role constants"""
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"

ROLE_LEVELS = {
    Role.ADMIN: 3,
    Role.EDITOR: 2,
    Role.VIEWER: 1,
}

def require_role(required_role: str):
    """Role permission decorator factory"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, current_user: User = Depends(get_current_user), **kwargs):
            user_level = ROLE_LEVELS.get(current_user.role, 0)
            required_level = ROLE_LEVELS.get(required_role, 1)
            
            if user_level < required_level:
                raise HTTPException(
                    status_code=403,
                    detail=f"Requires {required_role} permission"
                )
            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator

def require_admin(current_user: User = Depends(get_current_user)):
    """Admin only access"""
    if current_user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    return current_user

def require_editor(current_user: User = Depends(get_current_user)):
    """Editor access"""
    if current_user.role not in [Role.ADMIN, Role.EDITOR]:
        raise HTTPException(status_code=403, detail="Requires editor permission")
    return current_user

# ============== Data Isolation =============

def filter_by_org(query, current_user: User):
    """Filter by organization (tenant isolation)"""
    if current_user.role == Role.ADMIN:
        return query  # Admin sees all
    return query.filter(User.org_id == current_user.org_id)

def filter_by_owner(model_class, query, current_user: User, owner_field: str = "creator_id"):
    """Filter by creator (user private data)"""
    if current_user.role == Role.ADMIN:
        return query  # Admin sees all
    return query.filter(getattr(model_class, owner_field) == current_user.id)

__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_token",
    "get_current_user",
    "get_optional_user",
    "Role",
    "require_role",
    "require_admin",
    "require_editor",
    "filter_by_org",
    "filter_by_owner"
]