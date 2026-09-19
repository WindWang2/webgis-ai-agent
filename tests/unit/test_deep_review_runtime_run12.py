"""RUN-12: invalidation bridge finders must not block the event loop.

``find_affected_instances`` / ``find_affected_nodes`` run synchronous
SQLAlchemy queries; they were called directly from the async
``apply_to_workflow`` (event-loop thread). They must run via
``asyncio.to_thread`` like the ledger calls.
"""
from __future__ import annotations

import threading

import pytest

from app.services.spatial_events.invalidation_bridge import InvalidationBridge


class _FakeWorkflowSvc:
    async def apply_changes(self, instance_id, changes, *, owner_scope, source):
        return {"marked_stale": []}


@pytest.mark.asyncio
async def test_finders_run_off_event_loop_thread():
    bridge = InvalidationBridge(workflow_service=_FakeWorkflowSvc())
    main_thread = threading.get_ident()
    seen: dict = {}

    def find_instances(event):
        seen["instances"] = threading.get_ident()
        return []

    def find_nodes(instance_ids, ref):
        seen["nodes"] = threading.get_ident()
        return {}

    bridge.find_affected_instances = find_instances
    bridge.find_affected_nodes = find_nodes

    await bridge.apply_to_workflow({
        "kind": "dataset.version_changed",
        "org_id": "org-a",
        "project_id": "proj-1",
        "payload": {"ref": "ref-1"},
    })

    assert seen["instances"] != main_thread
    assert seen["nodes"] != main_thread
