"""Spatial Evidence / Claim / Provenance Graph tests (Direction 03, GOAL §21).

Offline, zero LLM, deterministic fixtures. Covers evidence liveness, claims,
semantics, map binding, product linkage, narrative, invalidation, determinism,
and bounded traversal performance.
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


from app.services.gis_harness.evidence_claim import (
    Claim,
    ClaimNarrativeProjection,
    ClaimStatus,
    ClaimStore,
    ClaimType,
    EvidenceFreshness,
    EvidenceKind,
    EvidenceNode,
    RelationEdge,
    RelationGraph,
    RelationType,
    Scope,
    affected_claims,
    answer_why_highest_density,
    bind_layer_to_statistic,
    claim_from_rank_table,
    claim_from_statistic,
    claims_for_artifact,
    contradictions,
    detect_contradictions,
    evidence_for,
    explain_map_feature,
    grounding_projection,
    invalidate_affected_claims,
    invalidate_binding_on_break_change,
    mark_evidence_superseded,
    narrative_claim_unverified,
    project_artifact_record,
    project_dataset_version,
    project_goal_evidence,
    project_product_facet,
    resolve_live_ref,
    verify_claim,
    why_claim,
    why_map_feature,
)


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
    tenant_id: str = ""


def _wuhou_scenario(store: Optional[ClaimStore] = None) -> ClaimStore:
    """武侯区学校密度最高 — end-to-end fixture."""
    store = store or ClaimStore()
    dsv = project_dataset_version(
        dataset_key="schools_chengdu",
        version_token="v12",
        content_fingerprint="fp_schools_v12",
        tenant_id="t1",
        session_id="s1",
        store=store,
    )
    art = FakeArtifact(
        artifact_id="ref:stats-schools-by-district",
        metadata={
            "stat_type": "density",
            "unit": "per_km2",
            "method": "admin_density",
            "value": 4.7,
            "subject": "武侯区",
        },
    )
    stat = project_artifact_record(art, tenant_id="t1", session_id="s1")
    stat = stat.model_copy(update={
        "kind": EvidenceKind.STATISTIC,
        "method": "admin_density",
        "freshness": EvidenceFreshness.FRESH,
        "metadata": {
            **stat.metadata,
            "stat_type": "density",
            "unit": "per_km2",
            "value": 4.7,
            "subject": "武侯区",
        },
        "scope": Scope(group_by="district", spatial_level="district", temporal_label="2024"),
    })
    store.upsert_evidence(stat)
    store.add_edge(RelationEdge(
        edge_id="e-stat-ds",
        relation=RelationType.DERIVED_FROM,
        src=stat.evidence_id,
        dst=dsv.evidence_id,
    ))
    unc = EvidenceNode(
        evidence_id="unc:schools-density",
        kind=EvidenceKind.UNCERTAINTY,
        ref="unc:schools-density",
        freshness=EvidenceFreshness.FRESH,
        tenant_id="t1",
        session_id="s1",
        method="bootstrap_ci",
    )
    store.upsert_evidence(unc)

    rows = [
        {"name": "锦江区", "value": 3.1},
        {"name": "武侯区", "value": 4.7},
        {"name": "青羊区", "value": 3.9},
    ]
    claim = claim_from_rank_table(
        rows,
        claim_type=ClaimType.DENSITY,
        unit="per_km2",
        method="admin_density",
        statistic_evidence_id=stat.evidence_id,
        dataset_version_evidence_id=dsv.evidence_id,
        temporal_label="2024",
        tenant_id="t1",
        session_id="s1",
        store=store,
    )
    assert claim is not None
    store.upsert_claim(claim.model_copy(update={"uncertainty_ref": unc.evidence_id}))
    bind_layer_to_statistic(
        layer_id="schools-density-choropleth",
        style_class="red",
        classification_breaks=[0, 2, 3.5, 5],
        metric_field="school_density",
        metric_value=4.7,
        statistic_evidence_id=stat.evidence_id,
        claim_id=claim.claim_id,
        dataset_version_ref=dsv.evidence_id,
        mapspec_revision="7",
        source_ref=art.artifact_id,
        tenant_id="t1",
        session_id="s1",
        store=store,
    )
    store.upsert_claim(store.get_claim(claim.claim_id).model_copy(update={
        "map_layer_id": "schools-density-choropleth",
        "uncertainty_ref": unc.evidence_id,
    }))
    return store


# ── Evidence ────────────────────────────────────────────────────────────

def test_live_ref_resolves():
    rec = FakeArtifact(artifact_id="ref:geojson-a", status="valid")
    out = resolve_live_ref("ref:geojson-a", records={"ref:geojson-a": rec})
    assert out["liveness"] == "valid"
    assert out["freshness"] == "fresh"


def test_missing_ref():
    out = resolve_live_ref("ref:missing", records={}, descriptors={})
    assert out["liveness"] == "missing"
    assert out["freshness"] == "missing"


def test_superseded_ref():
    rec = FakeArtifact(artifact_id="ref:old", status="superseded")
    out = resolve_live_ref("ref:old", records={"ref:old": rec})
    assert out["freshness"] == "superseded"


def test_stale_version_projection():
    store = ClaimStore()
    node = project_artifact_record(
        FakeArtifact(artifact_id="ref:x", status="stale"),
        tenant_id="t1",
    )
    store.upsert_evidence(node)
    assert store.get_evidence(node.evidence_id).freshness is EvidenceFreshness.STALE


def test_cross_tenant_ref_rejected():
    FakeArtifact(artifact_id="ref:a", status="valid", tenant_id="other")
    # attach tenant via dict-like
    class R:
        status = "valid"
        tenant_id = "tenant-B"
    out = resolve_live_ref(
        "ref:a",
        records={"ref:a": R()},
        expected_tenant_id="tenant-A",
    )
    assert out["liveness"] == "rejected_cross_tenant"


# ── Claims ──────────────────────────────────────────────────────────────

def test_supported_numeric_claim():
    store = _wuhou_scenario()
    claim = next(c for c in store.all_claims() if c.comparator == "highest")
    result = verify_claim(claim, store, require_uncertainty=True)
    assert result.status is ClaimStatus.SUPPORTED
    assert result.positive_proof is True


def test_unsupported_claim_missing_evidence():
    store = ClaimStore()
    claim = claim_from_statistic(
        subject="武侯区",
        claim_type=ClaimType.DENSITY,
        value=4.7,
        unit="per_km2",
        comparator="highest",
        method="admin_density",
        statistic_evidence_id="stat:missing",
        store=store,
    )
    result = verify_claim(claim, store)
    assert result.status is ClaimStatus.UNSUPPORTED
    assert result.positive_proof is False


def test_partial_evidence():
    store = ClaimStore()
    dsv = project_dataset_version(
        dataset_key="schools", version_token="v1", store=store, tenant_id="t1",
    )
    # only dataset present; statistic ref missing
    claim = claim_from_statistic(
        subject="武侯区",
        claim_type=ClaimType.DENSITY,
        value=4.7,
        method="admin_density",
        statistic_evidence_id="stat:gone",
        dataset_version_evidence_id=dsv.evidence_id,
        store=store,
    )
    result = verify_claim(claim, store)
    assert result.status in (ClaimStatus.PARTIALLY_SUPPORTED, ClaimStatus.UNSUPPORTED)
    assert result.positive_proof is False
    assert "partial_evidence" in result.reasons or "missing" in str(result.reasons)


def test_contradiction_hard():
    store = ClaimStore()
    dsv = project_dataset_version(dataset_key="s", version_token="v1", store=store)
    stat = EvidenceNode(
        evidence_id="stat:1", kind=EvidenceKind.STATISTIC, ref="ref:stats",
        freshness=EvidenceFreshness.FRESH, method="admin_density",
        metadata={"stat_type": "density", "unit": "per_km2"},
    )
    store.upsert_evidence(stat)
    c1 = claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.DENSITY, value=4.7,
        unit="per_km2", comparator="highest", method="admin_density",
        statistic_evidence_id="stat:1", dataset_version_evidence_id=dsv.evidence_id,
        spatial_scope=Scope(spatial_level="district", group_by="district"),
        temporal_scope=Scope(temporal_label="2024"),
        store=store,
    )
    c2 = claim_from_statistic(
        subject="锦江区", claim_type=ClaimType.DENSITY, value=5.0,
        unit="per_km2", comparator="highest", method="admin_density",
        statistic_evidence_id="stat:1", dataset_version_evidence_id=dsv.evidence_id,
        spatial_scope=Scope(spatial_level="district", group_by="district"),
        temporal_scope=Scope(temporal_label="2024"),
        store=store,
    )
    results = detect_contradictions(store)
    assert any(r.kind == "hard" for r in results)
    assert store.get_claim(c1.claim_id).status is ClaimStatus.CONTRADICTED
    assert store.get_claim(c2.claim_id).status is ClaimStatus.CONTRADICTED


def test_scoped_divergence_not_hard_contradiction():
    store = ClaimStore()
    stat = EvidenceNode(
        evidence_id="stat:1", kind=EvidenceKind.STATISTIC, ref="ref:stats",
        freshness=EvidenceFreshness.FRESH, method="admin_density",
        metadata={"stat_type": "density"},
    )
    store.upsert_evidence(stat)
    claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.DENSITY, value=4.7,
        comparator="highest", method="admin_density",
        statistic_evidence_id="stat:1",
        temporal_scope=Scope(temporal_label="2023"),
        store=store,
    )
    claim_from_statistic(
        subject="锦江区", claim_type=ClaimType.DENSITY, value=5.0,
        comparator="highest", method="admin_density",
        statistic_evidence_id="stat:1",
        temporal_scope=Scope(temporal_label="2024"),
        store=store,
    )
    results = detect_contradictions(store)
    assert results
    assert all(r.kind == "scoped_divergence" for r in results)


def test_stale_claim_after_invalidation():
    store = _wuhou_scenario()
    claim = next(c for c in store.all_claims() if c.comparator == "highest")
    dsv = next(e for e in store.all_evidence() if e.kind is EvidenceKind.DATASET_VERSION)
    report = invalidate_affected_claims(store, dsv.evidence_id)
    assert claim.claim_id in report["stale_claims"]
    assert store.get_claim(claim.claim_id).status is ClaimStatus.STALE


# ── Semantics ───────────────────────────────────────────────────────────

def test_count_vs_density_incompatible():
    store = ClaimStore()
    stat = EvidenceNode(
        evidence_id="stat:count", kind=EvidenceKind.STATISTIC, ref="ref:c",
        freshness=EvidenceFreshness.FRESH, method="count",
        metadata={"stat_type": "count", "unit": "count"},
    )
    store.upsert_evidence(stat)
    claim = claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.DENSITY, value=4.7,
        unit="per_km2", method="admin_density",
        statistic_evidence_id="stat:count", store=store,
    )
    result = verify_claim(claim, store)
    assert result.positive_proof is False
    assert any("incompatible_semantics" in r for r in result.reasons)


def test_percentage_vs_raw_count():
    store = ClaimStore()
    stat = EvidenceNode(
        evidence_id="stat:pct", kind=EvidenceKind.STATISTIC, ref="ref:p",
        freshness=EvidenceFreshness.FRESH, method="share",
        metadata={"stat_type": "percentage", "unit": "%"},
    )
    store.upsert_evidence(stat)
    claim = claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.COUNT, value=120,
        unit="count", method="count",
        statistic_evidence_id="stat:pct", store=store,
    )
    result = verify_claim(claim, store)
    assert result.positive_proof is False


def test_mismatched_temporal_scope():
    store = ClaimStore()
    stat = EvidenceNode(
        evidence_id="stat:1", kind=EvidenceKind.STATISTIC, ref="ref:s",
        freshness=EvidenceFreshness.FRESH, method="admin_density",
        metadata={"stat_type": "density", "temporal_label": "2020"},
        scope=Scope(temporal_label="2020"),
    )
    store.upsert_evidence(stat)
    claim = claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.DENSITY, value=4.7,
        method="admin_density", statistic_evidence_id="stat:1",
        temporal_scope=Scope(temporal_label="2024"), store=store,
    )
    result = verify_claim(claim, store)
    assert result.positive_proof is False
    assert any("temporal_mismatch" in r for r in result.reasons)


def test_mismatched_aoi():
    store = ClaimStore()
    stat = EvidenceNode(
        evidence_id="stat:1", kind=EvidenceKind.STATISTIC, ref="ref:s",
        freshness=EvidenceFreshness.FRESH, method="admin_density",
        metadata={"stat_type": "density", "aoi_ref": "aoi:jinjiang"},
        scope=Scope(aoi_ref="aoi:jinjiang"),
    )
    store.upsert_evidence(stat)
    claim = claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.DENSITY, value=4.7,
        method="admin_density", statistic_evidence_id="stat:1",
        spatial_scope=Scope(aoi_ref="aoi:wuhou"), store=store,
    )
    result = verify_claim(claim, store)
    assert any("aoi_mismatch" in r for r in result.reasons)


def test_mismatched_unit():
    store = ClaimStore()
    stat = EvidenceNode(
        evidence_id="stat:1", kind=EvidenceKind.STATISTIC, ref="ref:s",
        freshness=EvidenceFreshness.FRESH, method="admin_density",
        metadata={"stat_type": "density", "unit": "per_mi2"},
    )
    store.upsert_evidence(stat)
    claim = claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.DENSITY, value=4.7,
        unit="per_km2", method="admin_density",
        statistic_evidence_id="stat:1", store=store,
    )
    result = verify_claim(claim, store)
    assert any("unit_mismatch" in r for r in result.reasons)


# ── Map ─────────────────────────────────────────────────────────────────

def test_map_style_traces_to_statistic():
    store = _wuhou_scenario()
    claim = next(c for c in store.all_claims() if c.comparator == "highest")
    binding = bind_layer_to_statistic(
        layer_id="schools-density-choropleth",
        style_class="red",
        classification_breaks=[0, 2, 3.5, 5],
        metric_field="school_density",
        metric_value=4.7,
        statistic_evidence_id="art:ref:stats-schools-by-district",
        claim_id=claim.claim_id,
        dataset_version_ref=next(
            e.evidence_id for e in store.all_evidence() if e.kind is EvidenceKind.DATASET_VERSION
        ),
        mapspec_revision="7",
    )
    explained = explain_map_feature(binding)
    assert explained["chain"][0]["step"] == "rendered_layer"
    assert explained["chain"][4]["step"] == "statistic"
    assert explained["stale"] is False
    full = why_map_feature(store, binding, current_mapspec_revision="7")
    assert full["map_explanation"]["stale"] is False


def test_changed_breaks_invalidate_styling_explanation():
    binding = bind_layer_to_statistic(
        layer_id="L1", style_class="red", classification_breaks=[0, 1, 2],
        metric_field="d", metric_value=1.5, statistic_evidence_id="stat:1",
        mapspec_revision="1",
    )
    explained = explain_map_feature(binding, current_breaks=[0, 1, 3])
    assert explained["stale"] is True
    assert "classification_breaks_changed" in explained["reasons"]
    updated = invalidate_binding_on_break_change(binding, [0, 1, 3])
    assert updated.stale is True


# ── Product ─────────────────────────────────────────────────────────────

def test_product_facet_links_to_claims():
    store = _wuhou_scenario()
    claim = next(c for c in store.all_claims() if c.comparator == "highest")
    facet = project_product_facet(
        {"node_id": "statistics:admin", "kind": "statistics", "label": "各区密度",
         "artifact_ref": "ref:stats-schools-by-district"},
        lineage_refs=[{"ref": "ref:stats-schools-by-district"}],
        tenant_id="t1", session_id="s1", store=store,
    )
    store.upsert_claim(claim.model_copy(update={"product_facet_id": "statistics:admin"}))
    linked = claims_for_artifact(store, "ref:stats-schools-by-district")
    assert any(c["claim_id"] == claim.claim_id for c in linked["claims"])
    assert store.get_evidence(facet.evidence_id) is not None


def test_dead_artifact_marks_dependent_claim_stale():
    store = _wuhou_scenario()
    claim = next(c for c in store.all_claims() if c.comparator == "highest")
    # mark statistic expired then re-verify with records snapshot
    stat = next(e for e in store.all_evidence() if e.kind is EvidenceKind.STATISTIC)
    store.upsert_evidence(stat.model_copy(update={"freshness": EvidenceFreshness.EXPIRED}))
    records = {stat.ref: FakeArtifact(artifact_id=stat.ref, status="expired")}
    result = verify_claim(store.get_claim(claim.claim_id), store, records=records)
    assert result.positive_proof is False
    assert result.status in (ClaimStatus.UNSUPPORTED, ClaimStatus.STALE, ClaimStatus.PARTIALLY_SUPPORTED)


# ── Narrative ───────────────────────────────────────────────────────────

def test_narrative_can_reference_claim():
    store = _wuhou_scenario()
    claim = next(c for c in store.all_claims() if c.comparator == "highest")
    verify_claim(claim, store, require_uncertainty=True)
    proj = ClaimNarrativeProjection(store)
    binding = proj.sentence_binding(claim.claim_id, "武侯区学校密度最高。")
    assert binding["authoritative"] is True
    assert binding["value"] == 4.7
    pack = proj.storymap_trace_input([claim.claim_id])
    assert pack["schema"] == "claim_narrative_projection.v1"
    assert pack["claims"]


def test_free_text_claim_cannot_become_verified():
    store = ClaimStore()
    claim = narrative_claim_unverified("教育资源最好", tenant_id="t1", store=store)
    result = verify_claim(claim, store)
    assert result.status is ClaimStatus.UNSUPPORTED
    assert result.positive_proof is False


# ── Invalidation ────────────────────────────────────────────────────────

def test_dataset_version_update_affected_descendants():
    store = _wuhou_scenario()
    old = next(e for e in store.all_evidence() if e.kind is EvidenceKind.DATASET_VERSION)
    new = project_dataset_version(
        dataset_key="schools_chengdu", version_token="v13",
        content_fingerprint="fp_v13", tenant_id="t1", session_id="s1", store=store,
    )
    mark_evidence_superseded(store, old.evidence_id, new.evidence_id)
    report = affected_claims(store, old.evidence_id)
    assert report["affected_claims"]
    inv = invalidate_affected_claims(store, old.evidence_id)
    assert inv["stale_claims"]


# ── Determinism ─────────────────────────────────────────────────────────

def test_determinism_same_graph_same_verdict():
    s1 = _wuhou_scenario()
    s2 = _wuhou_scenario()
    c1 = next(c for c in s1.all_claims() if c.comparator == "highest")
    c2 = next(c for c in s2.all_claims() if c.comparator == "highest")
    assert c1.claim_id == c2.claim_id
    r1 = verify_claim(c1, s1, require_uncertainty=True)
    r2 = verify_claim(c2, s2, require_uncertainty=True)
    assert r1.to_bounded_dict() == r2.to_bounded_dict()


# ── Success criterion / grounding ───────────────────────────────────────

def test_why_wuhou_highest_school_density():
    store = _wuhou_scenario()
    claim = next(c for c in store.all_claims() if c.subject == "武侯区")
    verify_claim(claim, store, require_uncertainty=True)
    ans = answer_why_highest_density(store, subject_hint="武侯")
    g = ans["grounding"]
    assert g["status"] == "supported"
    assert g["claim"]["value"] == 4.7
    kinds = {e["kind"] for e in g["evidence"]}
    assert "statistic" in kinds
    assert "dataset_version" in kinds
    assert g["uncertainty"] is not None
    chain = why_claim(store, claim.claim_id)
    assert chain["verification"]["positive_proof"] is True


def test_grounding_never_pass_on_missing():
    store = ClaimStore()
    claim = claim_from_statistic(
        subject="X", claim_type=ClaimType.COUNT, value=1,
        method="count", statistic_evidence_id="missing", store=store,
    )
    g = grounding_projection(store, claim.claim_id)
    assert g["status"] != "supported"
    assert g["positive_proof"] is False


def test_goal_evidence_projector():
    store = ClaimStore()
    node = project_goal_evidence(
        {"id": "row:admin_aggregation", "kind": "tool_receipt", "status": "present",
         "revision": "ref:stats-1", "source": "chapter", "detail": "ok"},
        store=store,
    )
    assert node.freshness is EvidenceFreshness.FRESH


# ── Performance / bounds (GOAL §22) ─────────────────────────────────────

def test_bounded_traversal_cycle_safe():
    store = ClaimStore()
    for i in range(5):
        store.upsert_evidence(EvidenceNode(
            evidence_id=f"n{i}", kind=EvidenceKind.ANALYSIS, ref=f"r{i}",
        ))
    store.add_edge(RelationEdge(edge_id="e01", relation=RelationType.DERIVED_FROM, src="n0", dst="n1"))
    store.add_edge(RelationEdge(edge_id="e12", relation=RelationType.DERIVED_FROM, src="n1", dst="n2"))
    store.add_edge(RelationEdge(edge_id="e20", relation=RelationType.DERIVED_FROM, src="n2", dst="n0"))  # cycle
    g = RelationGraph(store)
    trav = g.traverse("n0", max_depth=10, max_nodes=10)
    assert trav.cycle_detected is True
    assert len(trav.nodes) <= 10


def test_perf_1k_nodes_10k_edges():
    store = ClaimStore(max_evidence=2000, max_claims=2000, max_edges=12000)
    for i in range(1000):
        store.upsert_evidence(EvidenceNode(
            evidence_id=f"e{i}", kind=EvidenceKind.ARTIFACT, ref=f"ref:{i}",
            freshness=EvidenceFreshness.FRESH,
        ))
    for i in range(10000):
        store.add_edge(RelationEdge(
            edge_id=f"edge{i}",
            relation=RelationType.DERIVED_FROM,
            src=f"e{i % 1000}",
            dst=f"e{(i + 1) % 1000}",
        ))
    g = RelationGraph(store)
    trav = g.traverse("e0", max_depth=6, max_nodes=256)
    assert len(trav.nodes) <= 256
    assert store.stats()["edges"] == 10000


def test_store_roundtrip():
    store = _wuhou_scenario()
    data = store.to_dict()
    restored = ClaimStore.from_dict(data)
    assert restored.stats()["claims"] == store.stats()["claims"]
    assert restored.stats()["evidence"] == store.stats()["evidence"]


def test_verify_rejects_cross_tenant_claim():
    store = _wuhou_scenario()
    claim = next(c for c in store.all_claims() if c.comparator == "highest")
    result = verify_claim(claim, store, expected_tenant_id="other-tenant")
    assert result.status is ClaimStatus.UNSUPPORTED
    assert "cross_tenant_rejected" in result.reasons


def test_evidence_for_and_contradictions_api():
    store = _wuhou_scenario()
    claim = next(c for c in store.all_claims())
    ev = evidence_for(store, claim.claim_id)
    assert ev["evidence"]
    # no hard contradictions in single-claim wuhou fixture
    assert contradictions(store)["count"] == 0

def test_verify_fail_closed_missing_unit_stat_type_value():
    """Missing proof fields must not invent SUPPORTED (#1330)."""
    store = ClaimStore()
    art = FakeArtifact(artifact_id="ref:bare-stats", metadata={"method": "admin_density"})
    node = project_artifact_record(art, tenant_id="t1", session_id="s1")
    store.upsert_evidence(node)
    claim = claim_from_statistic(
        subject="武侯区",
        claim_type=ClaimType.DENSITY,
        value=4.7,
        unit="per_km2",
        method="admin_density",
        statistic_evidence_id=node.evidence_id,
        tenant_id="t1",
        session_id="s1",
        store=store,
    )
    result = verify_claim(claim, store)
    assert result.status is not ClaimStatus.SUPPORTED
    assert result.positive_proof is False
    assert any(
        r in result.reasons
        for r in ("no_declared_stat_type", "evidence_unit_missing", "evidence_value_missing", "units_unspecified")
    )


def test_verify_value_mismatch_rejects_supported():
    store = ClaimStore()
    art = FakeArtifact(
        artifact_id="ref:stats-val",
        metadata={"stat_type": "density", "unit": "per_km2", "method": "admin_density", "value": 4.7},
    )
    node = project_artifact_record(art, tenant_id="t1", session_id="s1")
    store.upsert_evidence(node)
    assert node.metadata.get("value") == 4.7
    claim = claim_from_statistic(
        subject="武侯区",
        claim_type=ClaimType.DENSITY,
        value=999.0,  # tampered
        unit="per_km2",
        method="admin_density",
        statistic_evidence_id=node.evidence_id,
        tenant_id="t1",
        session_id="s1",
        store=store,
    )
    result = verify_claim(claim, store)
    assert result.status is not ClaimStatus.SUPPORTED
    assert result.positive_proof is False
    assert any(r.startswith("value_mismatch") for r in result.reasons)


def test_verify_empty_tenant_cannot_bypass_expected_tenant():
    store = ClaimStore()
    art = FakeArtifact(
        artifact_id="ref:stats-tenant",
        metadata={"stat_type": "density", "unit": "per_km2", "method": "x", "value": 1.0},
    )
    node = project_artifact_record(art, tenant_id="", session_id="s1")
    store.upsert_evidence(node)
    claim = claim_from_statistic(
        subject="x",
        claim_type=ClaimType.DENSITY,
        value=1.0,
        unit="per_km2",
        method="x",
        statistic_evidence_id=node.evidence_id,
        tenant_id="",  # empty
        session_id="s1",
        store=store,
    )
    result = verify_claim(claim, store, expected_tenant_id="tenant-A")
    assert result.status is ClaimStatus.UNSUPPORTED
    assert "cross_tenant_rejected" in result.reasons


def test_grounding_projection_does_not_mutate_claim_status():
    store = _wuhou_scenario()
    claim = next(c for c in store.all_claims() if c.comparator == "highest")
    assert claim.status is ClaimStatus.UNKNOWN
    ground = grounding_projection(store, claim.claim_id)
    # Read path may report a verdict, but must not persist into the store.
    refreshed = store.get_claim(claim.claim_id)
    assert refreshed is not None
    assert refreshed.status is ClaimStatus.UNKNOWN
    assert "status" in ground


# ── Adversarial fail-open regressions (#1712) ─────────────────────────────

def _wuhou_claim_with_uncertainty(unc_kind, unc_freshness):
    """武侯场景 + 替换 uncertainty 节点（其余证据面保持完整可 supported）。"""
    store = _wuhou_scenario()
    unc = next(
        e for e in store.all_evidence() if e.kind is EvidenceKind.UNCERTAINTY)
    store.upsert_evidence(unc.model_copy(update={
        "kind": unc_kind, "freshness": unc_freshness}))
    claim = next(c for c in store.all_claims() if c.comparator == "highest")
    return store, store.get_claim(claim.claim_id)


def test_verify_uncertainty_wrong_kind_is_not_supported():
    """#1712-1：uncertainty 节点类型错误但 freshness 非 MISSING → 义务未满足。

    旧实现用 and 连接两个独立失败条件，该组合静默通过 → SUPPORTED。
    """
    store, claim = _wuhou_claim_with_uncertainty(
        EvidenceKind.STATISTIC, EvidenceFreshness.FRESH)
    result = verify_claim(claim, store, require_uncertainty=True)
    assert result.status is not ClaimStatus.SUPPORTED
    assert result.positive_proof is False
    assert "uncertainty_kind_mismatch" in result.reasons


def test_verify_uncertainty_missing_freshness_is_not_supported():
    """#1712-1：类型正确但 freshness=MISSING → 义务未满足（reason 保真）。"""
    store, claim = _wuhou_claim_with_uncertainty(
        EvidenceKind.UNCERTAINTY, EvidenceFreshness.MISSING)
    result = verify_claim(claim, store, require_uncertainty=True)
    assert result.status is not ClaimStatus.SUPPORTED
    assert result.positive_proof is False
    assert "uncertainty_not_present" in result.reasons


def test_verify_uncertainty_both_conditions_fail_reports_both():
    """#1712-1：类型错 + MISSING 同时在场 → 两个 reason 都披露。"""
    store, claim = _wuhou_claim_with_uncertainty(
        EvidenceKind.ARTIFACT, EvidenceFreshness.MISSING)
    result = verify_claim(claim, store, require_uncertainty=True)
    assert result.positive_proof is False
    assert "uncertainty_kind_mismatch" in result.reasons
    assert "uncertainty_not_present" in result.reasons


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_verify_non_finite_claim_value_rejected(bad_value):
    """#1712-2：claim.value 非有限 → NaN 比较恒假会放行任何数值绑定。"""
    store = ClaimStore()
    stat = EvidenceNode(
        evidence_id="stat:nan", kind=EvidenceKind.STATISTIC, ref="ref:nan",
        freshness=EvidenceFreshness.FRESH, method="admin_density",
        metadata={"stat_type": "density", "unit": "per_km2", "value": 4.7},
    )
    store.upsert_evidence(stat)
    claim = claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.DENSITY, value=bad_value,
        unit="per_km2", method="admin_density",
        statistic_evidence_id="stat:nan", store=store,
    )
    result = verify_claim(claim, store)
    assert result.status is not ClaimStatus.SUPPORTED
    assert result.positive_proof is False
    assert "claim_value_not_finite" in result.reasons


