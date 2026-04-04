"""
数据库会话配置
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

from app.core.config import settings


def _get_database_url() -> str:
    """
    获取数据库 URL，支持多种配置方式：
    1. 完整 DATABASE_URL 环境变量（最优先）
    2. 单独的数据库组件环境变量(DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME)
    """
    # 直接使用 DATABASE_URL 环境变量（如已存在）
    database_url_env = os.environ.get("DATABASE_URL")
    if database_url_env:
        return database_url_env

    # 从独立环境变量或 settings 读取组件
    db_host = os.environ.get("DB_HOST") or settings.DB_HOST
    db_port = os.environ.get("DB_PORT") or settings.DB_PORT
    db_user = os.environ.get("DB_USER") or settings.DB_USER
    db_password = os.environ.get("DB_PASSWORD") or settings.DB_PASSWORD
    db_name = os.environ.get("DB_NAME") or settings.DB_NAME

    # 返回构建的 URL
    return (
        f"postgresql+psycopg2://{db_user}:{db_password}"
        f"@{db_host}:{db_port}/{db_name}"
    )


_DATABASE_URL = _get_database_url()

# 显示日志时不暴露明文密码
_display_url = _DATABASE_URL.replace(
    _DATABASE_URL.split("://")[1].split("@")[0],
    "****"
)
print(f"[DB] Connecting to: {_display_url}")

# 创建数据库引擎
# SQLite 需要 check_same_thread=False
connect_args = {}
if "sqlite" in _DATABASE_URL:
    connect_arg["check_same_thread"] = False

engine = create_engine(
    _DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=3600,
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session():
    """获取数据库会话（用于任务）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库表"""
    from app.models.db_models import Base
    Base.metadata.create_all(bind=engine)