"""F05 / ADR-0215 — End-to-end revalidation & safe-reuse scenarios.

Scenario coverage (direction DoD):
- invalidation → re-verify → current closed loop across real turns;
- data version change / CRS change block reuse and stale conclusions;
- style-only changes and no-change turns are non-events (no writes);
- ``GIS_CONTEXT_REVALIDATION=0`` restores post-#1487 behavior;
- cross-session continuation: explicit bind, scope guards, stale facts
  filtered, terminal mission refused.
"""
from __future__ import annotations

import asyncio

from app.services.gis_context import hotpath as hp
from app.services.gis_context.working_context import (
    DecisionRecord,
    FindingRef,
    GISWorkingContext,
)
from app.services.gis_harness.evidence_claim.contracts import (
    Claim,
    ClaimType,
    EvidenceFreshness,
    EvidenceKind,
    EvidenceNode,
)
from app.services.gis_harness.evidence_claim.store import ClaimStore
from app.services.gis_harness.hotpath_convergence.session_ctx import (
    get_or_create_claim_store,
    reset_turn_context,
)


def _plain(text: str) -> str:
    return text.replace("<untrusted_gis_context>", "").replace(
        "</untrusted_gis_context>", "")


def _mapspec(aoi_shift: float = 0.0):
    return {
        "view": {"center": [104.06 + aoi_shift, 30.57], "zoom": 10.0},
        "layers": [
            {"id": "L1", "type": "fill",
             "legend_spec": {"field": "school_count", "statistic": "sum"}},
        ],
        "sources": {"schools": {"ref_id": "ref:schools", "content_revision": "rev-1"}},
    }


def _run(coro):
    return asyncio.run(coro)


def _supported_claim(store: ClaimStore, claim_id: str = "claim-1") -> None:
    store.upsert_evidence(EvidenceNode(
        evidence_id="ev-1", kind=EvidenceKind.STATISTIC, ref="ref:schools",
        freshness=EvidenceFreshness.FRESH,
        metadata={"stat_type": "count", "unit": "个", "value": 42},
    ))
    store.upsert_claim(Claim(
        claim_id=claim_id, claim_type=ClaimType.COUNT, subject="schools",
        value=42.0, unit="个", method="capability:count_points",
        supporting_evidence_refs=["ev-1"],
    ))


def _wc_with_finding() -> GISWorkingContext:
    return GISWorkingContext(
        mission_id="msn-scen",
        org_id="org-1",
        project_id="prj-1",
        user_id="u-1",
        revision=2,
        findings=[FindingRef(claim_id="claim-1", status="supported", basis_revision=1)],
        accepted_assumptions=[DecisionRecord(
            text="分布集中于主城区", turn_id="t0", basis_revision=1)],
    )


def test_closed_loop_through_hotpath(wc_store, monkeypatch):
    """Turn 1 establishes the basis; turn 2's AOI drift stales the finding
    (card filters it); the revalidation tool restores it on the current
    basis; turn 3 renders it again."""
    monkeypatch.setattr(hp, "_store", lambda: wc_store)

    # Seed the context with a verified finding (as a prior turn would have).
    wc = _wc_with_finding()
    wc_store.save(wc)

    state = {"_mission_binding": {"mission_id": wc.mission_id, "org_id": "org-1"}}

    # Turn 1 — same world: no changes, finding renders.
    text1, rc1 = _run(hp.assemble_gis_context_card(
        "sess-1", org_id="org-1", project_id="prj-1",
        state=state, mapspec=_mapspec()))
    assert "已核实" in _plain(text1)
    assert rc1.stale_fields == 0

    # Turn 2 — AOI drifts: finding + assumption staled (attributed), card
    # filters the stale finding and surfaces the failure reason.
    text2, rc2 = _run(hp.assemble_gis_context_card(
        "sess-1", org_id="org-1", project_id="prj-1",
        state=state, mapspec=_mapspec(aoi_shift=0.35)))
    assert rc2.stale_fields >= 1
    assert "AOI_CHANGED" in rc2.stale_reason_kinds
    assert "已核实" not in _plain(text2)
    stored = wc_store.load(wc.mission_id, org_id="org-1")
    assert stored.findings[0].status == "stale"
    assert "basis.aoi" in stored.findings[0].stale_reasons

    # Active re-verification (tool path) — claim re-verified on the current
    # basis via the deterministic verifier.
    reset_turn_context()
    store = get_or_create_claim_store("sess-1", tenant_id="org-1")
    _supported_claim(store)
    _run(hp._persist_binding("sess-1", wc.mission_id, "org-1"))
    summary = _run(hp.request_revalidation(
        "sess-1", claim_ids=["claim-1"], org_id="org-1", project_id="prj-1",
        turn_id="t-rtv"))
    assert summary["ok"] is True
    assert summary["restored"] == 1
    receipt = summary["receipts"][0]
    assert receipt["verdict"] == "restored"
    assert receipt["basis_revision"] >= stored.revision

    persisted = wc_store.load(wc.mission_id, org_id="org-1")
    assert persisted.findings[0].status == "supported"
    assert persisted.findings[0].basis_revision == persisted.revision

    # Turn 3 — same (drifted) world: the reaffirmed + re-verified context
    # reconfirms its basis marker passively; the restored finding renders.
    _run(hp.request_revalidation(
        "sess-1", reaffirm_texts=["分布集中于主城区"],
        org_id="org-1", project_id="prj-1", turn_id="t-reaffirm"))
    text3, rc3 = _run(hp.assemble_gis_context_card(
        "sess-1", org_id="org-1", project_id="prj-1",
        state=state, mapspec=_mapspec(aoi_shift=0.35)))
    persisted3 = wc_store.load(wc.mission_id, org_id="org-1")
    assert "basis.aoi" not in persisted3.stale
    assert rc3.rtv_restored >= 1
    assert "已核实" in _plain(text3)


