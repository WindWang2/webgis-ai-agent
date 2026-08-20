"""Unit tests for Issue #656: Fail-closed production cartographic review.

Acceptance criteria from Issue #656 & ADR-0061 ~ ADR-0064:
1. Live pass: matching fingerprint, style loaded, no reconcile error, expected layers present (visibility-off still present).
2. Missing, stale, or mismatched live observation -> cartography not_evaluated and overall_passed is false.
3. Headless webgis_runtime_validate cannot pass the map alone and cannot change a live verdict.
4. Gesture camera mismatch does not fail runtime; ACK success does not count as cartographic pass.
5. Cartographic PASS does not set InteractionStateConvergenceRate to 100.
6. Production session no longer ANDs ToolChoice / ErrorRecovery / StepEfficiency; those 100-defaults do not green or red this gate.
7. Persisted review is session id, cartography, CartographicQuality gate, cartography-only overall_passed; no success_levels.
8. Telemetry digest still emits null for unevaluated rates.
9. Tests locked against CartographicQuality.
"""
import pytest
from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.lib.harness.evaluator import HarnessEvaluator
from app.lib.harness.evidence import (
    CartographicReviewEvidence,
    EvaluationRun,
)
from app.lib.harness.pi_agent_harness import PiAgentHarness


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
            "cities": {
                "type": "geojson",
                "inlineData": {"type": "FeatureCollection", "features": []},
                "profile": _point_profile(),
            }
        },
        "layers": [
            {
                "id": "cities_layer",
                "type": "circle",
                "source": "cities",
                "paint": {"circle-color": "#ff0000", "circle-radius": 5, "circle-opacity": 0.8},
            }
        ],
        "view": {"center": [120.0, 30.0], "zoom": 10.0},
    }


def test_cartographic_pass_does_not_set_interaction_state_convergence_rate_to_100():
    """ADR-0061: Cartographic PASS must not set InteractionStateConvergenceRate to 100."""
    evaluator = HarnessEvaluator()
    evidence_result = {
        "run_id": "r1",
        "session_id": "s1",
        "evidence": [],
        "metrics": {
            "ToolChoiceAccuracy": 100.0,
            "MapSpecValidity": 100.0,
            "CursorResolutionRate": 100.0,
            "StepEfficiency": 100.0,
            "ErrorRecoveryRate": 100.0,
            "InteractionEvidenceCoverage": 50.0,
            "MapCommandExecutionSuccessRate": 50.0,
            "InteractionStateConvergenceRate": 0.0,
            "InteractionRecoveryRate": 0.0,
        },
        "interaction": {"issued": 2, "acked": 1},
        "cartography": {
            "status": "passed",
            "trusted": True,
            "evaluated": True,
            "passed": True,
            "termination_reason": "quality_converged",
        },
    }
    result = evaluator.evaluate_evidence(
        evidence_result,
        require_evaluated=False,
        require_cartography=True,
    )
    # CartographicQuality passed
    assert result["checks"]["CartographicQuality"]["passed"] is True
    # InteractionStateConvergenceRate MUST NOT be lifted to 100.0
    assert result["metrics"]["InteractionStateConvergenceRate"] == 0.0
    assert result["checks"]["InteractionStateConvergenceRate"]["passed"] is False


