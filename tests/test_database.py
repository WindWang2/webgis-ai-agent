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


def test_to_async_url_bare_postgresql():
    """裸 postgresql:// → asyncpg 驱动"""
    from app.core.database import _to_async_url
    url = "postgresql://user:pass@localhost:5432/db"
    assert _to_async_url(url) == "postgresql+asyncpg://user:pass@localhost:5432/db"


def test_to_async_url_bare_postgres():
    """裸 postgres:// 同样改写为 asyncpg"""
    from app.core.database import _to_async_url
    assert _to_async_url("postgres://user@host/db") == "postgresql+asyncpg://user@host/db"


def test_to_async_url_psycopg2_pinned():
    """显式钉 psycopg2 的 URL 对称改写为 asyncpg（#1641）。

    同步侧 _to_sync_url 原样容忍已带 '+' 的 URL；async 侧原样返回会让
    create_async_engine 抛 InvalidRequestError（启动期响亮失败）。
    """
    from app.core.database import _to_async_url
    assert _to_async_url("postgresql+psycopg2://user@host/db") == (
        "postgresql+asyncpg://user@host/db"
    )


def test_to_async_url_sqlite_and_asyncpg_unchanged():
    """sqlite 换 aiosqlite；已带 asyncpg 驱动的 URL 原样返回"""
    from app.core.database import _to_async_url
    assert _to_async_url("sqlite:///./data/webgis.db") == "sqlite+aiosqlite:///./data/webgis.db"
    assert _to_async_url("postgresql+asyncpg://user@host/db") == "postgresql+asyncpg://user@host/db"


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


def test_layer_insert_autoincrements_id_on_sqlite(tmp_path):
    """P1 回归：layers.id 是 BigInteger 自增主键。SQLite 只把
    「INTEGER PRIMARY KEY」当 rowid 别名；缺 with_variant(Integer, "sqlite")
    时 create_all 建成 BIGINT 主键，不显式给 id 的 Layer 插入 NOT NULL 失败
    （图层创建整体不可用）。同文件其余 BigInteger 自增主键均有该 variant。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    import app.models  # noqa: F401  注册全部表（含 layers 的 FK 依赖表）
    from app.core.database import Base
    from app.models.db_model import Layer, Organization

    db_path = str(tmp_path / "layer_id.db")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        s.add(Organization(id=1, name="org", slug="org"))
        s.commit()
        # 不显式给 id：修复前 sqlite3.IntegrityError NOT NULL constraint failed: layers.id
        layer = Layer(org_id=1, name="layer-autoincrement", layer_type="vector")
        s.add(layer)
        s.commit()
        assert layer.id is not None
        assert s.get(Layer, layer.id) is layer
    engine.dispose()
