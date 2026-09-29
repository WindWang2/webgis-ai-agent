"""H09 — recompute orchestration: priority planning, atomic publish,
generation fencing (the concurrent-edit race), and failure semantics."""
from __future__ import annotations


from app.services.gis_context.memory_graph import (
    critique_edges,
    make_derived_finding,
    observe_facts,
    reuse_edges,
)
from app.services.gis_context.observation import observe_session
from app.services.gis_context.recompute import (
    KIND_RECOMPUTE,
    MAX_TASKS_PER_TURN,
    RecomputeResult,
    execute_recompute,
    plan_recompute,
)


def _obs(rev="17", ds=("ref:schools", "rev-1")):
    return observe_session(
        {"_cartographic_mutation_revision": rev},
        {"sources": {ds[0]: {"ref_id": ds[0], "content_revision": ds[1]}}},
    )


def _seed(wc, *, n_critique=1, reuse=True):
    observe_facts(wc, _obs())
    rows = []
    for i in range(n_critique):
        rows.append(make_derived_finding(
            wc, family="critique", ref=f"label_collision:L{i}",
            label="label_collision", digest=f"dg{i}", priority=2,
            edges=critique_edges(wc)))
        wc.upsert_derived_finding(rows[-1])
    if reuse:
        rows.append(make_derived_finding(
            wc, family="reuse", ref="reuse:project", label="项目复用判定",
            digest="reuse-dg", priority=1, edges=reuse_edges(wc)))
        wc.upsert_derived_finding(rows[-1])
    return rows


def _stale_all(wc):
    for f in wc.derived_findings:
        f.status = "stale"
        f.stale_reasons = ["DATASET_VERSION_CHANGED:test"]


def _ok_executor(wc, task, *, ctx):
    return RecomputeResult(
        ok=True, label=task.ref, detail="re-derived", digest=f"new-{task.ref}",
        edges=critique_edges(wc) if task.family == "critique" else reuse_edges(wc),
        evidence=["anchor:live"])


def test_plan_orders_by_priority_and_caps_per_turn(wc):
    _seed(wc, n_critique=6)
    _stale_all(wc)
    tasks = plan_recompute(wc)
    assert len(tasks) == MAX_TASKS_PER_TURN
    priorities = [t.priority for t in tasks]
    assert priorities == sorted(priorities, reverse=True)


def test_plan_family_filter_keeps_no_executor_families_out_of_the_ring(wc):
    """Critique recomputes through the completion verify loop — planning
    must not flood the receipt ring with no-executor rejections."""
    _seed(wc, n_critique=2, reuse=True)
    _stale_all(wc)
    assert [t.family for t in plan_recompute(wc, families={"reuse"})] == ["reuse"]


def test_execute_publishes_atomically_under_new_generation(wc):
    _seed(wc)
    _stale_all(wc)
    rev_before = wc.revision
    out = execute_recompute(
        wc, plan_recompute(wc), executors={
            "critique": _ok_executor, "reuse": _ok_executor},
        turn_id="t1",
        live_anchor=lambda dim, ref: {"mapspec": "17", "data": "rev-1"}.get(dim, ""))
    assert len(out.recomputed) == len(wc.derived_findings)
    assert out.failed == [] and out.changed
    for f in wc.derived_findings:
        assert f.status == "current"
        assert f.generation > rev_before
        assert f.stale_reasons == []
        assert f.digest.startswith("new-")
    receipts = [r for r in wc.revalidations if r.kind == KIND_RECOMPUTE]
    assert len(receipts) == len(out.recomputed)
    assert all(r.verdict == "restored" for r in receipts)


def test_executor_exception_keeps_finding_stale(wc):
    _seed(wc, reuse=False)
    _stale_all(wc)

    def boom(w, task, *, ctx):
        raise RuntimeError("critique inputs missing")

    out = execute_recompute(
        wc, plan_recompute(wc), executors={"critique": boom}, turn_id="t")
    assert out.recomputed == []
    assert out.failed and out.failed[0].startswith(
        wc.derived_findings[0].finding_id + ":executor_error")
    assert wc.derived_findings[0].status == "stale"
    assert wc.revalidations[-1].verdict == "rejected"
    assert "executor_error" in wc.revalidations[-1].reject_reason


def test_executor_refusal_keeps_finding_stale(wc):
    _seed(wc, reuse=False)
    _stale_all(wc)
    out = execute_recompute(
        wc, plan_recompute(wc),
        executors={"critique": lambda w, t, *, ctx: RecomputeResult(
            ok=False, reason="chapter_missing")},
        turn_id="t")
    assert out.recomputed == []
    assert wc.derived_findings[0].status == "stale"
    assert wc.revalidations[-1].reject_reason == "chapter_missing"