@pytest.mark.asyncio
async def test_headless_runtime_cannot_fail_live_verdict_and_cannot_pass_alone():
    """ADR-0061: Headless Playwright (webgis_runtime_validate) is record-only.
    It cannot fail a valid live Observed Map, and cannot pass a map missing live observation."""
    mapspec = _plain_mapspec()
    fingerprint = cartographic_fingerprint(mapspec)

    # 1. Headless failed (fatalError in report), but live Observed Map is valid -> pass
    harness = PiAgentHarness(session_id="s_headless_fail")
    async def _reader(sid):
        return {
            "session_id": sid,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "source": "frontend_runtime",
                    "session_id": sid,
                    "sequence": 5,
                    "mapspec_fingerprint": fingerprint,
                    "style_loaded": True,
                    "reconcile_error": None,
                    "layers": [{"id": "cities_layer", "visible": True, "style_converged": True}],
                }
            },
        }
    harness.cartography_state_reader = _reader
    # Record mutation
    harness.record_tool_call("tc_mut", "webgis_layer_upsert", {"layer": {"id": "cities_layer"}})
    harness.record_tool_result("tc_mut", "webgis_layer_upsert", {
        "status": "ok",
        "mapspec_fingerprint": fingerprint,
        "runtime_observation_seq": 1,
    })
    # Record headless failure
    harness.record_tool_call("tc_hr", "webgis_runtime_validate", {})
    harness.record_tool_result("tc_hr", "webgis_runtime_validate", {
        "status": "ok",
        "mapspec_fingerprint": fingerprint,
        "report": {"fatalError": "WebGL crash in headless browser", "pageErrors": []},
    })

    evidence = await harness._collect_cartographic_evidence({
        "tc_mut": {"result": {"mapspec_fingerprint": fingerprint, "runtime_observation_seq": 1}},
        "tc_hr": {"result": {"mapspec_fingerprint": fingerprint, "report": {"fatalError": "WebGL crash", "pageErrors": []}}},
    })
    assert evidence.status == "passed"
    assert evidence.runtime_status == "pass"

    # 2. Headless succeeded, but live observation is missing -> not_evaluated
    harness2 = PiAgentHarness(session_id="s_headless_alone")
    async def _reader2(sid):
        return {
            "session_id": sid,
            "mapspec": mapspec,
            "map_state": {},  # no live _cartographic_observation
        }
    harness2.cartography_state_reader = _reader2
    harness2.record_tool_call("tc_mut2", "webgis_layer_upsert", {"layer": {"id": "cities_layer"}})
    harness2.record_tool_result("tc_mut2", "webgis_layer_upsert", {
        "status": "ok",
        "mapspec_fingerprint": fingerprint,
        "runtime_observation_seq": 1,
    })
    harness2.record_tool_call("tc_hr2", "webgis_runtime_validate", {})
    harness2.record_tool_result("tc_hr2", "webgis_runtime_validate", {
        "status": "ok",
        "mapspec_fingerprint": fingerprint,
        "report": {"mapLoaded": True, "fatalError": None, "pageErrors": []},
    })
    evidence2 = await harness2._collect_cartographic_evidence({
        "tc_mut2": {"result": {"mapspec_fingerprint": fingerprint, "runtime_observation_seq": 1}},
        "tc_hr2": {"result": {"mapspec_fingerprint": fingerprint, "report": {"mapLoaded": True, "fatalError": None, "pageErrors": []}}},
    })
    assert evidence2.status == "not_evaluated"
    assert evidence2.runtime_status == "not_evaluated"
    assert evidence2.termination_reason == "stale_runtime_observation"


@pytest.mark.asyncio
async def test_gesture_camera_mismatch_does_not_fail_runtime_verdict():
    """ADR-0061: Gesture camera panning/zooming updates Observed Map only and is never pass/fail."""
    mapspec = _plain_mapspec()
    mapspec["view"] = {"center": [120.0, 30.0], "zoom": 10.0}
    fingerprint = cartographic_fingerprint(mapspec)

    harness = PiAgentHarness(session_id="s_gesture_camera")
    async def _reader3(sid):
        return {
            "session_id": sid,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "source": "frontend_runtime",
                    "session_id": sid,
                    "sequence": 3,
                    "mapspec_fingerprint": fingerprint,
                    "style_loaded": True,
                    "reconcile_error": None,
                    "layers": [{"id": "cities_layer", "visible": True, "style_converged": True}],
                    "viewport": {"center": [116.4, 39.9], "zoom": 5.0},  # user panned to Beijing
                }
            },
        }
    harness.cartography_state_reader = _reader3
    harness.record_tool_call("tc_mut", "webgis_layer_upsert", {"layer": {"id": "cities_layer"}})
    harness.record_tool_result("tc_mut", "webgis_layer_upsert", {
        "status": "ok",
        "mapspec_fingerprint": fingerprint,
        "runtime_observation_seq": 1,
    })

    evidence = await harness._collect_cartographic_evidence({
        "tc_mut": {"result": {"mapspec_fingerprint": fingerprint, "runtime_observation_seq": 1}},
    })
    assert evidence.status == "passed"
    assert evidence.runtime_status == "pass"
    assert evidence.passed is True


