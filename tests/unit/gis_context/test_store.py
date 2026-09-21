"""WorkingContextStore — revision CAS, conflict rebase, purge, org isolation."""
from __future__ import annotations

import pytest

from app.services.gis_context.store import (
    WorkingContextConflict,
    WorkingContextStore,
)
from app.services.gis_context.working_context import (
    DecisionRecord,
    GISWorkingContext,
    WorkingBasis,
)


def _patched_store_ops(store: WorkingContextStore, wc: GISWorkingContext):
    """Save with the on-disk revision the caller observed (the real protocol:
    callers bump ``wc.revision`` locally, then claim the pre-bump value)."""
    return store.save(wc, expected_revision=wc.revision - 1)


def test_insert_then_load_round_trip(wc_store, wc):
    assert wc_store.load(wc.mission_id, org_id="org-1") is None
    saved = _patched_store_ops(wc_store, wc)
    loaded = wc_store.load(wc.mission_id, org_id="org-1")
    assert loaded is not None and loaded.mission_id == saved.mission_id
    assert loaded.basis.aoi_name == "成都市"
    assert loaded.findings == [] and loaded.user_edits == []


def test_org_mismatch_never_loads(wc_store, wc):
    wc_store.save(wc, expected_revision=None)
    assert wc_store.load(wc.mission_id, org_id="org-2") is None
    # Fail-closed (review P1-1): a row carrying an org is invisible to a
    # requester with unknown (empty) org — never wild-carded.
    assert wc_store.load(wc.mission_id, org_id="") is None


def test_cas_conflict_preserves_stored_history(wc_store, wc):
    """Two writers both observed revision 1: the loser's save is rebased
    onto the stored winner — stored decision history survives, basis comes
    from the fresher side."""
    wc_store.save(wc, expected_revision=1)  # revision 1 observed by both

    # Both writers derive from revision 1 and bump to 2 (as the real flow
    # does in apply_changes) — the second writer must lose the CAS.
    winner = wc.next_revision()
    winner.accepted_assumptions.append(
        DecisionRecord(text="主城区为分布热点", turn_id="t2", basis_revision=2))

    loser = wc.next_revision()
    loser.basis = WorkingBasis(
        aoi_bbox=[102.9, 30.5, 104.5, 31.5], aoi_name="成都市全域",
        time_period="2025",  # fresher observation
    )

    _patched_store_ops(wc_store, winner)   # expected 1 == disk 1 → direct write
    merged = _patched_store_ops(wc_store, loser)  # CAS lost (1≠2) → rebase

    stored = wc_store.load(wc.mission_id, org_id="org-1")
    texts = {d.text for d in stored.accepted_assumptions}
    assert "主城区为分布热点" in texts          # winner history kept
    assert stored.basis.time_period == "2025"   # fresher basis kept
    assert stored.revision == 3                 # winner wrote 2, rebase wrote 3
    assert merged.revision == 3


def test_purge_is_idempotent_tombstone(wc_store, wc):
    wc_store.save(wc, expected_revision=None)
    assert wc_store.purge(wc.mission_id) is True
    assert wc_store.load(wc.mission_id, org_id="org-1") is None
    assert wc_store.purge(wc.mission_id) is False
    # A save against a purged row is refused, not resurrected.
    with pytest.raises(WorkingContextConflict):
        wc_store.save(wc.next_revision(), expected_revision=None)


def test_missing_mission_row_loads_none(wc_store):
    assert wc_store.load("msn-ghost", org_id="org-1") is None
