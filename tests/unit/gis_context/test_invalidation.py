"""M3 — Invalidation Engine: closed rule table + claim STALE propagation.

Invariant under test: old conclusions are never silently reusable after a
known basis drift; unknown-vs-unknown never invalidates; user edits are
never invalidated (user-wins).
"""
from __future__ import annotations

from app.services.gis_context.invalidation import apply_changes
from app.services.gis_context.observation import (
    ContextChange,
    SessionObservation,
    diff_against,
    observe_session,
)
from app.services.gis_context.working_context import (
    DecisionRecord,
    FindingRef,
    GISWorkingContext,
    WorkingBasis,
)


def _wc_with_finding(revision: int = 2) -> GISWorkingContext:
    wc = GISWorkingContext(
        mission_id="msn-inv",
        org_id="org-1",
        revision=revision,
        basis=WorkingBasis(
            aoi_bbox=[102.9, 30.5, 104.5, 31.5],
            time_period="2024",
            crs="EPSG:4326",
            measure_field="school_count",
            measure_statistic="sum",
        ),
        accepted_assumptions=[
            DecisionRecord(text="分布集中于主城区", turn_id="t1", basis_revision=1),
        ],
        findings=[FindingRef(claim_id="claim-1", status="supported", basis_revision=1)],
    )
    return wc


def _obs(**overrides) -> SessionObservation:
    obs = SessionObservation(
        aoi_bbox=[102.9, 30.5, 104.5, 31.5],
        time_period="2024",
        crs="EPSG:4326",
        measure_field="school_count",
        measure_statistic="sum",
    )
    for k, v in overrides.items():
        setattr(obs, k, v)
    return obs


def test_same_world_produces_no_changes():
    wc = _wc_with_finding()
    changes = diff_against(wc, _obs())
    assert changes == []
    outcome = apply_changes(wc, changes, obs=_obs(), turn_id="t2")
    assert not outcome.changed and wc.stale == {}
    assert wc.findings[0].status == "supported"


def test_first_observation_establishes_basis_without_invalidation():
    wc = _wc_with_finding()
    wc.basis.time_period = ""  # unknown before
    changes = diff_against(wc, _obs(time_period="2024"))
    # unknown → known is establishment: basis refresh, never invalidation
    assert [c.kind for c in changes] == ["BASIS_ESTABLISHED"]
    outcome = apply_changes(wc, changes, obs=_obs(time_period="2024"), turn_id="t2")
    assert outcome.changed and wc.stale == {}
    assert wc.basis.time_period == "2024"
    assert wc.findings[0].status == "supported"


def test_aoi_change_stales_spatial_conclusions():
    wc = _wc_with_finding()
    changes = diff_against(wc, _obs(aoi_bbox=[102.9, 30.5, 104.5, 31.8]))
    assert [c.kind for c in changes] == ["AOI_CHANGED"]
    outcome = apply_changes(wc, changes, obs=_obs(aoi_bbox=[102.9, 30.5, 104.5, 31.8]), turn_id="t2")
    assert outcome.changed and "basis.aoi" in outcome.stale_fields
    assert wc.findings[0].status == "stale"                 # claim marker on the ref
    assert wc.accepted_assumptions[0].stale_basis is True   # decision marked for re-check
    assert wc.basis.aoi_bbox == [102.9, 30.5, 104.5, 31.8]  # basis tracks the world


def test_dataset_version_change_invalidates_and_reports_ref():
    wc = _wc_with_finding()
    wc.basis.datasets = []
    from app.services.gis_context.working_context import BasisDataset

    wc.basis.datasets = [BasisDataset(ref_id="ref:schools", content_revision="rev-1")]
    obs = _obs()
    obs.datasets = [BasisDataset(ref_id="ref:schools", content_revision="rev-2")]
    changes = diff_against(wc, obs)
    kinds = [c.kind for c in changes]
    assert "DATASET_VERSION_CHANGED" in kinds
    assert changes[kinds.index("DATASET_VERSION_CHANGED")].ref_id == "ref:schools"
    outcome = apply_changes(wc, changes, obs=obs, turn_id="t2")
    assert "basis.datasets" in outcome.stale_fields
    assert outcome.stale_fields["basis.datasets"].startswith("DATASET_VERSION_CHANGED")
    assert wc.findings[0].status == "stale"


def test_unknown_dataset_revision_never_falsely_stales():
    """Datasets without authoritative revisions are not drift evidence."""
    wc = _wc_with_finding()
    from app.services.gis_context.working_context import BasisDataset

    wc.basis.datasets = [BasisDataset(ref_id="ref:schools", content_revision="rev-1")]
    obs = _obs()
    obs.datasets = [BasisDataset(ref_id="ref:schools", content_revision="")]
    assert diff_against(wc, obs) == []


