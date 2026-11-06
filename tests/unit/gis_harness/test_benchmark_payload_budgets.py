"""Separate raw diagnostics from the real model-facing dispatch payload."""
import json
import uuid

import pytest

from app.evaluation import GISBenchmarkRunner, get_all_cases
from app.evaluation.case import NumericAssertion
from app.evaluation.runner import _check_numeric
from app.services.session_data import session_data_manager
from app.services.mapspec.store import mapspec_store_instance
from app.services.tool_dispatch_service import ToolDispatchService


@pytest.mark.parametrize("text", ["ascii", "成都小学", "地图🗺️"])
@pytest.mark.parametrize("cached", [False, True])
def test_raw_budget_counts_canonical_utf8_bytes(text, cached):
    result = {"summary": text}
    blob = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    size = len(blob.encode("utf-8"))
    kwargs = dict(step_results=[result], mapspec=None, fixture_docs={}, quantities={})
    if cached:
        kwargs["step_blobs"] = [blob]
    assertion = NumericAssertion(source="step_result_bytes", op="<=", value=size)
    assert _check_numeric(assertion, **kwargs)
    assert not _check_numeric(assertion.model_copy(update={"value": size - 1}), **kwargs)


@pytest.mark.parametrize("text", ["ascii", "成都小学", "地图🗺️"])
def test_model_budget_measures_the_exact_dispatch_string(text):
    payload = json.dumps({"summary": text}, ensure_ascii=False)
    size = len(payload.encode("utf-8"))
    assertion = NumericAssertion(source="llm_payload_bytes", op="<=", value=size)
    kwargs = dict(step_results=[{}], mapspec=None, fixture_docs={}, quantities={})
    assert not _check_numeric(assertion, **kwargs)  # missing evidence never passes
    kwargs["llm_payloads"] = [payload]
    assert _check_numeric(assertion, **kwargs)
    assert not _check_numeric(assertion.model_copy(update={"value": size - 1}), **kwargs)


@pytest.mark.asyncio
async def test_g4_runs_real_dispatch_and_preserves_scope_disclosure(monkeypatch):
    """Real 150k fixture → registry → service → model/SSE, no dispatch stub."""
    case = next(case for case in get_all_cases() if case.id == "G4")
    calls = []
    dispatch = ToolDispatchService.dispatch

    async def capture(self, *args, **kwargs):
        result = await dispatch(self, *args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(ToolDispatchService, "dispatch", capture)
    sid = f"bench-g4-budget-{uuid.uuid4().hex}"
    try:
        evidence = await GISBenchmarkRunner()._run_execute_tier(case, sid)
        assert evidence["failures"] == []
        assert len(calls) == 1
        actual = calls[0]
        assert actual.status == "ok"
        assert evidence["llm_payloads"] == [actual.llm_payload]
        raw = actual.raw_result
        assert len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 30000
        assert len(actual.llm_payload.encode("utf-8")) <= 2500
        assert not raw.get("features") and not raw.get("data")
        for source in raw["mapspec"]["sources"].values():
            # inlineData may itself be a ref descriptor, never feature bodies.
            descriptor = source.get("inlineData") or {}
            assert not descriptor.get("features")
            assert descriptor.get("ref_id") or source.get("ref")
        model = json.loads(actual.llm_payload)
        for view in (raw, model, actual.slim_event):
            assert view["scope_geography"]["verdict"] == "unverified"
            assert any("scope_unverified" in warning for warning in view["warnings"])
    finally:
        await session_data_manager.clear_session(sid)
        await mapspec_store_instance.clear_session_files(sid)
