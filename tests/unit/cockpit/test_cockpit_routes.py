"""Agent Ops Cockpit read-projection routes (cockpit.v1) — additive, read-only.

与既有 geocompute routes 测试同一纪律：
- 真实 JWT（12h TTL，collection 时铸造）；
- org 隔离（他人/未知一律 404 / 空集，无存在性预言机）;
- store 全部指向进程内 SQLite / 进程内假件（TestClient 内）；
- session-scoped 端点走 require_owned_session 依赖覆盖（与 jobs.py
  _proven_session_ids 同一归属证明链，审计 S33 漏洞类）。
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app

client = TestClient(app)


def _auth(user_id: str, org_claim=1, role: str = "editor") -> dict[str, str]:
    from app.core.auth import create_access_token

    return {
        "Authorization": f"Bearer {create_access_token(
            {'sub': user_id, 'role': role, 'org_id': org_claim},
            expires_delta=timedelta(hours=12),
        )}"
    }


@pytest.fixture()
def cockpit_env(monkeypatch):
    """Hermetic cockpit env: in-memory mission store + fixed org mapping."""
    from app.core.database import Base
    from app.models import mission as _mission_models  # noqa: F401 — register tables
    from app.services.mission_runtime.service import MissionRuntimeService
    from app.services.mission_runtime.store import MissionStore
    from app.api.routes import cockpit as cockpit_mod

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    svc = MissionRuntimeService(store=MissionStore(factory=factory))
    monkeypatch.setattr(cockpit_mod, "get_mission_runtime", lambda: svc)

    org_holder = {"org": "org-a"}
    monkeypatch.setattr(
        cockpit_mod, "_effective_org", lambda user: org_holder["org"]
    )

    # session ownership: tests opt in per-case; default deny (404 semantics
    # identical to verify_session_owner without an oracle).
    from fastapi import HTTPException

    def _deny(session_id: str):
        raise HTTPException(status_code=404, detail="Session not found")

    owner_holder = {"allow": None}

    def _fake_dep(session_id: str):
        if owner_holder["allow"] is not None and session_id in owner_holder["allow"]:
            return object()
        raise HTTPException(status_code=404, detail="Session not found")

    from app.core.auth import require_owned_session

    app.dependency_overrides[require_owned_session] = _fake_dep

    # reset process-local session context + runtime trace per test
    from app.services.gis_harness.hotpath_convergence.session_ctx import (
        reset_turn_context,
    )
    from app.services.gis_harness.trace import get_runtime_trace

    reset_turn_context()
    get_runtime_trace().reset()

    yield {
        "svc": svc,
        "org": org_holder,
        "owner": owner_holder,
        "trace": get_runtime_trace(),
    }

    app.dependency_overrides.pop(require_owned_session, None)
    reset_turn_context()
    get_runtime_trace().reset()


def test_health_open_no_auth():
    res = client.get("/api/v1/cockpit/health")
    assert res.status_code == 200
    body = res.json()
    assert body["schema"] == "cockpit.v1"
    assert isinstance(body["enabled"], bool)


def test_kill_switch_503_on_reads(cockpit_env, monkeypatch):
    monkeypatch.setenv("GIS_MISSION_RUNTIME", "0")
    res = client.get("/api/v1/cockpit/missions", headers=_auth("u1"))
    assert res.status_code == 503
    # app-wide unified error envelope: {"code","success","message",...}
    assert res.json()["message"] == "cockpit_disabled"
    # health stays honest about the switch
    assert client.get("/api/v1/cockpit/health").json()["enabled"] is False


def test_missions_list_requires_auth(cockpit_env):
    assert client.get("/api/v1/cockpit/missions").status_code == 401


def test_missions_list_org_scoped(cockpit_env):
    svc = cockpit_env["svc"]
    m_a = svc.create(org_id="org-a", user_id="u1", root_goal="成都小学分布分析")
    svc.create(org_id="org-other", user_id="u2", root_goal="other tenant")

    res = client.get("/api/v1/cockpit/missions", headers=_auth("u1"))
    assert res.status_code == 200
    body = res.json()
    assert body["schema"] == "cockpit.v1"
    ids = [m["mission_id"] for m in body["missions"]]
    assert m_a.mission_id in ids
    assert all(m["org_id"] == "org-a" for m in body["missions"])
    # envelope protocol mirrors jobs.ts discipline
    assert "has_active" in body and "poll_after_ms" in body
    assert body["has_active"] is True
    assert 1000 <= body["poll_after_ms"] <= 30000


def test_missions_list_all_terminal_reports_no_active(cockpit_env):
    svc = cockpit_env["svc"]
    m = svc.create(org_id="org-a", user_id="u1", root_goal="g")
    svc.cancel(m.mission_id, worker_id="w1")
    body = client.get("/api/v1/cockpit/missions", headers=_auth("u1")).json()
    # unfinished-only projection for v1: cancelled mission not listed
    assert body["missions"] == []
    assert body["has_active"] is False


def test_mission_detail_composite(cockpit_env):
    svc = cockpit_env["svc"]
    m = svc.create(org_id="org-a", user_id="u1", root_goal="g", quota={"tokens": 100})
    svc.start(m.mission_id, worker_id="w1")

    res = client.get(f"/api/v1/cockpit/missions/{m.mission_id}", headers=_auth("u1"))
    assert res.status_code == 200
    body = res.json()
    assert body["schema"] == "cockpit.v1"
    assert body["mission"]["mission_id"] == m.mission_id
    assert body["mission"]["state"] == "running"
    assert body["mission"]["resource_budget"]["quota"]["tokens"] == 100
    assert body["diagnostics"]["state"] == "running"
    assert body["diagnostics"]["last_checkpoint"]


def test_mission_detail_cross_org_404(cockpit_env):
    svc = cockpit_env["svc"]
    m = svc.create(org_id="org-secret", user_id="u2", root_goal="g")
    res = client.get(f"/api/v1/cockpit/missions/{m.mission_id}", headers=_auth("u1"))
    assert res.status_code == 404
    assert res.json()["message"] == "mission_not_found"


def test_unknown_mission_404(cockpit_env):
    res = client.get("/api/v1/cockpit/missions/nope", headers=_auth("u1"))
    assert res.status_code == 404


def test_timeline_bounded_desc_with_goal_revisions(cockpit_env):
    svc = cockpit_env["svc"]
    m = svc.create(org_id="org-a", user_id="u1", root_goal="g")
    svc.start(m.mission_id, worker_id="w1")
    store = svc.store
    for i in range(40):  # store caps list at 32
        store.write_checkpoint(m.mission_id, lease_epoch=1, owner="w1", note=f"c{i}")

    res = client.get(
        f"/api/v1/cockpit/missions/{m.mission_id}/timeline", headers=_auth("u1")
    )
    assert res.status_code == 200
    body = res.json()
    assert body["schema"] == "cockpit.v1"
    assert body["mission_id"] == m.mission_id
    cps = body["checkpoints"]
    assert 1 < len(cps) <= 32
    created = [c["created_at"] for c in cps]
    assert created == sorted(created, reverse=True)
    assert all("goal_revision" in c and "state" in c for c in cps)
    assert body["state"] == "running"
    assert "refs" in body and "blocked_reason" in body


def test_swarm_projection(cockpit_env):
    from app.services.mission_runtime.contracts import (
        SwarmTaskDurableState, SwarmTaskReceipt,
    )

    svc = cockpit_env["svc"]
    m = svc.create(org_id="org-a", user_id="u1", root_goal="g")
    run = svc.store.create_swarm_run(
        m.mission_id, org_id="org-a", goal_slice="slice-1")
    svc.store.settle_swarm_task(run.swarm_run_id, SwarmTaskReceipt(
        task_id="t1", state=SwarmTaskDurableState.SUCCEEDED))

    res = client.get(f"/api/v1/cockpit/missions/{m.mission_id}/swarm", headers=_auth("u1"))
    assert res.status_code == 200
    body = res.json()
    assert body["schema"] == "cockpit.v1"
    assert len(body["runs"]) == 1
    run_view = body["runs"][0]
    assert run_view["swarm_run_id"] == run.swarm_run_id
    assert run_view["tasks"]["t1"]["state"] == SwarmTaskDurableState.SUCCEEDED.value
    # empty swarm list stays an empty list, not 404
    m2 = svc.create(org_id="org-a", user_id="u1", root_goal="g2")
    body2 = client.get(f"/api/v1/cockpit/missions/{m2.mission_id}/swarm", headers=_auth("u1")).json()
    assert body2["runs"] == []


def test_skill_view_absent_then_present(cockpit_env):
    cockpit_env["owner"]["allow"] = {"sess-1"}
    body = client.get(
        "/api/v1/cockpit/sessions/sess-1/skill", headers=_auth("u1")
    ).json()
    assert body["present"] is False
    assert body["guidance"] is None

    from app.services.gis_harness.hotpath_convergence.session_ctx import (
        set_skill_bundle, set_mission_id,
    )

    class _Bundle:  # mirrors SkillGuidanceBundle.to_bounded_dict contract
        def to_bounded_dict(self):
            return {
                "decision": {"selected_skill": "density-analysis", "confidence": 0.9},
                "projection": {"guides_planning": True},
                "shadow": None,
                "guides_planning": True,
                "pi_context": {"skill": "density-analysis"},
            }

    bounded = _Bundle().to_bounded_dict()
    # production write order (tools.py): set_skill_bundle(sid, bundle, bounded)
    set_skill_bundle("sess-1", _Bundle(), bounded)
    set_mission_id("sess-1", "m-123")
    body = client.get(
        "/api/v1/cockpit/sessions/sess-1/skill", headers=_auth("u1")
    ).json()
    assert body["present"] is True
    assert body["guidance"]["decision"]["selected_skill"] == "density-analysis"
    assert body["mission_id"] == "m-123"


def test_evidence_view_empty_and_populated(cockpit_env):
    cockpit_env["owner"]["allow"] = {"sess-1"}
    body = client.get(
        "/api/v1/cockpit/sessions/sess-1/evidence", headers=_auth("u1")
    ).json()
    assert body["present"] is False
    assert body["claims"] == []

    from app.services.gis_harness.evidence_claim.contracts import (
        Claim, ClaimStatus, ClaimType, EvidenceNode, EvidenceKind, RelationEdge,
        RelationType,
    )
    from app.services.gis_harness.hotpath_convergence.session_ctx import (
        get_or_create_claim_store,
    )

    store = get_or_create_claim_store("sess-1")
    ev = store.upsert_evidence(EvidenceNode(
        evidence_id="e1", kind=EvidenceKind.STATISTIC, ref="ref:ds/1"))
    assert ev.evidence_id == "e1"
    store.upsert_claim(Claim(
        claim_id="c1", claim_type=ClaimType.DENSITY, subject="成都市",
        value=12.5, unit="个/km²", status=ClaimStatus.SUPPORTED,
        supporting_evidence_refs=["e1"], session_id="sess-1"))
    store.add_edge(RelationEdge(
        edge_id="ed1", relation=RelationType.SUPPORTS, src="c1", dst="e1"))

    body = client.get(
        "/api/v1/cockpit/sessions/sess-1/evidence", headers=_auth("u1")
    ).json()
    assert body["present"] is True
    assert body["claims"][0]["claim_id"] == "c1"
    assert body["claims"][0]["status"] == "supported"
    assert body["edges"][0]["relation"] == "supports"
    assert body["stats"]["claims"] == 1


def test_trace_view_bounded_no_cot(cockpit_env):
    cockpit_env["owner"]["allow"] = {"sess-1"}
    tr = cockpit_env["trace"]
    tr.record("sess-1", "observation", layer="roads", ok=True)
    tr.record("sess-1", "action_intent", action="add_layer",
              # adversarial: oversized detail must be truncated server-side
              **{f"k{i}": "x" * 300 for i in range(12)})
    tr.record("sess-1", "not_a_stage", secret="value")  # closed vocabulary drops

    res = client.get(
        "/api/v1/cockpit/sessions/sess-1/trace", headers=_auth("u1")
    )
    assert res.status_code == 200
    body = res.json()
    assert body["schema"] == "cockpit.v1"
    events = body["events"]
    assert len(events) == 2  # unknown stage never recorded
    for ev in events:
        assert len(ev["detail"]) <= 8
        for v in ev["detail"].values():
            assert len(str(v)) <= 96
    assert "counters" in body and "summary" in body


def test_session_endpoint_unowned_404(cockpit_env):
    cockpit_env["owner"]["allow"] = None  # default deny
    res = client.get(
        "/api/v1/cockpit/sessions/sess-unknown/evidence", headers=_auth("u1")
    )
    assert res.status_code == 404


def test_list_limit_out_of_range_422(cockpit_env):
    # sibling-route convention (jobs.py): le=200 → 422; the FE client clamps
    # before sending. Server-side hard caps still bound every projection.
    res = client.get(
        "/api/v1/cockpit/missions", params={"limit": 100000}, headers=_auth("u1")
    )
    assert res.status_code == 422
