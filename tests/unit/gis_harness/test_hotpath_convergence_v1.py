"""Hot-path Convergence V1 — Direction 04 DoD matrix.

Hermetic: no LLM, no network. Covers:
- GIS_SKILL_POLICY=0 → unchanged planner / no skill_guidance
- GIS_MISSION_HOTPATH unset → no durable mission side effects
- claim ingest fail-closed / never invents SUPPORTED
- bounded pi card (no CoT)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from app.services.gis_harness.hotpath_convergence import (
    CLAIM_INGEST_ENV,
    MISSION_HOTPATH_ENV,
    bind_skill_guidance_at_plan_seam,
    build_hotpath_pi_context,
    claim_ingest_enabled,
    ingest_on_settle,
    maybe_bind_mission_for_turn,
    mission_hotpath_enabled,
    reset_turn_context,
)
from app.services.gis_harness.skills.policy import SKILL_POLICY_ENV
from app.services.gis_harness.evidence_claim import ClaimStore, ClaimStatus
from app.services.gis_harness.plan_candidates import generate_plan_candidates
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.mission_runtime.store import MissionStore
from app.services.mission_runtime.service import MissionRuntimeService


def _memory_runtime():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.core.database import Base
    import app.models.mission  # noqa: F401
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    store = MissionStore(factory=factory)
    return MissionRuntimeService(store=store), store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(SKILL_POLICY_ENV, raising=False)
    monkeypatch.delenv(MISSION_HOTPATH_ENV, raising=False)
    monkeypatch.delenv(CLAIM_INGEST_ENV, raising=False)
    monkeypatch.delenv("GIS_MISSION_RUNTIME", raising=False)
    reset_turn_context()
    yield
    reset_turn_context()
    monkeypatch.delenv(SKILL_POLICY_ENV, raising=False)
    monkeypatch.delenv(MISSION_HOTPATH_ENV, raising=False)
    monkeypatch.delenv(CLAIM_INGEST_ENV, raising=False)


@dataclass
class FakeArtifact:
    artifact_id: str
    artifact_type: str = "stats_table"
    status: str = "valid"
    revision: int = 1
    session_id: str = "s1"
    producer_capability: str = "admin_aggregation"
    inputs: List[str] = field(default_factory=list)
    replaces: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── SkillPolicy at plan seam ───────────────────────────────────────────


def test_skill_policy_off_returns_unchanged_inputs(monkeypatch):
    monkeypatch.setenv(SKILL_POLICY_ENV, "0")
    intent = resolve_map_request_intent("成都市武侯区学校密度分布")
    base = {"query": intent.query, "required_capabilities": ["x"]}
    out, bundle = bind_skill_guidance_at_plan_seam(intent, plan_inputs=base)
    assert bundle is None
    assert "skill_guidance" not in out
    assert out["required_capabilities"] == ["x"]
    assert out["query"] == base["query"]


def test_skill_policy_off_plan_candidates_identical(monkeypatch):
    monkeypatch.setenv(SKILL_POLICY_ENV, "0")
    intent = resolve_map_request_intent("成都市武侯区学校密度分布")
    a = generate_plan_candidates(intent, limit=4)
    b = generate_plan_candidates(intent, limit=4)
    assert a.to_bounded_dict() == b.to_bounded_dict()
    # Bind must not mutate candidate generation path.
    bind_skill_guidance_at_plan_seam(intent)
    c = generate_plan_candidates(intent, limit=4)
    assert c.to_bounded_dict() == a.to_bounded_dict()


def test_skill_policy_on_attaches_guidance(monkeypatch):
    monkeypatch.setenv(SKILL_POLICY_ENV, "1")
    intent = resolve_map_request_intent("成都市武侯区学校密度分布")
    out, bundle = bind_skill_guidance_at_plan_seam(
        intent,
        ontology_matches=["distribution.point_distribution"],
        record_evidence=False,
    )
    assert bundle is not None
    assert "skill_guidance" in out
    sg = out["skill_guidance"]
    assert "decision" in sg
    assert "guides_planning" in sg
    assert "pi_context" in sg


def test_compile_workflow_skill_guidance_respects_kill_switch(monkeypatch):
    from app.services.gis_harness.workflow_compiler import compile_workflow

    monkeypatch.setenv(SKILL_POLICY_ENV, "0")
    c = compile_workflow("成都市学校分布专题图")
    assert c.skill_guidance == {}

    monkeypatch.setenv(SKILL_POLICY_ENV, "1")
    c2 = compile_workflow("成都市学校分布专题图")
    # When library has matches may be non-empty; at minimum key exists as dict.
    assert isinstance(c2.skill_guidance, dict)


# ── Mission bind opt-in ────────────────────────────────────────────────


def test_mission_hotpath_default_off_no_durable_effects(monkeypatch):
    assert mission_hotpath_enabled() is False
    runtime, store = _memory_runtime()
    result = maybe_bind_mission_for_turn(
        session_id="sess-1",
        org_id="1",
        root_goal="分析成都市小学",
        runtime=runtime,
    )
    assert result.created is False
    assert result.mission_id == ""
    assert "disabled" in result.skipped_reason
    # No mission rows created
    unfinished = store.list_unfinished(org_id="1")
    assert unfinished == []


def test_mission_hotpath_on_creates_and_reuses(monkeypatch):
    monkeypatch.setenv(MISSION_HOTPATH_ENV, "1")
    monkeypatch.setenv("GIS_MISSION_RUNTIME", "1")
    assert mission_hotpath_enabled() is True
    runtime, store = _memory_runtime()

    created = maybe_bind_mission_for_turn(
        session_id="sess-1",
        org_id="org-a",
        user_id="u1",
        root_goal="分析成都市小学分布",
        runtime=runtime,
    )
    assert created.created is True
    assert created.mission_id
    mid = created.mission_id
    rec = store.get_mission(mid, org_id="org-a")
    assert rec is not None

    reused = maybe_bind_mission_for_turn(
        session_id="sess-1",
        org_id="org-a",
        root_goal="分析成都市小学分布",
        mission_id=mid,
        runtime=runtime,
    )
    assert reused.reused is True
    assert reused.mission_id == mid
    assert reused.created is False


def test_mission_passthrough_when_hotpath_off(monkeypatch):
    result = maybe_bind_mission_for_turn(
        mission_id="m-explicit",
        org_id="1",
        root_goal="x",
    )
    assert result.mission_id == "m-explicit"
    assert result.skipped_reason == "hotpath_off_passthrough"
    assert result.created is False


# ── Claim ingest fail-closed ───────────────────────────────────────────


def test_claim_ingest_disabled(monkeypatch):
    monkeypatch.setenv(CLAIM_INGEST_ENV, "0")
    assert claim_ingest_enabled() is False
    store = ClaimStore()
    report = ingest_on_settle(
        store,
        artifacts=[FakeArtifact(artifact_id="a1")],
        session_id="s1",
    )
    assert report.enabled is False
    assert report.evidence_ids == []
    assert store.all_evidence() == [] or len(list(store.all_evidence())) == 0


def test_claim_ingest_projects_evidence_unknown_not_supported():
    store = ClaimStore()
    art = FakeArtifact(
        artifact_id="ref:stats-1",
        metadata={
            "stat_type": "density",
            "unit": "per_km2",
            "method": "admin_density",
            "subject": "武侯区",
            "value": 12.5,
        },
    )
    report = ingest_on_settle(
        store,
        artifacts=[art],
        tenant_id="t1",
        session_id="s1",
    )
    assert report.enabled is True
    assert report.evidence_ids
    assert report.claim_ids
    assert report.any_supported is False
    for cid in report.claim_ids:
        claim = store.get_claim(cid)
        assert claim is not None
        assert claim.status != ClaimStatus.SUPPORTED


def test_claim_ingest_rank_without_statistic_evidence_fail_closed():
    store = ClaimStore()
    rows = [{"name": "武侯区", "value": 12.5}, {"name": "锦江区", "value": 10.0}]
    report = ingest_on_settle(
        store,
        rank_rows=rows,
        rank_meta={"method": "admin_density"},
        session_id="s1",
    )
    assert "rank_claim_missing_statistic_evidence" in report.errors
    assert report.claim_ids == []
    assert report.any_supported is False


def test_claim_ingest_rank_missing_claim_type_fail_closed():
    store = ClaimStore()
    art = FakeArtifact(artifact_id="ref:stats-rank", metadata={"method": "admin_density"})
    rows = [{"name": "武侯区", "value": 12.5}, {"name": "锦江区", "value": 10.0}]
    report = ingest_on_settle(
        store,
        artifacts=[art],
        rank_rows=rows,
        rank_meta={"method": "admin_density", "unit": "per_km2"},
        tenant_id="t1",
        session_id="s1",
    )
    assert "rank_claim_missing_claim_type" in report.errors
    assert report.claim_ids == []
    assert report.any_supported is False


def test_claim_ingest_rank_with_evidence_stays_unknown():
    store = ClaimStore()
    art = FakeArtifact(
        artifact_id="ref:stats-rank",
        metadata={
            "method": "admin_density",
            "stat_type": "density",
            "unit": "per_km2",
            "value": 12.5,
            "subject": "武侯区",
        },
    )
    rows = [{"name": "武侯区", "value": 12.5}, {"name": "锦江区", "value": 10.0}]
    report = ingest_on_settle(
        store,
        artifacts=[art],
        rank_rows=rows,
        rank_meta={
            "method": "admin_density",
            "unit": "per_km2",
            "claim_type": "density",
        },
        tenant_id="t1",
        session_id="s1",
    )
    assert report.claim_ids
    assert report.any_supported is False
    for st in report.statuses:
        assert st != "supported"


def test_claim_ingest_no_store_fail_closed():
    report = ingest_on_settle(None, artifacts=[FakeArtifact(artifact_id="a")])
    assert report.enabled is False
    assert "no_claim_store" in report.errors


# ── Pi bounded card ────────────────────────────────────────────────────


def test_pi_card_bounded_no_cot(monkeypatch):
    monkeypatch.setenv(SKILL_POLICY_ENV, "1")
    intent = resolve_map_request_intent("成都市学校密度")
    _, bundle = bind_skill_guidance_at_plan_seam(intent, record_evidence=False)
    store = ClaimStore()
    art = FakeArtifact(
        artifact_id="ref:s",
        metadata={"stat_type": "density", "subject": "武侯区", "value": 1.0, "unit": "n"},
    )
    ingest_on_settle(store, artifacts=[art], session_id="s1", tenant_id="t1")
    card = build_hotpath_pi_context(
        skill_bundle=bundle,
        claim_store=store,
    )
    assert card["schema"] == "hotpath_pi_context.v1"
    assert "chain_of_thought" not in card
    assert "cot" not in card
    assert "messages" not in card
    assert "thinking" not in card
    if card.get("skill"):
        assert "raw_llm" not in card["skill"]


def test_pi_card_empty_inputs():
    card = build_hotpath_pi_context()
    assert card["skill"] is None
    assert card["claims"] == []
    assert card["grounding"] is None


# ── Settle → verify contract (never invent SUPPORTED) ──────────────────


def test_settle_pi_card_does_not_invent_or_persist_supported():
    """#1330/#1334: ingest → build_hotpath_pi_context must not invent SUPPORTED."""
    from app.services.gis_harness.evidence_claim import verify_claim

    store = ClaimStore()
    # Incomplete proof metadata (no unit/stat_type) — classic invent path on master.
    art = FakeArtifact(
        artifact_id="ref:stats-settle",
        metadata={"method": "admin_density", "subject": "武侯区", "value": 12.5},
    )
    report = ingest_on_settle(
        store, artifacts=[art], tenant_id="t1", session_id="s1",
    )
    assert report.claim_ids
    assert report.any_supported is False
    cid = report.claim_ids[0]
    assert store.get_claim(cid).status != ClaimStatus.SUPPORTED

    card = build_hotpath_pi_context(
        claim_store=store,
        primary_claim_id=cid,
        expected_tenant_id="t1",
    )
    # Store must remain non-SUPPORTED after pi_card read path (no side effect).
    assert store.get_claim(cid).status != ClaimStatus.SUPPORTED
    for summary in card.get("claims") or []:
        assert summary.get("status") != "supported"
        assert summary.get("positive_proof") is not True

    # Value tamper must not verify SUPPORTED even if other fields later present.
    claim = store.get_claim(cid)
    claim = claim.model_copy(update={"value": 999.0})
    store.upsert_claim(claim)
    result = verify_claim(claim, store, expected_tenant_id="t1")
    assert result.status != ClaimStatus.SUPPORTED
    assert result.positive_proof is False