def test_verify_non_finite_evidence_value_rejected():
    """#1712-2：证据侧非有限值同样不得换来 value_bound。"""
    store = ClaimStore()
    stat = EvidenceNode(
        evidence_id="stat:evnan", kind=EvidenceKind.STATISTIC, ref="ref:evnan",
        freshness=EvidenceFreshness.FRESH, method="admin_density",
        metadata={"stat_type": "density", "unit": "per_km2",
                  "value": float("nan")},
    )
    store.upsert_evidence(stat)
    claim = claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.DENSITY, value=4.7,
        unit="per_km2", method="admin_density",
        statistic_evidence_id="stat:evnan", store=store,
    )
    result = verify_claim(claim, store)
    assert result.positive_proof is False
    assert "evidence_value_not_finite" in result.reasons


# ── Adversarial regressions (#1743) ──────────────────────────────────────

def _method_proof_fixture(store: ClaimStore) -> None:
    """完整正证明四件套（stat + dsv + unc），claim 由调用方决定是否带 store。"""
    project_dataset_version(
        dataset_key="s", version_token="v1", store=store, tenant_id="t1",
    )
    store.upsert_evidence(EvidenceNode(
        evidence_id="stat:1", kind=EvidenceKind.STATISTIC, ref="ref:stats",
        freshness=EvidenceFreshness.FRESH, method="admin_density",
        metadata={"stat_type": "density", "unit": "per_km2", "value": 4.7},
        tenant_id="t1", session_id="s1",
        scope=Scope(spatial_level="district", group_by="district", temporal_label="2024"),
    ))
    store.upsert_evidence(EvidenceNode(
        evidence_id="unc:1", kind=EvidenceKind.UNCERTAINTY, ref="unc:1",
        freshness=EvidenceFreshness.FRESH, tenant_id="t1", session_id="s1",
    ))


