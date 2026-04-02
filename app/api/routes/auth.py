"""
JWT Authentication API - Registration/Login/JWT/RBAC Permission
T012 User Permission System Development (Fixed Version)
"""
from fastapi import APIRouter, HTTPException, Depends, Body
from sqlalchemy.orm import Session as DbSession
from typing import Optional
from pydantic import BaseModel, EmailStr, validator
from datetime import datetime
import re

from app.core.config import get_settings
from app.db.session import get_db
from app.core.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    get_optional_user,
    Role
)
from app.models.db_models import User, Organization
from app.models.api_response import ApiResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()


# ============== Request/Response Models ==============
class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    org_name: Optional[str] = None
    
    @validator('username')
    def validate_username(cls, v):
        if len(v) < 3:
            raise ValueError('Username must be at least 3 characters')
        if len(v) > 50:
            raise ValueError('Username must not exceed 50 characters')
        if not re.match(r'^[\w]+$', v):
            raise ValueError('Only letters number underscore allowed')
        return v.strip().lower()
    
    @validator('password')
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('Password must be at least 6 character')
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


# ============== Auth Endpoints ==============
@router.post("/register", response_model=ApiResponse)
def register(req: RegisterRequest, db: DbSession = Depends(get_db)):
    """
    User Registration
    
    - username: Username (3-50 chars, alphanumeric + underscore only)
    - email: Email address
    - password: Password (at least 6 chars)
    - org_name: Optional, organization name (creates personal org)
    """
    # Check if username already exists
    if db.query(User).filter(User.username == req.username).first():
        return ApiResponse.fail(code="EXISTS_USER", message="Username already exists")
    
    # Check if email already registered
    if db.query(User).filter(User.email == req.email).first():
        return ApiResponse.fail(code="EXISTS_EMAIL", message="Email already registered")
    
    # Create or retrieve organization
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
    
    # Create user (first user becomes admin)
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
    
    # Generate token
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
    User Login
    
    Returns JWT access token (24-hour validity)
    """
    user = db.query(User).filter(User.email == req.email).first()
    
    if not user or not verify_password(req.password, user.password_hash):
        return ApiResponse.fail(code="AUTH_FAILED", message="Email or password incorrect")
    
    if not user.is_active:
        return ApiResponse.fail(code="DISABLED", message="Account has been disabled")
    
    # Update login info
    user.last_login = datetime.utcnow()
    user.login_count += 1
    db.commit()
    
    # Generate token
    token = create_access_token(data={
        "sub": str(user.id),
        "role": user.role,
        "org_id": str(user.org_id or "")
    })
    
    return ApiResponse.ok(data={
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 86400,  # 24 hours
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
    """Get current user information"""
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
):
    """Update personal information"""
    if full_name:
        current_user.full_name = full_name
    if avatar_url:
        current_user.avatar_url = avatar_url
    
    current_user.updated_at = datetime.utcnow()
    db.commit()
    
    return ApiResponse.ok(message="Updated successfully")


@router.post("/logout", response_model=ApiResponse)
def logout(
    authorization: str = Depends(HTTPBearer()),
    current_user: User = Depends(get_current_user)
):
    """Logout (add token to blacklist)"""
    # TODO: Add token JTI to blacklist
    return ApiResponse.ok(message="Logged out successfully")


# ============== RBAC Permission Endpoints ==============
@router.get("/permissions", response_model=ApiResponse)
def list_permissions(current_user: User = Depends(get_current_user)):
    """Get current user permission list"""
    perm_map = {
        "admin": [
            "layer:read", "layer:write", "layer:delete",
            "task:read", "task:write", "task:cancel",
            "user:manage", "org:manage"
        ],
        "editor": [
            "layer:read", "layer:write",
            "task:read", "task:write", "task:cancel"
        ],
        "viewer": ["layer:read", "task:read"]
    }
    
    return ApiResponse.ok(data={
        "role": current_user.role,
        "permissions": perm_map.get(current_user.role, [])
    })


# ============== User Management (Admin Only) ==============
@router.get("/users", response_model=ApiResponse)
def list_users(
    limit: int = 50,
    offset: int = 0,
    role_filter: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db)
):
    """User list (admin only)"""
    if current_user.role != Role.ADMIN:
        return ApiResponse.fail(code="FORBIDDEN", message="Admin only")
    
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
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "role": u.role,
                "is_active": u.is_active
            }
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
    """Change user role (admin only)"""
    if current_user.role != Role.ADMIN:
        return ApiResponse.fail(code="FORBIDDEN", message="Admin only")
    
    if new_role not in [Role.ADMIN, Role.EDITOR, Role.VIEWER]:
        return ApiResponse.fail(code="INVALID_ROLE", message="Invalid role")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return ApiResponse.fail(code="NOT_FOUND", message="User does not exist")
    
    user.role = new_role
    user.updated_at = datetime.utcnow()
    db.commit()
    
    return ApiResponse.ok(message=f"Role updated to {new_role}")


@router.post("/users/{user_id}/toggle", response_model=ApiResponse)
def toggle_user_status(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db)
):
    """Enable/disable user (admin only)"""
    if current_user.role != Role.ADMIN:
        return ApiResponse.fail(code="FORBIDDEN", message="Admin only")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return ApiResponse.fail(code="NOT_FOUND", message="User does not exist")
    
    user.is_active = not user.is_active
    db.commit()
    
    return ApiResponse.ok(message=f"User {'enabled' if user.is_active else 'disabled'}")


__all__ = ["router"]