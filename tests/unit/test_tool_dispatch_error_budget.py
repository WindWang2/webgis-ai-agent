"""Presentation budgets at the real registry -> dispatch-service error seam.

Only llm_payload is bounded. Raw diagnostics and data remain available to
classification, recovery and downstream consumers, not subject to this budget.
"""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.services.gis_harness.recovery_ledger import RecoveryLedger
from app.services.gis_harness.recovery_policy.wiring import recovery_llm_note
from app.services.gis_memory.pending import PendingMemoryBuffer
from app.services.jobs.cancellation import OperationCancelled
from app.services.llm_result_formatter import MSG_MAX_CHARS, wrap_error_dict_for_llm
from app.services.session_data import MemorySessionStore
from app.services.tool_dispatch_service import ToolDispatchService
from app.tools.registry import ToolRegistry


TOOL = "error_budget_probe"
SESSION = "error-budget-session"
CALL = {"id": "budget-call", "function": {"name": TOOL, "arguments": {}}}


@pytest.fixture
def dispatch_harness(monkeypatch, tmp_path):
    # Isolate persistence and optional runtime probes, not the error pipeline.
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("GIS_SITUATION_SUPPLY", "0")
    monkeypatch.setattr(
        "app.services.gis_harness.recovery_ledger._global_ledger", RecoveryLedger()
    )
    monkeypatch.setattr(
        "app.services.gis_memory.pending.pending_memory_buffer", PendingMemoryBuffer()
    )
    monkeypatch.setattr("app.services.tool_metrics.record_tool_call", Mock())
    store = MemorySessionStore()
    broadcast = Mock()

    def build(outcomes):
        calls = []
        remaining = iter(outcomes)

        async def probe():
            calls.append(True)
            outcome = next(remaining)
            if isinstance(outcome, BaseException):
                raise outcome
            return deepcopy(outcome)

        registry = ToolRegistry()
        registry.register(TOOL, "Error presentation budget probe", probe, side_effect="pure")
        service = ToolDispatchService(
            registry=registry, session_data=store, fire_broadcast=broadcast
        )
        return service, calls, store, broadcast

    return build


def _note(result):
    recovery = result.raw_result["recovery"]
    return recovery_llm_note(recovery["diagnostic"], recovery["decision"])


def _assert_presentation_budget(result):
    assert result.status == "error"
    assert len(result.llm_payload) <= MSG_MAX_CHARS
    # UTF-8 JSON string, not the whole ToolDispatchResult: C0 controls can
    # take six bytes each with ensure_ascii=False; escaped astral Unicode
    # can take twelve with ensure_ascii=True. Include the two quote bytes.
    for ensure_ascii, bytes_per_char in ((False, 6), (True, 12)):
        encoded = json.dumps(result.llm_payload, ensure_ascii=ensure_ascii).encode("utf-8")
        assert len(encoded) <= bytes_per_char * MSG_MAX_CHARS + 2
        assert json.loads(encoded) == result.llm_payload


@pytest.mark.parametrize(
    "unit",
    ["a", "测", "🗺", "\x00", '\n\t\r"\\', "<&>"],
    ids=["ascii", "cjk", "astral", "nul", "json-escapes", "xml"],
)
async def test_oversized_hint_preserves_raw_diagnostics_and_recovery(dispatch_harness, unit):
    message = "diagnostic " * 1000 + "HTTP 503 Service Unavailable"
    hint = unit * 10000
    original = {
        "success": False, "code": "TOOL_ERROR", "error_type": "ProviderError",
        "message": message, "correction_hint": hint,
        "data": {"diagnostics": ["full diagnostic record"] * 1000},
    }
    service, calls, store, broadcast = dispatch_harness([original])
    executed = set()
    result = await service.dispatch(deepcopy(CALL), SESSION, executed)

    _assert_presentation_budget(result)
    assert "…[已截断]" in result.llm_payload
    assert result.llm_payload.endswith("\n" + _note(result))
    assert result.llm_payload.count("[recovery]") == 1
    for key, value in original.items():
        assert result.raw_result[key] == value
    assert result.error_msg == message
    # The determining signal is beyond the presentation cut; classify the
    # original message, never the clipped text or the correction hint.
    assert result.raw_result["harness_failure"]["class"] == "timeout"
    recovery = result.raw_result["recovery"]
    assert recovery["diagnostic"]["reason"] == "upstream_server_error"
    assert recovery["decision"]["auto_retry"] is True
    assert len(json.dumps(result.raw_result, ensure_ascii=False).encode("utf-8")) > 15002
    assert calls == [True]  # Recovery decisions never execute another call.
    assert not executed
    assert result.geojson_ref is None and result.map_actions == []
    assert await store.list_refs(SESSION) == {}
    events = await store.get_event_log(SESSION)
    assert [event["event"] for event in events] == ["tool_failed"]
    assert events[0]["data"]["message"] == message[:200]
    broadcast.assert_not_called()