def test_verify_whitespace_only_method_rejected():
    """#1743：method 义务按 :280 注释口径显式实现——纯空白声明不算方法。"""
    store = ClaimStore()
    _method_proof_fixture(store)
    claim = claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.DENSITY, value=4.7,
        unit="per_km2", comparator="highest", method="   ",
        statistic_evidence_id="stat:1", dataset_version_evidence_id="dsv:s:v1",
        uncertainty_ref="unc:1",
        spatial_scope=Scope(spatial_level="district", group_by="district"),
        temporal_scope=Scope(temporal_label="2024"),
        tenant_id="t1", session_id="s1",
        store=store,
    )
    result = verify_claim(claim, store)
    assert result.status is not ClaimStatus.SUPPORTED
    assert result.positive_proof is False
    assert "method_missing" in result.reasons


def test_verify_declared_method_string_satisfies_method_obligation():
    """#1743：按 :280 注释口径，claim 上的 method 字符串即满足方法义务
    （结构性声明，不要求 method: 边在场）。

    app/evaluation/runner.py 的 supported 场景正例即按此口径构建
    （claim_from_statistic 不传 store → 无 method: 边）；若将来要把
    method: 边校验收紧为硬条件，需先给该场景补边，再更新本测试。
    """
    store = ClaimStore()
    _method_proof_fixture(store)
    claim = claim_from_statistic(
        subject="武侯区", claim_type=ClaimType.DENSITY, value=4.7,
        unit="per_km2", comparator="highest", method="admin_density",
        statistic_evidence_id="stat:1", dataset_version_evidence_id="dsv:s:v1",
        uncertainty_ref="unc:1",
        spatial_scope=Scope(spatial_level="district", group_by="district"),
        temporal_scope=Scope(temporal_label="2024"),
        tenant_id="t1", session_id="s1",
    )  # 不传 store → 不生成 method: 边（仅字符串声明）
    store.upsert_claim(claim)
    result = verify_claim(claim, store)
    assert result.status is ClaimStatus.SUPPORTED
    assert result.positive_proof is True
    assert "method_missing" not in result.reasons


