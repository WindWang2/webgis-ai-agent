"""M4 — Hot-path wiring: default-on, kill switch, stickiness, isolation,
mission-terminal lazy purge, backend-absent graceful degradation."""
from __future__ import annotations

import asyncio

import pytest

from app.services.gis_context import hotpath as hp
from app.services.gis_context.working_context import GISWorkingContext


def _plain(text: str) -> str:
    return text.replace("<untrusted_gis_context>", "").replace(
        "</untrusted_gis_context>", "")


def _mapspec(layers=1, sources=1):
    return {
        "view": {"bounds": [103.9, 30.6, 104.2, 30.8]},
        "layers": [
            {"id": f"L{i}", "type": "fill", "layout": {"visibility": "visible"}}
            for i in range(layers)
        ],
        "sources": (
            {"schools": {"ref_id": "ref:schools", "content_revision": "rev-1"}}
            if sources else {}
        ),
    }


def _run(coro):
    return asyncio.run(coro)


def test_kill_switch_restores_prior_behavior(wc_store, monkeypatch, wc):
    monkeypatch.setenv("GIS_CONTEXT_SCOPES", "0")
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-x", org_id="org-1", project_id="prj-1",
        state={"_mission_binding": {"mission_id": wc.mission_id, "org_id": "org-1"}},
        mapspec=_mapspec(),
    ))
    assert text == ""
    assert receipt.skipped_reason == "flag_off"


def test_no_mission_and_no_gis_work_is_a_clean_miss(monkeypatch):
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-1", org_id="org-1", project_id="prj-1",
        state={}, mapspec={},
    ))
    assert text == "" and receipt.miss_reason == "no_mission"


def test_auto_bind_is_evidence_gated_and_sticky(wc_store, mission_runtime, monkeypatch):
    """Default-on creates exactly one mission, only when real GIS work exists."""
    from app.services.session_data import session_data_manager

    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    monkeypatch.setattr(
        "app.services.mission_runtime.service.get_mission_runtime",
        lambda store=None: mission_runtime,
    )
    created = []

    real_create = mission_runtime.create

    def counting_create(**kw):
        rec = real_create(**kw)
        created.append(rec.mission_id)
        return rec

    monkeypatch.setattr(mission_runtime, "create", counting_create)

    # Turn 1 — chat without any map work: no mission row.
    _run(hp.assemble_gis_context_card(
        "sess-a", org_id="org-1", project_id="prj-1", user_id="u-1",
        query_text="成都学校分布", state={}, mapspec={},
    ))
    assert created == []

    # Turn 2 — map work observed: one mission is bound and persisted durably.
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-a", org_id="org-1", project_id="prj-1", user_id="u-1",
        query_text="成都学校分布", state={}, mapspec=_mapspec(),
    ))
    assert len(created) == 1 and receipt.notes == ["mission_bound"]
    durable = _run(session_data_manager.get_map_state("sess-a"))
    assert durable[hp.MISSION_BINDING_KEY]["mission_id"] == created[0]
    assert text  # first basis card renders

    # Turn 3 — sticky: the durable binding is reused, no second mission.
    text3, receipt3 = _run(hp.assemble_gis_context_card(
        "sess-a", org_id="org-1", project_id="prj-1", user_id="u-1",
        query_text="只看小学", state=durable, mapspec=_mapspec(),
    ))
    assert len(created) == 1
    assert text3


def test_org_mismatched_binding_is_refused(wc_store, wc):
    state = {"_mission_binding": {"mission_id": wc.mission_id, "org_id": "org-9"}}
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-b", org_id="org-1", project_id="prj-1",
        state=state, mapspec=_mapspec(),
    ))
    assert text == "" and receipt.miss_reason == "org_mismatch"


def test_project_mismatched_context_never_renders(wc_store, wc, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    wc_store.save(wc, expected_revision=None)  # context belongs to prj-1
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-c", org_id="org-1", project_id="prj-OTHER",
        state={"_mission_binding": {"mission_id": wc.mission_id, "org_id": "org-1"}},
        mapspec=_mapspec(),
    ))
    assert text == "" and receipt.miss_reason == "project_mismatch"


