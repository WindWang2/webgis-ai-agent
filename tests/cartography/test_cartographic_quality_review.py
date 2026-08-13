"""Acceptance tests for the structured cartographic quality review.

These tests exercise public seams only: semantic review, the bounded repair
composer, lifecycle results, and the harness quality gate.
"""

import copy
import shutil
import uuid

import pytest

from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
from app.lib.cartography.quality_loop import (
    cartographic_projection,
    cartographic_fingerprint,
    review_cartography,
    review_and_repair_cartography,
)
from app.lib.harness.evaluator import HarnessEvaluator
from app.lib.harness.evidence import CartographicReviewEvidence
from app.lib.harness.pi_agent_harness import PiAgentHarness
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager
from app.api.routes.chat import (
    CartographicRuntimeObservationRequest,
    _record_frontend_cartographic_observation,
    push_cartographic_runtime_observation,
)


@pytest.fixture
async def quality_session():
    session_id = f"quality-{uuid.uuid4().hex[:10]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)
    shutil.rmtree(BASE_STORAGE_DIR / session_id, ignore_errors=True)


def _point_profile(*, crs="EPSG:4326", crs_status="explicit"):
    return {
        "featureCount": 2,
        "geometryTypes": ["Point"],
        "bbox": [100.0, 20.0, 101.0, 21.0],
        "crs": crs,
        "crs_status": crs_status,
        "fields": {
            "value": {"type": "number", "min": 1, "max": 2, "null_count": 0}
        },
    }


def _plain_mapspec():
    return {
        "version": "1.0",
        "sources": {
            "points": {
                "type": "geojson",
                "inlineData": {"type": "FeatureCollection", "features": []},
            }
        },
        "layers": [
            {
                "id": "result",
                "source": "points",
                "type": "circle",
                "paint": {"circle-color": "#3366cc", "circle-opacity": 0.8},
            }
        ],
    }


def _explicit_crs_geojson():
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [100, 20]},
                "properties": {"value": 1},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [101, 21]},
                "properties": {"value": 2},
            },
        ],
    }


def test_review_pass_has_positive_structured_evidence():
    report = evaluate_cartography_semantics(
        _plain_mapspec(), {"points": _point_profile()}
    ).to_dict()

    assert report["status"] == "pass"
    assert report["evaluated_count"] > 0
    assert any(
        check["rule"] == "SOURCE_LAYER_REF"
        and check["status"] == "pass"
        and check["evidence_class"] == "deterministic"
        and check["evidence"] == {
            "layer_id": "result",
            "source_id": "points",
            "source_exists": True,
        }
        for check in report["checks"]
    )


def test_typed_failure_cannot_report_legacy_ok_or_zero_errors():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()
    mapspec["layers"][0]["legend_spec"] = {
        "type": "categorical",
        "field": "kind",
        "categories": [
            {"key": "a", "label": "A", "color": "not-a-color"},
            {"key": "a", "label": "A2", "color": "#3366cc"},
        ],
    }
    mapspec["layers"][0]["paint"]["circle-color"] = {
        "method": "match",
        "field": "kind",
        "cases": [["a", "#3366cc"]],
        "default": "#999999",
    }

    report = evaluate_cartography_semantics(mapspec).to_dict()

    assert report["status"] == "fail"
    assert report["ok"] is False
    assert report["error_count"] > 0
    classification = next(
        check for check in report["checks"]
        if check["rule"] == "CLASSIFICATION_INTEGRITY"
    )
    assert classification["evidence"]["colors_valid"] is False


def test_read_only_review_classifies_repairability_without_applying_patch():
    mapspec = _plain_mapspec()
    mapspec["layers"][0]["paint"]["circle-opacity"] = 9

    result = review_cartography(mapspec, {"points": _point_profile()})

    assert result.status == "failed_repairable"
    assert result.repair_count == 0
    assert result.mapspec == mapspec
    assert result.termination_reason == "review_only"


def test_review_with_no_applicable_evidence_is_not_evaluated_not_passed():
    report = evaluate_cartography_semantics(
        {"version": "1.0", "sources": {}, "layers": []}
    ).to_dict()

    assert report["status"] == "not_evaluated"
    assert report["evaluated_count"] == 0
    assert report["passed"] is False


def test_unknown_crs_is_truthful_warning_not_epsg_4326_pass():
    profile = _point_profile(crs=None, crs_status="unknown")
    report = evaluate_cartography_semantics(
        _plain_mapspec(), {"points": profile}
    ).to_dict()

    crs_check = next(c for c in report["checks"] if c["rule"] == "CRS_EVIDENCE")
    assert crs_check["status"] == "not_evaluated"
    assert crs_check["evidence"] == {
        "source_id": "points",
        "crs": None,
        "crs_status": "unknown",
    }
    assert report["status"] == "warning"
    assert report["complete"] is False
    assert report["passed"] is False

    loop = review_cartography(_plain_mapspec(), {"points": profile})
    assert loop.status == "partial"
    assert loop.termination_reason == "review_only"


def test_visual_overlap_is_explicitly_not_evaluated_and_not_a_quality_oracle():
    report = evaluate_cartography_semantics(
        _plain_mapspec(), {"points": _point_profile()}
    ).to_dict()

    visual = next(c for c in report["checks"] if c["rule"] == "VISUAL_OVERLAP")
    assert visual["status"] == "not_evaluated"
    assert visual["evidence_class"] == "visual"
    assert report["status"] == "pass"


def test_profile_only_source_cannot_pass_without_runtime_carrier():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"].pop("inlineData")
    mapspec["sources"]["points"]["profile"] = _point_profile()

    report = evaluate_cartography_semantics(mapspec).to_dict()

    address = next(c for c in report["checks"] if c["rule"] == "SOURCE_ADDRESSABILITY")
    assert address["status"] == "fail"
    assert report["passed"] is False