@pytest.mark.parametrize("comparator", ["", "eq"])
def test_contradiction_both_nan_values_not_hard(comparator):
    """#1743：双方 value 均 NaN（都算不出来）不构成 hard 矛盾。

    旧实现 NaN != NaN 恒为 True，同主体、同方法、同 scope 的两个 NaN claim
    被判硬矛盾并双双标为 CONTRADICTED，矛盾结论污染下游裁决。
    """
    store = ClaimStore()
    store.upsert_evidence(EvidenceNode(
        evidence_id="stat:1", kind=EvidenceKind.STATISTIC, ref="ref:stats",
        freshness=EvidenceFreshness.FRESH, method="admin_density",
        metadata={"stat_type": "density", "unit": "per_km2"},
        tenant_id="t1", session_id="s1",
    ))
    nan_common = dict(
        claim_type=ClaimType.DENSITY,
        subject="武侯区",
        value=float("nan"),
        unit="per_km2",
        comparator=comparator,
        method="admin_density",
        predicate="density",
        supporting_evidence_refs=["stat:1"],
        spatial_scope=Scope(spatial_level="district", group_by="district"),
        temporal_scope=Scope(temporal_label="2024"),
        tenant_id="t1",
        session_id="s1",
    )
    store.upsert_claim(Claim(claim_id="claim:nan-a", **nan_common))
    store.upsert_claim(Claim(claim_id="claim:nan-b", **nan_common))
    results = detect_contradictions(store)
    assert not any(r.kind == "hard" for r in results)
    assert store.get_claim("claim:nan-a").status is not ClaimStatus.CONTRADICTED
    assert store.get_claim("claim:nan-b").status is not ClaimStatus.CONTRADICTED


