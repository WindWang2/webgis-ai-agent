"""H09 — hot-path wiring end to end: fact capture on real turns,
dependency-precise staleness, reuse recompute refresh, and the kill
switch. Store is the hermetic in-memory fixture (conftest)."""
from __future__ import annotations

import asyncio

from app.services.gis_context import hotpath as hp
from app.services.gis_context.memory_graph import mapspec_token
from app.services.gis_context.working_context import (
    BasisDataset,
    ContextFact,
    DependencyEdge,
    DerivedFinding,
    GISWorkingContext,
    WorkingBasis,
)


def _run(coro):
    return asyncio.run(coro)


def _plain(text: str) -> str:
    return text.replace("<untrusted_gis_context>", "").replace(
        "</untrusted_gis_context>", "")


def _state(rev=5, provenance=None):
    state = {
        "_mission_binding": {"mission_id": "msn-wire0001", "org_id": "org-1"},
        "_cartographic_mutation_revision": rev,
    }
    if provenance:
        state["_gis_provenance"] = provenance
    return state


def _mapspec(ds_rev="rev-1"):
    return {
        "view": {"center": [104.06, 30.57], "zoom": 10.0},
        "layers": [{"id": "L1", "type": "fill", "layout": {"visibility": "visible"}}],
        "sources": {"schools": {"ref_id": "ref:schools", "content_revision": ds_rev}},
    }


def _candidate(subject="school-density", verdict="exact"):
    from types import SimpleNamespace

    return SimpleNamespace(
        entry=SimpleNamespace(subject=subject, authority_store="artifact",
                              authority_id="a-1"),
        verdict=verdict, reasons=[], stale_causes=[])


def _seed_graph_row(store, *, mapspec_rev=5, ds_rev="rev-1"):
    """A critique row anchored to the same world the observation will see."""
    wc = GISWorkingContext(
        mission_id="msn-wire0001", org_id="org-1", project_id="prj-1",
        revision=3,
        basis=WorkingBasis(
            aoi_bbox=[102.9, 30.5, 104.5, 31.5], aoi_name="成都市",
            datasets=[BasisDataset(ref_id="ref:schools", alias="schools",
                                   content_revision=ds_rev)],
        ),
    )
    wc.facts.append(ContextFact(fact_id="cf-1", kind="mapspec", ref="",
                                token=str(mapspec_rev), basis_revision=3))
    wc.facts.append(ContextFact(fact_id="cf-2", kind="dataset", ref="ref:schools",
                                token=ds_rev, basis_revision=3))
    wc.upsert_derived_finding(DerivedFinding(
        finding_id="df-1", family="critique", ref="label_collision:L1",
        label="label_collision", detail="ratio 0.4", status="current",
        generation=3, digest="dg-1", priority=2,
        depends_on=[DependencyEdge(dim="mapspec", ref="", anchor=str(mapspec_rev)),
                    DependencyEdge(dim="data", ref="ref:schools", anchor=ds_rev)],
        basis_revision=3))
    store.save(wc)
    return wc


def test_turn_captures_facts_and_renders_graph_rows(wc_store, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    monkeypatch.setattr(hp, "_fetch_reuse_candidates", lambda *a, **k: [])
    _seed_graph_row(wc_store)

    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-h09", org_id="org-1", project_id="prj-1", turn_id="t1",
        state=_state(rev=5), mapspec=_mapspec(),
    ))
    assert receipt.hit
    wc = wc_store.load("msn-wire0001", org_id="org-1")
    # Re-observing the same world is a stable capture (idempotent).
    kinds = {(f.kind, f.ref, f.token) for f in wc.facts}
    assert ("mapspec", "", "5") in kinds
    assert ("dataset", "ref:schools", "rev-1") in kinds
    # The anchored critique row renders as a grounded conclusion.
    assert "label_collision" in _plain(text)
    assert "依据" in _plain(text)


def test_semantic_user_edit_stales_mapspec_anchored_row_only(wc_store, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    monkeypatch.setattr(hp, "_fetch_reuse_candidates", lambda *a, **k: [])
    _wc = _seed_graph_row(wc_store)
    # A second critique row anchored only to the dataset (not the mapspec
    # structure) — a semantic mutation must not touch it.
    stored = wc_store.load("msn-wire0001", org_id="org-1")
    stored.upsert_derived_finding(DerivedFinding(
        finding_id="df-2", family="critique", ref="blank_map_risk:v1",
        label="blank_map_risk", status="current", generation=stored.revision,
        digest="dg-2", priority=1,
        depends_on=[DependencyEdge(dim="data", ref="ref:schools", anchor="rev-1")],
        basis_revision=stored.revision))
    stored.revision += 1
    wc_store.save(stored)

    provenance = [{
        "seq": 9, "origin": "user", "kind": "ReorderLayersIntent", "target": None,
        "revision": 9, "summary": "ReorderLayersIntent",
        "detail": {"mutation_id": "m-reorder", "override_kind": "semantic"},
    }]
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-h09", org_id="org-1", project_id="prj-1", turn_id="t2",
        state=_state(rev=10, provenance=provenance), mapspec=_mapspec(),
    ))
    assert receipt.graph_staled == 1
    after = wc_store.load("msn-wire0001", org_id="org-1")
    df1 = after.derived_finding("df-1")
    df2 = after.derived_finding("df-2")
    assert df1.status == "stale" and any("MAPSPEC_SEMANTIC" in r for r in df1.stale_reasons)
    assert df2.status == "current"  # dependency-precise: bystander untouched
    # Claim-level mirrors are never touched by user mutations (user-wins).
    assert all(f.status != "stale" for f in after.findings)
    # The stale row renders as recompute-owed, never as current.
    assert "需重算" in _plain(text)
    # The durable edit record carries the op id and semantic kind.
    assert any(e.op_id == "m-reorder" and e.kind == "reorder" for e in after.user_edits)