def test_default_visibility_is_explicitly_attributed_to_runtime_default():
    report = evaluate_cartography_semantics(
        _plain_mapspec(), {"points": _point_profile()}
    ).to_dict()

    visibility = next(c for c in report["checks"] if c["rule"] == "RESULT_VISIBILITY")
    assert visibility["status"] == "pass"
    assert visibility["evidence"]["visibility_source"] == "maplibre_default_visible"


def test_unknown_geometry_and_native_expression_are_not_evaluated_as_passes():
    mapspec = _plain_mapspec()
    mapspec["layers"][0]["paint"]["circle-color"] = ["get", "value"]
    profile = _point_profile()
    profile["geometryTypes"] = ["GeometryCollection"]

    report = evaluate_cartography_semantics(mapspec, {"points": profile}).to_dict()

    geometry = next(c for c in report["checks"] if c["rule"] == "GEOMETRY_LAYER_TYPE")
    expression = next(c for c in report["checks"] if c["rule"] == "STYLE_EXPRESSION_SUPPORT")
    assert geometry["status"] == "not_evaluated"
    assert expression["status"] == "not_evaluated"
    assert report["passed"] is False


def test_descriptor_unknown_fields_do_not_become_false_missing_field_failure():
    mapspec = _plain_mapspec()
    mapspec["layers"][0]["paint"]["circle-color"] = {
        "method": "interpolate", "field": "server_only",
        "stops": [[1, "#111111"], [2, "#eeeeee"]],
    }
    mapspec["layers"][0]["legend_spec"] = {
        "type": "continuous", "field": "server_only", "min": 1, "max": 2,
        "palette_colors": ["#111111", "#eeeeee"],
    }
    profile = _point_profile()
    profile["fields"] = {}
    profile["fields_status"] = "unknown"

    report = evaluate_cartography_semantics(mapspec, {"points": profile}).to_dict()

    field = next(c for c in report["checks"] if c["rule"] == "PAINT_FIELD_EXISTS")
    assert field["status"] == "not_evaluated"
    assert report["status"] == "warning"


def test_maplibre_rgba_color_is_valid_deterministic_style_evidence():
    mapspec = _plain_mapspec()
    rgba = "rgba(0,242,255,0.3)"
    mapspec["layers"][0]["paint"] = {
        "color": {"method": "match", "field": "kind", "cases": [["a", rgba]], "default": rgba}
    }
    mapspec["layers"][0]["legend_spec"] = {
        "type": "categorical",
        "field": "kind",
        "categories": [{"key": "a", "label": "A", "color": rgba}],
    }
    profile = _point_profile()
    profile["fields"]["kind"] = {
        "type": "string", "sampleValues": ["a"], "null_count": 0,
    }

    report = evaluate_cartography_semantics(mapspec, {"points": profile}).to_dict()

    colors = [
        check for check in report["checks"]
        if check["rule"] == "CLASSIFICATION_INTEGRITY"
    ]
    assert colors and all(check["status"] == "pass" for check in colors)


def test_raster_requires_truthful_bounds_independent_of_profile_bbox():
    mapspec = {
        "version": "1.0",
        "sources": {
            "r": {
                "type": "raster",
                "imageRef": "ref:raster/result",
                "profile": {
                    "bbox": [100, 20, 101, 21],
                    "crs": "EPSG:4326",
                    "crs_status": "explicit",
                },
            }
        },
        "layers": [{"id": "raster", "source": "r", "type": "raster"}],
    }

    report = evaluate_cartography_semantics(mapspec).to_dict()

    bounds = next(c for c in report["checks"] if c["rule"] == "RASTER_BOUNDS_VALIDITY")
    assert bounds["status"] == "fail"
    assert report["passed"] is False


def test_cartographic_generation_fingerprint_covers_filter_time_and_source_identity():
    base = _plain_mapspec()
    fingerprints = {cartographic_fingerprint(base)}
    for mutation in ("filter", "time", "data"):
        candidate = copy.deepcopy(base)
        if mutation == "filter":
            candidate["layers"][0]["filter"] = ["==", "kind", "a"]
        elif mutation == "time":
            candidate["time"] = {"field": "observed_at", "start": "2026-01-01"}
        else:
            candidate["sources"]["points"]["data_fingerprint"] = "data-sha256:new"
        fingerprints.add(cartographic_fingerprint(candidate))
    assert len(fingerprints) == 4


def test_safe_opacity_repair_is_bounded_and_does_not_mutate_input_or_data():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["inlineData"] = {
        "type": "FeatureCollection",
        "features": [{"id": "source-must-not-change"}],
    }
    mapspec["layers"][0]["paint"]["circle-opacity"] = float("inf")

    result = review_and_repair_cartography(
        mapspec, {"points": _point_profile()}, max_iterations=2
    )

    assert result.status == "passed"
    assert result.repair_count == 1
    assert result.attempts[0]["repairs"] == [
        {
            "operation": "normalize_opacity",
            "layer_id": "result",
            "property": "circle-opacity",
            "value": 1.0,
        }
    ]
    assert result.mapspec["layers"][0]["paint"]["circle-opacity"] == 1.0
    assert mapspec["layers"][0]["paint"]["circle-opacity"] == float("inf")
    assert result.mapspec["sources"]["points"]["inlineData"] == mapspec["sources"]["points"]["inlineData"]
    assert result.mapspec["sources"]["points"]["inlineData"] is mapspec["sources"]["points"]["inlineData"]
    assert result.counters["full_data_loads"] == 0
    assert result.counters["repair_attempts"] == 1


