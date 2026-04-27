"""Database Core Module"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings


class Base(DeclarativeBase):
    pass


def get_engine():
    connect_args = {}
    is_sqlite = settings.DATABASE_URL.startswith("sqlite")

    if is_sqlite:
        connect_args["check_same_thread"] = False

    engine_kwargs = {
        "url": settings.DATABASE_URL,
        "connect_args": connect_args,
    }

    if not is_sqlite:
        engine_kwargs.update({
            "pool_size": 10,
            "max_overflow": 20,
            "pool_timeout": 30,
            "pool_recycle": 3600,
        })

    return create_engine(**engine_kwargs)


Engine = get_engine()
SessionLocal = sessionmaker(bind=Engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=Engine)
