"""Behavior checks for previously unmapped review findings."""
import io
import math

import pytest
from PIL import Image


def test_pdf_invalid_legend_color_still_exports():
    from app.lib.cartography.pdf_renderer import generate_map_pdf
    image = io.BytesIO()
    Image.new("RGB", (16, 16)).save(image, format="PNG")
    result = generate_map_pdf(image.getvalue(), legend_items=[{"label": "one", "color": "invalid-color"}])
    assert result.startswith(b"%PDF")


def test_dot_density_discloses_rejection_sampling_shortfall():
    from app.lib.cartography.dot_density import generate_dot_density_features
    feature = {"type": "Feature", "properties": {"pop": 10}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [10, 10], [10, 10.00001], [0, 0.00001], [0, 0]]],
    }}
    result = generate_dot_density_features([feature], value_field="pop", unit_value=1)
    meta = result["__dot_density_meta"]
    assert meta["under_placed"]
    assert meta["placement_shortfall"] == 10 - len(result["features"])
    assert not meta["truncated"]


def test_story_camera_respects_tall_extent_and_aspect():
    from app.lib.storymap.camera_planner import plan_camera_for_bbox
    camera = plan_camera_for_bbox([0, -40, 5, -2], "macro_situation", aspect=2)
    assert camera["zoom"] <= math.log2(180 / (38 * 2)) + 1 + 0.0001
    assert camera["center"] == [2.5, -21.0]
    with pytest.raises(ValueError, match="aspect"):
        plan_camera_for_bbox([0, 0, 1, 1], "macro_situation", aspect=0)


def test_harness_predicate_failure_cannot_grant_credit():
    from app.lib.harness.pi_agent_harness import PiAgentHarness
    def unavailable(result):
        raise RuntimeError("unavailable")
    harness = PiAgentHarness(result_denies_credit=unavailable)
    assert harness._result_denies_credit({"success": True})


def test_revision_regresses_when_any_counter_moves_back():
    from app.services.gis_situation.contract import SituationRevision
    old = SituationRevision(mutation_revision=1, observation_sequence=10)
    assert not SituationRevision(mutation_revision=2, observation_sequence=9).ge(old)
    assert SituationRevision(mutation_revision=2, observation_sequence=10).ge(old)


def test_claim_lookup_cannot_find_another_session_same_tenant():
    from app.services.gis_context.hotpath import _find_claim_store
    from app.services.gis_harness.hotpath_convergence.session_ctx import get_turn_context
    class Store:
        def get_claim(self, claim_id):
            return object() if claim_id == "claim-local" else None
    ctx = get_turn_context("root-claim-owner", "org-review")
    ctx.claim_store = Store()
    try:
        assert _find_claim_store("claim-local", session_id="root-claim-owner", tenant_id="org-review") is ctx.claim_store
        assert _find_claim_store("claim-local", session_id="root-claim-other", tenant_id="org-review") is None
    finally:
        ctx.claim_store = None


def test_legend_field_mismatch_is_reported_once():
    from app.lib.cartography.semantic_checks import CartographyReport, _check_thematic_consistency
    report = CartographyReport()
    layer = {
        'legend_spec': {'type': 'categorical', 'field': 'legend_field', 'entries': []},
        'paint': {'fill-color': {'method': 'match', 'field': 'paint_field', 'stops': []}},
    }
    _check_thematic_consistency(report, 'L', 'S', layer, None)
    mismatches = [finding for finding in report.findings if finding.check == 'LEGEND_STYLE_EQUIVALENCE' and 'differs' in finding.message]
    assert len(mismatches) == 1


def test_mixed_numeric_stops_do_not_abort_cartographic_review():
    from app.lib.cartography.semantic_checks import CartographyReport, _check_layer_paint_fields
    report = CartographyReport()
    layer = {'paint': {'fill-color': {'method': 'step', 'field': 'value', 'stops': [[100, '#000000'], ['bad', '#ffffff']]}}}
    profile = {'fields': {'value': {'type': 'number', 'min': 0, 'max': 10}}}
    _check_layer_paint_fields(report, layer, 'L', 'S', profile, {'S'})
    assert any(finding.check == 'STOPS_DATA_RANGE' for finding in report.findings)