def test_authority_drift_through_hotpath(wc_store, monkeypatch):
    """Authority-side dataset drift (reconcile event) invalidates even when
    the MapSpec content_revision is unchanged."""
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    wc = _wc_with_finding()
    wc_store.save(wc)
    state = {"_mission_binding": {"mission_id": wc.mission_id, "org_id": "org-1"}}

    def drifting_reconcile(_wc, *, project_id):
        return (
            [{"kind": "DATASET_VERSION_CHANGED",
              "detail": "ref:schools:authority_drift", "ref_id": "ref:schools"}],
            True,
            {"ref:schools": "sha-new"},
        )

    monkeypatch.setattr(hp, "_reconcile_step", drifting_reconcile)
    text, rc = _run(hp.assemble_gis_context_card(
        "sess-2", org_id="org-1", project_id="prj-1",
        state=state, mapspec=_mapspec()))
    assert "DATASET_VERSION_CHANGED" in rc.stale_reason_kinds
    stored = wc_store.load(wc.mission_id, org_id="org-1")
    assert stored.findings[0].status == "stale"


def test_style_only_change_is_not_invalidation(wc_store, monkeypatch):
    """Export-format change (style plane) never stales conclusions; once the
    world is fully learned, a style-only turn performs no save at all
    (read-mostly discipline)."""
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    wc = _wc_with_finding()
    wc.basis.time_period = "2024"
    wc.basis.crs = "EPSG:4326"
    wc_store.save(wc)
    state = {"_mission_binding": {"mission_id": wc.mission_id, "org_id": "org-1"}}

    # Turn 1 — establish the basis (learning may bump; nothing stales).
    _run(hp.assemble_gis_context_card(
        "sess-3", org_id="org-1", project_id="prj-1",
        state=state, mapspec=_mapspec()))
    disk = wc_store.load(wc.mission_id, org_id="org-1")
    assert disk.stale == {}

    # Turn 2 — style-only change: no invalidation, no write.
    state2 = dict(state)
    state2["_export_target"] = {"format": "png"}
    _run(hp.assemble_gis_context_card(
        "sess-3", org_id="org-1", project_id="prj-1",
        state=state2, mapspec=_mapspec()))
    after_export = wc_store.load(wc.mission_id, org_id="org-1")
    assert after_export.stale == {}
    assert after_export.findings[0].status == "supported"

    # Turn 3 — fully learned world, no change: revision untouched.
    _run(hp.assemble_gis_context_card(
        "sess-3", org_id="org-1", project_id="prj-1",
        state=state2, mapspec=_mapspec()))
    after = wc_store.load(wc.mission_id, org_id="org-1")
    assert after.revision == after_export.revision
    assert after.findings[0].status == "supported"