def test_missing_legend_is_not_synthesized_from_paint():
    mapspec = _plain_mapspec()
    mapspec["layers"][0]["paint"]["circle-color"] = {
        "method": "step",
        "field": "value",
        "default": "#111111",
        "stops": [[2, "#eeeeee"]],
    }

    result = review_and_repair_cartography(
        mapspec, {"points": _point_profile()}
    )

    assert result.status == "failed_unrepairable"
    assert result.repair_count == 0
    assert "legend_spec" not in result.mapspec["layers"][0]


def test_hidden_layer_only_fails_when_result_visibility_is_explicitly_expected():
    deliberate = _plain_mapspec()
    deliberate["layers"][0]["visible"] = False
    report = evaluate_cartography_semantics(
        deliberate, {"points": _point_profile()}
    ).to_dict()
    visibility = next(c for c in report["checks"] if c["rule"] == "RESULT_VISIBILITY")
    assert visibility["status"] == "not_evaluated"
    assert report["status"] == "warning"

    expected = _plain_mapspec()
    expected["layers"][0]["visible"] = False
    expected["layers"][0]["cartographic_intent"] = {"expected_visible": True}
    repaired = review_and_repair_cartography(
        expected, {"points": _point_profile()}
    )
    assert repaired.status == "passed"
    assert repaired.mapspec["layers"][0]["visible"] is True
    assert repaired.attempts[0]["repairs"][0]["operation"] == "set_layer_visibility"


def test_unclassified_imagery_raster_does_not_require_thematic_legend():
    mapspec = {
        "version": "1.0",
        "sources": {
            "image": {
                    "type": "raster",
                    "imageRef": "ref:raster-image",
                    "bounds": [100, 20, 101, 21],
                "profile": {
                    "bbox": [100, 20, 101, 21],
                    "crs": "EPSG:4326",
                    "crs_status": "explicit",
                },
            }
        },
        "layers": [
            {
                "id": "image-result",
                "source": "image",
                "type": "raster",
                "paint": {"raster-opacity": 0.85},
            }
        ],
    }

    report = evaluate_cartography_semantics(mapspec).to_dict()

    assert report["status"] == "pass"
    assert report["profiles"] == ["raster_result"]
    assert not any(c["rule"] == "THEMATIC_LEGEND" for c in report["checks"])


def test_geographic_crs_rejects_impossible_latitude_extent():
    profile = _point_profile()
    profile["bbox"] = [100, 20, 101, 120]

    report = evaluate_cartography_semantics(
        _plain_mapspec(), {"points": profile}
    ).to_dict()

    compatibility = next(
        c for c in report["checks"] if c["rule"] == "CRS_BBOX_COMPATIBILITY"
    )
    assert compatibility["status"] == "fail"
    assert report["status"] == "fail"


def test_stale_style_is_regenerated_from_authoritative_existing_legend():
    mapspec = _plain_mapspec()
    mapspec["layers"][0]["legend_spec"] = {
        "type": "graduated",
        "field": "value",
        "breaks": [1, 2, 3],
        "palette_colors": ["#111111", "#eeeeee"],
        "labels": ["1–2", "2–3"],
    }
    mapspec["layers"][0]["paint"] = {
        "color": {
            "method": "step",
            "field": "wrong_field",
            "default": "#ff0000",
            "stops": [[2, "#00ff00"]],
        },
        "circle-opacity": 0.8,
    }

    result = review_and_repair_cartography(
        mapspec, {"points": _point_profile()}
    )

    assert result.status == "passed"
    assert result.repair_count == 1
    assert result.mapspec["layers"][0]["paint"]["color"] == {
        "method": "step",
        "field": "value",
        "default": "#111111",
        "stops": [[2.0, "#eeeeee"]],
    }


def test_repeated_identical_patch_terminates_without_looping_forever():
    mapspec = _plain_mapspec()
    mapspec["layers"][0]["paint"]["circle-opacity"] = 9

    # This port models a runtime that accepted a repair command but did not
    # converge. The loop must fingerprint the repeated patch and stop.
    result = review_and_repair_cartography(
        mapspec,
        {"points": _point_profile()},
        max_iterations=8,
        repair_executor=lambda current, _repairs: current,
    )

    assert result.status == "repair_exhausted"
    assert result.termination_reason == "repeated_failure"
    assert result.repair_count == 1


def test_stale_generation_is_superseded_before_repair_mutates_state():
    mapspec = _plain_mapspec()
    mapspec["layers"][0]["paint"]["circle-opacity"] = 9

    result = review_and_repair_cartography(
        mapspec,
        {"points": _point_profile()},
        is_current=lambda _fingerprint: False,
    )

    assert result.status == "superseded"
    assert result.repair_count == 0
    assert result.mapspec == mapspec


def test_cartographic_fingerprint_is_metadata_first():
    first = _plain_mapspec()
    second = _plain_mapspec()
    first["sources"]["points"]["inlineData"] = {
        "type": "FeatureCollection",
        "features": [{"id": 1}],
    }
    second["sources"]["points"]["inlineData"] = {
        "type": "FeatureCollection",
        "features": [{"id": i} for i in range(1000)],
    }

    assert cartographic_fingerprint(first) == cartographic_fingerprint(second)
    second["layers"][0]["paint"]["circle-color"] = "#ff0000"
    assert cartographic_fingerprint(first) != cartographic_fingerprint(second)


