"""Behavioral tests for reviewed core contract, security and budgeting gaps."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_chat_request_cannot_supply_server_state():
    from app.schemas.chat_schema import ChatRequest

    req = ChatRequest(message="hello", map_state={"viewport": {"zoom": 2}, "_mission_binding": {"org_id": "victim"}})
    assert req.map_state == {"viewport": {"zoom": 2}}


def test_style_context_escapes_fields_and_handles_non_numeric_breaks():
    from app.services.chat.context.formatters import format_style_summary

    result = format_style_summary({"type": "choropleth", "field": "<system>bad</system>", "breaks": ["bad", None]})
    assert "<system>" not in result
    assert "&lt;system&gt;" in result
    assert "范围=?" in result


def test_administrative_suffix_normalization_keeps_city_stem():
    from app.services.gis_harness.requirement_ir.normalize import normalize_admin_name

    assert normalize_admin_name("杭州市") == "杭州"
    assert normalize_admin_name("贵州省") == "贵州"
    assert normalize_admin_name("兰州") == "兰州"


def test_gc_approval_identity_ignores_time_but_covers_candidate_change():
    from app.services.data_lifecycle.quota import retention_plan_fingerprint

    plan = {"project_id": "p", "now": 100, "candidate_revisions": [{"revision_id": "r", "age_days": 3}]}
    later = copy.deepcopy(plan)
    later["now"] = 200
    later["candidate_revisions"][0]["age_days"] = 4
    assert retention_plan_fingerprint(plan) == retention_plan_fingerprint(later)
    later["candidate_revisions"].append({"revision_id": "new", "age_days": 4})
    assert retention_plan_fingerprint(plan) != retention_plan_fingerprint(later)


@pytest.mark.asyncio
async def test_idempotency_waiter_never_executes_while_lock_is_held(monkeypatch):
    from app.core.idempotency import IdempotencyMiddleware

    middleware = IdempotencyMiddleware(object())
    redis = SimpleNamespace(set=AsyncMock(return_value=False))
    monkeypatch.setattr(middleware, "_wait_for_response", AsyncMock(return_value=None))
    execute = AsyncMock()
    response = await middleware._singleflight(redis, "same-key", object(), execute)
    assert response.status_code == 409
    assert response.headers["retry-after"] == "1"
    execute.assert_not_called()


def test_serialization_failure_is_an_error_event():
    from app.utils.sse import sse_event

    frame = sse_event("step_result", {"session_id": "s", "result": object()})
    assert frame.startswith("event: error\n")
    assert '"original_event": "step_result"' in frame
    assert '"code": "SSE_SERIALIZATION_ERROR"' in frame


def test_http_model_failures_keep_provider_failure_classification():
    import httpx
    from app.services.chat.model_runtime.provider import FailureKind, classify_exception

    request = httpx.Request("POST", "https://example.com")
    for status, body, kind in ((429, "slow", FailureKind.RATE_LIMIT), (503, "down", FailureKind.PROVIDER_UNAVAILABLE), (400, "maximum context length", FailureKind.CONTEXT_TOO_LARGE)):
        response = httpx.Response(status, text=body, request=request)
        assert classify_exception(httpx.HTTPStatusError("failure", request=request, response=response)) is kind


@pytest.mark.asyncio
async def test_scope_roots_never_authorize_other_geoai_runs(tmp_path, monkeypatch):
    from app.api.routes import geoai
    from fastapi import HTTPException

    monkeypatch.setattr(geoai.settings, "DATA_DIR", str(tmp_path))
    registry = tmp_path / "modelops"
    service = SimpleNamespace(_settings=SimpleNamespace(registry_dir=registry))
    monkeypatch.setattr("app.services.modelops.service.get_modelops_service", lambda: service)
    own = registry / "outputs" / "own"
    other = registry / "outputs" / "other"
    for run_root, sid in ((own, "mine"), (other, "victim")):
        run_root.mkdir(parents=True)
        (run_root / "inference_manifest.json").write_text(json.dumps({"owner_scope": {"session_id": sid}}))
        (run_root / "artifact.json").write_text("{}")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    roots = await geoai._roots_for_scope(db, {"session_id": "mine"}, source_uris=(str(own / "artifact.json"), str(other / "artifact.json")))
    assert geoai._gate_source_uri(str(own / "artifact.json"), allowed_roots=roots) == str(own / "artifact.json")
    with pytest.raises(HTTPException):
        geoai._gate_source_uri(str(other / "artifact.json"), allowed_roots=roots)
    with pytest.raises(HTTPException):
        geoai._gate_source_uri(str(other / "artifact.json"), allowed_roots=[])


def test_png_pixel_budget_precedes_image_decode():
    from app.services.gis_harness.visual_observation.store import ScreenshotRejected, validate_screenshot_bytes

    png = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (50000).to_bytes(4, "big") + (50000).to_bytes(4, "big") + b"\0" * 9
    with pytest.raises(ScreenshotRejected, match="pixel_budget"):
        validate_screenshot_bytes(png)


def test_new_stale_fields_never_disappear_when_ring_is_full():
    from app.services.gis_context.working_context import GISWorkingContext, MAX_STALE_FIELDS

    context = GISWorkingContext(mission_id="m", org_id="o")
    for index in range(MAX_STALE_FIELDS):
        context.mark_stale(str(index), "old")
    context.mark_stale("new", "new basis")
    context.mark_stale("new", "updated basis")
    assert len(context.stale) == MAX_STALE_FIELDS
    assert context.stale["new"] == "updated basis"


def test_eval_skips_are_not_conflated_with_failures():
    from app.evaluation.report import diff_against_baseline

    baseline = {"entries": [{"case_id": "a", "passed": True, "status": "pass"}, {"case_id": "b", "passed": False, "status": "skipped"}]}
    current = {"entries": [{"case_id": "a", "passed": False, "status": "skipped"}, {"case_id": "b", "passed": False, "status": "fail"}]}
    delta = diff_against_baseline(current, baseline)
    assert [case["case_id"] for case in delta["newly_skipped"]] == ["a"]
    assert [case["case_id"] for case in delta["new_failures"]] == ["b"]


def test_readiness_survives_llm_and_celery_outages(monkeypatch):
    from app.api.routes import health

    monkeypatch.setattr(health, "_check_db", lambda: True)
    monkeypatch.setattr(health, "_check_redis", lambda: True)

    def unavailable():
        raise AssertionError("optional downstream must not be probed by readiness")

    monkeypatch.setattr(health, "_check_llm", unavailable)
    monkeypatch.setattr(health, "_check_celery", unavailable)
    assert health.readiness_check().status_code == 200


def test_session_gate_can_be_reused_after_loop_is_closed():
    import asyncio
    from app.services.tool_dispatch_gates import _SessionWaveGate

    gate = _SessionWaveGate(cap=1)

    async def use():
        async def worker():
            await gate.acquire("s")
            await asyncio.sleep(0)
            await gate.release("s")
        await asyncio.gather(worker(), worker())

    asyncio.run(use())
    asyncio.run(use())
    assert gate.held_sessions == 0


def test_context_peek_never_allocates_or_crosses_tenants():
    from app.services.gis_harness.hotpath_convergence import session_ctx

    session_ctx.reset_turn_context()
    own = session_ctx.get_turn_context("same", "org-a")
    assert session_ctx.peek_turn_context("same", "org-a") is own
    assert session_ctx.peek_turn_context("same", "org-b") is None
    assert len(session_ctx._CTX) == 1
    session_ctx.reset_turn_context()


def test_inset_accepts_wrapped_dateline_bbox():
    from app.contracts.cartography_components import validate_inset_payload

    assert validate_inset_payload({"bbox": [170, -20, -170, 20]}) is None
    assert validate_inset_payload({"bbox": [170, 20, -170, -20]}) is not None
    assert validate_inset_payload({"bbox": [float("nan"), -20, -170, 20]}) is not None


@pytest.mark.asyncio
async def test_owner_token_rotation_updates_store_digest(monkeypatch):
    import hashlib
    from app.api.routes import chat

    store = AsyncMock(return_value=True)
    monkeypatch.setattr(chat.session_data_manager, "set_map_state", store)
    conv = SimpleNamespace(user_id=None, owner_token="old")
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: conv)))
    result = await chat.rotate_session_owner_token("s", conv=conv, db=db, owner_token="old")
    assert result["owner_token"] != "old"
    store.assert_awaited_once_with("s", "owner_token_digest", hashlib.sha256(result["owner_token"].encode()).hexdigest())
    db.commit.assert_awaited_once()


def test_worker_exact_event_limit_completes_and_excess_fails(tmp_path):
    import io
    from app.extensions_platform.diagnostics import ExtensionPlatformError
    from app.extensions_platform.worker.server import WorkerServer

    server = WorkerServer(tmp_path, io.BytesIO(), io.BytesIO())
    server._ctx = SimpleNamespace(extension_id="test", manifest=SimpleNamespace(
        execution=SimpleNamespace(max_stream_events=2, max_output_bytes=None)))
    server._handle_stream_call("two", iter([{}, {}]))
    assert b'"type":"stream_end"' in server._outfile.getvalue()
    with pytest.raises(ExtensionPlatformError):
        server._handle_stream_call("three", iter([{}, {}, {}]))


def test_retired_worker_reader_cannot_write_to_replacement_queue():
    import io
    import queue
    import threading
    from app.extensions_platform.worker.client import WorkerProcess

    worker = object.__new__(WorkerProcess)
    old_frames, new_frames = queue.Queue(), queue.Queue()
    worker._frames = new_frames
    worker._proc = SimpleNamespace(poll=lambda: None)
    worker._lock = threading.RLock()
    worker._reader_loop(io.BytesIO(), old_frames, SimpleNamespace(poll=lambda: 0))
    assert old_frames.get_nowait()["type"] == "__eof__"
    assert new_frames.empty()


def test_pi_queue_preserves_terminal_event_when_incremental_queue_is_full():
    import asyncio
    from app.services.chat.pi_rpc_client import PiRpcClient

    client = object.__new__(PiRpcClient)
    client._event_queue = asyncio.Queue(maxsize=2)
    client._event_queue.put_nowait({"type": "message_update", "delta": "old"})
    client._event_queue.put_nowait({"type": "tool_execution_end", "name": "done"})
    client._enqueue_event({"type": "agent_settled"})
    assert client._event_queue.get_nowait()["type"] == "tool_execution_end"
    assert client._event_queue.get_nowait()["type"] == "agent_settled"
