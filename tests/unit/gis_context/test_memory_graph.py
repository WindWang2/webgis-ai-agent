"""H09 — memory graph: durable fact capture, dependency-precise
invalidation, anchoring discipline, and bounds."""
from __future__ import annotations

from app.services.gis_context.memory_graph import (
    CRITIQUE_CODE_PREFIXES,
    FactDrift,
    critique_edges,
    dataset_token,
    invalidate_graph,
    make_derived_finding,
    mapspec_token,
    observe_facts,
    reuse_edges,
)
from app.services.gis_context.observation import (
    ContextChange,
    observe_session,
)
from app.services.gis_context.working_context import (
    MAX_DERIVED_FINDINGS,
    FACT_KINDS,
    DerivedFinding,
)


def _obs(rev="17", ds=("ref:schools", "rev-1"), **kw):
    state = {"_cartographic_mutation_revision": rev} if rev else {}
    mapspec = {
        "view": {"center": [104.06, 30.57], "zoom": 10.0},
        "layers": [{"id": "L1", "type": "fill"}],
        "sources": ({ds[0]: {"ref_id": ds[0], "content_revision": ds[1]}} if ds else {}),
    }
    return observe_session(state, mapspec, **kw)


def _finding(wc, *, ref="label_collision:L1", edges=None, family="critique",
             status="current", generation=None):
    df = make_derived_finding(
        wc, family=family, ref=ref, label=ref.split(":")[0], digest="dg",
        priority=2, edges=edges if edges is not None else critique_edges(wc))
    df.status = status
    if generation is not None:
        df.generation = generation
    wc.upsert_derived_finding(df)
    return df


def test_capture_first_sighting_is_learning_not_drift(wc):
    drifts = observe_facts(wc, _obs())
    assert drifts == []
    assert dataset_token(wc, "ref:schools") == "rev-1"
    assert mapspec_token(wc) == "17"


def test_capture_is_idempotent_and_drifts_only_on_known_advance(wc):
    observe_facts(wc, _obs())
    # Same world again → no drift, no duplicate facts.
    assert observe_facts(wc, _obs()) == []
    assert len(wc.facts) == 2
    # Dataset advances → exactly one drift with the token transition.
    drifts = observe_facts(wc, _obs(rev="17", ds=("ref:schools", "rev-2")))
    assert drifts == [FactDrift(kind="dataset", ref="ref:schools",
                                old_token="rev-1", new_token="rev-2")]
    assert dataset_token(wc, "ref:schools") == "rev-2"


def test_perception_never_enters_the_graph(wc):
    """Viewport/hover are ephemeral by construction: an observation that
    carries *only* viewport movement produces no facts and no edits."""
    obs = observe_session({"viewport": {"bounds": [1.0, 2.0, 3.0, 4.0]}}, {})
    assert observe_facts(wc, obs) == []
    assert wc.facts == []
    assert obs.user_edits == []
    # The closed vocabulary is the second gate — no perception kind exists.
    assert "perception" not in FACT_KINDS and "viewport" not in FACT_KINDS


def test_unknown_token_never_overwrites_known(wc):
    observe_facts(wc, _obs())
    wc.upsert_fact(kind="dataset", ref="ref:schools", token="")  # ignored
    assert dataset_token(wc, "ref:schools") == "rev-1"
    drifts = observe_facts(wc, _obs(rev="17", ds=("ref:schools", "")))
    assert drifts == [] and dataset_token(wc, "ref:schools") == "rev-1"


def test_dataset_change_stales_only_findings_anchored_to_that_dataset(wc):
    observe_facts(wc, _obs())
    wc.basis.datasets = list(wc.basis.datasets)  # fixture basis already has the dataset
    anchored = _finding(wc, ref="label_collision:L1")  # critique_edges → data edge
    # A finding with no data edge (basis.aoi only) must survive.
    from app.services.gis_context.working_context import DependencyEdge

    bystander = _finding(wc, ref="blank_map_risk:view1", edges=[
        DependencyEdge(dim="basis.aoi", anchor=str(wc.revision))])
    observe_facts(wc, _obs(rev="17", ds=("ref:schools", "rev-9")))
    hits = invalidate_graph(wc, [ContextChange(
        kind="DATASET_VERSION_CHANGED", ref_id="ref:schools", detail="rev-1->rev-9")])
    assert [h.finding_id for h in hits] == [anchored.finding_id]
    assert anchored.status == "stale"
    assert "DATASET_VERSION_CHANGED:rev-1->rev-9" in anchored.stale_reasons
    assert bystander.status == "current"


def test_other_dataset_change_does_not_touch_anchored_finding(wc):
    observe_facts(wc, _obs())
    anchored = _finding(wc)
    hits = invalidate_graph(wc, [ContextChange(
        kind="DATASET_VERSION_CHANGED", ref_id="ref:other", detail="x")])
    assert hits == [] and anchored.status == "current"


def test_mapspec_semantic_change_stales_mapspec_anchored_findings(wc):
    observe_facts(wc, _obs())
    anchored = _finding(wc)
    observe_facts(wc, _obs(rev="18"))
    hits = invalidate_graph(wc, [ContextChange(kind="MAPSPEC_SEMANTIC_CHANGED")])
    assert [h.finding_id for h in hits] == [anchored.finding_id]
    assert anchored.status == "stale"


