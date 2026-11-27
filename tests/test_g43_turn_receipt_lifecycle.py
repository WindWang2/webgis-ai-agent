"""G43（ADR-0295）lifecycle 包装层测试：C22 best-effort 契约。

store 抛错（sqlite 连接失败 / 序列化失败等）必须被包装层吞掉并返回
None，绝不让回执面异常穿透打断 live turn。
"""
from __future__ import annotations

from app.services import turn_context_store as store
from app.services.chat import turn_receipt_lifecycle as lc


async def _boom(*a, **kw):
    raise RuntimeError("db down")


class _BadReceipt:
    mode = "typed"

    def as_bounded_dict(self):
        raise RuntimeError("serialize failed")


async def test_persist_assembly_receipt_store_error_returns_none(monkeypatch):
    monkeypatch.setattr(store, "record_assembly", _boom)
    assert await lc.persist_assembly_receipt(
        None, session_id="s1", turn_id="t1"
    ) is None


async def test_persist_assembly_receipt_serialize_error_returns_none(monkeypatch):
    called = []

    async def _record(**kw):
        called.append(kw)
        return 1

    monkeypatch.setattr(store, "record_assembly", _record)
    assert await lc.persist_assembly_receipt(
        _BadReceipt(), session_id="s1", turn_id="t1"
    ) is None
    assert called == []


async def test_persist_assembly_receipt_read_error_returns_none(monkeypatch):
    async def _record(**kw):
        return 1

    monkeypatch.setattr(store, "record_assembly", _record)
    monkeypatch.setattr(store, "get_receipt", _boom)
    assert await lc.persist_assembly_receipt(
        None, session_id="s1", turn_id="t1"
    ) is None


async def test_mark_helpers_swallow_store_errors(monkeypatch):
    monkeypatch.setattr(store, "record_not_sent", _boom)
    monkeypatch.setattr(store, "record_submitted", _boom)
    monkeypatch.setattr(store, "record_unknown", _boom)
    monkeypatch.setattr(store, "record_turn_outcome", _boom)

    assert await lc.mark_not_sent(
        "t1", reason="pi_send_error", session_id="s1"
    ) is None
    assert await lc.mark_submitted(
        "t1", disposition="started", session_id="s1"
    ) is None
    assert await lc.mark_unknown("t1", session_id="s1") is None
    await lc.mark_turn_outcome("t1", outcome="failed")


async def test_mark_helpers_swallow_read_errors(monkeypatch):
    async def _ok(*a, **kw):
        return True

    monkeypatch.setattr(store, "record_not_sent", _ok)
    monkeypatch.setattr(store, "record_submitted", _ok)
    monkeypatch.setattr(store, "record_unknown", _ok)
    monkeypatch.setattr(store, "get_receipt", _boom)

    assert await lc.mark_not_sent(
        "t1", reason="pi_send_error", session_id="s1"
    ) is None
    assert await lc.mark_submitted(
        "t1", disposition="started", session_id="s1"
    ) is None
    assert await lc.mark_unknown("t1", session_id="s1") is None
