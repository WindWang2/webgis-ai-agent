"""F05 / ADR-0215 D6 — Cross-replica user-edit idempotency.

Invariants under test:
- one user delivery (one MapSpec mutation_id) replayed across replicas /
  rebase copies converges to exactly one working-context record;
- op identity rides the deterministic observation (provenance
  ``detail.mutation_id``), never free text;
- user-wins holds: edits are deduped, never overwritten or invalidated;
- legacy records without op ids keep pre-ADR-0215 behavior.
"""
from __future__ import annotations

from app.services.gis_context.invalidation import apply_changes
from app.services.gis_context.observation import observe_session
from app.services.gis_context.store import _rebase
from app.services.gis_context.working_context import GISWorkingContext


def _state_with_hidden(layer_id: str, mutation_id: str) -> dict:
    provenance_entry = {
        "seq": 7,
        "ts": "2026-09-26T00:00:00+00:00",
        "origin": "user",
        "actor": "map_panel",
        "kind": "PatchLayerPresentationIntent",
        "target": layer_id,
        "revision": 9,
        "detail": {"visible": False, "mutation_id": mutation_id},
    }
    return {"_gis_provenance": [provenance_entry]}


def _wc() -> GISWorkingContext:
    return GISWorkingContext(mission_id="msn-edit", org_id="org-1", revision=2)


def test_observation_projects_mutation_id():
    state = _state_with_hidden("layer-1", "mut-abc")
    obs = observe_session(state, {})
    assert obs.user_hidden_layers == ["layer-1"]
    assert obs.user_edit_ops == {"layer-1": "mut-abc"}


def test_replayed_delivery_records_single_edit():
    """The same user delivery observed twice (at-least-once delivery across
    replicas) records ONE edit — identity is the mutation_id."""
    wc = _wc()
    state = _state_with_hidden("layer-1", "mut-abc")
    for _ in range(3):
        obs = observe_session(state, {})
        apply_changes(wc, [], obs=obs, turn_id="t1")
    assert len(wc.user_edits) == 1
    assert wc.user_edits[0].op_id == "mut-abc"


def test_rebase_convergence_across_replicas():
    """Replica A observed two deliveries (seq 1,2); replica B only saw the
    second (its own seq 1). The rebase must converge by op identity — the
    same delivery must not appear twice under different seqs."""
    replica_a = _wc()
    apply_changes(replica_a, [], obs=observe_session(
        _state_with_hidden("layer-1", "mut-1"), {}), turn_id="t-a1")
    apply_changes(replica_a, [], obs=observe_session(
        _state_with_hidden("layer-2", "mut-2"), {}), turn_id="t-a2")
    replica_b = _wc()
    apply_changes(replica_b, [], obs=observe_session(
        _state_with_hidden("layer-2", "mut-2"), {}), turn_id="t-b")

    assert [e.seq for e in replica_a.user_edits] == [1, 2]
    assert replica_b.user_edits[0].seq == 1  # per-copy sequence

    merged = _rebase(replica_a, replica_b)
    assert len(merged.user_edits) == 2
    assert {e.op_id for e in merged.user_edits} == {"mut-1", "mut-2"}


def test_distinct_deliveries_both_recorded():
    wc = _wc()
    apply_changes(wc, [], obs=observe_session(
        _state_with_hidden("layer-1", "mut-1"), {}), turn_id="t1")
    apply_changes(wc, [], obs=observe_session(
        _state_with_hidden("layer-2", "mut-2"), {}), turn_id="t2")
    assert len(wc.user_edits) == 2
    assert {e.op_id for e in wc.user_edits} == {"mut-1", "mut-2"}


def test_edits_survive_invalidation_user_wins():
    """USER_EDIT never stales anything and edits are never dropped by the
    engine (user-wins)."""
    wc = _wc()
    apply_changes(wc, [], obs=observe_session(
        _state_with_hidden("layer-1", "mut-1"), {}), turn_id="t1")
    from app.services.gis_context.observation import ContextChange

    outcome = apply_changes(
        wc, [ContextChange(kind="AOI_CHANGED", detail="drift")], turn_id="t2")
    assert outcome.recorded_edits == 0
    assert len(wc.user_edits) == 1
    assert "USER_EDIT" not in outcome.changes


