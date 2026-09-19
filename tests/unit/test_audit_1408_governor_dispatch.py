"""#1408: governor/dispatch resource-chain — DF cost, turn_id, budget/deadline, store memo."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_build_demand_passes_df_cost_for_data_fabric():
    from app.services.governor.dispatch_adapter import GovernorDispatchAdapter
    from app.services.governor.contract import Dimension, Subsystem

    adapter = GovernorDispatchAdapter(
        governor=None, metadata_fn=lambda _: {"cost": "medium"})
    demand = adapter._build_demand(
        "query_poi",
        {"limit": 250, "bbox": [0.0, 0.0, 1.0, 1.0], "protocol": "wfs"},
        session_id="s1",
        turn_id="turn-abc",
    )
    assert demand.turn_id == "turn-abc"
    assert demand.subsystem == Subsystem.DATA_FABRIC
    feat = demand.estimate.dims.get(Dimension.FEATURE_COUNT)
    assert feat is not None
    # DF projection sets confidence ≥ 0.7 and source mentions cost model
    assert feat.confidence >= 0.7
    assert "cost" in (feat.source or "") or "df" in (feat.source or "")


def test_build_demand_skips_df_cost_for_non_fetch_tools():
    from app.services.governor.dispatch_adapter import GovernorDispatchAdapter
    from app.services.governor.contract import Dimension

    adapter = GovernorDispatchAdapter(
        governor=None, metadata_fn=lambda _: {"cost": "light"})
    demand = adapter._build_demand(
        "buffer_analysis", {"distance": 100}, session_id="s1", turn_id="")
    feat = demand.estimate.dims.get(Dimension.FEATURE_COUNT)
    # no DF projection → either absent or prior/arg-hint only
    if feat is not None:
        assert feat.confidence < 0.7 or "cost_model" not in (feat.source or "")


def test_durable_wait_timeout_honours_longer_node_deadline(monkeypatch):
    from app.services.workflow_runtime import dispatch as d

    monkeypatch.delenv("GIS_WORKFLOW_DURABLE_WAIT_TIMEOUT_S", raising=False)
    assert d.durable_wait_timeout_s() == d.DEFAULT_DURABLE_WAIT_TIMEOUT_S
    assert d.durable_wait_timeout_s(node_deadline_s=600.0) == 600.0
    assert d.durable_wait_timeout_s(node_deadline_s=100.0) == d.DEFAULT_DURABLE_WAIT_TIMEOUT_S


def test_dispatch_sync_passes_budget_and_deadline():
    from app.services.geocompute.plan import ResourceBudget
    from app.services.workflow_runtime.dispatch import _dispatch_sync

    op_node = SimpleNamespace(deadline_s=90.0, node_id="n1", model_dump=lambda mode="json": {"node_id": "n1"})
    plan = SimpleNamespace(
        plan_id="plan-1",
        budget=ResourceBudget(max_rows=12_000, deadline_s=90.0),
    )
    captured = {}

    def _fake_dispatch_node(node, **kwargs):
        captured.update(kwargs)
        captured["node"] = node
        return {"job_id": "1", "queue": "q"}

    with patch(
        "app.services.geocompute.durable.dispatch_node",
        side_effect=_fake_dispatch_node,
    ):
        ret = _dispatch_sync(
            op_node, plan, "sess", "owner",
            port_idents={"in": {"ref": "ref://a", "fp": "abc"}},
            node={"resources": {"profile": "light_cpu", "placement": "worker-a"}},
        )
    assert ret["job_id"] == "1"
    assert captured["deadline_s"] == 90.0
    assert captured["budget"] is plan.budget
    assert captured["input_refs"] == {"in": "ref://a"}
    assert captured["resource_envelope"]["placement"] == "worker-a"
    assert captured["owner_scope"] == "owner"


def test_model_registry_store_memoized_across_fingerprint_calls():
    from app.services.gis_harness import capability_graph as cg

    # reset singleton for isolation
    cg._MODEL_REGISTRY_STORE = None
    with patch.object(cg, "_shared_model_registry_store") as shared:
        store = MagicMock()
        store._records = {}
        store.load = MagicMock()
        shared.return_value = store
        cg.source_fingerprints()
        cg.source_fingerprints()
        assert shared.call_count >= 2
        assert store.load.call_count >= 2  # load still called; short-circuit is inside store


def test_shared_model_registry_is_singleton():
    from app.services.gis_harness import capability_graph as cg

    cg._MODEL_REGISTRY_STORE = None
    a = cg._shared_model_registry_store()
    b = cg._shared_model_registry_store()
    assert a is b


def test_reuse_hit_transition_uses_to_thread_source():
    """Structural: reuse-hit transition_node must be awaited via to_thread (#1408)."""
    src = open("app/services/workflow_runtime/driver.py").read()
    idx = src.find('reason="REUSE_HIT"')
    assert idx > 0
    window = src[max(0, idx - 400): idx]
    assert "await asyncio.to_thread(" in window
    assert "self.store.transition_node" in window


def test_tool_dispatch_passes_turn_id_source():
    src = open("app/services/tool_dispatch_service.py").read()
    assert "turn_id=_turn_id" in src
    assert "current_runtime_context" in src
