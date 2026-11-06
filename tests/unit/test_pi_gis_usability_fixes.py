"""Regression locks for the Pi × GIS harness usability pass.

These tests reproduce confirmed defects. They do not refresh goldens or
weaken an existing assertion.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.lib.cancellation import CancellationToken, OperationCancelled, use_token
from app.services.gis_harness.completion.pipeline import (
    _is_task_complete,
    finalization_sse_payload,
)
from app.services.gis_harness.completion.contracts import MapCompletionResult
from app.services.gis_harness.heatmap_restyle import (
    recolor_heatmap_layer,
    resolve_heatmap_palette,
)
from app.services.gis_harness.scope_ref_guard import scope_ref_mismatch
from app.services.gis_harness.goal_satisfaction.contracts import (
    EvidenceKind,
    GoalRequirement,
    RequirementKind,
    RequirementState,
)
from app.services.gis_harness.goal_satisfaction.evaluator import _eval_map_requirement


ROOT = Path(__file__).resolve().parents[2]


def test_unknown_render_is_not_task_complete():
    stored = {
        "product_verdict": "READY_WITH_WARNINGS",
        "final_map_status": "verified_with_degradation",
        "render_status": "unknown",
    }
    assert _is_task_complete(stored) is False
    result = MapCompletionResult(
        status="complete",
        summary="map product validated",
        render_status="unknown",
        final_map_status="verified_with_degradation",
        product_verdict="READY_WITH_WARNINGS",
    )
    assert finalization_sse_payload(result, "s1")["task_complete"] is False


def test_verified_render_can_still_complete_with_warnings():
    assert _is_task_complete({
        "product_verdict": "READY_WITH_WARNINGS",
        "final_map_status": "verified_with_degradation",
        "render_status": "verified",
    }) is True


def test_goal_map_requirement_not_fulfilled_without_observation():
    requirement = GoalRequirement(
        id="map",
        kind=RequirementKind.MAP,
        summary="show the map",
    )
    verdict = _eval_map_requirement(
        requirement,
        [],
        {},
        {
            "product_verdict": "READY_WITH_WARNINGS",
            "final_map_status": "verified_with_degradation",
            "render_status": "unknown",
        },
    )
    assert verdict.verdict == RequirementState.NOT_EVALUATED
    assert EvidenceKind.MAP_PRODUCT  # import used by the evaluator's evidence filter


def test_stamped_query_place_blocks_city_reuse():
    from app.services.gis_harness.scope_ref_guard import (
        attach_query_place,
        place_evidence_from_payload,
        scope_ref_mismatch,
    )

    payload = attach_query_place(
        {"type": "FeatureCollection", "features": [{"properties": {"name": "学校"}}]},
        "成都市",
    )
    assert payload["district"] == "成都市"
    reason = scope_ref_mismatch(
        "北京市",
        descriptor=place_evidence_from_payload(None, payload),
        features=payload["features"],
    )
    assert reason is not None
    assert scope_ref_mismatch(
        "成都市",
        descriptor=place_evidence_from_payload(None, payload),
    ) is None


def test_selection_filter_does_not_keep_unselected_points():
    from app.tools.advanced_spatial import _filter_features_by_ids

    source = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "a", "properties": {}, "geometry": {"type": "Point", "coordinates": [1, 1]}},
            {"type": "Feature", "id": "b", "properties": {}, "geometry": {"type": "Point", "coordinates": [2, 2]}},
        ],
    }
    kept = _filter_features_by_ids(source, ["a"])
    assert [f["id"] for f in kept["features"]] == ["a"]
    missed = _filter_features_by_ids(source, ["missing"])
    assert missed["success"] is False
    assert missed["code"] == "selection_miss"


def test_online_fallback_payload_is_an_error():
    from app.services.llm_result_formatter import is_error_dict

    payload = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature"}],
        "success": False,
        "code": "partial_online_fallback",
        "error": "本地 POI 库缺失",
    }
    assert is_error_dict(payload) is True


def test_scope_ref_rejects_chengdu_points_for_beijing():
    features = [{
        "type": "Feature",
        "properties": {"cityname": "成都市", "name": "某小学"},
        "geometry": {"type": "Point", "coordinates": [104.06, 30.67]},
    }]
    reason = scope_ref_mismatch("北京市", features=features)
    assert reason is not None
    assert reason.startswith("scope_ref_mismatch")
    assert scope_ref_mismatch("成都市", features=features) is None
    assert scope_ref_mismatch("北京市", features=[{
        "type": "Feature",
        "properties": {"name": "无城市字段"},
    }]) is None


def test_heatmap_blue_restyle_does_not_touch_source():
    assert resolve_heatmap_palette("蓝色") == "blues"
    assert resolve_heatmap_palette("not-a-palette") is None
    layer = {
        "id": "heat-1",
        "type": "heatmap",
        "source": "src-1",
        "heatmap": {"radius_px": 28},
        "paint": {"heatmap-opacity": 0.9, "heatmap-color": ["interpolate"]},
    }
    painted = recolor_heatmap_layer(layer, "blues")
    assert painted["source"] == "src-1"
    assert painted["heatmap"]["palette"] == "blues"
    assert painted["legend_spec"]["palette"] == "Blues"
    assert "heatmap-color" in painted["paint"]
    assert layer["paint"]["heatmap-color"] == ["interpolate"]


def test_fractional_bandwidth_is_not_replaced_with_default():
    from app.lib.cartography.heatmap_contract import normalize_heatmap_radius

    contract = normalize_heatmap_radius(bandwidth_m=250.5, radius_px=30)
    assert contract.bandwidth_m == 250.5
    contract_legacy = normalize_heatmap_radius(legacy_radius=1500.25)
    assert contract_legacy.bandwidth_m == 1500.25


def test_extension_wrap_rejects_only_frozen_natives():
    source = (ROOT / "app/extensions/webgis-tools/index.mjs").read_text(encoding="utf-8")
    assert "nativeNames.has(toolName)" in source
    assert "via this proxy" not in source
    assert "call heatmap_data or h3_binning directly" in source


def test_nearest_facility_is_not_a_dijkstra_candidate():
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    algo = get_algorithm_registry().get("network.closest_facility")
    assert algo is not None
    assert "nearest_facility" not in algo.tool_candidates
    assert "network_closest_facility" in algo.tool_candidates


@pytest.mark.asyncio
async def test_cancelled_turn_does_not_apply_mapspec(monkeypatch):
    from app.services.mapspec_store import MapSpecStore

    called = False

    async def boom(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("cancelled turn wrote MapSpec")

    monkeypatch.setattr(
        "app.services.gis_world_state.apply_gis_mutation",
        boom,
    )
    token = CancellationToken(job_id="turn-cancel")
    token.cancel("user stop")
    store = MapSpecStore()
    with use_token(token):
        with pytest.raises(OperationCancelled):
            await store._apply("sess", object())
    assert called is False


@pytest.mark.asyncio
async def test_pi_worker_resets_session_when_gis_session_changes():
    from unittest.mock import AsyncMock, MagicMock

    from app.agent_pi_bridge import PiBridge

    rpc = MagicMock()
    rpc.request = AsyncMock(return_value={"cancelled": False})
    rpc.events = None
    bridge = PiBridge(rpc=rpc)
    bridge._drain_stale_events = AsyncMock()

    await bridge._isolate_vendor_session("sess-a")
    rpc.request.assert_not_called()

    await bridge._isolate_vendor_session("sess-a")
    rpc.request.assert_not_called()

    await bridge._isolate_vendor_session("sess-b")
    rpc.request.assert_awaited_once_with("new_session", {})
    bridge._drain_stale_events.assert_awaited()