def test_cartographic_projection_drops_arbitrary_metadata_and_source_url_secrets():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"].update({
        "url": "https://user:secret@example.test/data?token=private",
        "metadata": {"huge": "x" * 1_000_000},
    })
    mapspec["layers"][0]["metadata"] = {"huge": "x" * 1_000_000}

    projection = cartographic_projection(mapspec)
    encoded = str(projection)

    assert len(encoded) < 20_000
    assert "private" not in encoded
    assert "secret" not in encoded
    assert "metadata" not in projection["layers"][0]
    assert "url" not in projection["sources"]["points"]


@pytest.mark.asyncio
async def test_lifecycle_applies_safe_repair_and_returns_structured_review(quality_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(quality_session, InitProjectIntent())

    result = await engine.apply_mutation(
        quality_session,
        UpsertLayerIntent(
            layer={
                "id": "result",
                "source": "points",
                "type": "circle",
                "paint": {"circle-color": "#3366cc", "circle-opacity": 4},
            },
            source_data=_explicit_crs_geojson(),
        ),
    )

    assert result.is_error is False
    assert result.mapspec["layers"][0]["paint"]["circle-opacity"] == 1.0
    assert result.mapspec_fingerprint == cartographic_fingerprint(result.mapspec)
    assert result.cartographic_review["stage"] == "desired_state"
    assert result.cartographic_review["status"] == "passed"
    assert result.cartographic_review["repair_count"] == 1


@pytest.mark.asyncio
async def test_lifecycle_execution_success_does_not_hide_quality_failure(quality_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(quality_session, InitProjectIntent())
    thematic_without_legend = {
        "id": "thematic",
        "source": "points",
        "type": "circle",
        "paint": {
            "color": {
                "method": "step",
                "field": "value",
                "default": "#111111",
                "stops": [[2, "#eeeeee"]],
            }
        },
    }

    result = await engine.apply_mutation(
        quality_session,
        UpsertLayerIntent(
            layer=thematic_without_legend,
            source_data=_explicit_crs_geojson(),
        ),
    )

    payload = result.to_dict()
    assert payload["success"] is True
    assert payload["is_compiled"] is True
    assert payload["cartographic_review"]["status"] == "failed_unrepairable"
    assert payload["cartographic_review"]["review"]["passed"] is False


def _record_mapspec_mutation(
    harness: PiAgentHarness,
    mapspec: dict,
    *,
    reported_fingerprint: str | None = None,
    reported_status: str = "passed",
    observation_seq: int = 0,
    tool_call_id: str = "call-1",
):
    fingerprint = reported_fingerprint or cartographic_fingerprint(mapspec)
    harness.record_tool_call(
        tool_call_id, "webgis_layer_upsert", {"layer": {"id": "result"}}
    )
    harness.record_tool_result(
        tool_call_id,
        "webgis_layer_upsert",
        {
            "success": True,
            "is_compiled": True,
            "mapspec_fingerprint": fingerprint,
            "runtime_observation_seq": observation_seq,
            "runtime_projection_fingerprint": "runtime-test-projection",
            # Transported evidence is deliberately forgeable in these tests;
            # the harness must never use it as its quality oracle.
            "cartographic_review": {
                "stage": "desired_state",
                "status": reported_status,
                "final_fingerprint": fingerprint,
                "review": {"status": "pass", "passed": True, "checks": []},
            },
        },
    )


@pytest.mark.asyncio
async def test_harness_recomputes_quality_instead_of_trusting_tool_pass():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()
    mapspec["layers"][0]["paint"]["circle-color"] = {
        "method": "step",
        "field": "value",
        "default": "#111111",
        "stops": [[2, "#eeeeee"]],
    }

    async def reader(session_id: str):
        return {
            "session_id": session_id,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "session_id": session_id,
                    "sequence": 1,
                    "source": "frontend",
                },
                "layers": [{"id": "result", "visible": True, "opacity": 0.8}],
            },
        }

    harness = PiAgentHarness(session_id="quality-a", cartography_state_reader=reader)
    _record_mapspec_mutation(harness, mapspec, reported_status="passed")

    result = await harness.evaluate_with_evidence()

    assert result["cartography"]["desired_status"] == "fail"
    assert result["cartography"]["status"] == "failed_unrepairable"
    assert result["cartography"]["trusted"] is True
    gate = HarnessEvaluator().evaluate_evidence(
        result, require_evaluated=False, require_cartography=True
    )
    assert gate["checks"]["CartographicQuality"]["passed"] is False
    assert gate["overall_passed"] is False


def test_harness_evaluator_rejects_untrusted_or_contradictory_quality_payload():
    forged = {
        "metrics": {},
        "evidence": [],
        "interaction": {"issued": 0},
        "cartography": {
            "status": "passed",
            "trusted": False,
            "evaluated": True,
            "passed": True,
        },
    }

    gate = HarnessEvaluator().evaluate_evidence(
        forged,
        require_evaluated=False,
        require_cartography=True,
    )

    assert gate["checks"]["CartographicQuality"]["passed"] is False
    assert gate["checks"]["CartographicQuality"]["trusted"] is False
    assert gate["overall_passed"] is False


@pytest.mark.asyncio
async def test_harness_pass_requires_new_session_owned_runtime_observation():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()

    async def reader(session_id: str):
        return {
            "session_id": session_id,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "session_id": session_id,
                    "sequence": 4,
                    "source": "frontend_runtime",
                    "mapspec_fingerprint": cartographic_fingerprint(mapspec),
                    "style_loaded": True,
                    "layers": [
                        {
                            "id": "result",
                            "visible": True,
                            "opacity": 0.8,
                            "legend_spec": None,
                            "style_converged": True,
                            "projection_fingerprint": "runtime-test-projection",
                        }
                    ],
                },
            },
        }

    harness = PiAgentHarness(session_id="quality-b", cartography_state_reader=reader)
    _record_mapspec_mutation(harness, mapspec, observation_seq=3)

    result = await harness.evaluate_with_evidence()

    assert result["cartography"]["status"] == "passed"
    assert result["cartography"]["runtime_status"] == "pass"
    assert result["cartography"]["mapspec_fingerprint"] == cartographic_fingerprint(mapspec)
    gate = HarnessEvaluator().evaluate_evidence(
        result, require_evaluated=False, require_cartography=True
    )
    assert gate["checks"]["CartographicQuality"]["passed"] is True


