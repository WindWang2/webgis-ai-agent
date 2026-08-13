"""Acceptance tests for the structured cartographic quality review.

These tests exercise public seams only: semantic review, the bounded repair
composer, lifecycle results, and the harness quality gate.
"""

import shutil
import uuid

import pytest

from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
from app.lib.cartography.quality_loop import (
    cartographic_fingerprint,
    review_cartography,
    review_and_repair_cartography,
)
from app.lib.harness.evaluator import HarnessEvaluator
from app.lib.harness.pi_agent_harness import PiAgentHarness
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager
from app.api.routes.chat import _record_frontend_cartographic_observation


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
        "sources": {"points": {"type": "geojson"}},
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
                "type": "image",
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
):
    fingerprint = reported_fingerprint or cartographic_fingerprint(mapspec)
    harness.record_tool_call(
        "call-1", "webgis_layer_upsert", {"layer": {"id": "result"}}
    )
    harness.record_tool_result(
        "call-1",
        "webgis_layer_upsert",
        {
            "success": True,
            "is_compiled": True,
            "mapspec_fingerprint": fingerprint,
            "runtime_observation_seq": observation_seq,
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
                    "source": "frontend",
                },
                "layers": [
                    {
                        "id": "result",
                        "visible": True,
                        "opacity": 0.8,
                        "legend_spec": None,
                    }
                ],
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
                    "source": "frontend",
                },
                # The main HUD mounts analysis outputs under the ref identity,
                # not the semantic MapSpec layer id.
                "layers": [{
                    "id": "ref:geojson-owned-result",
                    "_refId": "ref:geojson-owned-result",
                    "visible": True,
                    "opacity": 0.8,
                }],
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
                    "source": "frontend",
                },
                "layers": [{
                    # Even an equal semantic id cannot bypass conflicting ref
                    # provenance from another result.
                    "id": "result",
                    "_refId": "ref:geojson-other-session-result",
                    "name": "result",
                    "visible": True,
                    "opacity": 0.8,
                }],
            },
        }

    harness = PiAgentHarness(session_id="quality-wrong-ref", cartography_state_reader=reader)
    _record_mapspec_mutation(harness, mapspec, observation_seq=1)

    result = await harness.evaluate_with_evidence()

    assert result["cartography"]["status"] == "failed_repairable"
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
                    "source": "frontend",
                },
                "layers": [{"id": "result", "visible": True}],
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

    assert state["_cartographic_observation"] == {
        "session_id": quality_session,
        "sequence": 2,
        "source": "frontend",
        "layer_count": 1,
    }
    assert "source" not in state["layers"][0]
    assert "features" not in state["layers"][0]["style"]
    assert state["layers"][0]["legend_spec"]["categories"] == ["a"]


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

    assert len(state["layers"]) <= 128
    assert len(str(state["layers"])) < 262_144
    assert state["_cartographic_observation"]["layer_count"] == len(state["layers"])


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
                    "source": "frontend",
                },
                "layers": [{"id": "result", "visible": True, "opacity": 0.8}],
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
