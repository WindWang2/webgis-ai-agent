"""数据库模块测试"""
import os
import pytest
from sqlalchemy import inspect


def test_import_database():
    from app.core.database import Base
    assert Base is not None


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "sqlite").startswith("sqlite"),
    reason="engine is Postgres in CI (DATABASE_URL != sqlite); sqlite-specific check skipped",
)
def test_engine_is_sqlite():
    from app.core.database import get_engine
    engine = get_engine()
    assert "sqlite" in str(engine.url)


def test_models_defined():
    from app.core.database import Base
    table_names = Base.metadata.tables.keys()
    assert "conversations" in table_names
    assert "messages" in table_names
    assert "layers" in table_names


def test_to_sync_url_bare_postgresql():
    """裸 postgresql:// 显式钉 psycopg2 驱动（#1592）"""
    from app.core.database import _to_sync_url
    url = "postgresql://user:pass@localhost:5432/db"
    assert _to_sync_url(url) == "postgresql+psycopg2://user:pass@localhost:5432/db"


def test_to_sync_url_bare_postgres():
    """裸 postgres:// 同样钉 psycopg2 驱动（#1592）"""
    from app.core.database import _to_sync_url
    url = "postgres://user:pass@localhost:5432/db"
    assert _to_sync_url(url) == "postgresql+psycopg2://user:pass@localhost:5432/db"


def test_to_sync_url_keeps_explicit_driver():
    """已带 '+' 驱动后缀的 URL 原样返回"""
    from app.core.database import _to_sync_url
    assert _to_sync_url("postgresql+psycopg2://user@host/db") == "postgresql+psycopg2://user@host/db"
    assert _to_sync_url("postgresql+asyncpg://user@host/db") == "postgresql+asyncpg://user@host/db"


def test_to_sync_url_sqlite_unchanged():
    """sqlite URL 原样返回"""
    from app.core.database import _to_sync_url
    assert _to_sync_url("sqlite:///./data/webgis.db") == "sqlite:///./data/webgis.db"


def test_init_db_creates_tables(tmp_path):
    """用临时数据库测试建表"""
    from sqlalchemy import create_engine
    from app.core.database import Base

    db_path = str(tmp_path / "test.db")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert "conversations" in tables
    assert "messages" in tables
    assert "layers" in tables