@pytest.mark.asyncio
async def test_matching_headless_fatal_error_is_deterministic_failure_not_visual_warning():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()
    fingerprint = cartographic_fingerprint(mapspec)

    async def reader(session_id: str):
        return {
            "session_id": session_id,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": _runtime_observation(
                    session_id, fingerprint, sequence=2, visible=True
                )
            },
        }

    harness = PiAgentHarness(session_id="headless-fatal", cartography_state_reader=reader)
    _record_mapspec_mutation(harness, mapspec, observation_seq=1)
    harness.record_tool_call("runtime-1", "webgis_runtime_validate", {})
    harness.record_tool_result(
        "runtime-1",
        "webgis_runtime_validate",
        {
            "valid": False,
            "mapspec_fingerprint": fingerprint,
            "report": {
                "mapLoaded": False,
                "fatalError": "MapLibre initialization failed",
                "pageErrors": [],
            },
            "visual_evidence": {
                "evidence_class": "heuristic", "status": "evaluated",
            },
        },
    )

    result = await harness.evaluate_with_evidence()

    fatal = next(
        check for check in result["cartography"]["checks"]
        if check["rule"] == "HEADLESS_RUNTIME_EXECUTION"
    )
    assert fatal["status"] == "fail"
    assert fatal["evidence_class"] == "deterministic"
    assert result["cartography"]["status"] == "failed_unrepairable"
    gate = HarnessEvaluator().evaluate_evidence(
        result, require_evaluated=False, require_cartography=True
    )
    assert gate["checks"]["CartographicQuality"]["passed"] is False


@pytest.mark.asyncio
async def test_harness_matches_runtime_layer_only_through_exact_result_provenance():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"].update({
        "profile": _point_profile(),
        "ref": "ref:geojson-owned-result",
    })
    mapspec["layers"][0]["provenance"] = {
        "result_ref": "ref:geojson-owned-result",
    }

    async def reader(session_id: str):
        return {
            "session_id": session_id,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "session_id": session_id,
                    "sequence": 2,
                    "source": "frontend_runtime",
                    "mapspec_fingerprint": cartographic_fingerprint(mapspec),
                    "style_loaded": True,
                    # The main HUD mounts analysis outputs under the ref identity,
                    # not the semantic MapSpec layer id.
                    "layers": [{
                        "id": "ref:geojson-owned-result",
                        "_refId": "ref:geojson-owned-result",
                        "visible": True,
                        "opacity": 0.8,
                        "style_converged": True,
                        "projection_fingerprint": "runtime-test-projection",
                    }],
                },
            },
        }

    harness = PiAgentHarness(session_id="quality-ref", cartography_state_reader=reader)
    _record_mapspec_mutation(harness, mapspec, observation_seq=1)

    result = await harness.evaluate_with_evidence()

    assert result["cartography"]["status"] == "passed"
    presence = next(
        check for check in result["cartography"]["checks"]
        if check["rule"] == "RUNTIME_RESULT_PRESENCE"
    )
    assert presence["evidence"]["matched_identity"] == "ref:geojson-owned-result"


@pytest.mark.asyncio
async def test_harness_rejects_same_named_layer_with_wrong_result_identity():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"].update({
        "profile": _point_profile(),
        "ref": "ref:geojson-expected",
    })
    mapspec["layers"][0]["provenance"] = {"result_ref": "ref:geojson-expected"}

    async def reader(session_id: str):
        return {
            "session_id": session_id,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "session_id": session_id,
                    "sequence": 2,
                    "source": "frontend_runtime",
                    "mapspec_fingerprint": cartographic_fingerprint(mapspec),
                    "style_loaded": True,
                    "layers": [{
                        # Even an equal semantic id cannot bypass conflicting ref
                        # provenance from another result.
                        "id": "result",
                        "_refId": "ref:geojson-other-session-result",
                        "name": "result",
                        "visible": True,
                        "opacity": 0.8,
                        "style_converged": True,
                    }],
                },
            },
        }

    harness = PiAgentHarness(session_id="quality-wrong-ref", cartography_state_reader=reader)
    _record_mapspec_mutation(harness, mapspec, observation_seq=1)

    result = await harness.evaluate_with_evidence()

    assert result["cartography"]["status"] == "failed_unrepairable"
    presence = next(
        check for check in result["cartography"]["checks"]
        if check["rule"] == "RUNTIME_RESULT_PRESENCE"
    )
    assert presence["status"] == "fail"
    assert presence["evidence"]["runtime_layer_present"] is False


