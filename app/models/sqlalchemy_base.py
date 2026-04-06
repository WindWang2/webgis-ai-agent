"""
SQLAlchemy 单例实例和 Base 管理器
B011 Fix: 采用单例模式集中管理 SQLAlchemy，避免重复定义冲突
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import QueuePool
from typing import Generator, Optional
import os

_Base = None
_Engine = None
_SessionLocal = None

def get_base():
    """获取全局 Base 单例"""
    global _Base
    if _Base is None:
        _Base = declarative_base()
    return _Base

def get_engine(database_url: Optional[str] = None):
    """获取全局 Engine 单例"""
    global _Engine
    if _Engine is None:
        if database_url is None:
            database_url = _get_database_url()
        
        engine_args = {
            "poolclass": QueuePool,
            "pool_size": 5,
            "max_overflow": 10,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
        }
        
        if "sqlite" in database_url.lower():
            engine_args["connect_args"] = {"check_same_thread": False}
        
        _Engine = create_engine(database_url, **engine_args)
    
    return _Engine

def get_session_maker():
    """获取全局 SessionMaker 单例"""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            autocommit=False, 
            autoflush=False, 
            bind=get_engine()
        )
    return _SessionLocal

def _get_database_url() -> str:
    from app.core.config import settings
    database_url_env = os.environ.get("DATABASE_URL")
    if database_url_env:
        return database_url_env
    return (
        f"postgresql+psycopg2://{settings.DB_USER}:{settings.DB_PASSWORD}"
        f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    )

def get_db() -> Generator[Session, None, None]:
    SessionLocal = get_session_maker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_db_session():
    SessionLocal = get_session_maker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    from app.models.db_model import Base
    Base.metadata.create_all(bind=get_engine())

def shutdown_db():
    global _Engine, _SessionLocal, _Base
    if _Engine:
        _Engine.dispose()
        _Engine = None
    _SessionLocal = None
    _Base = None

__all__ = [
    "get_base",
    "get_engine", 
    "get_session_maker",
    "get_db",
    "get_db_session",
    "init_db",
    "shutdown_db"
]