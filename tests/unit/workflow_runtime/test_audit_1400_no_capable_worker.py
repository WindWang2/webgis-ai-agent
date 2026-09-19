"""#1400: NoCapableWorker is typed non-retryable; registry producers exist."""
from __future__ import annotations

import pytest

from app.services.workflow_runtime import retry as RT
from app.services.workflow_runtime.dispatch import (
    AutoDispatcher, NoCapableWorker, choose_dispatch,
)
from app.services.workflow_runtime.adapters_geocompute import GeoComputeNodeOutcome


def test_no_capable_worker_not_retryable():
    assert RT.error_retryable("NO_CAPABLE_WORKER") is False
    assert RT.error_retryable("NO_CAPABLE_WORKER", "transient_db") is False


@pytest.mark.asyncio
async def test_auto_dispatcher_maps_no_capable_worker(monkeypatch):
    class EmptyReg:
        def list_active(self, **kw):
            return []

    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH", "auto")
    monkeypatch.setenv("GIS_WORKFLOW_ISOLATE_LARGE_TASKS", "1")
    # reload mode helpers pick env — choose_dispatch reads env each call
    from app.services.workflow_runtime import dispatch as D
    monkeypatch.setattr(D, "isolation_enabled", lambda: True)
    monkeypatch.setattr(D, "dispatch_mode", lambda: "auto")

    d = AutoDispatcher(owner_scope="u:a", registry=EmptyReg())
    outcome = await d.execute(
        node={"node_id": "n", "resources": {"profile": "raster"}},
        dag={}, input_refs=[], params={}, session_id="s",
        port_idents={"in": {"rows": 50000}}, cancel_token=None,
    )
    assert isinstance(outcome, GeoComputeNodeOutcome)
    assert outcome.ok is False
    assert outcome.error_code == "NO_CAPABLE_WORKER"


def test_choose_dispatch_raises_when_isolate_and_empty():
    class EmptyReg:
        def list_active(self, **kw):
            return []

    from app.services.workflow_runtime import dispatch as D
    # force isolate path
    import os
    os.environ["GIS_WORKFLOW_ISOLATE_LARGE_TASKS"] = "1"
    try:
        # monkey via direct: isolation_enabled reads env
        from unittest.mock import patch
        with patch.object(D, "isolation_enabled", return_value=True), \
             patch.object(D, "dispatch_mode", return_value="auto"):
            with pytest.raises(NoCapableWorker):
                choose_dispatch(
                    {"node_id": "n", "resources": {"profile": "light_cpu"}},
                    input_rows=50000, registry=EmptyReg())
    finally:
        os.environ.pop("GIS_WORKFLOW_ISOLATE_LARGE_TASKS", None)


def test_celery_ready_registers_workflow_workers():
    src = open("app/services/geocompute/cluster/workers.py").read()
    assert "WorkerRegistry().register" in src
    assert '"durable"' in src or "'durable'" in src