@pytest.mark.asyncio
async def test_hidden_by_intent_layers_count_as_present():
    """Issue #656: Expected layers present (hidden-by-intent / visibility-off still counts as present)."""
    mapspec = _plain_mapspec()
    mapspec["layers"][0]["visible"] = False
    mapspec["layers"][0]["cartographic_intent"] = {"expected_visible": False}
    fingerprint = cartographic_fingerprint(mapspec)

    harness = PiAgentHarness(session_id="s_hidden_layer")
    async def _reader4(sid):
        return {
            "session_id": sid,
            "mapspec": mapspec,
            "map_state": {
                "_cartographic_observation": {
                    "source": "frontend_runtime",
                    "session_id": sid,
                    "sequence": 2,
                    "mapspec_fingerprint": fingerprint,
                    "style_loaded": True,
                    "reconcile_error": None,
                    "layers": [{"id": "cities_layer", "visible": False, "style_converged": True}],
                }
            },
        }
    harness.cartography_state_reader = _reader4
    harness.record_tool_call("tc_mut", "webgis_layer_upsert", {"layer": {"id": "cities_layer"}})
    harness.record_tool_result("tc_mut", "webgis_layer_upsert", {
        "status": "ok",
        "mapspec_fingerprint": fingerprint,
        "runtime_observation_seq": 1,
    })

    evidence = await harness._collect_cartographic_evidence({
        "tc_mut": {"result": {"mapspec_fingerprint": fingerprint, "runtime_observation_seq": 1}},
    })
    assert evidence.passed is True
    assert evidence.status in ("passed", "passed_with_warnings")
    assert evidence.runtime_status == "pass"
    presence_check = next(c for c in evidence.checks if c.get("rule") == "RUNTIME_RESULT_PRESENCE")
    assert presence_check["status"] == "pass"
    visibility_check = next(c for c in evidence.checks if c.get("rule") == "RUNTIME_RESULT_VISIBILITY")
    assert visibility_check["status"] == "pass"


@pytest.mark.asyncio
async def test_evaluate_cartographic_session_overall_passed_is_cartography_only(monkeypatch):
    """ADR-0063 / Issue #656: evaluate_cartographic_session overall_passed means CartographicQuality only.
    It does NOT AND ToolChoice/ErrorRecovery/StepEfficiency float defaults.
    Do not persist success_levels on review."""
    import app.agent_pi_bridge as bridge
    from app.services.session_data import session_data_manager

    session_id = "s_prod_gate_carto"
    mapspec = _plain_mapspec()
    fingerprint = cartographic_fingerprint(mapspec)

    # Clean up session state
    await session_data_manager.set_map_state(session_id, "mapspec", mapspec)
    await session_data_manager.set_map_state(
        session_id,
        "_cartographic_observation",
        {
            "source": "frontend_runtime",
            "session_id": session_id,
            "sequence": 2,
            "mapspec_fingerprint": fingerprint,
            "style_loaded": True,
            "reconcile_error": None,
            "layers": [{"id": "cities_layer", "visible": True, "style_converged": True}],
        },
    )

    harness = bridge._get_session_harness(session_id, create=True)
    assert harness is not None
    harness.record_tool_call("tc1", "webgis_layer_upsert", {"layer": {"id": "cities_layer"}})
    harness.record_tool_result("tc1", "webgis_layer_upsert", {
        "status": "ok",
        "mapspec_fingerprint": fingerprint,
        "runtime_observation_seq": 1,
    })

    # Call evaluate_cartographic_session
    review = await bridge.evaluate_cartographic_session(session_id)

    # overall_passed is strictly based on CartographicQuality
    assert review["overall_passed"] is True
    assert review["cartography"]["status"] == "passed"
    assert review["gate"]["passed"] is True
    assert "success_levels" not in review

    # Check stored _cartographic_review
    stored = await session_data_manager.get_map_state(session_id)
    stored_review = stored.get("_cartographic_review")
    assert stored_review is not None
    assert stored_review["overall_passed"] is True
    assert "success_levels" not in stored_review

    # Now test when cartography is not_evaluated (e.g. observation missing)
    session_id_missing = "s_prod_gate_missing"
    await session_data_manager.set_map_state(session_id_missing, "mapspec", mapspec)
    harness_m = bridge._get_session_harness(session_id_missing, create=True)
    harness_m.record_tool_call("tc_m", "webgis_layer_upsert", {"layer": {"id": "cities_layer"}})
    harness_m.record_tool_result("tc_m", "webgis_layer_upsert", {
        "status": "ok",
        "mapspec_fingerprint": fingerprint,
        "runtime_observation_seq": 1,
    })

    review_m = await bridge.evaluate_cartographic_session(session_id_missing)
    assert review_m["overall_passed"] is False
    assert review_m["cartography"]["status"] == "not_evaluated"
    assert review_m["gate"]["passed"] is False


def test_success_levels_l5_stays_not_evaluated():
    """ADR-0064: L5 stays not_evaluated; success_levels are not the production review."""
    harness = PiAgentHarness(session_id="s_l5")
    run = EvaluationRun(run_id="r1", session_id="s_l5", evidence=[])
    cartography = CartographicReviewEvidence(session_id="s_l5")
    cartography.status = "passed"
    cartography.runtime_status = "pass"
    cartography.trusted = True
    cartography.desired_review = {"passed": True}

    levels = harness._success_levels(run, cartography)
    assert levels["goal_satisfaction"]["level"] == 5
    assert levels["goal_satisfaction"]["status"] == "not_evaluated"
    assert levels["cartographic_quality"]["status"] == "pass"