@pytest.mark.asyncio
async def test_harness_stale_observation_and_stale_fingerprint_cannot_pass():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()

    async def stale_observation(session_id: str):
        return {
            "session_id": session_id,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "session_id": session_id,
                    "sequence": 7,
                    "source": "frontend_runtime",
                    "mapspec_fingerprint": cartographic_fingerprint(mapspec),
                    "style_loaded": True,
                    "layers": [{
                        "id": "result", "visible": True,
                        "opacity": 0.8, "style_converged": True,
                    }],
                },
            },
        }

    stale_state_harness = PiAgentHarness(
        session_id="quality-c", cartography_state_reader=stale_observation
    )
    _record_mapspec_mutation(stale_state_harness, mapspec, observation_seq=7)
    stale_state = await stale_state_harness.evaluate_with_evidence()
    assert stale_state["cartography"]["status"] == "not_evaluated"
    assert stale_state["cartography"]["termination_reason"] == "stale_runtime_observation"

    stale_fp_harness = PiAgentHarness(
        session_id="quality-c", cartography_state_reader=stale_observation
    )
    _record_mapspec_mutation(
        stale_fp_harness,
        mapspec,
        reported_fingerprint="carto-sha256:" + "0" * 64,
        observation_seq=0,
    )
    stale_fp = await stale_fp_harness.evaluate_with_evidence()
    assert stale_fp["cartography"]["status"] == "superseded"
    assert stale_fp["cartography"]["termination_reason"] == "stale_mapspec_fingerprint"


@pytest.mark.asyncio
async def test_harness_rejects_cross_session_cartographic_state():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()

    async def wrong_session(_session_id: str):
        return {"session_id": "tenant-b", "mapspec": mapspec, "map_state": {}}

    harness = PiAgentHarness(
        session_id="tenant-a", cartography_state_reader=wrong_session
    )
    _record_mapspec_mutation(harness, mapspec)

    result = await harness.evaluate_with_evidence()

    assert result["cartography"]["status"] == "failed_unrepairable"
    assert result["cartography"]["termination_reason"] == "session_mismatch"


@pytest.mark.asyncio
async def test_frontend_observation_is_bounded_metadata_and_monotonic(quality_session):
    oversized_features = [{"id": i} for i in range(500)]
    snapshot = {
        "viewport": {"center": [100, 20], "zoom": 8},
        "layers": [
            {
                "id": "result",
                "visible": True,
                "opacity": 0.8,
                "style": {"color": "#3366cc", "features": oversized_features},
                "legend_spec": {
                    "type": "categorical",
                    "field": "kind",
                    "categories": ["a"],
                    "colors": ["#3366cc"],
                },
                "source": {"type": "FeatureCollection", "features": oversized_features},
            }
        ],
    }

    await _record_frontend_cartographic_observation(quality_session, snapshot)
    await _record_frontend_cartographic_observation(quality_session, snapshot)
    state = await session_data_manager.get_map_state(quality_session)

    context = state["_cartographic_context_observation"]
    assert {key: context[key] for key in (
        "session_id", "sequence", "source", "layer_count"
    )} == {
        "session_id": quality_session,
        "sequence": 2,
        "source": "frontend_pre_turn",
        "layer_count": 1,
    }
    assert "layers" not in state
    assert "source" not in context["layers"][0]
    assert "features" not in context["layers"][0]["style"]
    assert context["layers"][0]["legend_spec"]["categories"] == ["a"]


@pytest.mark.asyncio
async def test_frontend_observation_rejects_pathological_nested_metadata(quality_session):
    nested = "leaf"
    for _ in range(20):
        nested = {"nested": nested}
    snapshot = {
        "layers": [
            {
                "id": f"layer-{index}",
                "visible": True,
                "style": nested,
            }
            for index in range(256)
        ]
    }

    await _record_frontend_cartographic_observation(quality_session, snapshot)
    state = await session_data_manager.get_map_state(quality_session)

    layers = state["_cartographic_context_observation"]["layers"]
    assert len(layers) <= 128
    assert len(str(layers)) < 262_144
    assert state["_cartographic_context_observation"]["layer_count"] == len(layers)


@pytest.mark.asyncio
async def test_stale_ack_and_user_supersession_cannot_certify_new_mapspec():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()
    current_fp = cartographic_fingerprint(mapspec)

    async def reader(session_id: str):
        return {
            "session_id": session_id,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "session_id": session_id,
                    "sequence": 2,
                    "source": "frontend",
                },
                "layers": [{"id": "result", "visible": True}],
            },
        }

    async def succeeded_ack(_session_id: str):
        return [{
            "action_id": "ma-stale",
            "status": "succeeded",
            "actual": {"confirmed": True},
        }]

    stale = PiAgentHarness(session_id="action-s", cartography_state_reader=reader)
    _record_mapspec_mutation(stale, mapspec)
    stale.record_map_action_issued(
        session_id="action-s",
        tool_call_id="call-1",
        action_id="ma-stale",
        command="LAYER_VISIBILITY_UPDATE",
        requested={"layer_id": "result", "visible": True},
        mapspec_fingerprint="carto-sha256:" + "f" * 64,
    )
    stale_result = await stale.evaluate_with_evidence(
        map_action_reader=succeeded_ack
    )
    assert stale_result["cartography"]["status"] == "superseded"
    assert stale_result["cartography"]["termination_reason"] == "stale_action_fingerprint"

    async def superseded_ack(_session_id: str):
        return [{
            "action_id": "ma-current",
            "status": "superseded",
            "actual": {"reason": "user_gesture"},
        }]

    user_wins = PiAgentHarness(session_id="action-s", cartography_state_reader=reader)
    _record_mapspec_mutation(user_wins, mapspec)
    user_wins.record_map_action_issued(
        session_id="action-s",
        tool_call_id="call-1",
        action_id="ma-current",
        command="LAYER_VISIBILITY_UPDATE",
        requested={"layer_id": "result", "visible": True},
        mapspec_fingerprint=current_fp,
    )
    superseded = await user_wins.evaluate_with_evidence(
        map_action_reader=superseded_ack
    )
    assert superseded["cartography"]["status"] == "superseded"
    assert superseded["cartography"]["termination_reason"] == "user_or_newer_intent"


