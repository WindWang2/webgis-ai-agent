"""Async interface tests for both session_data backends."""
import pytest

from app.services.session_data import SessionDataManager


async def test_memory_store_returns_ref():
    sdm = SessionDataManager()
    ref = await sdm.store("s1", {"x": 1})
    assert ref.startswith("ref:")


async def test_memory_get_returns_stored_data():
    sdm = SessionDataManager()
    ref = await sdm.store("s2", {"hello": "world"})
    data = await sdm.get("s2", ref)
    assert data == {"hello": "world"}


async def test_memory_resolve_alias():
    sdm = SessionDataManager()
    ref = await sdm.store("s3", {})
    await sdm.set_alias("s3", ref, "my-layer")
    resolved = await sdm.resolve_alias("s3", "my-layer")
    assert resolved == ref


async def test_memory_get_session_metadata():
    sdm = SessionDataManager()
    await sdm.store("s4", {"a": 1})
    meta = await sdm.get_session_metadata("s4")
    assert "map_state" in meta
    assert "list_refs" in meta
    assert "event_log" in meta
    assert "started_at" in meta


async def test_memory_append_and_get_event_log():
    sdm = SessionDataManager()
    await sdm.append_event("s5", "click", {"x": 1})
    log = await sdm.get_event_log("s5")
    assert len(log) == 1
    assert log[0]["event"] == "click"


async def test_memory_clear_session():
    sdm = SessionDataManager()
    await sdm.store("s6", {})
    await sdm.clear_session("s6")
    refs = await sdm.list_refs("s6")
    assert refs == {}
