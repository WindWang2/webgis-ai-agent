"""F05 / ADR-0215 — Evidence-backed revalidation engine.

Invariants under test:
- stale→current only through evidence-checked engine paths; every attempt
  (restored *or* rejected) leaves a receipt; callers cannot upgrade by
  assertion;
- restores re-stamp ``basis_revision`` so the next drift re-stales;
- loop protection: state-based idempotency, bounded marker passes;
- replay determinism: identical inputs → identical receipt ids/verdicts;
- v1 payloads still load (schema v2 compat).
"""
from __future__ import annotations

from app.services.gis_context.invalidation import apply_changes
from app.services.gis_context.observation import (
    SessionObservation,
    diff_against,
)
from app.services.gis_context.revalidation import (
    KIND_CLAIM,
    KIND_DECISION,
    KIND_MARKER,
    MAX_PASSIVE_MARKERS,
    REJECT_ATTRIBUTED_STALE_REMAINING,
    REJECT_CLAIM_NOT_SUPPORTED,
    REJECT_CLAIM_UNVERIFIABLE,
    REJECT_FIELD_UNOBSERVED,
    REJECT_NOT_STALE,
    REJECT_NO_CLAIM_STORE,
    REJECT_TARGET_UNKNOWN,
    reaffirm_decisions,
    reconfirm_markers,
    revalidate_claims,
)
from app.services.gis_context.working_context import (
    DecisionRecord,
    FindingRef,
    GISWorkingContext,
    SCHEMA_VERSION,
)
from app.services.gis_harness.evidence_claim.contracts import (
    Claim,
    ClaimStatus,
    ClaimType,
    EvidenceFreshness,
    EvidenceKind,
    EvidenceNode,
)
from app.services.gis_harness.evidence_claim.store import ClaimStore


def _wc(**overrides) -> GISWorkingContext:
    kwargs = dict(
        mission_id="msn-rtv",
        org_id="org-1",
        revision=3,
        basis_overrides=None,
    )
    kwargs.update(overrides)
    basis_kwargs = kwargs.pop("basis_overrides") or {}
    from app.services.gis_context.working_context import WorkingBasis

    return GISWorkingContext(
        basis=WorkingBasis(
            aoi_bbox=[102.9, 30.5, 104.5, 31.5],
            time_period="2024",
            crs="EPSG:4326",
            measure_field="school_count",
            measure_statistic="sum",
            **basis_kwargs,
        ),
        findings=[FindingRef(
            claim_id="claim-1", status="supported", basis_revision=1,
        )],
        **kwargs,
    )


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


def _supported_claim_store(claim_id: str = "claim-1") -> ClaimStore:
    """A claim store whose claim verifies SUPPORTED (deterministic pipeline)."""
    store = ClaimStore()
    store.upsert_evidence(EvidenceNode(
        evidence_id="ev-1",
        kind=EvidenceKind.STATISTIC,
        ref="ref:schools",
        freshness=EvidenceFreshness.FRESH,
        metadata={"stat_type": "count", "unit": "个", "value": 42},
    ))
    store.upsert_claim(Claim(
        claim_id=claim_id,
        claim_type=ClaimType.COUNT,
        subject="schools",
        value=42.0,
        unit="个",
        method="capability:count_points",
        supporting_evidence_refs=["ev-1"],
    ))
    return store


def _stale_via_engine(wc: GISWorkingContext, obs: SessionObservation = None):
    """Run a real basis drift through the invalidation engine so the fixture
    exercises the same attribution path production uses."""
    drifted = obs or _obs(aoi_bbox=[103.9, 30.8, 104.9, 31.8])
    changes = diff_against(wc, drifted)
    return apply_changes(wc, changes, obs=drifted, turn_id="t-drift")


# ── CLAIM_REVERIFIED ─────────────────────────────────────────────────────