def test_claim_store_scoped_by_tenant_and_session():
    """#1331: empty vs _anon and cross-tenant stores must not collide."""
    from app.services.gis_harness.hotpath_convergence.session_ctx import (
        get_or_create_claim_store,
        reset_turn_context,
    )

    reset_turn_context()
    a = get_or_create_claim_store("", tenant_id="")
    b = get_or_create_claim_store("_anon", tenant_id="")
    assert a is not b

    t1 = get_or_create_claim_store("sess-1", tenant_id="tenant-A")
    t2 = get_or_create_claim_store("sess-1", tenant_id="tenant-B")
    assert t1 is not t2

    same = get_or_create_claim_store("sess-1", tenant_id="tenant-A")
    assert same is t1
    reset_turn_context()


def test_mission_runtime_off_and_no_disable(monkeypatch):
    """#1332: GIS_MISSION_RUNTIME=off/no must disable runtime (and hotpath gate)."""
    from app.services.mission_runtime.service import mission_runtime_enabled

    monkeypatch.setenv("GIS_MISSION_RUNTIME", "off")
    assert mission_runtime_enabled() is False
    monkeypatch.setenv(MISSION_HOTPATH_ENV, "1")
    assert mission_hotpath_enabled() is False

    monkeypatch.setenv("GIS_MISSION_RUNTIME", "no")
    assert mission_runtime_enabled() is False

    monkeypatch.setenv("GIS_MISSION_RUNTIME", "1")
    assert mission_runtime_enabled() is True


