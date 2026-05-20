"""Regressions for two pre-existing bugs caught during UI verification:

1) conversations.user_id missing in SQLite (init_db wasn't called in lifespan)
2) ChatEngine._call_llm_stream was a sync generator due to dead yield after return
"""
import inspect

import pytest

from app.services.chat_engine import ChatEngine


def test_call_llm_stream_is_not_a_sync_generator():
    """Bug2 regression: 函数体里跟 return 之后的 dead yield 让整个函数变成 sync gen，
    导致 chat_stream 里 `async for` 抛 'requires __aiter__'。修复后两个 flag 都应为 False。"""
    assert not inspect.isgeneratorfunction(ChatEngine._call_llm_stream)
    assert not inspect.isasyncgenfunction(ChatEngine._call_llm_stream)


def test_lifespan_calls_init_db(monkeypatch):
    """Bug1 regression: lifespan 启动时必须跑 init_db, 否则 SQLite 里的 conversations
    永远缺 user_id 列。这里只验证生命周期调用路径有 init_db, 不真去开 DB。"""
    called = {"flag": False}

    def fake_init_db():
        called["flag"] = True

    # 模块级 patch，让 lifespan 内部 `from app.core.database import init_db` 拿到 fake
    import app.core.database as _db
    monkeypatch.setattr(_db, "init_db", fake_init_db)

    # 触发 lifespan 起步逻辑
    import asyncio
    from app.main import lifespan, app

    async def _drive():
        async with lifespan(app):
            pass

    # 工具初始化可能慢但不该抛
    asyncio.run(_drive())
    assert called["flag"] is True


def test_runtime_migration_adds_user_id_column(tmp_path, monkeypatch):
    """直接拉一个空的 SQLite, 跑 _apply_runtime_migrations, 确认 user_id 列被加上。"""
    from sqlalchemy import create_engine, text
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    # 手动建一个老 schema 的 conversations（没 user_id）
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE conversations (id VARCHAR(255) PRIMARY KEY, title VARCHAR(200), "
            "created_at DATETIME, updated_at DATETIME)"
        ))

    # 把 _apply_runtime_migrations 的 Engine 替换成临时的
    import app.core.database as _db
    monkeypatch.setattr(_db, "Engine", engine)

    # 让 SQLite 路径生效
    class _Stub:
        DATABASE_URL = f"sqlite:///{db_path}"
    monkeypatch.setattr(_db, "settings", _Stub())

    _db._apply_runtime_migrations()

    with engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info(conversations)")).fetchall()
        names = {row[1] for row in cols}
    assert "user_id" in names

    # idempotent: 再跑一次不报错
    _db._apply_runtime_migrations()
