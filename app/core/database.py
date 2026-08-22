"""Database Core Module"""
import os

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

    from sqlalchemy.pool import NullPool
    _async_url = _to_async_url(settings.DATABASE_URL)
    _async_kwargs: dict = {}
    if _async_url.startswith("sqlite+aiosqlite"):
        # aiosqlite 在默认 QueuePool 下跨 asyncio loop 会泄露 WorkerThread 导致 pytest 挂起。
        # NullPool 确保 Session 关闭时立即销毁 aiosqlite 连接与后台线程。
        _async_kwargs["poolclass"] = NullPool
        _async_kwargs["connect_args"] = {"check_same_thread": False}
    elif settings.is_production():
        # 生产环境用 QueuePool 复用连接（asyncpg 连接池在长生命周期下高效）。
        _async_kwargs.update(
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=3600,
        )
    else:
        # 开发/测试环境（含 CI）：NullPool 让每个 AsyncSession 拿独立连接并在关闭时立即归还。
        # TestClient 在 threadpool 跑 async 路由，QueuePool 会把同一个 asyncpg 连接并发派给
        # 多个 session，触发 'cannot perform operation: another operation is in progress'。
        _async_kwargs["poolclass"] = NullPool

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
    """Create schema at startup.

    audit #839: create_all previously ran on EVERY engine including Postgres,
    silently creating tables outside Alembic's version control (the schema
    drift that migrations 0017-0020 later had to close proves the risk is
    real in this repo). Mirror the runtime-migration gate: create_all is a
    SQLite/dev convenience; Postgres deployments own their schema via Alembic
    (README deployment path). Set ALLOW_CREATE_ALL_ON_POSTGRES=1 to restore
    the old behavior explicitly (single-container experiments).
    """
    import logging

    log = logging.getLogger(__name__)
    is_sqlite = str(settings.DATABASE_URL).startswith("sqlite")
    if is_sqlite or os.environ.get("ALLOW_CREATE_ALL_ON_POSTGRES") == "1":
        if not is_sqlite:
            log.warning(
                "[init_db] ALLOW_CREATE_ALL_ON_POSTGRES=1 — create_all running "
                "against a non-sqlite database outside Alembic's control"
            )
        Base.metadata.create_all(bind=Engine)
    else:
        log.info(
            "[init_db] non-sqlite database — skipping create_all; schema is "
            "owned by Alembic (audit #839)"
        )
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