@pytest.mark.parametrize("error_type", [None, "参数校验", "ProviderError", "&" * 10000])
async def test_wrapped_message_is_bounded_after_html_escaping(dispatch_harness, error_type):
    # _untrusted caps fields before HTML escaping, which can expand a single
    # 500-character field beyond the final message budget.
    original = {
        "success": False, "code": "TOOL_ERROR", "error_type": error_type,
        "message": "&" * 10000 + "HTTP 503 Service Unavailable",
    }
    service, calls, _, _ = dispatch_harness([original])
    result = await service.dispatch(deepcopy(CALL), SESSION, set())

    _assert_presentation_budget(result)
    assert result.llm_payload.startswith("[工具执行失败]")
    assert "…[已截断]" in result.llm_payload
    assert result.llm_payload.endswith("\n" + _note(result))
    assert result.error_msg == original["message"]
    for key, value in original.items():
        assert result.raw_result[key] == value
    assert result.raw_result["harness_failure"]["class"] == "timeout"
    assert calls == [True]


@pytest.mark.parametrize("shape", ["error", "type", "status", "summary"])
async def test_normal_return_failure_shapes_share_the_budget(dispatch_harness, shape):
    message = "&" * 10000
    original = {
        "error": {"error": message},
        "type": {"type": "error", "message": message},
        "status": {"status": "failed", "message": message},
        "summary": {"success": False, "summary": message, "data": None},
    }[shape]
    service, calls, _, _ = dispatch_harness([original])
    result = await service.dispatch(deepcopy(CALL), SESSION, set())
    _assert_presentation_budget(result)
    assert result.raw_result["code"] == "tool_error"
    assert result.error_msg == message
    for key, value in original.items():
        assert result.raw_result[key] == value
    assert calls == [True]


@pytest.mark.parametrize("error_type", [None, "参数校验", "ProviderError"])
@pytest.mark.parametrize("hint", [None, "", "请检查参数。\n保留这个换行。"])
async def test_small_payload_is_exactly_backward_compatible(dispatch_harness, error_type, hint):
    original = {
        "success": False, "code": "TOOL_ERROR", "error_type": error_type,
        "message": "区域不存在 <未知> & 已过期", "correction_hint": hint,
    }
    expected = hint or wrap_error_dict_for_llm(TOOL, original)
    service, _, _, _ = dispatch_harness([original])
    result = await service.dispatch(deepcopy(CALL), SESSION, set())
    note = _note(result)
    if note and note not in expected:
        expected += "\n" + note
    assert result.llm_payload == expected
    assert "…[已截断]" not in result.llm_payload
    assert result.error_msg == original["message"]


@pytest.mark.parametrize("delta", [-1, 0, 1])
async def test_recovery_note_counts_towards_the_boundary(dispatch_harness, delta):
    original = {"success": False, "code": "TOOL_ERROR", "message": "HTTP 503"}
    near_boundary = dict(original)
    service, _, _, _ = dispatch_harness([original, near_boundary])
    first = await service.dispatch(deepcopy(CALL), "boundary-reference", set())
    note = _note(first)
    hint = "a" * (MSG_MAX_CHARS - len(note) - 1 + delta)
    near_boundary["correction_hint"] = hint
    result = await service.dispatch(deepcopy(CALL), SESSION, set())

    _assert_presentation_budget(result)
    assert _note(result) == note
    assert result.raw_result["correction_hint"] == hint
    assert result.llm_payload.endswith("\n" + note)
    if delta <= 0:
        assert result.llm_payload == hint + "\n" + note
    else:
        assert len(result.llm_payload) == MSG_MAX_CHARS
        assert "…[已截断]" in result.llm_payload


