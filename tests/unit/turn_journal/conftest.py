"""turn_journal 测试夹具：临时 SQLite 账本 + sink 单例隔离。

每个测试拿到独立的内存 SQLite（StaticPool 单连接，与 workflow_runtime
store 测试同款）；kernel 接线测试把 ``TurnJournalSink`` 单例替换为指向
该 SQLite 的实例，跑完 flush 后复位——生产单例零残留。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base


@pytest.fixture()
def ledger_factory():
    """独立内存 SQLite 会话工厂（turn_events 等全部建表）。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def ledger(ledger_factory):
    from app.services.turn_journal.ledger import TurnEventLedger

    return TurnEventLedger(factory=ledger_factory)


@pytest.fixture()
def journal_sink(ledger):
    """把进程级 sink 单例替换为指向测试 SQLite 的实例（复位保证）。"""
    from app.services.turn_journal import sink as sink_mod
    from app.services.turn_journal.sink import TurnJournalSink

    sink_mod.reset_turn_journal_sink()
    test_sink = TurnJournalSink(ledger=ledger, queue_max=8)
    sink_mod._sink = test_sink
    yield test_sink
    sink_mod.reset_turn_journal_sink()