def test_create_swarm_run_requires_mission_org_match():
    """#1332: cross-org create_swarm_run must fail closed."""
    from app.services.mission_runtime.store import TransitionRejected
    from app.services.mission_runtime.swarm_bridge import DurableSwarmBridge

    runtime, store = _memory_runtime()
    m = runtime.create(org_id="org-A", user_id="u", root_goal="g")
    runtime.start(m.mission_id, worker_id="w1", org_id="org-A")
    bridge = DurableSwarmBridge(store)
    with pytest.raises(TransitionRejected):
        bridge.begin_run(
            m.mission_id,
            org_id="org-B",
            task_descriptors=[{"task_id": "t1", "side_effect": "pure"}],
        )
    assert store.list_swarm_runs_for_mission(m.mission_id, org_id="org-A") == []
    # Matching org still works.
    run = bridge.begin_run(
        m.mission_id,
        org_id="org-A",
        task_descriptors=[{"task_id": "t1", "side_effect": "pure"}],
    )
    assert run.swarm_run_id
    assert store.list_swarm_runs_for_mission(m.mission_id, org_id="org-B") == []


def test_projector_copies_proof_fields():
    """#1333: project_artifact_record must retain stat_type/unit/value/subject."""
    from app.services.gis_harness.evidence_claim import project_artifact_record

    node = project_artifact_record(
        FakeArtifact(
            artifact_id="ref:proof",
            metadata={
                "stat_type": "density",
                "unit": "per_km2",
                "value": 3.14,
                "subject": "武侯区",
                "method": "admin_density",
            },
        ),
        tenant_id="t1",
        session_id="s1",
    )
    assert node.metadata.get("stat_type") == "density"
    assert node.metadata.get("unit") == "per_km2"
    assert node.metadata.get("value") == 3.14
    assert node.metadata.get("subject") == "武侯区"
