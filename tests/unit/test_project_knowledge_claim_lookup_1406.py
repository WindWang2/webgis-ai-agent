"""#1406: claim-verified reuse must resolve live ClaimStore status."""
from __future__ import annotations

import pytest

from app.services.gis_harness.evidence_claim.contracts import Claim, ClaimStatus, ClaimType
from app.services.gis_harness.hotpath_convergence.session_ctx import (
    get_or_create_claim_store,
    reset_turn_context,
)
from app.services.project_knowledge.contract import (
    AS_ARTIFACT,
    EK_ARTIFACT,
    KnowledgeEntry,
    REL_VERIFIED_BY,
    RefTag,
    ReuseQuery,
    ST_ACTIVE,
    VERDICT_EXACT,
)
from app.services.project_knowledge import retrieval as retrieval_mod


@pytest.fixture(autouse=True)
def _clean_ctx():
    reset_turn_context()
    yield
    reset_turn_context()


def test_claim_statuses_resolves_supported_from_session_store():
    store = get_or_create_claim_store("sess-pk-1406", tenant_id="")
    store.upsert_claim(
        Claim(
            claim_id="clm_supported_1",
            claim_type=ClaimType.DENSITY,
            narrative="density ok",
            status=ClaimStatus.SUPPORTED,
        )
    )
    statuses = retrieval_mod._claim_statuses_for(["clm_supported_1", "clm_missing"])
    assert statuses["clm_supported_1"] == "supported"
    assert statuses["clm_missing"] is None


def test_evaluate_entry_claims_supported_reason(monkeypatch):
    """Positive-proof branch: supported claim appears in reasons (not unverifiable)."""
    entry = KnowledgeEntry(
        id="ke-1",
        org_id="org-a",
        project_id="proj_1",
        entity_kind=EK_ARTIFACT,
        authority_store=AS_ARTIFACT,
        authority_id="art-1",
        subject="asset.tif",
        version_token="tok-1",
        refs=[RefTag(relation=REL_VERIFIED_BY, authority="session_ref", id="clm_ok")],
        status=ST_ACTIVE,
    )
    store = get_or_create_claim_store("sess-pk-1406b")
    store.upsert_claim(
        Claim(claim_id="clm_ok", claim_type=ClaimType.NARRATIVE, narrative="ok", status=ClaimStatus.SUPPORTED)
    )

    # Bypass authority liveness / bbox gates — focus claim branch.
    monkeypatch.setattr(
        retrieval_mod, "live_version_token", lambda *a, **k: "tok-1"
    )

    class _DummyDb:
        pass

    cand = retrieval_mod._evaluate_entry(
        _DummyDb(), entry=entry, query=ReuseQuery(),
    )
    assert any("claim 支持" in r for r in cand.reasons)
    assert not any(c.startswith("claim_unverifiable") for c in cand.stale_causes)
