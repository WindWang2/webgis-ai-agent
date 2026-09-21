"""M1 — Context Scope Contract: isolation rules and bounded projection."""
from __future__ import annotations

from app.services.gis_context.scope import (
    InvalidationPolicy,
    ScopeRef,
    ScopeTier,
    SensitivityClass,
    mission_scope_ref,
)


def test_bounded_dict_caps_every_field():
    ref = ScopeRef(
        tier=ScopeTier.MISSION,
        scope_id="m" * 200,
        org_id="o" * 200,
        project_id="p" * 200,
        user_id="u" * 200,
        source="s" * 200,
        revision="r" * 200,
    )
    d = ref.to_bounded_dict()
    assert all(len(v) <= 64 for v in (
        d["scope_id"], d["org_id"], d["project_id"], d["user_id"]))
    assert len(d["source"]) <= 48 and len(d["revision"]) <= 32
    assert d["tier"] == "mission"


def test_session_local_never_renders_outside_its_session():
    ref = ScopeRef(
        tier=ScopeTier.SESSION, scope_id="sess-a",
        sensitivity=SensitivityClass.SESSION_LOCAL,
    )
    assert ref.renderable_in(session_id="sess-a")
    assert not ref.renderable_in(session_id="sess-b")
    # Absent scope_id = no isolation anchor → deny (fail-closed).
    assert not ScopeRef(
        tier=ScopeTier.SESSION, scope_id="",
        sensitivity=SensitivityClass.SESSION_LOCAL,
    ).renderable_in(session_id="sess-a")


def test_project_scoped_record_cannot_leak_across_projects():
    ref = ScopeRef(
        tier=ScopeTier.PROJECT, project_id="prj-1",
        sensitivity=SensitivityClass.PROJECT_SCOPED,
    )
    assert ref.renderable_in(project_id="prj-1")
    assert not ref.renderable_in(project_id="prj-2")
    assert not ref.renderable_in(project_id="")


def test_org_scoped_record_cannot_leak_across_orgs():
    ref = ScopeRef(
        tier=ScopeTier.MISSION,
        org_id="org-1", sensitivity=SensitivityClass.ORG_SCOPED,
    )
    assert ref.renderable_in(org_id="org-1")
    assert not ref.renderable_in(org_id="org-2")


def test_mission_scope_ref_is_project_scoped_and_terminal_dying():
    ref = mission_scope_ref(mission_id="msn-1", org_id="org-1", project_id="prj-1")
    assert ref.tier is ScopeTier.MISSION
    assert ref.sensitivity is SensitivityClass.PROJECT_SCOPED
    assert ref.policy is InvalidationPolicy.ON_MISSION_TERMINAL
    # Without a project it degrades to org-scoped (never session-local).
    ref2 = mission_scope_ref(mission_id="msn-1", org_id="org-1")
    assert ref2.sensitivity is SensitivityClass.ORG_SCOPED