def test_claim_restore_closed_loop():
    """invalidation stales → deterministic re-verify restores, re-stamped."""
    wc = _wc()
    _stale_via_engine(wc)
    assert wc.findings[0].status == "stale"
    assert "basis.aoi" in wc.findings[0].stale_reasons

    receipts = revalidate_claims(
        wc, ["claim-1"], claim_store=_supported_claim_store(),
        token_resolver=lambda ref: None, turn_id="t1")
    assert len(receipts) == 1
    r = receipts[0]
    assert r.verdict == "restored" and r.kind == KIND_CLAIM
    assert r.basis_revision == wc.revision
    assert wc.findings[0].status == "supported"
    assert wc.findings[0].stale_reasons == []
    assert wc.findings[0].basis_revision == wc.revision
    assert any(e.ref == "verify" for e in r.evidence)
    assert len(wc.revalidations) == 1


def test_claim_recheck_evidence_tokens_in_receipt():
    wc = _wc()
    _stale_via_engine(wc)
    receipts = revalidate_claims(
        wc, ["claim-1"], claim_store=_supported_claim_store(),
        token_resolver=lambda ref: None, turn_id="t1")
    r = receipts[0]
    assert any(c.check == "basis_liveness" and c.verdict == "pass" for c in r.checks)
    assert any(c.check == "claim_verify" and c.verdict == "pass" for c in r.checks)


def test_claim_rejected_when_no_claim_store():
    wc = _wc()
    _stale_via_engine(wc)
    receipts = revalidate_claims(wc, ["claim-1"], claim_store=None, turn_id="t1")
    assert receipts[0].verdict == "rejected"
    assert receipts[0].reject_reason == REJECT_NO_CLAIM_STORE
    assert wc.findings[0].status == "stale"


def test_claim_rejected_when_claim_unresolvable():
    wc = _wc()
    _stale_via_engine(wc)
    receipts = revalidate_claims(
        wc, ["claim-1"], claim_store=ClaimStore(), turn_id="t1")
    assert receipts[0].reject_reason == REJECT_CLAIM_UNVERIFIABLE
    assert wc.findings[0].status == "stale"


def test_claim_rejected_when_verify_disagrees():
    """The deterministic verifier, not the caller, decides: a claim whose
    evidence contradicts its value stays stale."""
    wc = _wc()
    _stale_via_engine(wc)
    store = _supported_claim_store()
    claim = store.get_claim("claim-1")
    store.upsert_claim(claim.model_copy(update={"value": 999.0}))
    receipts = revalidate_claims(
        wc, ["claim-1"], claim_store=store,
        token_resolver=lambda ref: None, turn_id="t1")
    assert receipts[0].verdict == "rejected"
    assert receipts[0].reject_reason == REJECT_CLAIM_NOT_SUPPORTED
    assert wc.findings[0].status == "stale"


def test_claim_rejected_on_basis_drift():
    """Authority token advanced → restore refused (basis_advanced)."""
    from app.services.gis_context.working_context import BasisDataset

    wc = _wc(basis_overrides={"datasets": [
        BasisDataset(ref_id="ref:schools", content_revision="rev-1",
                     version_fingerprint="sha-accepted"),
    ]})
    _stale_via_engine(wc)
    receipts = revalidate_claims(
        wc, ["claim-1"], claim_store=_supported_claim_store(),
        token_resolver=lambda ref: "sha-moved", turn_id="t1")
    assert receipts[0].verdict == "rejected"
    assert receipts[0].reject_reason == "basis_advanced"
    assert wc.findings[0].status == "stale"
    assert any(c.check == "basis_liveness" and c.verdict == "fail"
               for c in receipts[0].checks)


def test_claim_unknown_and_not_stale_targets():
    wc = _wc()
    receipts = revalidate_claims(wc, ["claim-nope"], claim_store=ClaimStore())
    assert receipts[0].reject_reason == REJECT_TARGET_UNKNOWN
    receipts = revalidate_claims(
        wc, ["claim-1"], claim_store=_supported_claim_store())
    # status is "supported" (never staled) → honest no-op rejection
    assert receipts[0].reject_reason == REJECT_NOT_STALE
    # no receipts appended for non-restorable no-ops? — they ARE appended
    # (bounded ring, observability), but state is unchanged.
    assert all(r.verdict == "rejected" for r in wc.revalidations)


