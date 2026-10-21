"""Regression tests for #521: RedisSessionStore map-state/data writers must not
json.dumps large payloads on the event loop, and the client-controlled map_state
entry DTOs must reject oversized payloads truthfully (pydantic ValidationError →
422), never silently truncate.

The offload sites under test (overwrite, set_map_state, update_layer_in_state,
remove_layer_from_state, append_event, append_map_action_event) used to call
``json.dumps`` inline on the event loop while store() already offloaded via
``asyncio.to_thread``. A multi-MB payload (checkpoint rollback blobs, k8s
100m-body chat/stream posts) stalls the whole loop for 0.6-4 s.

Technique mirrors tests/test_event_loop_offload_427.py: the slow work is faked
with a sync ``time.sleep`` that records its thread id, and the test asserts the
main event loop stays responsive *while* the work is running. The store is
backed by in-process fakeredis so no Redis server is needed.
"""
import asyncio
import json
import threading
import time

import pytest
from pydantic import ValidationError
from unittest.mock import patch

from app.api.routes.chat import ChatRequest, MapStatePushRequest
from app.services.session_data_redis import RedisSessionStore

_main_thread = threading.get_ident()
_real_dumps = json.dumps


def _make_store() -> RedisSessionStore:
    import fakeredis.aioredis

    return RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )


def _slow_dumps(observed: dict):
    """Fake json.dumps: records the calling thread and blocks 0.7s — long
    enough that the test can observe the loop staying responsive while a
    worker thread does the work."""

    def _dumps(obj, *a, **kw):
        if "thread" not in observed:
            observed["thread"] = threading.get_ident()
            time.sleep(0.7)
            return "{}"
        return _real_dumps(obj, *a, **kw)

    return _dumps


async def _assert_dumps_off_loop(store, method_call, observed: dict):
    """Mirror of _assert_loop_responsive_while (test_event_loop_offload_427):
    with the dump offloaded the task is still running when a 0.05s timer fires;
    with the dump on the loop the task finishes first and the assertion fails."""
    task = asyncio.create_task(method_call())
    await asyncio.sleep(0.15)  # let it enter the slow work
    assert not task.done(), "work finished before the test could observe it"

    ticks = []

    async def _tick():
        await asyncio.sleep(0.05)
        ticks.append(True)

    tick = asyncio.create_task(_tick())
    await asyncio.sleep(0.15)
    assert tick.done() and ticks, "event loop was blocked during the work"
    assert not task.done(), "event loop was blocked during the work"
    await task
    assert observed["thread"] != _main_thread, "json.dumps ran on the event loop thread"


@pytest.mark.asyncio
async def test_overwrite_dumps_off_loop(monkeypatch):
    store = _make_store()
    sid = "sess-521-overwrite"
    ref_id = await store.store(sid, {"seed": True})  # seeded before the patch
    observed = {}
    monkeypatch.setattr(json, "dumps", _slow_dumps(observed))

    await _assert_dumps_off_loop(
        store,
        lambda: store.overwrite(sid, ref_id, {"features": [{"type": "Feature"} for _ in range(50000)]}),
        observed,
    )


@pytest.mark.asyncio
async def test_set_map_state_dumps_off_loop(monkeypatch):
    store = _make_store()
    sid = "sess-521-set-map-state"
    observed = {}
    monkeypatch.setattr(json, "dumps", _slow_dumps(observed))

    await _assert_dumps_off_loop(
        store,
        lambda: store.set_map_state(sid, "viewport", {"zoom": 10, "pad": "x" * 200000}),
        observed,
    )


@pytest.mark.asyncio
async def test_update_layer_in_state_dumps_off_loop(monkeypatch):
    store = _make_store()
    sid = "sess-521-update-layer"
    layers = [{"id": f"layer-{i}", "style": {"color": "#ff0000"}} for i in range(3000)]
    assert await store.set_map_state(sid, "layers", layers) is True  # seeded pre-patch
    observed = {}
    monkeypatch.setattr(json, "dumps", _slow_dumps(observed))

    await _assert_dumps_off_loop(
        store,
        lambda: store.update_layer_in_state(sid, "layer-0", {"visible": True}),
        observed,
    )


@pytest.mark.asyncio
async def test_remove_layer_from_state_dumps_off_loop(monkeypatch):
    store = _make_store()
    sid = "sess-521-remove-layer"
    layers = [{"id": f"layer-{i}", "style": {"color": "#ff0000"}} for i in range(3000)]
    assert await store.set_map_state(sid, "layers", layers) is True  # seeded pre-patch
    observed = {}
    monkeypatch.setattr(json, "dumps", _slow_dumps(observed))

    await _assert_dumps_off_loop(
        store,
        lambda: store.remove_layer_from_state(sid, "layer-0"),
        observed,
    )


