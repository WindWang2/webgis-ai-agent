"""Repeated dispatch must wait for same-wave publication, not race ref storage."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.lib.cancellation import OperationCancelled
from app.services.gis_harness.analysis_workflows.contracts import (
    AnalysisArtifactEnvelope,
    AnalysisStepSpec,
    AnalysisWorkflowContract,
)
from app.services.gis_harness.analysis_workflows.executor import WorkflowExecutor
from app.services.gis_harness.analysis_workflows.state import new_state


SESSION = "awf-repeat-race"
FC = {
    "type": "FeatureCollection",
    "features": [{"type": "Feature", "properties": {"id": 1},
                  "geometry": {"type": "Point", "coordinates": [104, 30]}}],
}


def _contract(*steps):
    return AnalysisWorkflowContract(
        workflow_id="awf.repeat.race", family="admin_facility_distribution",
        steps=steps)


def _repeat():
    return SimpleNamespace(status="repeated", raw_result=None)


@pytest.mark.parametrize("producer_index", [0, 2])
async def test_repeated_steps_wait_for_same_wave_envelope(producer_index):
    """A late-listed producer and unrelated work still run concurrently."""
    storing = asyncio.Event()
    release_store = asyncio.Event()
    repeats_returned = asyncio.Event()
    independent_finished = asyncio.Event()
    stored = []
    dispatch_calls = []
    repeats = 0

    class Store:
        async def store(self, session_id, payload, *, prefix):
            assert session_id == SESSION and prefix == "geojson"
            stored.append(payload)
            storing.set()
            await release_store.wait()
            return "ref:geojson-producer"

        async def get(self, session_id, ref):
            assert session_id == SESSION and ref == "ref:geojson-producer"
            return FC

    async def dispatch(tool, args, session_id):
        nonlocal repeats
        assert session_id == SESSION
        dispatch_calls.append(tool)
        if tool == "independent":
            await repeats_returned.wait()
            independent_finished.set()
            return {"success": True}
        if tool == "downstream":
            assert args["geojson"] == FC
            return {"success": True}
        index = dispatch_calls.count("producer") - 1
        if index == producer_index:
            return {"success": True, "data": FC}
        await storing.wait()
        repeats += 1
        if repeats == 2:
            repeats_returned.set()
        return _repeat()

    contract = _contract(
        *(AnalysisStepSpec(step_id=sid, tool_name="producer")
          for sid in ("a", "b", "c")),
        AnalysisStepSpec(step_id="independent", tool_name="independent"),
        AnalysisStepSpec(step_id="downstream", tool_name="downstream",
                         role_inputs={"geojson": "step:b"}),
    )
    state = new_state(contract, contract_fingerprint="fp")
    executor = WorkflowExecutor(registry=None, session_data=Store(),
                                dispatch=dispatch)
    task = asyncio.create_task(executor.execute(SESSION, contract, state))
    try:
        await asyncio.wait_for(independent_finished.wait(), timeout=5)
        producer_id = ("a", "b", "c")[producer_index]
        assert all(state.steps[sid].state == "running" for sid in ("a", "b", "c"))
        assert not task.done()
    finally:
        release_store.set()
        await asyncio.wait_for(task, timeout=5)

    assert all(entry.state == "satisfied" for entry in state.steps.values())
    assert stored == [FC]
    assert dispatch_calls.count("producer") == 3
    assert dispatch_calls.count("downstream") == 1
    for sid in ("a", "b", "c"):
        assert state.envelopes[sid].ref == "ref:geojson-producer"
        assert state.envelopes[sid].step_id == sid
        assert state.envelopes[sid].owner_session_id == SESSION
        assert state.receipts[sid]["step_id"] == sid
        assert state.steps[sid].attempts == 1
        if sid != producer_id:
            assert state.envelopes[sid] is not state.envelopes[producer_id]
            source = state.receipts[sid]["reused_from_step"]
            seen = {sid}
            while source != producer_id:
                assert source not in seen
                seen.add(source)
                source = state.receipts[source]["reused_from_step"]


@pytest.mark.parametrize("failure", ["error", "exception", "cancelled", "store"])
async def test_repeated_step_does_not_reuse_failed_producer(failure):
    repeat_returned = asyncio.Event()
    calls = 0

    class Store:
        async def store(self, session_id, payload, *, prefix):
            await repeat_returned.wait()
            raise OSError("storage unavailable")

    async def dispatch(tool, args, session_id):
        nonlocal calls
        calls += 1
        if calls > 1:
            repeat_returned.set()
            return _repeat()
        await repeat_returned.wait()
        if failure == "exception":
            raise RuntimeError("producer failed")
        if failure == "cancelled":
            raise OperationCancelled("producer cancelled")
        if failure == "store":
            return {"success": True, "data": FC}
        return {"success": False, "reason_code": "PRODUCER_FAILED"}

    contract = _contract(*(AnalysisStepSpec(step_id=sid, tool_name="producer")
                           for sid in ("a", "b")))
    state = new_state(contract, contract_fingerprint="fp")
    # A previous attempt's envelope must not turn a failed rerun into success.
    state.envelopes["a"] = AnalysisArtifactEnvelope(
        step_id="a", workflow_id=contract.workflow_id, tool_name="producer",
        ref="ref:geojson-stale")
    executor = WorkflowExecutor(registry=None, session_data=Store(),
                                dispatch=dispatch)
    await asyncio.wait_for(executor.execute(SESSION, contract, state), timeout=5)

    assert state.steps["a"].state == (
        "cancelled" if failure == "cancelled" else "failed")
    assert state.steps["b"].state == "failed"
    assert state.steps["b"].reason_code == "TOOL_DISPATCH_REPEATED"
    assert "b" not in state.envelopes
    assert state.receipts == {}


async def test_all_repeated_steps_fail_without_waiting_on_each_other():
    async def dispatch(tool, args, session_id):
        return _repeat()

    contract = _contract(*(AnalysisStepSpec(step_id=sid, tool_name="producer")
                           for sid in ("a", "b", "c")))
    state = new_state(contract, contract_fingerprint="fp")
    executor = WorkflowExecutor(registry=None, dispatch=dispatch)
    await asyncio.wait_for(executor.execute(SESSION, contract, state), timeout=5)
    assert all(entry.state == "failed" and
               entry.reason_code == "TOOL_DISPATCH_REPEATED"
               for entry in state.steps.values())
    assert state.envelopes == state.receipts == {}


async def test_repeated_step_reuses_prior_wave_envelope():
    calls = 0

    async def dispatch(tool, args, session_id):
        nonlocal calls
        calls += 1
        return {"success": True} if calls == 1 else _repeat()

    contract = _contract(
        AnalysisStepSpec(step_id="a", tool_name="producer"),
        AnalysisStepSpec(step_id="b", tool_name="producer", depends_on=("a",)),
    )
    state = new_state(contract, contract_fingerprint="fp")
    executor = WorkflowExecutor(registry=None, dispatch=dispatch)
    await executor.execute(SESSION, contract, state)
    assert state.status == "satisfied"
    assert state.receipts["b"]["reused_from_step"] == "a"
    assert state.envelopes["a"].fingerprint == state.envelopes["b"].fingerprint


async def test_repeated_step_cannot_reuse_different_fingerprint():
    async def dispatch(tool, args, session_id):
        if args["value"] == 1:
            return {"success": True}
        return _repeat()

    contract = _contract(
        AnalysisStepSpec(step_id="a", tool_name="producer", step_params={"value": 1}),
        AnalysisStepSpec(step_id="b", tool_name="producer", step_params={"value": 2}),
    )
    state = new_state(contract, contract_fingerprint="fp")
    executor = WorkflowExecutor(registry=None, dispatch=dispatch)
    await executor.execute(SESSION, contract, state)
    assert state.steps["a"].state == "satisfied"
    assert state.steps["b"].state == "failed"
    assert "b" not in state.envelopes


async def test_cancelling_wave_preserves_task_cancellation():
    repeat_returned = asyncio.Event()
    producer_cancelled = asyncio.Event()
    calls = 0

    async def dispatch(tool, args, session_id):
        nonlocal calls
        calls += 1
        if calls > 1:
            repeat_returned.set()
            return _repeat()
        try:
            await asyncio.Event().wait()
        finally:
            producer_cancelled.set()

    contract = _contract(*(AnalysisStepSpec(step_id=sid, tool_name="producer")
                           for sid in ("a", "b")))
    state = new_state(contract, contract_fingerprint="fp")
    executor = WorkflowExecutor(registry=None, dispatch=dispatch)
    task = asyncio.create_task(executor.execute(SESSION, contract, state))
    try:
        await asyncio.wait_for(repeat_returned.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert producer_cancelled.is_set()
    assert all(entry.state != "satisfied" for entry in state.steps.values())
    assert state.envelopes == state.receipts == {}
