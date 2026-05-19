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
    _apply_runtime_migrations()


def _apply_runtime_migrations() -> None:
    """运行守卫式增量迁移：仅 SQLite。

    Base.metadata.create_all 只创建缺失的表，对已存在的表不会补字段。
    本项目还没上 Alembic（M10），所以这里给少量轻量字段做最小幂等迁移。
    Postgres 部署请用 Alembic，不会走到这里。
    """
    import logging
    from sqlalchemy import text
    log = logging.getLogger(__name__)
    if not str(settings.DATABASE_URL).startswith("sqlite"):
        return
    try:
        with Engine.begin() as conn:
            # conversations.user_id：A2 资源所有权改造，新增的字段
            cols = conn.execute(text("PRAGMA table_info(conversations)")).fetchall()
            names = {row[1] for row in cols}
            if "user_id" not in names:
                conn.execute(text(
                    "ALTER TABLE conversations ADD COLUMN user_id VARCHAR(255)"
                ))
                log.info("[Migration] added conversations.user_id")
                # SQLite 不在 ADD COLUMN 时自动建索引，单独建
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_conversations_user_id ON conversations(user_id)"
                ))
    except Exception as e:
        log.warning(f"[Migration] runtime migration skipped: {e}")
