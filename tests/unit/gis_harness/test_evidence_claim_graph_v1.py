"""Spatial Evidence / Claim / Provenance Graph tests (Direction 03, GOAL §21).

Offline, zero LLM, deterministic fixtures. Covers evidence liveness, claims,
semantics, map binding, product linkage, narrative, invalidation, determinism,
and bounded traversal performance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


from app.services.gis_harness.evidence_claim import (
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
        metadata={"stat_type": "density", "unit": "per_km2", "method": "admin_density"},
    )
    stat = project_artifact_record(art, tenant_id="t1", session_id="s1")
    stat = stat.model_copy(update={
        "kind": EvidenceKind.STATISTIC,
        "method": "admin_density",
        "freshness": EvidenceFreshness.FRESH,
        "metadata": {**stat.metadata, "stat_type": "density", "unit": "per_km2"},
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
