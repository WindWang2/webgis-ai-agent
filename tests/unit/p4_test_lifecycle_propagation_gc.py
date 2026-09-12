"""Data Lifecycle — PropagationReport 投影与 GC 委托面（P4 补强 D4/D5）。"""
from __future__ import annotations

import pytest

from app.services.data_lifecycle.gc import execute_session_gc
from app.services.data_lifecycle.service import PropagationReport


def test_propagation_report_to_dict_is_readonly_serializable() -> None:
    report = PropagationReport(
        source_artifact_id="art-1", verdict="stale",
        marked=["art-2", "art-3"], already_stale=["art-4"],
        not_in_ledger=["art-5"])
    d = report.to_dict()
    assert d["source_artifact_id"] == "art-1"
    assert d["verdict"] == "stale"
    assert d["marked_stale"] == ["art-2", "art-3"]
    assert d["marked_count"] == 2
    assert d["already_stale"] == ["art-4"]
    assert d["not_in_ledger"] == ["art-5"]


def test_propagation_report_empty_marked_has_zero_count() -> None:
    report = PropagationReport("src", "fresh", [], [], [])
    assert report.to_dict()["marked_count"] == 0


@pytest.mark.asyncio
async def test_session_gc_delegates_to_registry(monkeypatch) -> None:
    # GC 完全委托 collect_orphan_refs（锁内活引用重检纪律）—— 委托面钉住。
    called = {"args": None}

    async def _fake_collect(session_id):
        called["args"] = session_id
        return ["ref:orphan-1"]

    import app.services.artifact_registry as registry
    monkeypatch.setattr(registry, "collect_orphan_refs", _fake_collect)
    removed = await execute_session_gc("s-gc")
    assert called["args"] == "s-gc"
    assert removed == ["ref:orphan-1"]


@pytest.mark.asyncio
async def test_session_gc_empty_is_empty_list(monkeypatch) -> None:
    import app.services.artifact_registry as registry

    async def _none(session_id):
        return []

    monkeypatch.setattr(registry, "collect_orphan_refs", _none)
    assert await execute_session_gc("s-empty") == []
