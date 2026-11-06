"""Behavioral regressions from the final full-issue coverage audit."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import json

import fakeredis
import fakeredis.aioredis
import pytest


@pytest.mark.asyncio
async def test_provenance_append_preserves_concurrent_worker_entries(monkeypatch):
    import app.services.gis_world_state.provenance as module
    from app.services.session_data_redis import RedisSessionStore

    server = fakeredis.FakeServer()
    first = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    second = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    stores = [RedisSessionStore("redis://unused", redis=client) for client in (first, second)]
    entries = [module.ProvenanceEntry(seq=i, ts="now", origin="user", actor="test",
               kind="PatchLayerPresentationIntent", target=f"layer-{i}", revision=i) for i in (1, 2)]
    monkeypatch.setattr(module, "session_data_manager", stores[0])
    task = asyncio.create_task(module.append_provenance("session", entries[0]))
    await asyncio.sleep(0)
    monkeypatch.setattr(module, "session_data_manager", stores[1])
    await asyncio.gather(task, module.append_provenance("session", entries[1]))
    stored = json.loads(await first.hget(stores[0]._state_key("session"), module._PROVENANCE_KEY))
    assert {entry["target"] for entry in stored} == {"layer-1", "layer-2"}
    await first.aclose()
    await second.aclose()


@pytest.mark.asyncio
async def test_world_state_uses_latest_presentation_and_durable_user_marker(monkeypatch):
    import app.services.gis_world_state.state as module

    entries = [dict(target="shown", kind="PatchLayerPresentationIntent", origin="user",
                    revision=1, seq=1, detail={"visible": False}),
               dict(target="shown", kind="PatchLayerPresentationIntent", origin="user",
                    revision=2, seq=2, detail={"visible": True})]
    mapspec = {"layers": [{"id": "durable-hidden", "cartographic_intent": {
        "presentation_owner": "user", "expected_visible": False}}]}
    store = SimpleNamespace(get_map_state=AsyncMock(return_value={"mapspec": mapspec, "_gis_provenance": entries}))
    monkeypatch.setattr(module, "session_data_manager", store)
    snapshot = await module.build_world_state("session")
    assert snapshot["interaction"]["user_hidden_layers"] == ["durable-hidden"]


def test_ws_semantic_anchors_ignore_auth_rate_and_ownership_comment_text(monkeypatch):
    import app.lib.quality.api_compat as module

    original_read = module._read
    fake = '''# rate ownership check token_version Sec-WebSocket-Protocol
async def endpoint(websocket):
    await websocket.accept()
'''
    monkeypatch.setattr(module, "_read", lambda path: fake if path == "app/api/routes/ws.py" else original_read(path))
    anchors = module._ws_auth_anchors()
    assert anchors["session_ownership_fail_closed"] is False
    assert anchors["per_ip_rate_limited"] is False


@pytest.mark.asyncio
async def test_document_deletion_does_not_compact_on_the_request_path():
    from app.services.rag.engine import KnowledgeEngine, TenantContext

    store = SimpleNamespace(mark_deleted=lambda *a, **kw: True,
                            get_stats=lambda: {"deleted_count": 100, "total_vectors": 100})
    engine = KnowledgeEngine(vector_store=store)
    engine.compact_index = AsyncMock(side_effect=AssertionError("request must not compact"))
    assert await engine.delete_document("document", TenantContext(user_id="owner"))
    engine.compact_index.assert_not_awaited()
