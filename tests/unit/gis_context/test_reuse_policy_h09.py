"""H09 — four-tier reuse policy: verdict/cause mapping, closed reason
codes, and token evidence."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.gis_context.reuse_policy import (
    REASON_DATA_MOVED,
    REASON_FINGERPRINTS_MATCH,
    REASON_KNOWLEDGE_ONLY,
    REASON_PARTIAL_RECOMPUTE,
    REUSE_TIERS,
    decide_reuse,
)
from app.services.gis_context.working_context import BasisDataset, WorkingBasis


def _cand(verdict, causes=(), subject="school-density-map"):
    return SimpleNamespace(
        entry=SimpleNamespace(subject=subject, authority_store="artifact",
                              authority_id="a-1"),
        verdict=verdict, reasons=list(causes), stale_causes=list(causes))


def _wc():
    from app.services.gis_context.working_context import GISWorkingContext

    return GISWorkingContext(
        mission_id="msn-reuse001", org_id="org-1",
        basis=WorkingBasis(datasets=[BasisDataset(
            ref_id="ref:schools", content_revision="rev-1",
            version_fingerprint="sha256:abc", authority_id="ref:schools")]))


def test_exact_maps_with_fingerprint_evidence():
    d = decide_reuse(_cand("exact"), wc=_wc())
    assert d.tier == "exact"
    assert d.reasons == [REASON_FINGERPRINTS_MATCH]
    assert d.evidence == ["ref:schools@sha256:abc"]  # the claimed token


def test_recompute_partial_is_compatible_with_scoped_reasons():
    d = decide_reuse(_cand("recompute_partial", causes=["aoi_drift"]), wc=_wc())
    assert d.tier == "compatible"
    assert d.reasons[0] == REASON_PARTIAL_RECOMPUTE
    assert "aoi_drift" in d.reasons


def test_version_causes_force_must_recompute():
    for cause in ("version_bump", "head_changed", "request_input_stale:ref:schools"):
        d = decide_reuse(_cand("not_reusable", causes=[cause]), wc=_wc())
        assert d.tier == "must_recompute", cause
        assert d.reasons[0] == REASON_DATA_MOVED
        assert cause in d.evidence  # causal evidence carries the cause


def test_request_input_stale_evidence_includes_claimed_token():
    d = decide_reuse(_cand("not_reusable", causes=["request_input_stale:ref:schools"]),
                     wc=_wc())
    assert any(e.startswith("claimed:ref:schools@") for e in d.evidence)


def test_revoked_knowledge_is_informative_only():
    for cause in ("claim_lost", "scope_gone", "manual"):
        d = decide_reuse(_cand("not_reusable", causes=[cause]), wc=_wc())
        assert d.tier == "stale_but_informative", cause
        assert d.reasons[0] == REASON_KNOWLEDGE_ONLY


def test_unknown_verdict_never_looks_reusable():
    d = decide_reuse(_cand("??"), wc=_wc())
    assert d.tier == "stale_but_informative"


def test_no_wc_still_decides():
    d = decide_reuse(_cand("exact"))
    assert d.tier == "exact" and d.evidence == []


def test_tier_vocabulary_is_closed_and_labelled():
    assert set(REUSE_TIERS) == {"exact", "compatible", "stale_but_informative",
                                "must_recompute"}
    labels = {decide_reuse(_cand(v), wc=_wc()).label for v in
              ("exact", "recompute_partial", "not_reusable")}
    assert all(isinstance(label, str) and label for label in labels)