@pytest.mark.asyncio
async def test_append_event_dumps_off_loop(monkeypatch):
    store = _make_store()
    sid = "sess-521-append-event"
    observed = {}
    monkeypatch.setattr(json, "dumps", _slow_dumps(observed))

    await _assert_dumps_off_loop(
        store,
        lambda: store.append_event(sid, "tool_result", {"payload": "x" * 200000}),
        observed,
    )


@pytest.mark.asyncio
async def test_append_map_action_event_dumps_off_loop(monkeypatch):
    store = _make_store()
    sid = "sess-521-append-ack"
    observed = {}
    monkeypatch.setattr(json, "dumps", _slow_dumps(observed))

    await _assert_dumps_off_loop(
        store,
        lambda: store.append_map_action_event(sid, {"action_id": "a-1", "payload": "x" * 200000}),
        observed,
    )


# ─── DTO caps: client-controlled map_state entries must reject oversized ─────
# ─── payloads truthfully (422 via pydantic), never silently truncate. ────────


def test_map_state_push_request_layers_capped():
    with pytest.raises(ValidationError):
        MapStatePushRequest(layers=[{"id": f"layer-{i}"} for i in range(200)])


def test_map_state_push_request_accepts_bounded_layers():
    req = MapStatePushRequest(layers=[{"id": f"layer-{i}"} for i in range(128)])
    assert len(req.layers) == 128


def test_map_state_push_request_serialized_size_capped():
    with pytest.raises(ValidationError):
        MapStatePushRequest(viewport={"pad": "x" * 300_000})


def test_map_state_push_request_accepts_normal_payload():
    req = MapStatePushRequest(layers=[{"id": "l1"}], viewport={"zoom": 10}, base_layer="osm")
    assert req.layers == [{"id": "l1"}]
    assert req.viewport == {"zoom": 10}


def test_chat_request_map_state_serialized_size_capped():
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", map_state={"big": "x" * 300_000})


def test_chat_request_accepts_normal_map_state():
    req = ChatRequest(message="hi", map_state={"viewport": {"zoom": 10}, "layers": [{"id": "l1"}]})
    assert req.map_state["viewport"]["zoom"] == 10


# ─── #1717: ChatRequest 客户端可控字符串字段边界 + 全局请求体预算 ────────────


def test_chat_request_session_id_bounded():
    """#1717：session_id 客户端可控 —— 补 max_length=128（与 lakehouse_schema 对齐）。"""
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", session_id="s" * 129)
    assert ChatRequest(message="hi", session_id="s" * 128).session_id == "s" * 128
    assert ChatRequest(message="hi", session_id=None).session_id is None


def test_chat_request_skill_name_bounded():
    """#1717：skill_name 客户端可控 —— 补 max_length=128。"""
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", skill_name="k" * 129)
    assert ChatRequest(message="hi", skill_name="k" * 128).skill_name == "k" * 128


def test_chat_request_project_id_bounded():
    """#1717：project_id 客户端可控 —— 补 max_length=64。"""
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", project_id="p" * 65)
    assert ChatRequest(message="hi", project_id="p" * 64).project_id == "p" * 64
    assert ChatRequest(message="hi", project_id="proj_minjiang").project_id == "proj_minjiang"


def test_chat_request_size_gate_applies_without_map_state():
    """#1717：序列化大小闸不得以 map_state 缺席为豁免。

    旧实现是 ``self.map_state is not None and len(...) > 256KB`` —— 无
    map_state 的巨型请求体完全无界。现为全局请求体预算：强制把
    model_dump_json 钳出超限序列化，map_state 缺席时同样必须 422。
    """
    req = ChatRequest(message="hi")
    assert req.map_state is None  # 前提：map_state 缺席

    oversized = '{"blob": "' + "x" * (300 * 1024) + '"}'
    with patch.object(ChatRequest, "model_dump_json", return_value=oversized):
        with pytest.raises(ValidationError, match="exceeds 256KB"):
            ChatRequest(message="hi")


def test_chat_request_size_gate_message_only_still_accepts():
    """#1717 反向边界：正常小请求体（无 map_state）不得被全局闸误伤。"""
    req = ChatRequest(message="hi", session_id="sess-1", project_id="proj_abc")
    assert req.message == "hi"
