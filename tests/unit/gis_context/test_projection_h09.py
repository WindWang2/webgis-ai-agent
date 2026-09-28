"""H09 — token-budgeted memory projection: golden determinism, budget
truncation, dependency closure, and honest stale rendering."""
from __future__ import annotations

from app.services.gis_context.memory_graph import (
    critique_edges,
    invalidate_graph,
    make_derived_finding,
    observe_facts,
)
from app.services.gis_context.observation import ContextChange, observe_session
from app.services.gis_context.projection import (
    DEFAULT_TOKEN_BUDGET,
    project_memory,
)
from app.services.gis_context.working_context import GISWorkingContext


def _obs(rev="17", ds=("ref:schools", "rev-1")):
    return observe_session(
        {"_cartographic_mutation_revision": rev},
        {"sources": {ds[0]: {"ref_id": ds[0], "content_revision": ds[1]}}},
    )


def _wc_with_graph():
    from app.services.gis_context.working_context import BasisDataset, WorkingBasis

    wc = GISWorkingContext(
        mission_id="msn-proj0001", org_id="org-1",
        basis=WorkingBasis(datasets=[BasisDataset(
            ref_id="ref:schools", content_revision="rev-1")]))
    observe_facts(wc, _obs())
    f1 = make_derived_finding(
        wc, family="critique", ref="label_collision:L1", label="label_collision",
        detail="collision ratio 0.4", digest="dg1", priority=3,
        edges=critique_edges(wc))
    wc.upsert_derived_finding(f1)
    f2 = make_derived_finding(
        wc, family="reuse", ref="reuse:project", label="项目复用判定",
        detail="exact:ref:schools", digest="dg2", priority=1,
        edges=critique_edges(wc))
    wc.upsert_derived_finding(f2)
    return wc


def test_empty_graph_projects_nothing():
    wc = GISWorkingContext(mission_id="msn-empty01", org_id="org-1")
    p = project_memory(wc)
    assert p.lines == [] and p.used_tokens == 0 and not p.truncated
    assert project_memory(None).lines == []


def test_golden_projection_is_byte_stable():
    wc = _wc_with_graph()
    golden = project_memory(wc, token_budget=DEFAULT_TOKEN_BUDGET).lines
    # Same context in → byte-identical projection out.
    wc2 = _wc_with_graph()
    assert project_memory(wc2, token_budget=DEFAULT_TOKEN_BUDGET).lines == golden
    assert len(golden) == 4
    assert all(line.startswith(("事实:", "结论:")) for line in golden)
    assert any("label_collision" in line for line in golden)
    assert any("项目复用判定" in line for line in golden)
    # Anchor summaries are part of the golden shape (the closure evidence).
    assert any("依据 mapspec@" in line for line in golden)


def test_budget_truncation_is_deterministic_and_counted():
    wc = _wc_with_graph()
    p = project_memory(wc, token_budget=25)
    assert p.truncated and p.dropped >= 1
    assert p.used_tokens <= 25
    again = project_memory(wc, token_budget=25)
    assert again.lines == p.lines and again.dropped == p.dropped


def test_dependency_closure_drops_ungrounded_findings():
    wc = _wc_with_graph()
    # Tiny budget: only the first fact line fits; the finding whose anchor
    # facts did not make the cut must not render as current.
    p = project_memory(wc, token_budget=8)
    finding_lines = [line for line in p.lines if line.startswith("结论:")]
    for line in finding_lines:
        # every rendered conclusion carries its anchor summary
        assert "依据" in line
    assert p.closure_drops or finding_lines == []


def test_stale_findings_render_as_recompute_owed_with_attribution():
    wc = _wc_with_graph()
    observe_facts(wc, _obs(rev="17", ds=("ref:schools", "rev-2")))
    invalidate_graph(wc, [ContextChange(
        kind="DATASET_VERSION_CHANGED", ref_id="ref:schools", detail="rev-1->rev-2")])
    p = project_memory(wc, token_budget=DEFAULT_TOKEN_BUDGET)
    stale_lines = [line for line in p.lines if "需重算" in line]
    assert stale_lines, "stale rows must surface as recompute-owed lines"
    assert any("DATASET_VERSION_CHANGED" in line for line in stale_lines)
    # And never as current conclusions.
    assert not any(
        line.startswith("结论:") and "需重算" not in line
        and ("label_collision" in line or "项目复用判定" in line)
        for line in p.lines)


def test_tokens_never_under_count():
    from app.services.gis_context.projection import estimate_tokens

    for text in ("a", "abcd", "abcdefgh", "x" * 97):
        assert estimate_tokens(text) >= len(text) / 4