def test_basis_dim_invalidation_is_generation_scoped(wc):
    from app.services.gis_context.working_context import DependencyEdge

    observe_facts(wc, _obs())
    old = _finding(wc, ref="blank_map_risk:v1", edges=[
        DependencyEdge(dim="basis.aoi", anchor="1")], generation=1)
    # A finding derived under the revision the change lands on is current
    # (the change is not "after" its derivation).
    wc.revision = 5
    fresh = _finding(wc, ref="blank_map_risk:v2", edges=[
        DependencyEdge(dim="basis.aoi", anchor="5")], generation=5)
    hits = invalidate_graph(
        wc, [ContextChange(kind="AOI_CHANGED", detail="drift")],
        at_revision=5)
    assert [h.finding_id for h in hits] == [old.finding_id]
    assert old.status == "stale" and fresh.status == "current"


def test_unedged_derived_finding_is_fail_closed(wc):
    rogue = DerivedFinding(finding_id="df-rogue", family="critique", ref="x:1")
    wc.upsert_derived_finding(rogue)
    hits = invalidate_graph(wc, [ContextChange(kind="CRS_CHANGED", detail="e")])
    assert [h.finding_id for h in hits] == ["df-rogue"]
    assert rogue.status == "stale"


def test_stale_rows_never_re_stale_and_superseded_stay_terminal(wc):
    observe_facts(wc, _obs())
    stale_row = _finding(wc, status="stale")
    dead_row = _finding(wc, ref="export_component_missing:v1", status="superseded")
    observe_facts(wc, _obs(rev="17", ds=("ref:schools", "rev-7")))
    hits = invalidate_graph(wc, [ContextChange(
        kind="DATASET_VERSION_CHANGED", ref_id="ref:schools", detail="d")])
    assert hits == []  # stale/superseded are not re-touched by the walk
    assert stale_row.status == "stale" and dead_row.status == "superseded"


def test_recompute_reanchors_finding_under_new_generation(wc):
    observe_facts(wc, _obs())
    df = _finding(wc)
    old_edges = [(e.dim, e.ref, e.anchor) for e in df.depends_on]
    observe_facts(wc, _obs(rev="17", ds=("ref:schools", "rev-5")))
    invalidate_graph(wc, [ContextChange(
        kind="DATASET_VERSION_CHANGED", ref_id="ref:schools", detail="d")])
    assert df.status == "stale"
    # A successful recompute publishes under the new generation with fresh
    # anchors (the engine path is exercised in test_recompute_h09).
    wc.revision += 1
    fresh = make_derived_finding(
        wc, family="critique", ref=df.ref, label=df.label, digest="dg2",
        priority=2, edges=critique_edges(wc))
    fresh.finding_id = df.finding_id
    wc.upsert_derived_finding(fresh)
    stored = wc.derived_finding(df.finding_id)
    assert stored.status == "current"
    assert stored.generation == wc.revision
    assert [(e.dim, e.ref, e.anchor) for e in stored.depends_on] != old_edges
    assert dataset_token(wc, "ref:schools") == "rev-5"
    assert any(e.dim == "data" and e.anchor == "rev-5" for e in stored.depends_on)


def test_derived_finding_bound_evicts_stale_first(wc):
    for i in range(MAX_DERIVED_FINDINGS):
        _finding(wc, ref=f"blank_map_risk:v{i}")
    fresh = _finding(wc, ref="label_collision:keep", status="current")
    stale = _finding(wc, ref="label_collision:dead", status="stale")
    _finding(wc, ref="label_collision:overflow")  # bound hit → eviction
    assert wc.derived_finding(fresh.finding_id) is not None
    assert wc.derived_finding(stale.finding_id) is None  # stale evicted first
    assert len(wc.derived_findings) == MAX_DERIVED_FINDINGS


def test_unknown_family_is_rejected_at_construction(wc):
    try:
        make_derived_finding(wc, family="vibes", ref="x", label="x", edges=[])
    except ValueError as exc:
        assert "unknown_derived_family" in str(exc)
    else:
        raise AssertionError("unknown family accepted")


def test_critique_code_vocabulary_is_closed():
    assert set(CRITIQUE_CODE_PREFIXES) == {
        "blank_map_risk", "invalid_result_bounds", "export_component_missing",
        "label_collision", "planned_observed_mismatch",
    }


def test_reuse_edges_carry_query_shaping_dims(wc):
    observe_facts(wc, _obs())
    edges = reuse_edges(wc)
    dims = [e.dim for e in edges]
    assert "data" in dims and "basis.time_period" in dims and "basis.aoi" in dims
    data_edge = next(e for e in edges if e.dim == "data")
    assert data_edge.ref == "ref:schools" and data_edge.anchor == "rev-1"


def test_graph_walk_is_bounded_work(wc):
    """Synthetic bound: the worst-case per-turn walk (full graph, maximum
    change batch) stays orders of magnitude under a generous, machine-
    stable threshold — the walk is O(changes × findings × edges), all
    hard-capped."""
    import time

    observe_facts(wc, _obs())
    for i in range(8):  # MAX_DERIVED_FINDINGS
        _finding(wc, ref=f"label_collision:L{i}")
    changes = [
        ContextChange(kind="DATASET_VERSION_CHANGED", ref_id=f"ref:ds{i}")
        for i in range(12)
    ] + [ContextChange(kind=k) for k in (
        "AOI_CHANGED", "CRS_CHANGED", "MEASURE_CHANGED",
        "TIME_PERIOD_CHANGED", "PRODUCT_GOAL_CHANGED", "MAPSPEC_SEMANTIC_CHANGED")]
    start = time.perf_counter()
    for _ in range(50):  # ~50 turns of worst-case work
        for f in wc.derived_findings:
            f.status = "current"
            f.stale_reasons = []
        invalidate_graph(wc, changes)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"graph walk unbounded: {elapsed:.3f}s for 50 turns"