def test_crs_change_stales_geometry_conclusions():
    wc = _wc_with_finding()
    changes = diff_against(wc, _obs(crs="EPSG:4490"))
    assert [c.kind for c in changes] == ["CRS_CHANGED"]
    outcome = apply_changes(wc, changes, obs=_obs(crs="EPSG:4490"), turn_id="t2")
    assert "basis.crs" in outcome.stale_fields
    assert wc.findings[0].status == "stale"


def test_measure_change_and_time_change_each_stale():
    wc = _wc_with_finding()
    outcome = apply_changes(
        wc, diff_against(wc, _obs(measure_statistic="mean")),
        obs=_obs(measure_statistic="mean"), turn_id="t2")
    assert "basis.measure" in outcome.stale_fields

    wc2 = _wc_with_finding()
    outcome2 = apply_changes(
        wc2, diff_against(wc2, _obs(time_period="2025")),
        obs=_obs(time_period="2025"), turn_id="t3")
    assert "basis.time_period" in outcome2.stale_fields
    assert wc2.findings[0].status == "stale"


def test_product_goal_change_marks_recipe_not_claims():
    wc = _wc_with_finding()
    wc.basis.recipe_id = ""
    outcome = apply_changes(
        wc, diff_against(wc, _obs(recipe_id="choropleth_v2")),
        obs=_obs(recipe_id="choropleth_v2"), turn_id="t2")
    assert outcome.changed
    assert "basis.recipe_id" not in outcome.stale_fields  # establishment
    # Establishment with empty→known never stales conclusions.
    assert wc.findings[0].status == "supported"

    wc2 = _wc_with_finding()
    wc2.basis.recipe_id = "choropleth_v1"
    outcome2 = apply_changes(
        wc2, diff_against(wc2, _obs(recipe_id="districts_stats")),
        obs=_obs(recipe_id="districts_stats"), turn_id="t3")
    assert "basis.recipe_id" in outcome2.stale_fields


def test_user_edits_recorded_and_never_staled():
    wc = _wc_with_finding()
    obs = _obs(user_hidden_layers=["layer-parks"])
    outcome = apply_changes(wc, [], obs=obs, turn_id="t2")
    assert outcome.recorded_edits == 1
    assert wc.user_edits[0].layer_id == "layer-parks"
    assert wc.user_edits[0].kind == "hide"
    assert wc.findings[0].status == "supported"  # user-wins: no invalidation
    # Idempotent per turn — the same hidden layer is not re-recorded.
    outcome2 = apply_changes(wc, [], obs=obs, turn_id="t3")
    assert outcome2.recorded_edits == 0


def test_claim_propagation_marks_store_stale():
    """The engine reuses the ClaimStore currency — no fifth verdict system."""
    from app.services.gis_harness.evidence_claim.contracts import (
        Claim,
        ClaimStatus,
        ClaimType,
    )
    from app.services.gis_harness.evidence_claim.store import ClaimStore

    store = ClaimStore()
    store.upsert_claim(Claim(
        claim_id="claim-1", claim_type=ClaimType.SUM,
        status=ClaimStatus.SUPPORTED, tenant_id="t", session_id="s",
    ))
    wc = _wc_with_finding()
    changes = [ContextChange(kind="AOI_CHANGED", detail="view_bounds_drift")]
    apply_changes(wc, changes, obs=_obs(), claim_store=store, turn_id="t2")
    assert store.get_claim("claim-1").status is ClaimStatus.STALE


def test_observe_session_projects_mapspec_and_provenance():
    state = {"_gis_provenance": {"user_hidden_layers": ["L-parks"]}}
    mapspec = {
        "view": {"bounds": [103.9, 30.6, 104.2, 30.8]},
        "crs": "EPSG:4326",
        "sources": {
            "schools": {"ref_id": "ref:schools", "content_revision": "rev-3"},
        },
        "layers": [
            {"id": "L-schools", "type": "choropleth", "metric": "school_count",
             "statistic": "sum"},
            {"id": "L-parks", "type": "fill"},
        ],
    }
    obs = observe_session(state, mapspec)
    assert obs.aoi_bbox == [103.9, 30.6, 104.2, 30.8]
    assert obs.crs == "EPSG:4326"
    assert [d.ref_id for d in obs.datasets] == ["ref:schools"]
    assert obs.measure_field == "school_count"
    assert obs.user_hidden_layers == ["L-parks"]

    empty = observe_session({}, {})
    assert empty.aoi_bbox is None and empty.datasets == []
