"""数据库模块测试"""
import os
import pytest
from sqlalchemy import inspect


def test_import_database():
    from app.core.database import Base, get_engine, SessionLocal, init_db
    assert Base is not None


def test_engine_is_sqlite():
    from app.core.database import get_engine
    engine = get_engine()
    assert "sqlite" in str(engine.url)


def test_models_defined():
    from app.core.database import Base
    from app.models.db_model import Conversation, Message, Layer
    table_names = Base.metadata.tables.keys()
    assert "conversations" in table_names
    assert "messages" in table_names
    assert "layers" in table_names


def test_init_db_creates_tables(tmp_path):
    """用临时数据库测试建表"""
    from sqlalchemy import create_engine
    from app.core.database import Base
    from app.models.db_model import Conversation, Message, Layer

    db_path = str(tmp_path / "test.db")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert "conversations" in tables
    assert "messages" in tables
    assert "layers" in tables