@pytest.mark.asyncio
async def test_chat_token_events_do_not_trigger_cartographic_review():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()
    calls = 0

    async def reader(session_id: str):
        nonlocal calls
        calls += 1
        return {
            "session_id": session_id,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "session_id": session_id,
                    "sequence": 1,
                    "source": "frontend",
                },
                "layers": [{"id": "result", "visible": True}],
            },
        }

    harness = PiAgentHarness(session_id="token-s", cartography_state_reader=reader)
    _record_mapspec_mutation(harness, mapspec)
    for index in range(100):
        harness.record_sse_event({"type": "token", "delta": str(index)})
    assert calls == 0

    await harness.evaluate_with_evidence()
    assert calls == 1


@pytest.mark.asyncio
async def test_unchanged_mapspec_reuses_deterministic_review():
    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()

    async def reader(session_id: str):
        return {
            "session_id": session_id,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "session_id": session_id,
                    "sequence": 2,
                    "source": "frontend_runtime",
                    "mapspec_fingerprint": cartographic_fingerprint(mapspec),
                    "style_loaded": True,
                    "layers": [{
                        "id": "result", "visible": True, "opacity": 0.8,
                        "style_converged": True,
                        "projection_fingerprint": "runtime-test-projection",
                    }],
                },
            },
        }

    harness = PiAgentHarness(session_id="cache-s", cartography_state_reader=reader)
    _record_mapspec_mutation(harness, mapspec)

    first = await harness.evaluate_with_evidence()
    second = await harness.evaluate_with_evidence()

    assert first["cartography"]["counters"]["review_invocations"] == 1
    assert second["cartography"]["counters"]["review_invocations"] == 0
    assert second["cartography"]["counters"]["review_cache_hits"] == 1
    assert second["cartography"]["status"] == "passed"


@pytest.mark.asyncio
async def test_production_session_harness_re_evaluates_after_runtime_and_ack(
    quality_session,
):
    import app.agent_pi_bridge as bridge
    from app.services.mapspec.store import mapspec_store_instance

    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()
    fingerprint = cartographic_fingerprint(mapspec)
    await mapspec_store_instance.save_mapspec(quality_session, mapspec)

    harness = bridge._get_session_harness(quality_session, create=True)
    assert harness is not None
    _record_mapspec_mutation(harness, mapspec, observation_seq=0)
    harness.record_map_action_issued(
        session_id=quality_session,
        tool_call_id="call-1",
        action_id="ma-runtime",
        command="add_layer",
        requested={"layer_id": "result"},
        mapspec_fingerprint=fingerprint,
    )

    observation = CartographicRuntimeObservationRequest(
        mapspec_fingerprint=fingerprint,
        layers=[{
            "id": "result",
            "visible": True,
            "opacity": 0.8,
            "style_converged": True,
            "source_converged": True,
            "runtime_layer_count": 1,
            "projection_fingerprint": "runtime-test-projection",
        }],
        viewport={},
        style_loaded=True,
    )
    pending = await push_cartographic_runtime_observation(
        quality_session, observation, _conv=object()
    )
    assert pending["cartography"]["termination_reason"] == "runtime_action_ack_pending"
    assert pending["cartography"]["passed"] is False

    await session_data_manager.append_map_action_event(
        quality_session,
        {
            "action_id": "ma-runtime",
            "command": "add_layer",
            "status": "succeeded",
            "actual": {"store_mounted": True},
        },
    )
    converged = await bridge.evaluate_cartographic_session(quality_session)

    assert converged["cartography"]["status"] == "passed"
    assert converged["cartography"]["passed"] is True
    assert converged["gate"]["passed"] is True
    stored = await session_data_manager.get_map_state(quality_session)
    assert stored["_cartographic_review"]["cartography"]["status"] == "passed"
    assert stored["_cartographic_observation"]["mapspec_fingerprint"] == fingerprint

    bridge._harnesses.pop(quality_session, None)


def _runtime_observation(
    session_id: str,
    fingerprint: str,
    *,
    sequence: int,
    visible: bool,
    repair_action_id: str | None = None,
) -> dict:
    layer = {
        "id": "result",
        "runtime_store_id": "result",
        "visible": visible,
        "opacity": 0.8,
        "style_converged": True,
        "source_converged": True,
        "runtime_layer_count": 1,
        "projection_fingerprint": "runtime-test-projection",
    }
    if repair_action_id:
        layer["repair_action_id"] = repair_action_id
    return {
        "session_id": session_id,
        "sequence": sequence,
        "source": "frontend_runtime",
        "mapspec_fingerprint": fingerprint,
        "style_loaded": True,
        "reconcile_error": "",
        "layers": [layer],
    }


@pytest.mark.asyncio
async def test_runtime_hidden_layer_auto_repairs_then_ack_and_new_observation_pass(
    quality_session,
):
    import app.agent_pi_bridge as bridge
    from app.services.mapspec.store import mapspec_store_instance

    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()
    fingerprint = cartographic_fingerprint(mapspec)
    await mapspec_store_instance.save_mapspec(quality_session, mapspec)
    harness = bridge._get_session_harness(quality_session, create=True)
    assert harness is not None
    _record_mapspec_mutation(harness, mapspec, observation_seq=0)
    await session_data_manager.set_map_state(
        quality_session,
        "_cartographic_observation",
        _runtime_observation(quality_session, fingerprint, sequence=1, visible=False),
    )

    repairing = await bridge.evaluate_cartographic_session(quality_session)

    action = repairing["repair_action"]
    assert action["command"] == "cartographic_runtime_repair"
    assert action["params"]["repair_patches"][0]["desired"]["visible"] is True
    assert len(harness.tool_calls) == 1

    await session_data_manager.append_map_action_event(
        quality_session,
        {
            "action_id": action["action_id"],
            "command": action["command"],
            "status": "succeeded",
            "actual": {"confirmed": True},
        },
    )
    await session_data_manager.set_map_state(
        quality_session,
        "_cartographic_observation",
        _runtime_observation(
            quality_session,
            fingerprint,
            sequence=2,
            visible=True,
            repair_action_id=action["action_id"],
        ),
    )

    converged = await bridge.evaluate_cartographic_session(quality_session)

    assert converged["cartography"]["status"] == "passed"
    assert converged["cartography"]["termination_reason"] == "quality_converged"
    assert len(harness.tool_calls) == 1, "presentation repair must not rerun GIS analysis"
    state = await session_data_manager.get_map_state(quality_session)
    assert state["_cartographic_repair_state"]["attempts"][0]["status"] == "succeeded"
    bridge._harnesses.pop(quality_session, None)