def test_concurrent_world_advance_refuses_old_generation(wc):
    """The DoD race: the user edits the map while a recompute is in
    flight — the result derived from the pre-edit anchors must never
    publish."""
    _seed(wc, reuse=False)
    _stale_all(wc)
    obs_now = _obs(rev="99")  # the world moved *during* the recompute

    def live_anchor(dim, ref):
        if dim == "mapspec":
            return obs_now.mapspec_revision
        if dim == "data":
            for ds in obs_now.datasets:
                if ds.ref_id == ref:
                    return ds.content_revision
        return ""

    out = execute_recompute(
        wc, plan_recompute(wc), executors={"critique": _ok_executor},
        turn_id="t", live_anchor=live_anchor)
    assert out.recomputed == []
    assert out.failed[0].endswith("superseded_by_newer_world")
    assert wc.derived_findings[0].status == "stale"  # never current
    receipt = wc.revalidations[-1]
    assert receipt.verdict == "rejected"
    assert "superseded_by_newer_world" in receipt.reject_reason


def test_subject_gone_supersedes_instead_of_zombie_stale(wc):
    _seed(wc, n_critique=0, reuse=True)
    _stale_all(wc)
    out = execute_recompute(
        wc, plan_recompute(wc),
        executors={"reuse": lambda w, t, *, ctx: RecomputeResult(
            ok=True, superseded=True, evidence=["no_candidates"])},
        turn_id="t")
    assert out.superseded == [wc.derived_findings[0].finding_id]
    assert wc.derived_findings[0].status == "superseded"


def test_current_row_is_a_no_op_not_a_recompute(wc):
    _seed(wc, reuse=False)
    called = []

    def spy(w, task, *, ctx):
        called.append(task.finding_id)
        return _ok_executor(w, task, ctx=ctx)

    out = execute_recompute(
        wc, plan_recompute(wc), executors={"critique": spy}, turn_id="t")
    assert called == [] and out.recomputed == []
    assert all(f.status == "current" for f in wc.derived_findings)


def test_unknown_executor_family_is_reported_not_crashed(wc):
    _seed(wc, n_critique=0, reuse=True)
    _stale_all(wc)
    out = execute_recompute(wc, plan_recompute(wc), executors={}, turn_id="t")
    assert out.failed[0].endswith("no_executor")
    assert wc.derived_findings[0].status == "stale"


def test_transient_refusal_never_persists_a_receipt(wc):
    """Recurring environment conditions (deferred/empty reuse fetch) must
    not flood the shared persisted receipt ring — the rejection stays
    in-memory only."""
    _seed(wc, n_critique=0, reuse=True)
    _stale_all(wc)
    ring_before = len(wc.revalidations)
    for _ in range(5):
        out = execute_recompute(
            wc, plan_recompute(wc),
            executors={"reuse": lambda w, t, *, ctx: RecomputeResult(
                ok=False, reason="reuse_fetch_empty", transient=True)},
            turn_id="t")
        assert out.recomputed == []
    assert wc.derived_findings[0].status == "stale"
    assert len(wc.revalidations) == ring_before  # nothing persisted
    assert out.failed[0].endswith("reuse_fetch_empty")


def test_store_cas_is_the_second_fence(wc_store, wc):
    """A recompute commit that loses the CAS race is rebased onto the
    stored winner — it can never overwrite a concurrent writer's state."""
    wc.mission_id = "msn-race0001"
    _seed(wc, reuse=False)
    _stale_all(wc)
    wc_store.save(wc)

    # Concurrent writer advances the stored row (e.g. another session).
    winner = wc_store.load(wc.mission_id, org_id=wc.org_id)
    winner.revision += 50
    winner.updated_turn_id = "concurrent"
    wc_store.save(winner)

    disk = wc_store.load(wc.mission_id, org_id=wc.org_id)
    out = execute_recompute(
        disk, plan_recompute(disk),
        executors={"critique": _ok_executor}, turn_id="t",
        live_anchor=lambda dim, ref: "")
    assert out.recomputed  # in-memory publish succeeded under its generation
    # The losing save (stale expected_revision) rebases onto the winner —
    # the concurrent writer's revision is preserved and its state survives,
    # while the loser's fresh recompute wins on generation identity.
    wc_store.save(disk, expected_revision=int(winner.revision) - 1)
    final = wc_store.load(wc.mission_id, org_id=wc.org_id)
    assert int(final.revision) >= int(winner.revision)
    assert final.derived_finding("df-1").status == "current"
    # The loser's fresh recompute won on generation identity (seeded gen=1).
    assert final.derived_finding("df-1").generation > 1


def test_recompute_receipts_are_deterministic_ids_in_the_ring(wc):
    _seed(wc, reuse=False)
    _stale_all(wc)
    execute_recompute(
        wc, plan_recompute(wc, families=set()), executors={}, turn_id="t")
    execute_recompute(
        wc, plan_recompute(wc),
        executors={"critique": _ok_executor}, turn_id="t",
        live_anchor=lambda d, r: "")
    ids = [r.receipt_id for r in wc.revalidations if r.kind == KIND_RECOMPUTE]
    assert len(ids) == 1 and ids[0].startswith("rtv-")