def test_revalidation_flag_off_restores_prior_behavior(wc_store, monkeypatch):
    """GIS_CONTEXT_REVALIDATION=0: no passive reconfirm, no fingerprint
    reconciliation — the post-#1487 one-way semantics (drift stales; the
    hot path never restores or reconciles). The explicit tool path stays
    available: it is user-driven, evidence-checked work."""
    monkeypatch.setenv("GIS_CONTEXT_REVALIDATION", "0")
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    wc = _wc_with_finding()
    wc.basis.time_period = "2024"
    wc_store.save(wc)
    state = {"_mission_binding": {"mission_id": wc.mission_id, "org_id": "org-1"}}

    # Turn 1 — establish basis; Turn 2 — AOI drift stales the finding.
    _run(hp.assemble_gis_context_card(
        "sess-4", org_id="org-1", project_id="prj-1",
        state=state, mapspec=_mapspec()))
    text, rc = _run(hp.assemble_gis_context_card(
        "sess-4", org_id="org-1", project_id="prj-1",
        state=state, mapspec=_mapspec(aoi_shift=0.35)))
    assert "AOI_CHANGED" in rc.stale_reason_kinds
    stored = wc_store.load(wc.mission_id, org_id="org-1")
    assert stored.findings[0].status == "stale"
    revision_after_drift = stored.revision

    def must_not_run(_wc, *, project_id):
        raise AssertionError("reconcile must not run when flag is off")

    monkeypatch.setattr(hp, "_reconcile_step", must_not_run)

    # Passive hot-path turns never restore the marker (flag off) and never
    # reconcile fingerprints.
    _run(hp.assemble_gis_context_card(
        "sess-4", org_id="org-1", project_id="prj-1",
        state=state, mapspec=_mapspec(aoi_shift=0.35)))
    still = wc_store.load(wc.mission_id, org_id="org-1")
    assert still.findings[0].status == "stale"
    assert still.revision == revision_after_drift

    # Active tool path remains available (explicit, evidence-checked).
    reset_turn_context()
    store = get_or_create_claim_store("sess-4", tenant_id="org-1")
    _supported_claim(store)
    _run(hp._persist_binding("sess-4", wc.mission_id, "org-1"))
    summary = _run(hp.request_revalidation(
        "sess-4", claim_ids=["claim-1"], org_id="org-1", project_id="prj-1"))
    assert summary["ok"] is True
    assert summary["restored"] == 1


def test_cross_session_continuation(wc_store, monkeypatch):
    """Session B binds to session A's mission: scope guards hold, stale
    findings never render as current, terminal missions are refused."""
    from app.services.session_data import session_data_manager

    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    wc = _wc_with_finding()
    wc_store.save(wc)

    # Org mismatch → invisible (no existence leak).
    ok, reason = _run(hp.bind_session_mission(
        "sess-b", wc.mission_id, org_id="org-9", project_id="prj-1"))
    assert ok is False and reason == "no_context"

    # Project mismatch → refused.
    ok, reason = _run(hp.bind_session_mission(
        "sess-b", wc.mission_id, org_id="org-1", project_id="prj-other"))
    assert ok is False and reason == "scope_mismatch"

    # Correct scope → bound durably for session B.
    ok, reason = _run(hp.bind_session_mission(
        "sess-b", wc.mission_id, org_id="org-1", project_id="prj-1"))
    assert ok is True and reason == ""
    durable = _run(session_data_manager.get_map_state("sess-b"))
    assert durable[hp.MISSION_BINDING_KEY]["mission_id"] == wc.mission_id

    # Session B's first turn renders the shared context (stale facts
    # filtered) — same world, nothing stale yet.
    text, rc = _run(hp.assemble_gis_context_card(
        "sess-b", org_id="org-1", project_id="prj-1",
        state=durable, mapspec=_mapspec()))
    assert text
    assert rc.stale_fields == 0

    # AOI drift (from session B's own viewport) → the shared finding is
    # stale for BOTH sessions; B's card filters it.
    text2, rc2 = _run(hp.assemble_gis_context_card(
        "sess-b", org_id="org-1", project_id="prj-1",
        state=durable, mapspec=_mapspec(aoi_shift=0.35)))
    assert rc2.stale_fields >= 1
    assert "已核实" not in _plain(text2)


def test_terminal_mission_refused_and_purged(wc_store, mission_runtime, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    monkeypatch.setattr(
        "app.services.mission_runtime.service.get_mission_runtime",
        lambda store=None: mission_runtime,
    )
    rec = mission_runtime.create(
        org_id="org-1", user_id="u-1", project_id="prj-1",
        root_goal="g", session_id="sess-t")
    mission_runtime.start(rec.mission_id, worker_id="w-1", org_id="org-1")
    wc = _wc_with_finding()
    wc.mission_id = rec.mission_id
    wc_store.save(wc)
    mission_runtime.complete(rec.mission_id, worker_id="w-1", org_id="org-1")

    ok, reason = _run(hp.bind_session_mission(
        "sess-new", rec.mission_id, org_id="org-1", project_id="prj-1"))
    assert ok is False and reason == "mission_terminal"
    assert wc_store.load(rec.mission_id, org_id="org-1") is None  # purged