def test_contradiction_one_nan_vs_value_still_hard():
    """#1743 修复范围仅限「皆 NaN」：单侧 NaN vs 实值仍是冲突（语义不变）。"""
    store = ClaimStore()
    store.upsert_evidence(EvidenceNode(
        evidence_id="stat:1", kind=EvidenceKind.STATISTIC, ref="ref:stats",
        freshness=EvidenceFreshness.FRESH, method="admin_density",
        metadata={"stat_type": "density", "unit": "per_km2"},
        tenant_id="t1", session_id="s1",
    ))
    base = dict(
        claim_type=ClaimType.DENSITY,
        subject="武侯区",
        unit="per_km2",
        method="admin_density",
        predicate="density",
        supporting_evidence_refs=["stat:1"],
        spatial_scope=Scope(spatial_level="district", group_by="district"),
        temporal_scope=Scope(temporal_label="2024"),
        tenant_id="t1",
        session_id="s1",
    )
    store.upsert_claim(Claim(claim_id="claim:nan-a", value=float("nan"), **base))
    store.upsert_claim(Claim(claim_id="claim:val-b", value=5.0, **base))
    results = detect_contradictions(store)
    assert any(r.kind == "hard" for r in results)
    assert store.get_claim("claim:nan-a").status is ClaimStatus.CONTRADICTED
    assert store.get_claim("claim:val-b").status is ClaimStatus.CONTRADICTED
