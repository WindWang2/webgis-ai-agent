"""
用户模型 - 组织/用户/RBAC角色定义
T012 用户权限体系
""""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Index
)
from sqlalchemy.orm import relationship, declarative_base
Base = declarative_base()
class Organization(Base):
    """组织/租户"""
    __tablename__ = "organizations"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    domain = Column(String(255))
    plan = Column(String(50), default="free")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class User(Base):
    """
    用户模型 - 包含RBAC角色和信息
    role: admin(管理员)/editor(编辑者)/viewer(访客)
    """
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"))
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255))
    role = Column(String(20), default="viewer", index=True)  # admin/editor/viewer
    avatar_url = Column(String(500))
    phone = Column(String(20))
    is_active = Column(Boolean, default=True)
    email_verified = Column(Boolean, default=False)
    last_login = Column(DateTime)
    login_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_user_org_role", "org_id", "role"),
    )
    
    organization = relationship("Organization", lazy="joined")

class UserSession(Base):
    """用户会话 - JWT黑名单/刷新令牌"""
    __tablename__ = "user_sessions"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_jti = Column(String(100), unique=True, index=True)  # JWT ID
    device_info = Column(String(255))
    ip_address = Column(String(50))
    refresh_token = Column(String(255))
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", lazy="joined")
__all__ = ["Base", "Organization", "User", "UserSession"]