def test_reuse_row_created_then_recomputed_after_dataset_drift(
        wc_store, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    candidates = [_candidate(verdict="exact")]
    monkeypatch.setattr(hp, "_fetch_reuse_candidates",
                        lambda *a, **k: list(candidates))
    _seed_graph_row(wc_store)
    # No reuse row yet → turn 1 creates the durable decision row.
    _run(hp.assemble_gis_context_card(
        "sess-h09", org_id="org-1", project_id="prj-1", turn_id="t1",
        state=_state(rev=5), mapspec=_mapspec(),
    ))
    wc = wc_store.load("msn-wire0001", org_id="org-1")
    reuse_rows = [f for f in wc.derived_findings if f.family == "reuse"]
    assert len(reuse_rows) == 1 and reuse_rows[0].status == "current"

    # Turn 2: the dataset advanced → the reuse decision stales on its data
    # anchor, and the recompute executor re-derives it in the same turn.
    candidates.clear()
    candidates.append(_candidate(verdict="exact", subject="changed-subject"))
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-h09", org_id="org-1", project_id="prj-1", turn_id="t2",
        state=_state(rev=5), mapspec=_mapspec(ds_rev="rev-2"),
    ))
    assert receipt.graph_recomputed == 1
    after = wc_store.load("msn-wire0001", org_id="org-1")
    row = next(f for f in after.derived_findings if f.family == "reuse")
    assert row.status == "current"
    assert "changed-subject" in row.digest  # refreshed derivation published
    assert any(e.dim == "data" and e.anchor == "rev-2" for e in row.depends_on)
    kinds = [r.kind for r in after.revalidations]
    assert "FINDING_RECOMPUTED" in kinds
    restored = [r for r in after.revalidations
                if r.kind == "FINDING_RECOMPUTED" and r.verdict == "restored"]
    assert restored and restored[-1].target == row.finding_id


def test_kill_switch_restores_v2_graph_silence(wc_store, monkeypatch):
    monkeypatch.setenv("GIS_CONTEXT_MEMORY_GRAPH", "0")
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    monkeypatch.setattr(hp, "_fetch_reuse_candidates", lambda *a, **k: [])
    _seed_graph_row(wc_store)
    provenance = [{
        "seq": 9, "origin": "user", "kind": "ReorderLayersIntent", "target": None,
        "revision": 9, "summary": "ReorderLayersIntent",
        "detail": {"mutation_id": "m-reorder", "override_kind": "semantic"},
    }]
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-h09", org_id="org-1", project_id="prj-1", turn_id="t1",
        state=_state(rev=6, provenance=provenance), mapspec=_mapspec(),
    ))
    assert receipt.graph_staled == 0 and receipt.graph_recomputed == 0
    wc = wc_store.load("msn-wire0001", org_id="org-1")
    # No NEW capture under the kill switch: the seeded mapspec token stays
    # at 5 even though the world advanced to 6.
    assert mapspec_token(wc) == "5"
    df1 = wc.derived_finding("df-1")
    assert df1.status == "current"  # no graph walk
    assert not any(e.op_id == "m-reorder" for e in wc.user_edits)
    assert "需重算" not in _plain(text)


