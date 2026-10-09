"""H09 — durable user-edit capture: provenance projection, the
presentation/semantic split, idempotency, and the kill switch."""
from __future__ import annotations

from app.services.gis_context.invalidation import apply_changes
from app.services.gis_context.observation import observe_session
from app.services.gis_context.working_context import GISWorkingContext


def _prov(*entries):
    return {"_gis_provenance": list(entries)}


_HIDE = {"seq": 1, "origin": "user", "kind": "PatchLayerPresentationIntent",
         "target": "L1", "revision": 1,
         "detail": {"visible": False, "mutation_id": "m-hide",
                    "override_kind": "presentation"}}
_OPACITY = {"seq": 2, "origin": "user", "kind": "PatchLayerPresentationIntent",
            "target": "L2", "revision": 2,
            "detail": {"opacity": 0.4, "mutation_id": "m-op",
                       "override_kind": "presentation"}}
_SHOW = {"seq": 3, "origin": "user", "kind": "PatchLayerPresentationIntent",
         "target": "L2", "revision": 3,
         "detail": {"visible": True, "mutation_id": "m-show",
                    "override_kind": "presentation"}}
_RESTYLE = {"seq": 4, "origin": "user", "kind": "PatchLayerStyleIntent",
            "target": "L3", "revision": 4,
            "detail": {"mutation_id": "m-style", "override_kind": "presentation"}}
_REORDER = {"seq": 5, "origin": "user", "kind": "ReorderLayersIntent",
            "target": None, "revision": 5, "summary": "ReorderLayersIntent",
            "detail": {"mutation_id": "m-order", "override_kind": "semantic"}}
_COMPONENT = {"seq": 6, "origin": "user", "kind": "PatchComponentIntent",
              "target": "legend", "revision": 6,
              "detail": {"mutation_id": "m-comp", "override_kind": "presentation"}}
_SEMANTIC = {"seq": 7, "origin": "user", "kind": "UpsertLayerIntent",
             "target": "L9", "revision": 7, "summary": "UpsertLayerIntent",
             "detail": {"mutation_id": "m-sem", "override_kind": "semantic"}}
_AGENT = {"seq": 8, "origin": "agent", "kind": "PatchLayerStyleIntent",
          "target": "L1", "revision": 8,
          "detail": {"mutation_id": "m-agent", "override_kind": "semantic"}}


def _wc():
    return GISWorkingContext(mission_id="msn-edits0001", org_id="org-1")


def test_projection_covers_all_durable_kinds_and_skips_hide_and_agent():
    obs = observe_session(_prov(_HIDE, _OPACITY, _SHOW, _RESTYLE, _REORDER,
                                _COMPONENT, _SEMANTIC, _AGENT), {})
    kinds = {e.kind for e in obs.user_edits}
    assert kinds == {"opacity", "show", "restyle", "reorder", "component", "semantic"}
    assert "hide" not in kinds  # the user_hidden_layers path owns hide
    assert all(e.op_id != "m-agent" for e in obs.user_edits)  # origin=user only
    by_op = {e.op_id: e for e in obs.user_edits}
    assert by_op["m-op"].detail.startswith("opacity=")
    assert by_op["m-show"].detail == "visible=true"
    # Presentation never invalidates; semantic mutations do.
    assert not by_op["m-op"].analysis_affecting
    assert not by_op["m-style"].analysis_affecting
    assert not by_op["m-comp"].analysis_affecting
    assert by_op["m-order"].analysis_affecting
    assert by_op["m-sem"].analysis_affecting
    # hide still reaches the dedicated channel with its op id.
    assert obs.user_hidden_layers == ["L1"]
    assert obs.user_edit_ops == {"L1": "m-hide"}


def test_apply_records_edits_with_detail_and_marks_semantic_change(wc):
    obs = observe_session(_prov(_OPACITY, _REORDER), {})
    outcome = apply_changes(wc, [], obs=obs, turn_id="t1")
    kinds = {e.kind: e for e in wc.user_edits}
    assert set(kinds) == {"opacity", "reorder"}
    assert kinds["opacity"].detail.startswith("opacity=")
    assert kinds["opacity"].op_id == "m-op"
    assert "MAPSPEC_SEMANTIC_CHANGED" in outcome.changes
    assert outcome.recorded_edits == 2


