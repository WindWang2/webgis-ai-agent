"""
JWT 认证模块
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
# ============== 密码工具 ==============
def hash_password(password: str) -> str:
    """BCrypt密码哈希"""
    return pwd_context.hash(password)
def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain, hashed)
# ============== JWT 工具 ==============
def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta]=None) -> str:
    """创建JWT访问令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=24)
    to_encode.update({"exp": expire.isoformat(), "iat": datetime.utcnow().isoformat()})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt
def decode_token(token: str) -> Dict[str, Any]:
    """解码并验证JWT令牌"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        logger.warning(f"JWT验证失败: {e}")
        raise HTTPException(status_code=401, detail="无效令牌")
# ============== 依赖注入 ==============
async def get_current_user(
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: DbSession = Depends(get_db)
) -> User:
    """
    获取当前登录用户
    
    用于需要认证的端点
    ```python
    @app.get("/protected")
    def protected_route(user: User = Depends(get_current_user)):
        return {"user_id": user.id}
    ```
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="需要认证")
    
    payload = decode_token(authorization.credential)
    user_id = payload.get("sub") or payload.get("user_id")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="无效负载")
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    
    return user
def get_optional_user(
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: DbSession = Depends(get_db)
) -> Optional[User]:
    """获取当前用户(可选，未登录)"""
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
# ============== 角色权限 ==============
class Role:
    """角色常量"""
    ADMIN = "admin"      # 管理员
    EDITOR = "editor"    # 编辑者  
    VIEWER = "viewer"    # 访客
ROLE_LEVELS = {
    Role.ADMIN: 3,
    Role.EDITOR: 2,
    Role.VIEWER: 1,
}
def require_role(required_role: str):
    """角色权限装饰器"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, current_user: User = Depends(get_current_user), **kwargs):
            user_level = ROLE_LEVELS.get(current_user.role, 0)
            required_level = ROLE_LEVELS.get(required_role, 1)
            
            if user_level < required_level:
                raise HTTPException(
                    status_code=403,
                    detail=f"需要{required_role}权限"
                )
            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator
def require_admin(current_user: User = Depends(get_current_user)):
    """仅管理员可访问"""
    if current_user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="仅管理员可访问")
    return current_user
def require_editor(current_user: User = Depends(get_current_user)):
    """编辑者可访问"""
    if current_user.role not in [Role.ADMIN, Role.EDITOR]:
        raise HTTPException(status_code=403, detail="需要编辑权限")
    return current_user
# ============== 数据隔离 ==============
def filter_by_org(query, current_user: User):
    """按组织过滤数据(租户隔离)"""
    if current_user.role == Role.ADMIN:
        return query  # 管理员看全部
    return query.filter(User.org_id == current_user.org_id)
def filter_by_owner(model_class, query, current_user: User, owner_field: str = "creator_id"):
    """按创建者过滤(用户私有数据)"""
    if current_user.role == Role.ADMIN:
        return query  # 管理员看全部
    return query.filter(getattr(model_class, owner_field) == current_user.id)
__all__ = [
    "hash_password", "verify_password",
    "create_access_token", "decode_token",
    "get_current_user", "get_optional_user",
    "Role", "require_role", "require_admin", "require_editor",
    "filter_by_org", "filter_by_owner"
]