def test_legacy_edit_without_op_id_unchanged():
    """No mutation id available (legacy provenance / snapshot-derived):
    append with per-copy seq — pre-ADR-0215 behavior preserved."""
    wc = _wc()
    ok = wc.add_user_edit(layer_id="l1", kind="hide", turn_id="t1")
    assert ok is True
    ok = wc.add_user_edit(layer_id="l2", kind="hide", turn_id="t2")
    assert ok is True
    assert [e.seq for e in wc.user_edits] == [1, 2]
    assert all(e.op_id == "" for e in wc.user_edits)


def test_op_id_dedup_on_direct_append():
    wc = _wc()
    assert wc.add_user_edit(layer_id="l1", kind="hide", op_id="mut-1") is True
    # Same op replayed (even with different layer/kind noise) → idempotent.
    assert wc.add_user_edit(layer_id="l1", kind="hide", op_id="mut-1") is True
    assert len(wc.user_edits) == 1
    assert wc.add_user_edit(layer_id="l1", kind="hide", op_id="mut-2") is True
    assert len(wc.user_edits) == 2


def test_rebase_receipt_ring_union():
    """A CAS loser's receipts survive the rebase (evidence trail is not
    dropped when the state it described already won)."""
    from app.services.gis_context.working_context import RevalidationReceipt

    stored = _wc()
    stored.append_receipt(RevalidationReceipt(
        receipt_id="rtv-1", kind="CLAIM_REVERIFIED", target="c1",
        basis_revision=3, verdict="restored"))
    incoming = _wc()
    incoming.append_receipt(RevalidationReceipt(
        receipt_id="rtv-1", kind="CLAIM_REVERIFIED", target="c1",
        basis_revision=3, verdict="restored"))
    incoming.append_receipt(RevalidationReceipt(
        receipt_id="rtv-2", kind="DECISION_REAFFIRM", target="dec:x",
        basis_revision=4, verdict="rejected", reject_reason="not_stale"))

    merged = _rebase(stored, incoming)
    ids = [r.receipt_id for r in merged.revalidations]
    assert ids == ["rtv-1", "rtv-2"]
    assert merged.rtv_seq == 1  # max(stored, incoming) monotonic


def test_rebase_decision_reaffirm_propagates():
    """A reaffirm on one replica (higher basis_revision) must not be drowned
    by the stored stale copy on the other."""
    from app.services.gis_context.working_context import DecisionRecord

    stored = _wc()
    stored.accepted_assumptions = [DecisionRecord(
        text="假设A", turn_id="t1", basis_revision=2, stale_basis=True,
        stale_reasons=["basis.aoi"])]
    incoming = _wc()
    incoming.accepted_assumptions = [DecisionRecord(
        text="假设A", turn_id="t1", basis_revision=9, stale_basis=False,
        stale_reasons=[])]

    merged = _rebase(stored, incoming)
    assert merged.accepted_assumptions[0].stale_basis is False
    assert merged.accepted_assumptions[0].basis_revision == 9


def test_rebase_finding_tie_prefers_safer_status():
    """Equal basis_revision: a stale status must beat supported — a rebase
    can never resurrect a revoked conclusion."""
    from app.services.gis_context.working_context import FindingRef

    stored = _wc()
    stored.findings = [FindingRef(claim_id="c1", status="stale", basis_revision=4)]
    incoming = _wc()
    incoming.findings = [FindingRef(claim_id="c1", status="supported", basis_revision=4)]
    merged = _rebase(stored, incoming)
    assert merged.findings[0].status == "stale"

    # But a genuinely newer restore (higher revision) wins.
    incoming2 = _wc()
    incoming2.findings = [FindingRef(claim_id="c1", status="supported", basis_revision=5)]
    merged2 = _rebase(stored, incoming2)
    assert merged2.findings[0].status == "supported"
    assert merged2.findings[0].basis_revision == 5