def test_read_mostly_turn_writes_nothing(wc_store, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    monkeypatch.setattr(hp, "_fetch_reuse_candidates", lambda *a, **k: [])
    _seed_graph_row(wc_store)
    kw = dict(
        org_id="org-1", project_id="prj-1",
        state=_state(rev=5), mapspec=_mapspec(),
    )
    _run(hp.assemble_gis_context_card("sess-h09", turn_id="t1", **kw))
    wc1 = wc_store.load("msn-wire0001", org_id="org-1")
    _run(hp.assemble_gis_context_card("sess-h09", turn_id="t2", **kw))
    wc2 = wc_store.load("msn-wire0001", org_id="org-1")
    assert int(wc2.revision) == int(wc1.revision)  # no phantom transitions
    assert wc2.facts == wc1.facts
    assert [f.digest for f in wc2.derived_findings] == \
        [f.digest for f in wc1.derived_findings]


def test_agent_side_map_advance_stales_mapspec_anchored_rows(
        wc_store, monkeypatch):
    """An agent mutation advances the MapSpec revision without any user
    semantic edit — the captured token drift itself must drive the walk
    (miss-stale is the dangerous direction)."""
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    monkeypatch.setattr(hp, "_fetch_reuse_candidates", lambda *a, **k: [])
    _seed_graph_row(wc_store)
    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-h09", org_id="org-1", project_id="prj-1", turn_id="t1",
        state=_state(rev=6), mapspec=_mapspec(),  # no provenance: agent path
    ))
    assert receipt.graph_staled == 1
    after = wc_store.load("msn-wire0001", org_id="org-1")
    df1 = after.derived_finding("df-1")
    assert df1.status == "stale"
    assert mapspec_token(after) == "6"
    assert "需重算" in _plain(text)


def test_reuse_recompute_defers_without_a_turn_fetch(wc_store, monkeypatch):
    """include_reuse=False turns must not fall back to a second retrieval
    on the event loop: the stale row stays stale, and the recurring refusal
    stays out of the persisted receipt ring."""
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    monkeypatch.setattr(hp, "_fetch_reuse_candidates", lambda *a, **k: [])
    wc = _seed_graph_row(wc_store)
    wc.upsert_derived_finding(DerivedFinding(
        finding_id="df-r", family="reuse", ref="reuse:project",
        label="项目复用判定", status="stale", generation=3, digest="old",
        priority=1, stale_reasons=["DATASET_VERSION_CHANGED:x"],
        depends_on=[DependencyEdge(dim="data", ref="ref:schools", anchor="rev-1")],
        basis_revision=3))
    wc_store.save(wc)

    text, receipt = _run(hp.assemble_gis_context_card(
        "sess-h09", org_id="org-1", project_id="prj-1", turn_id="t1",
        state=_state(rev=5), mapspec=_mapspec(), include_reuse=False,
    ))
    after = wc_store.load("msn-wire0001", org_id="org-1")
    row = after.derived_finding("df-r")
    assert row.status == "stale" and row.digest == "old"
    assert receipt.graph_recomputed == 0
    assert not [r for r in after.revalidations if r.kind == "FINDING_RECOMPUTED"]


def test_capture_resolves_mission_without_caller_state(wc_store, monkeypatch):
    """P0 regression: the finalizer passes neither state nor org — the
    capture entry must resolve the mission from the session's durable
    binding itself (and run its blocking body off the event loop)."""
    import types

    from app.services.gis_context import memory_graph as mg

    state = {"_mission_binding": {"mission_id": "msn-wire0001", "org_id": "org-1"},
             "_cartographic_mutation_revision": 5}

    async def fake_session_state(session_id):
        return state

    monkeypatch.setattr(mg, "_session_state", fake_session_state)
    monkeypatch.setattr(
        "app.services.gis_context.store.WorkingContextStore",
        lambda: wc_store)
    _seed_graph_row(wc_store)
    findings = [types.SimpleNamespace(
        code="label_collision", target="L1", severity="warning",
        detail="collision ratio 0.5")]
    recorded = _run(mg.record_critique_findings("sess-final", findings=findings))
    assert recorded == 1
    after = wc_store.load("msn-wire0001", org_id="org-1")
    row = next(f for f in after.derived_findings
               if f.ref == "label_collision:L1")
    assert row.status == "current" and row.detail == "collision ratio 0.5"
    assert ("mapspec", "", "5") in {(f.kind, f.ref, f.token) for f in after.facts}
    # Idempotent: the same derivation again records nothing.
    assert _run(mg.record_critique_findings("sess-final", findings=findings)) == 0


def test_capture_target_fallback_refuses_without_any_source(monkeypatch):
    """No binding, no turn context, no tenant → refuse (fail-closed: never
    wild-card a capture across tenants)."""
    import types

    from app.services.gis_context import memory_graph as mg

    def no_ctx(session_id, tenant_id=""):
        raise RuntimeError("no turn context")

    monkeypatch.setattr(
        "app.services.gis_harness.hotpath_convergence.session_ctx.get_turn_context",
        no_ctx)
    assert mg._resolve_capture_target("sess-x", None, org_id="") == ("", "")
    # And the tenant-scan fallback only fires when a mission exists.
    monkeypatch.setattr(
        "app.services.gis_harness.hotpath_convergence.session_ctx.get_turn_context",
        lambda session_id, tenant_id="": types.SimpleNamespace(mission_id="msn-1"))
    monkeypatch.setattr(hp, "_tenant_scan", lambda session_id: "org-9")
    assert mg._resolve_capture_target("sess-x", None, org_id="") == ("msn-1", "org-9")
