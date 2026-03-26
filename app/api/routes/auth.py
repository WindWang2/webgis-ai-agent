"""
用户认证 API - 注册/登录/JWT/RBAC权限
T012 用户权限体系开发
""""
from fastapi import APIRouter, HTTPException, Depends, Body
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session as DbSession
from typing import Optional
from pydantic import BaseModel, EmailStr, validator
from datetime import datetime, timedelta
import re
from app.core.config import get_settings
from app.db.session import get_db
from app.core.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, get_optional_user, Role
)
from app.models.user_model import User, Organization
from app.models.api_response import ApiResponse
router = APIRouter(prefix="/auth", tags=["认证"])
settings = get_settings()
security = HTTPBearer(auto_error=False)
# ============== 请求/响应模型 ==============
class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    org_name: Optional[str] = None  # 可选创建组织
    
    @validator('username')
    def validate_username(cls, v):
        if len(v) < 3: raise ValueError('用户名至少3字符')
        if len(v) > 50: raise ValueError('用户名最多50字符')
        if not re.match(r'^[\w]+$', v): raise ValueError('仅字母数字下划线')
        return v.strip().lower()
    
    @validator('password')
    def validate_password(cls, v):
        if len(v) < 6: raise ValueError('密码至少6字符')
        return v
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒
    user: dict
# ============== 认证端点 ==============
@router.post("/register", response_model=ApiResponse)
def register(req: RegisterRequest, db: DbSession = Depends(get_db)):
    """
    用户注册
    
    - username: 用户名(3-50字符，仅字母数字)
    - email: 邮箱
    - password: 密码(至少6字符)
    - org_name: 可选，组织名称(创建个人组织)
    """
    # 检查用户名和邮箱是否存在
    if db.query(User).filter(User.username == req.username).first():
        return ApiResponse.fail(code="EXISTS_USER", message="用户名已存在")
    if db.query(User).filter(User.email == req.email).first():
        return ApiResponse.fail(code="EXISTS_EMAIL", message="邮箱已注册")
    
    # 创建或获取组织
    org_id = None
    if req.org_name:
        org = db.query(Organization).filter(
            Organization.slug == req.org_name.lower().replace(' ', '-')
        ).first()
        if not org:
            org = Organization(
                name=req.org_name,
                slug=req.org_name.lower().replace(' ', '-')
            )
            db.add(org)
            db.flush()
        org_id = org.id
    
    # 创建用户(第一个用户为admin)
    role = Role.ADMIN if db.query(User).count() == 0 else Role.VIEWER
    user = User(
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        role=role,
        org_id=org_id,
        is_active=True,
        email_verified=False
    )
    db.add(user)
    db.commit()
    
    # 生成令牌
    token = create_access_token(data={"sub": str(user.id), "role": role})
    
    return ApiResponse.ok(data={
        "user_id": user.id,
        "username": user.username,
        "role": role,
        "token": token
    })
@router.post("/login", response_model=ApiResponse)
def login(req: LoginRequest, db: DbSession = Depends(get_db)):
    """
    用户登录
    
    返回 JWT 访问令牌(24小时有效期)
    """
    user = db.query(User).filter(User.email == req.email).first()
    
    if not user or not verify_password(req.password, user.password_hash):
        return ApiResponse.fail(code="AUTH_FAILED", message="邮箱或密码错误")
    
    if not user.is_active:
        return ApiResponse.fail(code="DISABLED", message="账户已禁用")
    
    # 更新登录信息
    user.last_login = datetime.utcnow()
    user.login_count += 1
    db.commit()
    
    # 生成令牌
    token = create_access_token(data={
        "sub": str(user.id),
        "role": user.role,
        "org_id": str(user.org_id or "")
    })
    
    return ApiResponse.ok(data={
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 86400,
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "org_id": user.org_id
        }
    })
@router.get("/me", response_model=ApiResponse)
def get_profile(current_user: User = Depends(get_current_user)):
    """获取当前用户信息""""
    return ApiResponse.ok(data={
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "org_id": current_user.org_id,
        "avatar_url": current_user.avatar_url,
        "created_at": current_user.created_at.isoformat(),
        "last_login": current_user.last_login.isoformat() if current_user.last_login else None
    })
@router.put("/me", response_model=ApiResponse)
def update_profile(
    full_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db)
): """更新个人信息""";
    if full_name: current_user.full_name = full_name
    if avatar_url: current_user.avatar_url = avatar_url
    current_user.updated_at = datetime.utcnow()
    db.commit()
    return ApiResponse.ok(message="更新成功")
@router.post("/logout", response_model=ApiResponse)
def logout(authorization: str = Depends(HTTPBearer()), current_user: User = Depends(get_current_user)):
    """退出登录(令牌加入黑名单)"""
    # TODO: 将 token JTI 加入黑名单
    return ApiResponse.ok(message="已退出登录")
# ============== RBAC 权限端点 ==============
@router.get("/permissions", response_model=ApiResponse)
def list_permissions(current_user: User = Depends(get_current_user)):
    """获取当前用户权限列表"""
    perm_map = {
        "admin": ["layer:read", "layer:write", "layer:delete", "task:read", "task:write", "task:cancel", "user:manage", "org:manage"],
        "editor": ["layer:read", "layer:write", "task:read", "task:write", "task:cancel"],
        "viewer": ["layer:read", "task:read"]
    }
    return ApiResponse.ok(data={"role": current_user.role, "permissions": perm_map.get(current_user.role, [])})
# ============== 用户管理(仅Admin) ==============
@router.get("/users", response_model=ApiResponse)
def list_users(
    limit: int = 50,
    offset: int = 0,
    role_filter: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db)
):
    """用户列表(仅Admin)"""
    if current_user.role != Role.ADMIN:
        return ApiResponse.fail(code="FORBIDDEN", message="仅管理员")
    
    query = db.query(User)
    if role_filter:
        query = query.filter(User.role == role_filter)
    
    total = query.count()
    users = query.offset(offset).limit(limit).all()
    
    return ApiResponse.ok(data={
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {"id": u.id, "username": u.username, "email": u.email, "role": u.role, "is_active": u.is_active}
            for u in users
        ]
    })
@router.post("/users/{user_id}/role", response_model=ApiResponse)
def update_user_role(
    user_id: int,
    new_role: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db)
):
    """修改用户角色(仅Admin)"""
    if current_user.role != Role.ADMIN:
        return ApiResponse.fail(code="FORBIDDEN", message="仅管理员")
    
    if new_role not in [Role.ADMIN, Role.EDITOR, Role.VIEWER]:
        return ApiResponse.fail(code="INVALID_ROLE", message="无效角色")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return ApiResponse.fail(code="NOT_FOUND", message="用户不存在")
    
    user.role = new_role
    user.updated_at = datetime.utcnow()
    db.commit()
    
    return ApiResponse.ok(message=f"角色已更新为{new_role}")
@router.post("/users/{user_id}/toggle", response_model=ApiResponse)
def toggle_user_status(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db)
):
    """启用/禁用用户(仅Admin)"""
    if current_user.role != Role.ADMIN:
        return ApiResponse.fail(code="FORBIDDEN", message="仅管理员")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return ApiResponse.fail(code="NOT_FOUND", message="用户不存在")
    
    user.is_active = not user.is_active
    db.commit()
    
    return ApiResponse.ok(message=f"用户已{'启用' if user.is_active else '禁用'}")
__all__ = ["router"]