@pytest.mark.parametrize("position", ["small", "early", "late"])
async def test_existing_recovery_note_is_preserved_without_duplication(dispatch_harness, position):
    original = {"success": False, "code": "TOOL_ERROR", "message": "HTTP 503"}
    embedded = dict(original)
    service, _, _, _ = dispatch_harness([original, embedded])
    first = await service.dispatch(deepcopy(CALL), "embedded-reference", set())
    note = _note(first)
    hint = {
        "small": "prefix\n" + note + "\nsuffix",
        "early": note + "\n" + "a" * 10000,
        "late": "a" * 10000 + "\n" + note,
    }[position]
    embedded["correction_hint"] = hint
    result = await service.dispatch(deepcopy(CALL), SESSION, set())

    _assert_presentation_budget(result)
    assert _note(result) == note
    assert result.llm_payload.count(note) == 1
    assert result.raw_result["correction_hint"] == hint
    if position == "small":
        assert result.llm_payload == hint
    elif position == "late":
        assert result.llm_payload.endswith("\n" + note)


@pytest.mark.parametrize("hint", [None, "\x00" * 10000])
async def test_optional_recovery_failure_does_not_skip_budget(dispatch_harness, monkeypatch, hint):
    # A missing optional recovery record must not bypass the presentation gate.
    monkeypatch.setattr(
        "app.services.gis_harness.recovery_policy.wiring.build_recovery_payload",
        Mock(side_effect=RuntimeError("recovery unavailable")),
    )
    original = {
        "success": False, "code": "TOOL_ERROR", "message": "&" * 10000,
        "correction_hint": hint,
    }
    service, calls, _, _ = dispatch_harness([original])
    result = await service.dispatch(deepcopy(CALL), SESSION, set())
    _assert_presentation_budget(result)
    assert len(result.llm_payload) == MSG_MAX_CHARS
    assert result.llm_payload.endswith("…[已截断]")
    assert "[recovery]" not in result.llm_payload
    assert "recovery" not in result.raw_result
    assert result.error_msg == original["message"]
    assert result.raw_result["correction_hint"] == hint
    assert calls == [True]


@pytest.mark.parametrize("exception_type", [ValueError, KeyError, FileNotFoundError])
async def test_real_registry_exception_retries_without_losing_dedup(dispatch_harness, exception_type):
    message = "长错误" * 10000
    error = exception_type(message)
    success = {"success": True, "message": "recovered", "count": 7}
    service, calls, store, _ = dispatch_harness([error, success])
    executed = set()

    failed = await service.dispatch(deepcopy(CALL), SESSION, executed)
    _assert_presentation_budget(failed)
    assert failed.error_msg == str(error)
    assert failed.raw_result["error_type"] == exception_type.__name__
    assert failed.raw_result["code"] == (
        "VALIDATION_ERROR" if exception_type is ValueError else "NOT_FOUND"
    )
    assert len(failed.raw_result["correction_hint"]) > MSG_MAX_CHARS
    assert not executed

    recovered = await service.dispatch(deepcopy(CALL), SESSION, executed)
    assert recovered.status == "ok"
    assert recovered.raw_result == success
    assert recovered.llm_payload == json.dumps(success, ensure_ascii=False)
    assert recovered.error_msg is None
    repeated = await service.dispatch(deepcopy(CALL), SESSION, executed)
    assert repeated.status == "repeated"
    assert calls == [True, True]
    assert [e["event"] for e in await store.get_event_log(SESSION)] == ["tool_failed", "tool_executed"]


@pytest.mark.parametrize("exception_type", [OperationCancelled, asyncio.CancelledError])
async def test_cancellation_still_propagates_and_allows_retry(dispatch_harness, exception_type):
    cancellation = exception_type("cancelled " * 10000)
    service, calls, store, _ = dispatch_harness([cancellation, {"success": True}])
    executed = set()
    with pytest.raises(exception_type) as caught:
        await service.dispatch(deepcopy(CALL), SESSION, executed)
    assert caught.value is cancellation
    assert not executed
    assert await store.get_event_log(SESSION) == []
    assert (await service.dispatch(deepcopy(CALL), SESSION, executed)).status == "ok"
    assert calls == [True, True]