def test_mission_terminal_lazy_purge(wc_store, mission_runtime, wc, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    monkeypatch.setattr(
        "app.services.mission_runtime.service.get_mission_runtime",
        lambda store=None: mission_runtime,
    )
    rec = mission_runtime.create(org_id="org-1", user_id="u-1", root_goal="g")
    mission_runtime.start(rec.mission_id, worker_id="w-1", org_id="org-1")
    wc.mission_id = rec.mission_id
    wc_store.save(wc, expected_revision=None)

    mission_runtime.complete(rec.mission_id, worker_id="w-1", org_id="org-1")

    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-d", org_id="org-1", project_id="prj-1",
        state={"_mission_binding": {"mission_id": rec.mission_id, "org_id": "org-1"}},
        mapspec=_mapspec(),
    ))
    assert text == "" and receipt.miss_reason == "mission_terminal"
    assert wc_store.load(rec.mission_id, org_id="org-1") is None  # purged


def test_backend_absent_degrades_to_empty_card(monkeypatch):
    """No DB, no mission runtime wiring — the turn still gets "" + reason,
    never an exception (graceful no-op requirement)."""
    from app.services.gis_context.store import WorkingContextStore

    def broken_store():
        raise RuntimeError("db down")

    monkeypatch.setattr(hp, "_store", broken_store)
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-e", org_id="org-1", project_id="prj-1",
        state={"_mission_binding": {"mission_id": "msn-z", "org_id": "org-1"}},
        mapspec=_mapspec(),
    ))
    assert text == ""
    assert receipt.miss_reason or receipt.skipped_reason


def test_concurrent_sessions_do_not_cross_talk(wc_store, mission_runtime, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    monkeypatch.setattr(
        "app.services.mission_runtime.service.get_mission_runtime",
        lambda store=None: mission_runtime,
    )
    _run(hp.assemble_gis_context_card(
        "sess-a", org_id="org-1", project_id="prj-1", user_id="u-1",
        state={}, mapspec=_mapspec(), query_text="学校分布",
    ))
    _run(hp.assemble_gis_context_card(
        "sess-b", org_id="org-1", project_id="prj-2", user_id="u-2",
        state={}, mapspec=_mapspec(layers=2), query_text="河流水系",
    ))
    from app.services.session_data import session_data_manager

    durable_a = _run(session_data_manager.get_map_state("sess-a"))
    durable_b = _run(session_data_manager.get_map_state("sess-b"))
    mid_a = durable_a[hp.MISSION_BINDING_KEY]["mission_id"]
    mid_b = durable_b[hp.MISSION_BINDING_KEY]["mission_id"]
    assert mid_a and mid_b and mid_a != mid_b


def test_budget_yield_skips_render(wc_store, wc, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    wc_store.save(wc, expected_revision=None)
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-f", org_id="org-1", project_id="prj-1",
        state={"_mission_binding": {"mission_id": wc.mission_id, "org_id": "org-1"}},
        mapspec=_mapspec(), budget_used=9999,
    ))
    assert text == "" and receipt.skipped_reason == "budget_skipped"


def test_include_reuse_false_skips_reuse_section(wc_store, wc, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    wc_store.save(wc, expected_revision=None)
    async def _no_reuse(*a, **kw):  # would blow up if actually called
        raise AssertionError("reuse fetch must be skipped")
    monkeypatch.setattr(hp, "_fetch_reuse_candidates", _no_reuse)
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-g", org_id="org-1", project_id="prj-1",
        state={"_mission_binding": {"mission_id": wc.mission_id, "org_id": "org-1"}},
        mapspec=_mapspec(), include_reuse=False,
    ))
    assert "项目复用" not in text


def test_durable_binding_survives_process_local_loss(wc_store, wc, monkeypatch):
    """Restart simulation: session_ctx is empty; the map_state binding
    still resolves the mission (session ⊂ mission continuity)."""
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    wc_store.save(wc, expected_revision=None)
    text, _ = _run(hp.assemble_gis_context_card(
        "sess-h", org_id="org-1", project_id="prj-1",
        state={"_mission_binding": {"mission_id": wc.mission_id, "org_id": "org-1"}},
        mapspec=_mapspec(),
    ))
    assert "AOI=成都市" in _plain(text)