def test_presentation_edits_never_invalidate_findings_or_decisions(wc):
    from app.services.gis_context.working_context import DecisionRecord, FindingRef

    wc.findings.append(FindingRef(claim_id="c1", status="supported",
                                  basis_revision=wc.revision))
    wc.accepted_assumptions.append(DecisionRecord(
        text="小学集中主城", turn_id="t", basis_revision=wc.revision))
    obs = observe_session(_prov(_OPACITY, _SHOW, _RESTYLE, _COMPONENT), {})
    outcome = apply_changes(wc, [], obs=obs, turn_id="t")
    assert wc.findings[0].status == "supported"
    assert wc.findings[0].stale_reasons == []
    assert wc.accepted_assumptions[0].stale_basis is False
    assert "MAPSPEC_SEMANTIC_CHANGED" not in outcome.changes
    assert wc.stale == {}


def test_reobserved_provenance_is_read_mostly(wc):
    """A replayed delivery (same mutation_id) must never turn a read-mostly
    turn into a write — idempotency is a transition discipline, not just a
    dedupe cosmetic."""
    obs = observe_session(_prov(_OPACITY, _REORDER), {})
    first = apply_changes(wc, [], obs=obs, turn_id="t1")
    assert first.recorded_edits == 2
    rev_after_first = wc.revision
    second = apply_changes(wc, [], obs=obs, turn_id="t2")
    assert second.recorded_edits == 0
    assert "MAPSPEC_SEMANTIC_CHANGED" not in second.changes
    assert wc.revision == rev_after_first  # no phantom transition
    assert len(wc.user_edits) == 2


def test_new_user_edit_turn_bumps_revision_and_updated_turn_id(wc):
    """A genuinely new user edit is a transition: the CAS token must
    advance (and updated_turn_id move) or the store's revision CAS lets a
    same-revision concurrent writer overwrite the edit record wholesale —
    no bump, no rebase, first writer's edits lost."""
    rev0 = wc.revision
    obs = observe_session(_prov(_OPACITY, _REORDER), {})
    outcome = apply_changes(wc, [], obs=obs, turn_id="t-edit")
    assert outcome.recorded_edits == 2          # presentation + semantic
    assert wc.revision == rev0 + 1              # one bump per transition
    assert wc.updated_turn_id == "t-edit"


def test_reobserved_hide_does_not_bump_revision(wc):
    """The read-mostly rule holds on the (pre-H09) hide channel too: a
    replayed hide keeps the revision and the last-writer turn id frozen."""
    obs = observe_session(_prov(_HIDE), {})
    apply_changes(wc, [], obs=obs, turn_id="t1")
    rev, turn = wc.revision, wc.updated_turn_id
    outcome = apply_changes(wc, [], obs=obs, turn_id="t2")
    assert outcome.recorded_edits == 0
    assert wc.revision == rev and wc.updated_turn_id == turn


def test_no_op_id_edits_are_first_wins_per_layer_and_kind(wc):
    detail = dict(_OPACITY["detail"])          # keep the opacity payload…
    detail.pop("mutation_id", None)            # …but no cross-replica id
    legacy = dict(_OPACITY, detail=detail)
    obs = observe_session(_prov(legacy), {})
    apply_changes(wc, [], obs=obs, turn_id="t1")
    apply_changes(wc, [], obs=obs, turn_id="t2")
    assert len([e for e in wc.user_edits if e.kind == "opacity"]) == 1


def test_hide_path_unchanged_under_memory_graph_flag_off(wc, monkeypatch):
    monkeypatch.setenv("GIS_CONTEXT_MEMORY_GRAPH", "0")
    obs = observe_session(_prov(_HIDE, _REORDER), {})
    outcome = apply_changes(wc, [], obs=obs, turn_id="t1")
    kinds = {e.kind for e in wc.user_edits}
    assert kinds == {"hide"}  # v2 parity: hide only
    assert "MAPSPEC_SEMANTIC_CHANGED" not in outcome.changes
    monkeypatch.setenv("GIS_CONTEXT_MEMORY_GRAPH", "1")
    obs = observe_session(_prov(_HIDE, _REORDER), {})
    apply_changes(wc, [], obs=obs, turn_id="t2")
    kinds = {e.kind for e in wc.user_edits}
    assert kinds == {"hide", "reorder"}


def test_edit_bound_is_respected(wc):
    from app.services.gis_context.working_context import MAX_USER_EDITS

    for i in range(MAX_USER_EDITS + 4):
        entry = {"seq": i, "origin": "user", "kind": "ReorderLayersIntent",
                 "target": None, "revision": i,
                 "detail": {"mutation_id": f"m-{i}", "override_kind": "semantic"}}
        obs = observe_session(_prov(entry), {})
        apply_changes(wc, [], obs=obs, turn_id=f"t{i}")
    assert len(wc.user_edits) == MAX_USER_EDITS
