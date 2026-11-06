"""Behavioral regressions for the low harness audit's boundary cases."""
import asyncio

import pytest

from app.services.gis_harness.evidence_claim import (
    Claim, ClaimStore, ClaimType, EvidenceKind, EvidenceNode, RelationEdge,
    RelationType, detect_contradictions,
)


def test_reverse_indexes_follow_replacements_and_clear():
    store = ClaimStore()
    store.upsert_evidence(EvidenceNode(evidence_id="e1", kind=EvidenceKind.STATISTIC, ref="old"))
    claim = Claim(claim_id="c1", claim_type=ClaimType.DENSITY, subject="A", supporting_evidence_refs=["e1"])
    store.upsert_claim(claim)
    edge = RelationEdge(edge_id="edge", src="a", dst="b", relation=RelationType.DERIVED_FROM)
    store.add_edge(edge)
    assert [c.claim_id for c in store.claims_for_artifact("old")] == ["c1"]
    store.upsert_evidence(store.get_evidence("e1").model_copy(update={"ref": "new"}))
    assert store.claims_for_artifact("old") == []
    assert [c.claim_id for c in store.claims_for_artifact("new")] == ["c1"]
    store.add_edge(edge.model_copy(update={"src": "new-source", "dst": "new-target"}))
    assert store.edges_from("a") == store.edges_to("b") == []
    assert store.edges_from("new-source")[0].dst == "new-target"
    store.clear()
    assert store.edges_from("new-source") == store.claims_for_artifact("new") == []


def test_highest_claim_bucket_compares_different_subjects():
    store = ClaimStore()
    for ident, subject in [("c1", "A"), ("c2", "B")]:
        store.upsert_claim(Claim(claim_id=ident, claim_type=ClaimType.DENSITY,
            subject=subject, comparator="highest", value=3))
    assert len(detect_contradictions(store)) == 1


@pytest.mark.asyncio
async def test_recovery_store_failure_is_exhausted(monkeypatch):
    from app.services.gis_harness import durable_context, runtime_repair
    async def unavailable(*args, **kwargs):
        raise durable_context.RecoveryStoreUnavailable("offline")
    monkeypatch.setattr(durable_context, "update_recovery_state", unavailable)
    outcome = runtime_repair.RuntimeRepairOutcome()
    await runtime_repair._attach_continuation(outcome, "s", exhausted=False)
    assert outcome.exhausted
    assert outcome.continuation["reason"] == "recovery_store_unavailable"


def test_truncating_goal_requirements_retains_unknown_sentinel():
    from app.services.gis_harness.goal_satisfaction.requirements import derive_goal_contract
    contract = derive_goal_contract({"analysis_steps": [
        {"capability": f"analysis-{i}", "status": "complete"} for i in range(80)
    ]})
    assert any(r.capability == "requirements_truncated" and r.required for r in contract.requirements)


def test_unknown_turn_identifier_is_not_digit_concatenation():
    from app.services.gis_harness.representation_binding.semantic_projection import _turn_of
    assert _turn_of("f20033a7-9134-4d22") == 0
    assert _turn_of("turn-12") == 12


@pytest.mark.asyncio
async def test_concurrent_delegations_reserve_one_running_record(monkeypatch):
    from types import SimpleNamespace
    from app.services import session_plan
    from app.services.gis_harness import delegation
    plan = SimpleNamespace(gis_chapter={"query": "q"}, user_goal="q")
    async def load(*args):
        await asyncio.sleep(0)
        return plan
    async def save(value):
        await asyncio.sleep(0)
    monkeypatch.setattr(session_plan, "load_session_plan", load)
    monkeypatch.setattr(session_plan, "save_session_plan", save)
    goal = session_plan.goal_key(plan.gis_chapter, plan.user_goal)
    spec = delegation.DelegationSpec(role="cartography_reviewer", task="inspect", plan_step="qa:7")
    import time
    records = [delegation.DelegationRecord(delegation_id=f"d-{i}", spec=spec,
        status=delegation.STATUS_RUNNING, started_at=time.time()) for i in range(2)]
    reserved = await asyncio.gather(*(delegation._reserve_delegation("audit-reservation-test", record, goal) for record in records))
    assert reserved[0].delegation_id == reserved[1].delegation_id
    assert len(plan.gis_chapter[delegation.DELEGATIONS_KEY]["records"]) == 1


def test_trace_trimming_skips_protected_first_segment(tmp_path, monkeypatch):
    from app.services.gis_harness import trace_store
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(trace_store, "MAX_RECORDS_PER_SESSION", 20)
    for i in range(21):
        stages = [{"stage": "FINAL_VERDICT" if i < 16 else "USER_INTENT", "stage_id": 17, "ts": 1.0}]
        assert trace_store.persist_chain({"turn_id": f"protected-{i}", "session_id": "protect", "stages": stages}, session_id="protect")
    records = trace_store.read_chains("protect")
    assert len(records) == 20
    assert {r["turn_id"] for r in records} >= {f"protected-{i}" for i in range(16)}


@pytest.mark.asyncio
async def test_extracted_handler_receives_registered_tool_surface(monkeypatch):
    from app.services.gis_harness.tools import register_gis_harness_tools
    from app.services.gis_harness.planner_runtime import get_planner_runtime
    from app.tools.registry import ToolRegistry
    planner = get_planner_runtime()
    original = planner.plan_from_intent
    observed = []
    def capture(*args, **kwargs):
        observed.append(kwargs.get("available_tools"))
        return original(*args, **kwargs)
    monkeypatch.setattr(planner, "plan_from_intent", capture)
    registry = ToolRegistry()
    register_gis_harness_tools(registry)
    result = await registry.dispatch("webgis_map_intent", {"query": "成都小学分布"})
    assert result["success"]
    assert observed and observed[0] == set(registry.list_tools())
