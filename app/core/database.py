"""数据库核心模块 - SQLite"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings


class Base(DeclarativeBase):
    pass


def get_engine():
    return create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False}
    )


Engine = get_engine()
SessionLocal = sessionmaker(bind=Engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """创建所有表"""
    Base.metadata.create_all(bind=Engine)