def test_restored_claim_restales_on_next_drift():
    """The restore is not a pardon: the next real drift re-stales."""
    wc = _wc()
    _stale_via_engine(wc)
    revalidate_claims(
        wc, ["claim-1"], claim_store=_supported_claim_store(),
        token_resolver=lambda ref: None, turn_id="t1")
    assert wc.findings[0].status == "supported"
    _stale_via_engine(wc, _obs(aoi_bbox=[104.5, 31.0, 105.5, 32.0]))
    assert wc.findings[0].status == "stale"
    assert wc.findings[0].basis_revision < wc.revision


# ── DECISION_REAFFIRM ────────────────────────────────────────────────────

def test_decision_reaffirm_match_only():
    wc = _wc()
    wc.accepted_assumptions = [DecisionRecord(
        text="分布集中于主城区", turn_id="t1", basis_revision=1)]
    _stale_via_engine(wc)
    assert wc.accepted_assumptions[0].stale_basis is True
    assert wc.accepted_assumptions[0].stale_reasons

    # No exact match → rejected, nothing changes.
    receipts = reaffirm_decisions(wc, ["完全不同的文本"], turn_id="t2")
    assert receipts[0].reject_reason == REJECT_TARGET_UNKNOWN

    receipts = reaffirm_decisions(wc, ["分布集中于主城区"], turn_id="t2")
    r = receipts[0]
    assert r.verdict == "restored" and r.kind == KIND_DECISION
    assert wc.accepted_assumptions[0].stale_basis is False
    assert wc.accepted_assumptions[0].basis_revision == wc.revision

    # Second reaffirm → not_stale no-op (state-based loop guard).
    receipts = reaffirm_decisions(wc, ["分布集中于主城区"], turn_id="t3")
    assert receipts[0].reject_reason == REJECT_NOT_STALE


def test_reaffirm_cannot_invent_decisions():
    wc = _wc()
    n = len(wc.accepted_assumptions)
    receipts = reaffirm_decisions(wc, ["全新编造的假设"], turn_id="t1")
    assert receipts[0].verdict == "rejected"
    assert len(wc.accepted_assumptions) == n


# ── BASIS_RECONFIRMED (passive) ──────────────────────────────────────────

def test_marker_reconfirm_blocked_by_attributed_finding():
    wc = _wc()
    _stale_via_engine(wc)
    assert "basis.aoi" in wc.stale
    receipts = reconfirm_markers(wc, _obs(), turn_id="t1")
    aoi = [r for r in receipts if r.target == "basis.aoi"]
    assert aoi and aoi[0].verdict == "rejected"
    assert aoi[0].reject_reason == REJECT_ATTRIBUTED_STALE_REMAINING
    assert "basis.aoi" in wc.stale


def test_marker_reconfirm_after_full_reverification():
    wc = _wc()
    wc.accepted_assumptions = [DecisionRecord(
        text="分布集中于主城区", turn_id="t1", basis_revision=1)]
    _stale_via_engine(wc)
    revalidate_claims(
        wc, ["claim-1"], claim_store=_supported_claim_store(),
        token_resolver=lambda ref: None, turn_id="t1")
    reaffirm_decisions(wc, ["分布集中于主城区"], turn_id="t2")

    receipts = reconfirm_markers(wc, _obs(), turn_id="t3")
    restored = [r for r in receipts if r.verdict == "restored"]
    assert any(r.target == "basis.aoi" for r in restored)
    assert "basis.aoi" not in wc.stale
    # Every restore carries observation evidence.
    aoi = next(r for r in restored if r.target == "basis.aoi")
    assert any(e.ref == "obs.aoi" for e in aoi.evidence)


def test_marker_reconfirm_needs_observed_field():
    wc = _wc()
    wc.mark_stale("basis.crs", "CRS_CHANGED:EPSG:3857")
    receipts = reconfirm_markers(wc, _obs(crs=""), turn_id="t1")
    crs = [r for r in receipts if r.target == "basis.crs"]
    assert crs and crs[0].reject_reason == REJECT_FIELD_UNOBSERVED
    assert "basis.crs" in wc.stale


