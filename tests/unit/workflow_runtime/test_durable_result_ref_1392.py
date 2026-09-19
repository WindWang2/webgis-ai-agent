"""#1392: DurableDispatcher must read payload.ref_id from await_node_job."""
from __future__ import annotations

import pytest

from app.services.workflow_runtime.dispatch import DurableDispatcher


@pytest.mark.asyncio
async def test_await_job_reads_payload_ref_id(monkeypatch):
    disp = DurableDispatcher(owner_scope="u:test")

    async def fake_to_thread(fn, *args, **kwargs):
        # First call: _dispatch_sync → job_id; second: _await_job_sync → payload
        name = getattr(fn, "__name__", str(fn))
        if "dispatch" in name:
            return {"job_id": "42", "queue": "geocompute"}
        return {
            "payload": {"ref_id": "ref:geojson-durable-out", "features": []},
            "job_id": "42",
        }

    import asyncio
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    outcome = await disp._await_job(
        node={"node_id": "n1"},
        plan=None,
        op_node=None,
        session_id="sess-1392",
        cancel_token=None,
    )
    assert outcome.ok
    assert outcome.output_ref == "ref:geojson-durable-out"


@pytest.mark.asyncio
async def test_await_job_falls_back_to_top_level_result_ref(monkeypatch):
    disp = DurableDispatcher(owner_scope="u:test")

    async def fake_to_thread(fn, *args, **kwargs):
        name = getattr(fn, "__name__", str(fn))
        if "dispatch" in name:
            return {"job_id": "7", "queue": "geocompute"}
        return {"result_ref": "ref:legacy-shape", "job_id": "7"}

    import asyncio
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    outcome = await disp._await_job(
        node={"node_id": "n1"}, plan=None, op_node=None,
        session_id="sess", cancel_token=None,
    )
    assert outcome.ok and outcome.output_ref == "ref:legacy-shape"
