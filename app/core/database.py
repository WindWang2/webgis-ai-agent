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


# Async support
def _to_async_url(url: str) -> str:
    """Transform a sync DB URL into an async-compatible driver URL."""
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


try:
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    _async_url = _to_async_url(settings.DATABASE_URL)
    _async_kwargs: dict = {}
    if _async_url.startswith("sqlite+aiosqlite"):
        # aiosqlite 不支持连接池（单文件单连接），pool_size/max_overflow 会被忽略
        # 但仍会触发 deprecation 警告，且必须给 connect_args 防止跨线程报错。
        _async_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        _async_kwargs.update(
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=3600,
        )

    AsyncEngine = create_async_engine(_async_url, **_async_kwargs)
    AsyncSessionLocal = async_sessionmaker(bind=AsyncEngine, expire_on_commit=False)
except ImportError:
    AsyncEngine = None  # type: ignore[misc,assignment]
    AsyncSessionLocal = None  # type: ignore[misc,assignment]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    """Async DB dependency for FastAPI routes. Falls back to threadpool if async driver unavailable."""
    if AsyncSessionLocal is not None:
        async with AsyncSessionLocal() as db:
            yield db
    else:
        # Fallback: run sync session in threadpool
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()


def init_db():
    Base.metadata.create_all(bind=Engine)