def test_goal_marker_outside_closed_set():
    """Goal revisions re-ground through new work — never auto-cleared."""
    wc = _wc()
    wc.mark_stale("goal", "goal_revision=4")
    receipts = reconfirm_markers(wc, _obs(), turn_id="t1")
    assert all(r.target != "goal" for r in receipts)
    assert "goal" in wc.stale


def test_passive_rejections_not_recorded_in_ring():
    """A persistent blocker must not flood the ring or force writes."""
    wc = _wc()
    _stale_via_engine(wc)
    revision_after_drift = wc.revision
    for _ in range(5):
        reconfirm_markers(wc, _obs(), turn_id="t", record_rejections=False)
    assert len(wc.revalidations) == 0
    assert wc.revision == revision_after_drift  # read-mostly stays read-mostly


def test_passive_marker_cap():
    wc = _wc()
    wc.mark_stale("basis.aoi", "AOI_CHANGED:x")
    wc.mark_stale("basis.crs", "CRS_CHANGED:x")
    wc.mark_stale("basis.time_period", "TIME_PERIOD_CHANGED:x")
    wc.mark_stale("basis.measure", "MEASURE_CHANGED:x")
    wc.mark_stale("basis.datasets", "DATASET_VERSION_CHANGED:x")
    receipts = reconfirm_markers(wc, _obs(), turn_id="t", record_rejections=False)
    assert len(receipts) <= MAX_PASSIVE_MARKERS


# ── Replay determinism / bounds / compat ─────────────────────────────────

def test_replay_determinism():
    """Same turn sequence → same receipt ids and verdicts (ADR-0215 D2)."""
    def run():
        wc = _wc()
        wc.accepted_assumptions = [DecisionRecord(
            text="分布集中于主城区", turn_id="t1", basis_revision=1)]
        _stale_via_engine(wc)
        revalidate_claims(
            wc, ["claim-1"], claim_store=_supported_claim_store(),
            token_resolver=lambda ref: None, turn_id="t2")
        reaffirm_decisions(wc, ["分布集中于主城区"], turn_id="t3")
        reconfirm_markers(wc, _obs(), turn_id="t4")
        return [(r.receipt_id, r.kind, r.target, r.verdict) for r in wc.revalidations]

    assert run() == run()


def test_receipt_ring_bounded_and_payload_budget():
    from app.services.gis_context.working_context import MAX_REVALIDATIONS

    wc = _wc()
    for i in range(MAX_REVALIDATIONS + 6):
        reaffirm_decisions(wc, [f"不存在的假设-{i}"], turn_id=f"t{i}")  # rejected receipts
    assert len(wc.revalidations) == MAX_REVALIDATIONS
    assert wc.rtv_seq == MAX_REVALIDATIONS + 6
    # Payload still serializes within budget with a full ring.
    wc.payload()


def test_v1_payload_compat():
    """A pre-ADR-0215 payload loads unchanged; new fields default."""
    v1 = {
        "schema_version": "gis_working_context.v1",
        "mission_id": "msn-old",
        "org_id": "org-1",
        "revision": 2,
        "basis": {"aoi_bbox": [1.0, 2.0, 3.0, 4.0], "time_period": "2024"},
        "findings": [{"claim_id": "c1", "status": "supported", "basis_revision": 1}],
        "user_edits": [{"seq": 1, "layer_id": "l1", "kind": "hide"}],
        "stale": {"basis.aoi": "AOI_CHANGED:x"},
    }
    wc = GISWorkingContext.from_payload(v1)
    assert wc.findings[0].stale_reasons == []
    assert wc.user_edits[0].op_id == ""
    assert wc.revalidations == []
    assert SCHEMA_VERSION == "gis_working_context.v2"


def test_claim_status_authoritative_after_restore():
    """persist_status=True: the claim store itself carries the refreshed
    currency — the restoration is the existing pipeline's verdict, not a
    parallel truth."""
    wc = _wc()
    _stale_via_engine(wc)
    store = _supported_claim_store()
    store.mark_claim_status("claim-1", ClaimStatus.STALE)
    revalidate_claims(wc, ["claim-1"], claim_store=store,
                      token_resolver=lambda ref: None, turn_id="t1")
    assert store.get_claim("claim-1").status == ClaimStatus.SUPPORTED
