"""M2 — GIS Working Context: bounded sections, payload gate, mutation helpers."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.services.gis_context.working_context import (
    MAX_PAYLOAD_BYTES,
    BasisDataset,
    DecisionRecord,
    FindingRef,
    GISWorkingContext,
    WorkingBasis,
)


def test_extra_fields_are_rejected_not_silently_dropped():
    with pytest.raises(ValidationError):
        GISWorkingContext.model_validate({
            "mission_id": "msn-x", "not_a_field": 1,
        })


def test_section_bounds_are_enforced():
    wc = GISWorkingContext(mission_id="msn-bounds")
    for i in range(20):
        wc.upsert_finding(f"claim-{i}", "supported", 1)
    assert len(wc.findings) == 8  # MAX_FINDINGS
    for i in range(20):
        wc.add_user_edit(layer_id=f"L{i}", kind="hide", turn_id="t1")
    assert len(wc.user_edits) == 12  # MAX_USER_EDITS — extra edits never
    # silently overwrite; the caller sees False and can surface it.
    with pytest.raises(ValidationError):
        GISWorkingContext(
            mission_id="msn-ds",
            basis=WorkingBasis(datasets=[
                BasisDataset(ref_id=f"r{i}") for i in range(13)
            ]),
        )


def test_payload_round_trip_and_byte_budget():
    wc = GISWorkingContext(
        mission_id="msn-rt",
        basis=WorkingBasis(
            aoi_bbox=[1.0, 2.0, 3.0, 4.0],
            datasets=[BasisDataset(ref_id="ref:a", content_revision="rev-9")],
        ),
        accepted_assumptions=[DecisionRecord(text="小学分布集中于主城区", turn_id="t1", basis_revision=1)],
        findings=[FindingRef(claim_id="claim-1", status="supported", basis_revision=1)],
    )
    data = wc.payload()
    restored = GISWorkingContext.from_payload(json.loads(json.dumps(data)))
    assert restored.basis.aoi_bbox == [1.0, 2.0, 3.0, 4.0]
    assert restored.accepted_assumptions[0].text == "小学分布集中于主城区"
    assert restored.findings[0].claim_id == "claim-1"


def test_payload_over_budget_fails_closed(monkeypatch):
    """The 16KB gate refuses oversized payloads before any DB write."""
    import app.services.gis_context.working_context as wcm

    big = GISWorkingContext(mission_id="msn-big")
    assert len(json.dumps(big.payload()).encode()) < MAX_PAYLOAD_BYTES

    monkeypatch.setattr(wcm, "MAX_PAYLOAD_BYTES", 64)
    with pytest.raises(ValueError):
        big.payload()


def test_user_edit_dedup_and_append_only_seq():
    wc = GISWorkingContext(mission_id="msn-ue")
    assert wc.add_user_edit(layer_id="L1", kind="hide", turn_id="t1")
    assert wc.add_user_edit(layer_id="L1", kind="restyle", turn_id="t1")
    # Same (layer, kind) with a new seq is a distinct record (seq grows).
    seqs = [e.seq for e in wc.user_edits]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_finding_upsert_updates_in_place():
    wc = GISWorkingContext(mission_id="msn-f")
    wc.upsert_finding("claim-1", "supported", 1)
    wc.upsert_finding("claim-1", "stale", 2)
    assert len(wc.findings) == 1
    assert wc.findings[0].status == "stale"
    assert wc.findings[0].basis_revision == 2


def test_scope_ref_carries_owner_and_terminal_policy():
    wc = GISWorkingContext(
        mission_id="msn-s", org_id="org-1", project_id="prj-1", revision=7,
    )
    ref = wc.scope_ref()
    assert ref.scope_id == "msn-s"
    assert ref.revision == "7"
    assert ref.policy.value == "on_mission_terminal"
