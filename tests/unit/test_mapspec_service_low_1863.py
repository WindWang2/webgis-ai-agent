"""Mutation safety checks for the previously unmapped 1863 claims."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.mapspec.checkpoint import discard_checkpoint
from app.services.mapspec.coordinator import compile_via_cli, validate
from app.services.mapspec.lifecycle_engine import InitProjectIntent, MapSpecLifecycleEngine, PatchLayerPresentationIntent, UpsertLayerIntent


async def test_checkpoint_discard_rejects_traversal_before_filesystem_write(tmp_path):
    sentinel = tmp_path / "outside"
    sentinel.mkdir()
    with pytest.raises(ValueError):
        await discard_checkpoint(tmp_path, "../../outside")
    assert sentinel.is_dir()


@pytest.mark.parametrize("stops", [[["bad", "red"], [3, "blue"]], [0, 1], [[None, "red"]], None])
def test_bad_stop_structures_are_validation_errors(stops):
    result = validate({"sources": {"s": {}}, "layers": [{"id": "l", "source": "s", "paint": {"circle-color": {"method": "step", "stops": stops}}}]})
    assert result["success"] is False
    assert result["errors"]


async def test_missing_pinned_cli_does_not_run_npm(monkeypatch, tmp_path):
    from app.services.mapspec import coordinator
    monkeypatch.setattr(coordinator, "_CLI_LOCAL_BIN", tmp_path / "missing-jiti")
    result = await compile_via_cli(tmp_path / "input.json", tmp_path / "out")
    assert result["success"] is False
    assert result["report"]["errors"][0]["code"] == "CLI_UNAVAILABLE"


async def test_guard_exception_rejects_mutation_without_writing(monkeypatch):
    import app.services.mapspec.lifecycle_engine as module
    monkeypatch.setattr(module, "_spatial_guardrails_enabled", lambda: True)
    monkeypatch.setattr(module, "_get_spatial_guardrails", lambda: SimpleNamespace(check_intent=lambda _: 1 / 0))
    engine = MapSpecLifecycleEngine()
    result = await engine.apply_mutation("guard-" + uuid4().hex, InitProjectIntent())
    assert result.is_error and result.error_code == "spatial_guard_unavailable"


async def test_presentation_batch_does_not_create_recovery_checkpoint(monkeypatch):
    import app.services.mapspec.lifecycle_engine as module
    sid = "batch-low-" + uuid4().hex
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    await engine.apply_mutation(sid, UpsertLayerIntent(layer={"id": "l", "type": "circle", "source": "s"}, source_data={"type": "FeatureCollection", "features": []}))
    checkpoint = AsyncMock(side_effect=AssertionError("presentation batch must not checkpoint"))
    monkeypatch.setattr(module, "create_checkpoint", checkpoint)
    result = await engine.apply_presentation_batch(sid, [PatchLayerPresentationIntent(layer_id="l", visible=False)])
    assert not result.is_error
    assert result.checkpoint_id is None
    assert result.mapspec["layers"][0]["layout"]["visibility"] == "none"
    checkpoint.assert_not_awaited()


async def test_explorer_owner_classification_uses_durable_registration(monkeypatch):
    from app.services.explorer import orchestrator
    monkeypatch.setattr(orchestrator, "get_session_tasks", lambda _: [("owned", "u"), ("anonymous", "")])
    observed = []

    async def stream(ids):
        observed.extend(ids)
        yield "progress"

    monkeypatch.setattr(orchestrator._bridge_orchestrator, "stream_session_progress", stream)
    monkeypatch.setattr(orchestrator, "remove_session_task", lambda *_: None)
    assert [item async for item in orchestrator.bridge_session_explorer_progress("s", "u")] == ["progress"]
    assert observed == ["anonymous"]


async def test_cube_budget_directory_walk_runs_off_event_loop(monkeypatch, tmp_path):
    import threading
    from app.services.workspace.durability import materialize_ref_payload
    from app.services import artifact_registry
    from app.services import s3_blob_store
    main_thread = threading.get_ident()
    threads = []
    cube = tmp_path / "cube"
    cube.mkdir()
    (cube / "chunk").write_bytes(b"1234")
    real_rglob = type(cube).rglob

    def observe(path, pattern):
        threads.append(threading.get_ident())
        return real_rglob(path, pattern)

    monkeypatch.setattr(type(cube), "rglob", observe)
    monkeypatch.setattr(artifact_registry, "is_raster_ref", lambda _: False)
    monkeypatch.setattr(artifact_registry, "is_fabric_parquet_ref", lambda _: False)
    monkeypatch.setattr(artifact_registry, "is_cube_ref", lambda _: True)
    monkeypatch.setattr(artifact_registry, "cube_store_path", lambda *_: cube)
    monkeypatch.setattr(s3_blob_store, "get_object_store", lambda: object())
    assert await materialize_ref_payload("s", "ref:cube", budget_bytes=1) == (None, "budget")
    assert threads and all(ident != main_thread for ident in threads)


async def test_redis_heartbeat_never_resurrects_missing_or_expired_presence(monkeypatch):
    import fakeredis.aioredis
    import json
    import time
    from app.services.collab.presence import PresenceRegistry, _HEARTBEAT_SCRIPT, _KEY
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    registry = PresenceRegistry()
    registry._scripts = {"heartbeat": client.register_script(_HEARTBEAT_SCRIPT)}
    monkeypatch.setattr(registry, "_redis", lambda: client)
    key = _KEY.format(sid="s")
    await registry.heartbeat("s", "missing", {"label": "resurrect"})
    assert not await client.hexists(key, "missing")
    await client.hset(key, "expired", json.dumps({"cid": "expired", "exp": int(time.time()) - 10}))
    await registry.heartbeat("s", "expired", {})
    assert not await client.hexists(key, "expired")
    await client.aclose()
