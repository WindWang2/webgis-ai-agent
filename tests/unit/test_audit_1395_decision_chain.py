"""#1395: harness decision-chain wiring — capability bind + mission/knowledge Pi path."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_flags(monkeypatch):
    monkeypatch.delenv("GIS_CAPABILITY_DISPATCH_BIND", raising=False)
    monkeypatch.delenv("GIS_MISSION_HOTPATH", raising=False)
    monkeypatch.delenv("GIS_MISSION_RUNTIME", raising=False)
    monkeypatch.delenv("GIS_PROJECT_KNOWLEDGE", raising=False)
    from app.services.gis_harness.hotpath_convergence import reset_turn_context

    reset_turn_context()
    yield
    reset_turn_context()
    monkeypatch.delenv("GIS_CAPABILITY_DISPATCH_BIND", raising=False)
    monkeypatch.delenv("GIS_MISSION_HOTPATH", raising=False)
    monkeypatch.delenv("GIS_MISSION_RUNTIME", raising=False)
    monkeypatch.delenv("GIS_PROJECT_KNOWLEDGE", raising=False)


# ── capability bind at dispatch ────────────────────────────────────────


def test_plan_candidates_v8_has_production_caller_via_bind():
    """plan_candidates_v8 is no longer test-only — bind module imports it.

    ADR-0204 D3：完整求值（含 plan_candidates_v8 调用）迁入
    ``bind_tool_capability``；``check_tool_capability_at_dispatch`` 是
    兼容包装。
    """
    import inspect
    from app.services.gis_harness.hotpath_convergence import capability_bind as mod

    src = inspect.getsource(mod.bind_tool_capability)
    assert "plan_candidates_v8" in src


def test_capability_bind_kill_switch_off(monkeypatch):
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        check_tool_capability_at_dispatch,
    )

    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "0")
    reg = MagicMock()
    reg.metadata.return_value = {"capabilities": ["kde_density"]}
    assert check_tool_capability_at_dispatch("any_tool", registry=reg) is None


def test_capability_bind_allows_when_not_excluded(monkeypatch):
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        check_tool_capability_at_dispatch,
    )
    from app.services.gis_harness.qualification_v8 import (
        QualificationResult,
        QualificationStatus,
    )
    from app.services.gis_harness.candidate_planner_v8 import Candidate, CandidatePlan

    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")

    cand = Candidate(
        kind="tool",
        id="good_tool",
        qualification=QualificationResult(status=QualificationStatus.ELIGIBLE),
        latency_class="fast",
        reliability_penalty=0.0,
        score=0.0,
    )
    plan = CandidatePlan(capability_id="cap_x", candidates=[cand], excluded=[])

    def _fake_plan(cap, ctx, **_kw):
        return plan

    monkeypatch.setattr(
        "app.services.gis_harness.candidate_planner_v8.plan_candidates_v8",
        _fake_plan,
    )
    # Also patch the import site inside the function via module reload path:

    monkeypatch.setattr(
        "app.services.gis_harness.hotpath_convergence.capability_bind.plan_candidates_v8",
        _fake_plan,
        raising=False,
    )

    # Patch at the import used inside the function by wrapping plan_candidates_v8
    # where it is imported from:
    import app.services.gis_harness.candidate_planner_v8 as cp

    monkeypatch.setattr(cp, "plan_candidates_v8", _fake_plan)

    reg = MagicMock()
    reg.metadata.return_value = {"capabilities": ["cap_x"]}
    assert check_tool_capability_at_dispatch("good_tool", registry=reg) is None


def test_capability_bind_denies_ineligible_when_alternative_exists(monkeypatch):
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        CAPABILITY_INELIGIBLE_CODE,
        check_tool_capability_at_dispatch,
    )
    from app.services.gis_harness.qualification_v8 import (
        QualificationResult,
        QualificationStatus,
    )
    from app.services.gis_harness.candidate_planner_v8 import Candidate, CandidatePlan

    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")

    good = Candidate(
        kind="tool",
        id="eligible_alt",
        qualification=QualificationResult(status=QualificationStatus.ELIGIBLE),
        latency_class="fast",
        reliability_penalty=0.0,
        score=0.0,
    )
    plan = CandidatePlan(
        capability_id="cap_x",
        candidates=[good],
        excluded=[{
            "kind": "tool",
            "id": "bad_tool",
            "qualification": {
                "status": "ineligible",
                "reasons": [{
                    "check": "gpu",
                    "observed": "false",
                    "expected": "true",
                    "hint": "enable GPU",
                }],
            },
        }],
    )

    import app.services.gis_harness.candidate_planner_v8 as cp

    monkeypatch.setattr(cp, "plan_candidates_v8", lambda *a, **k: plan)

    reg = MagicMock()
    reg.metadata.return_value = {"capabilities": ["cap_x"]}
    decision = check_tool_capability_at_dispatch("bad_tool", registry=reg)
    assert decision is not None
    assert decision.allowed is False
    assert decision.code == CAPABILITY_INELIGIBLE_CODE
    assert decision.alternatives[0]["id"] == "eligible_alt"
    assert "INELIGIBLE" in decision.denial_text()
    details = decision.to_details()
    assert details["code"] == CAPABILITY_INELIGIBLE_CODE
    assert details["retryable"] is True


def test_capability_bind_pass_when_ineligible_but_no_alternative(monkeypatch):
    """No eligible alt → fail-open to governor/registry (honest residual path)."""
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        check_tool_capability_at_dispatch,
    )
    from app.services.gis_harness.candidate_planner_v8 import CandidatePlan

    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")
    plan = CandidatePlan(
        capability_id="cap_x",
        candidates=[],
        excluded=[{
            "kind": "tool",
            "id": "lonely_tool",
            "qualification": {"status": "ineligible", "reasons": []},
        }],
    )
    import app.services.gis_harness.candidate_planner_v8 as cp

    monkeypatch.setattr(cp, "plan_candidates_v8", lambda *a, **k: plan)
    reg = MagicMock()
    reg.metadata.return_value = {"capabilities": ["cap_x"]}
    assert check_tool_capability_at_dispatch("lonely_tool", registry=reg) is None


# ── mission bind on Pi path ────────────────────────────────────────────


def test_pi_mission_bind_noop_when_hotpath_off(monkeypatch):
    from app.services.gis_harness.hotpath_convergence.pi_mission import (
        maybe_bind_mission_for_pi_turn,
    )
    from app.services.gis_harness.hotpath_convergence import get_turn_context

    monkeypatch.setenv("GIS_MISSION_HOTPATH", "0")
    monkeypatch.setenv("GIS_MISSION_RUNTIME", "1")
    result = maybe_bind_mission_for_pi_turn(
        session_id="s-1395",
        org_id="1",
        root_goal="buffer rivers",
    )
    assert result.mission_id == ""
    assert "disabled" in result.skipped_reason or result.skipped_reason
    assert get_turn_context("s-1395", tenant_id="1").mission_id == ""


def test_pi_mission_bind_creates_when_hotpath_on(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core.database import Base
    import app.models.mission  # noqa: F401
    from app.services.mission_runtime.store import MissionStore
    from app.services.mission_runtime.service import MissionRuntimeService
    from app.services.gis_harness.hotpath_convergence.pi_mission import (
        maybe_bind_mission_for_pi_turn,
    )
    from app.services.gis_harness.hotpath_convergence import get_turn_context

    monkeypatch.setenv("GIS_MISSION_HOTPATH", "1")
    monkeypatch.setenv("GIS_MISSION_RUNTIME", "1")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = MissionRuntimeService(store=MissionStore(factory=factory))

    result = maybe_bind_mission_for_pi_turn(
        session_id="s-1395-on",
        org_id="42",
        user_id="u1",
        root_goal="multi-step GIS analysis",
        runtime=runtime,
    )
    assert result.created is True
    assert result.mission_id
    assert get_turn_context("s-1395-on", tenant_id="42").mission_id == result.mission_id


# ── project knowledge activate on context path ─────────────────────────


def test_project_knowledge_block_empty_when_flag_off(monkeypatch):
    from app.services.chat.context_assembler import _build_project_knowledge_block

    monkeypatch.setenv("GIS_PROJECT_KNOWLEDGE", "0")
    assert _build_project_knowledge_block("proj_x", org_id="1") == ""


def test_project_knowledge_block_empty_without_org(monkeypatch):
    from app.services.chat.context_assembler import _build_project_knowledge_block

    monkeypatch.setenv("GIS_PROJECT_KNOWLEDGE", "1")
    assert _build_project_knowledge_block("proj_x", org_id="") == ""
    assert _build_project_knowledge_block("proj_x", org_id=None) == ""


def test_project_knowledge_block_renders_when_entries(monkeypatch):
    from app.services.chat import context_assembler as ca
    from app.services.project_knowledge.contract import (
        AS_ARTIFACT,
        EK_ARTIFACT,
        KnowledgeEntry,
    )

    monkeypatch.setenv("GIS_PROJECT_KNOWLEDGE", "1")

    class _FakeProject:
        id = "proj_1"
        name = "Demo"
        org_id = 1

    class _FakeDB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ca, "_get_session_local", lambda: (lambda: _FakeDB()))

    class _PS:
        @staticmethod
        def get_project_with_auth(db, project_id, user_id=None, org_id=None):
            return _FakeProject()

    monkeypatch.setattr(
        "app.services.project_service.ProjectService.get_project_with_auth",
        _PS.get_project_with_auth,
    )

    entry = KnowledgeEntry(
        id="pkx_1",
        org_id="1",
        project_id="proj_1",
        entity_kind=EK_ARTIFACT,
        authority_store=AS_ARTIFACT,
        authority_id="art-1",
        subject="土地覆盖",
        version_token="tok",
        summary="可复用产物",
        status="active",
    )

    import app.services.project_knowledge.store as ks

    monkeypatch.setattr(
        ks, "get_active_entries",
        lambda db, **kw: [entry],
    )

    text = ca._build_project_knowledge_block(
        "proj_1", org_id="1", user_id="u1",
    )
    assert "<project_knowledge" in text
    assert "土地覆盖" in text


def test_dispatch_tool_bound_returns_capability_denial(monkeypatch):
    """End-to-end: dispatch surfaces CAPABILITY_INELIGIBLE（Pi/legacy 单点）。

    ADR-0204 D3：bind 唯一调用点迁入 ToolDispatchService.dispatch —— Pi
    桥经共享 service 走同一闸；拒绝细节经 raw_result（Pi details）与
    llm_payload（content）原样流达。
    """
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        CAPABILITY_INELIGIBLE_CODE,
        CapabilityBindOutcome,
        CapabilityDispatchDecision,
    )

    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")

    decision = CapabilityDispatchDecision(
        allowed=False,
        code=CAPABILITY_INELIGIBLE_CODE,
        reason="gpu: false (expected true)",
        capability_id="cap_x",
        tool_name="bad_tool",
        alternatives=[{"kind": "tool", "id": "good_tool", "score": 0.0}],
    )
    outcome = CapabilityBindOutcome(
        decision=decision,
        evidence={
            "tool": "bad_tool", "action": "refused",
            "capabilities": ["cap_x"], "capability": "cap_x",
            "status": "ineligible", "reason": "gpu: false (expected true)",
            "code": CAPABILITY_INELIGIBLE_CODE,
            "alternatives": decision.alternatives[:2],
        },
    )
    monkeypatch.setattr(
        "app.services.gis_harness.hotpath_convergence.bind_tool_capability",
        lambda *a, **k: outcome,
    )

    from app.services.tool_dispatch_service import ToolDispatchService

    reg = MagicMock()
    reg.list_tools.return_value = ["bad_tool"]
    reg.metadata.return_value = {"tier": 1, "capabilities": ["cap_x"]}
    service = ToolDispatchService(registry=reg)

    tc = {"id": "tc-1", "function": {"name": "bad_tool", "arguments": {}}}

    async def _run():
        return await service.dispatch(tc, "s-deny", set())

    result = asyncio.run(_run())
    assert result.status == "error"
    assert result.raw_result.get("code") == CAPABILITY_INELIGIBLE_CODE
    assert result.capability_evidence["action"] == "refused"
    assert "INELIGIBLE" in result.llm_payload

    # Pi details 契约锁定：raw_result 经 _slim_pi_details_payload 原样流达
    # （PiToolResponse details 的来源，agent_pi_bridge 同一函数）。
    from app.agent_pi_bridge import _slim_pi_details_payload

    details = _slim_pi_details_payload(result)
    assert details.get("code") == CAPABILITY_INELIGIBLE_CODE
    assert details.get("capability") == "cap_x"