@pytest.mark.asyncio
async def test_identical_runtime_repair_terminates_and_user_cancel_supersedes(
    quality_session,
):
    import app.agent_pi_bridge as bridge
    from app.services.mapspec.store import mapspec_store_instance

    mapspec = _plain_mapspec()
    mapspec["sources"]["points"]["profile"] = _point_profile()
    fingerprint = cartographic_fingerprint(mapspec)
    await mapspec_store_instance.save_mapspec(quality_session, mapspec)
    harness = bridge._get_session_harness(quality_session, create=True)
    assert harness is not None
    _record_mapspec_mutation(harness, mapspec, observation_seq=0)
    await session_data_manager.set_map_state(
        quality_session,
        "_cartographic_observation",
        _runtime_observation(quality_session, fingerprint, sequence=1, visible=False),
    )
    first = await bridge.evaluate_cartographic_session(quality_session)
    action = first["repair_action"]
    await session_data_manager.append_map_action_event(
        quality_session,
        {
            "action_id": action["action_id"],
            "command": action["command"],
            "status": "succeeded",
            "actual": {"confirmed": True},
        },
    )
    await session_data_manager.set_map_state(
        quality_session,
        "_cartographic_observation",
        _runtime_observation(
            quality_session,
            fingerprint,
            sequence=2,
            visible=False,
            repair_action_id=action["action_id"],
        ),
    )

    exhausted = await bridge.evaluate_cartographic_session(quality_session)
    assert exhausted["cartography"]["status"] == "repair_exhausted"
    assert exhausted["cartography"]["termination_reason"] == "repeated_runtime_repair"
    assert "repair_action" not in exhausted

    # A fresh generation gets one repair, but explicit frontend cancellation
    # (the user changed state) terminates as superseded instead of fighting it.
    mapspec["time"] = {"generation": 2}
    fingerprint2 = cartographic_fingerprint(mapspec)
    await mapspec_store_instance.save_mapspec(quality_session, mapspec)
    _record_mapspec_mutation(
        harness,
        mapspec,
        reported_fingerprint=fingerprint2,
        observation_seq=2,
        tool_call_id="call-2",
    )
    await session_data_manager.set_map_state(
        quality_session,
        "_cartographic_observation",
        _runtime_observation(quality_session, fingerprint2, sequence=3, visible=False),
    )
    second = await bridge.evaluate_cartographic_session(quality_session)
    cancelled_action = second["repair_action"]
    await session_data_manager.append_map_action_event(
        quality_session,
        {
            "action_id": cancelled_action["action_id"],
            "command": cancelled_action["command"],
            "status": "cancelled",
            "error": "superseded_by_user",
            "actual": {"reason": "superseded_by_user"},
        },
    )

    superseded = await bridge.evaluate_cartographic_session(quality_session)
    assert superseded["cartography"]["status"] == "superseded"
    assert superseded["cartography"]["termination_reason"] == "user_or_newer_intent"
    bridge._harnesses.pop(quality_session, None)


def test_session_harness_registry_never_retags_or_shares_accumulators():
    import app.agent_pi_bridge as bridge

    session_a = f"registry-a-{uuid.uuid4().hex[:8]}"
    session_b = f"registry-b-{uuid.uuid4().hex[:8]}"
    harness_a = bridge._get_session_harness(session_a, create=True)
    harness_b = bridge._get_session_harness(session_b, create=True)

    assert harness_a is not None and harness_b is not None
    assert harness_a is not harness_b
    assert harness_a.session_id == session_a
    assert harness_b.session_id == session_b
    harness_a.record_tool_call("a-only", "webgis_layer_upsert", {})
    assert harness_b.tool_calls == []

    bridge._harnesses.pop(session_a, None)
    bridge._harnesses.pop(session_b, None)


def test_persisted_cartographic_evidence_is_bounded_without_losing_verdict():
    evidence = CartographicReviewEvidence(
        session_id="bounded",
        status="failed_repairable",
        trusted=True,
        desired_review={
            "status": "fail",
            "checks": [{"rule": str(index)} for index in range(80)],
            "findings": [{"check": str(index)} for index in range(40)],
        },
        checks=[{"rule": str(index)} for index in range(80)],
        repair_attempts=[{"iteration": index} for index in range(5)],
        visual_evidence=[{"id": index} for index in range(8)],
    )

    serialized = evidence.to_dict()

    assert serialized["status"] == "failed_repairable"
    assert len(serialized["desired_review"]["checks"]) == 64
    assert serialized["desired_review"]["checks_omitted"] == 16
    assert len(serialized["checks"]) == 64
    assert serialized["checks_omitted"] == 16
    assert len(serialized["repair_attempts"]) == 4
    assert serialized["repair_attempts_omitted"] == 1
    assert len(serialized["visual_evidence"]) == 4
    assert serialized["visual_evidence_omitted"] == 4
