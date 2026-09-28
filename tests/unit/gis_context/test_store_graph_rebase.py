"""H09 — store rebase semantics for the memory graph: CAS losers converge
without resurrecting revoked currency."""
from __future__ import annotations

from app.services.gis_context.store import WorkingContextStore, _rebase
from app.services.gis_context.working_context import (
    ContextFact,
    DerivedFinding,
    GISWorkingContext,
)


def _wc(mission="msn-rebase01"):
    return GISWorkingContext(mission_id=mission, org_id="org-1")


def _df(fid, *, generation=1, status="current", ref="label_collision:L1"):
    from app.services.gis_context.working_context import DependencyEdge

    return DerivedFinding(
        finding_id=fid, family="critique", ref=ref, label="label_collision",
        status=status, generation=generation, digest=f"dg-{fid}", priority=2,
        depends_on=[DependencyEdge(dim="mapspec", ref="", anchor="17")])


def test_facts_union_newer_token_wins():
    stored = _wc()
    stored.upsert_fact(kind="dataset", ref="ds1", token="r1", basis_revision=1)
    incoming = _wc()
    incoming.upsert_fact(kind="dataset", ref="ds1", token="r2", basis_revision=2)
    incoming.upsert_fact(kind="dataset", ref="ds2", token="r9", basis_revision=2)
    merged = _rebase(stored, incoming)
    tokens = {f.ref: f.token for f in merged.facts}
    assert tokens == {"ds1": "r2", "ds2": "r9"}
    assert merged.fact_seq >= 2


def test_fact_seq_monotonic_across_rebase():
    stored = _wc()
    stored.fact_seq = 5
    incoming = _wc()
    incoming.fact_seq = 9
    assert _rebase(stored, incoming).fact_seq == 9
    assert _rebase(incoming, stored).fact_seq == 9


def test_derived_findings_newer_generation_wins():
    stored = _wc()
    stored.upsert_derived_finding(_df("df-1", generation=3, status="stale"))
    incoming = _wc()
    incoming.upsert_derived_finding(_df("df-1", generation=4, status="current"))
    merged = _rebase(stored, incoming)
    assert merged.derived_finding("df-1").status == "current"
    assert merged.derived_finding("df-1").generation == 4


def test_same_generation_tie_never_resurrects_revoked_currency():
    stored = _wc()
    stored.upsert_derived_finding(_df("df-1", generation=4, status="stale"))
    incoming = _wc()
    incoming.upsert_derived_finding(_df("df-1", generation=4, status="current"))
    merged = _rebase(stored, incoming)
    assert merged.derived_finding("df-1").status == "stale"
    # And a retired row stays retired against a stale claim at the same gen.
    stored2 = _wc()
    stored2.upsert_derived_finding(_df("df-2", generation=4, status="superseded"))
    incoming2 = _wc()
    incoming2.upsert_derived_finding(_df("df-2", generation=4, status="stale"))
    assert _rebase(stored2, incoming2).derived_finding("df-2").status == "superseded"


def test_user_edit_detail_survives_rebase():
    stored = _wc()
    incoming = _wc()
    incoming.add_user_edit(layer_id="L2", kind="opacity", turn_id="t",
                           op_id="m-op", detail="opacity=0.40")
    merged = _rebase(stored, incoming)
    assert [e.detail for e in merged.user_edits] == ["opacity=0.40"]


def test_rebased_recompute_survives_persistence(wc_store):
    """The full convergence loop: two replicas diverge, the loser's fresher
    recompute wins through save → rebase → reload."""
    wc = _wc("msn-rebase02")
    wc.upsert_derived_finding(_df("df-1", generation=1, status="stale"))
    wc_store.save(wc)
    winner = wc_store.load(wc.mission_id, org_id=wc.org_id)
    winner.upsert_derived_finding(_df("df-1", generation=2, status="current"))
    winner.revision += 1
    wc_store.save(winner)
    # The stale replica saves with a stale expected revision → rebase path.
    loser = wc.model_copy(deep=True)
    loser.revision += 1
    wc_store.save(loser, expected_revision=1)
    final = wc_store.load(wc.mission_id, org_id=wc.org_id)
    assert final.derived_finding("df-1").status == "current"
    assert final.derived_finding("df-1").generation == 2
