"""Selection helper boundaries and the registered nearest-facility contract."""

import copy
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.tools import advanced_spatial
from app.tools.registry import ToolRegistry


def test_legacy_helper_imports_are_reexported():
    from app.tools import _spatial_selection

    assert advanced_spatial._filter_features_by_ids is _spatial_selection._filter_features_by_ids
    assert advanced_spatial._scope_source_points is _spatial_selection._scope_source_points


@pytest.fixture
def source():
    return {
        "type": "FeatureCollection",
        "name": "sources",
        "count": 2,
        "features": [
            {"type": "Feature", "id": fid, "properties": {"name": fid},
             "geometry": {"type": "Point", "coordinates": coordinates}}
            for fid, coordinates in [("a", [116.4, 39.9]), ("b", [116.5, 39.9])]
        ],
    }


@pytest.fixture
def selection_services(monkeypatch):
    from app.services import session_data
    from app.services.gis_harness.interaction_ir import projection
    from app.services.gis_situation import analysis_scope

    snapshot = AsyncMock(return_value={})
    map_state = AsyncMock(return_value={})
    validate = AsyncMock(return_value={"status": "usable"})
    monkeypatch.setattr(projection, "interaction_snapshot_via_store", snapshot)
    monkeypatch.setattr(session_data.session_data_manager, "get_map_state", map_state)
    monkeypatch.setattr(analysis_scope, "validate_analysis_scope", validate)
    return SimpleNamespace(
        snapshot=snapshot, map_state=map_state, validate=validate,
        store=session_data.session_data_manager,
    )


@pytest.mark.parametrize("ids", [[], [None, ""]])
@pytest.mark.parametrize("source", [None, "ref:points", {"features": []}])
def test_filter_without_ids_preserves_input_identity(source, ids):
    assert advanced_spatial._filter_features_by_ids(source, ids) is source


def test_filter_preserves_order_metadata_and_input(source):
    before = copy.deepcopy(source)
    selected = advanced_spatial._filter_features_by_ids(source, ["b", "a", "b"])
    assert selected == before
    assert selected is not source
    assert selected["features"] is not source["features"]
    assert selected["features"][0] is source["features"][0]
    one = advanced_spatial._filter_features_by_ids(source, ["b", "missing"])
    assert one == {**source, "features": [source["features"][1]], "count": 1}
    assert source == before


def test_filter_uses_shared_feature_identity_and_skips_non_features():
    features = [
        None, "invalid", {"id": True}, {"id": 0},
        {"properties": {"OBJECTID": 7}},
        {"id": "top", "properties": {"OBJECTID": 7}},
    ]
    result = advanced_spatial._filter_features_by_ids(
        {"features": features}, [0, 7, "7", None, ""],
    )
    assert result == {"features": [features[3], features[4]], "count": 2}


@pytest.mark.parametrize("source", [None, "ref:points", [], {}, {"features": None}, {"features": ()}])
def test_filter_rejects_unusable_source_without_falling_back(source):
    result = advanced_spatial._filter_features_by_ids(source, ["a"])
    assert result == {
        "success": False, "code": "selection_unusable",
        "error": ("无法按选择过滤源点：缺少 features" if isinstance(source, dict)
                  else "无法按选择过滤源点：输入不是 FeatureCollection"),
    }


def test_filter_missing_ids_does_not_return_full_layer(source):
    assert advanced_spatial._filter_features_by_ids(source, ["missing"]) == {
        "success": False, "code": "selection_miss",
        "error": "选择中的要素不在源点集合内，未对整层做距离分析",
    }


@pytest.mark.parametrize("ids", [None, [], [None, "", 0, False]])
async def test_scope_without_session_or_ids_preserves_ref_token(ids, selection_services):
    token = "ref:unresolved-points"
    assert await advanced_spatial._scope_source_points(token, ids, None) is token
    selection_services.snapshot.assert_not_awaited()
    selection_services.map_state.assert_not_awaited()
    selection_services.validate.assert_not_awaited()


async def test_scope_explicit_ids_bypass_session_selection(source, selection_services):
    result = await advanced_spatial._scope_source_points(source, [None, "", "b"], "session-a")
    assert result["features"] == [source["features"][1]]
    selection_services.snapshot.assert_not_awaited()
    selection_services.map_state.assert_not_awaited()
    selection_services.validate.assert_not_awaited()


async def test_scope_no_live_selection_preserves_source(source, selection_services):
    assert await advanced_spatial._scope_source_points(source, None, "session-a") is source
    selection_services.snapshot.assert_awaited_once_with(selection_services.store, "session-a")
    selection_services.map_state.assert_awaited_once_with("session-a")
    selection_services.validate.assert_not_awaited()


@pytest.mark.parametrize("validated_ids,expected_ids", [(["b"], ["b"]), ([], ["a", "b"]), (None, ["a", "b"])])
async def test_scope_live_selection_uses_validated_ids(source, selection_services, validated_ids, expected_ids):
    selection_services.snapshot.return_value = {
        "event_count": 1,
        "selection": {"selected_ids": ["a", "b"], "layer_id": "points", "dataset_ref": "ref:points"},
    }
    selection_services.validate.return_value = {"status": "usable", "feature_ids": validated_ids}
    result = await advanced_spatial._scope_source_points(source, None, "session-a")
    assert [feature["id"] for feature in result["features"]] == expected_ids
    selection_services.validate.assert_awaited_once()
    session_id, claimed = selection_services.validate.await_args.args
    assert session_id == "session-a"
    assert claimed["feature_ids"] == ["a", "b"]
    assert claimed["dataset_ref"] == "ref:points"
    assert claimed["sources"] == ["ir_ledger"]


@pytest.mark.parametrize("status", ["stale", "unusable", None])
async def test_scope_invalid_live_selection_preserves_error(source, selection_services, status):
    selection_services.snapshot.return_value = {"selection": {"feature_id": "a"}}
    selection_services.validate.return_value = {"status": status, "reason": "ref_unresolvable"}
    assert await advanced_spatial._scope_source_points(source, None, "session-a") == {
        "success": False, "code": "selection_unusable",
        "error": "当前选择未通过归属校验，未对整层做距离分析",
        "reason": "ref_unresolvable",
    }


@pytest.mark.parametrize("observation,expected_ids", [
    ({"selected_feature": {"feature_id": "a"}}, ["a"]),
    ({"selection": {"selected_ids": ["b"]}}, ["b"]),
    ({"selected_feature": {"feature_id": "a"}, "selection": {"selected_ids": ["b"]}}, ["b"]),
    ({"selected_feature": {"selected_ids": ["a"]}, "selection": {"selected_ids": ["b"]}}, ["a"]),
    ({"selected_feature": "invalid", "selection": {"selected_ids": ["b"]}}, ["b"]),
])
async def test_scope_merges_pre_turn_selection_without_mutation(source, selection_services, observation, expected_ids):
    before = copy.deepcopy(observation)
    selection_services.snapshot.return_value = {"event_count": 0, "selection": None}
    selection_services.map_state.return_value = {"_cartographic_context_observation": observation}
    result = await advanced_spatial._scope_source_points(source, None, "session-a")
    assert [feature["id"] for feature in result["features"]] == expected_ids
    assert observation == before
    assert selection_services.validate.await_args.args[1]["sources"] == ["pre_turn"]


async def test_scope_current_deselection_overrides_old_pre_turn(source, selection_services):
    selection_services.snapshot.return_value = {"event_count": 1, "selection": None}
    selection_services.map_state.return_value = {
        "_cartographic_context_observation": {"selected_feature": {"feature_id": "a"}},
    }
    assert await advanced_spatial._scope_source_points(source, None, "session-a") is source
    selection_services.validate.assert_not_awaited()


async def test_scope_snapshot_failure_retains_pre_turn_fallback(source, selection_services):
    selection_services.snapshot.side_effect = RuntimeError("snapshot unavailable")
    selection_services.map_state.return_value = {
        "_cartographic_context_observation": {"selection": {"selected_ids": ["b"]}},
    }
    result = await advanced_spatial._scope_source_points(source, None, "session-a")
    assert result["features"] == [source["features"][1]]


async def test_scope_map_state_failure_retains_live_selection(source, selection_services):
    selection_services.snapshot.return_value = {"selection": {"feature_id": "a"}}
    selection_services.map_state.side_effect = RuntimeError("map state unavailable")
    result = await advanced_spatial._scope_source_points(source, None, "session-a")
    assert result["features"] == [source["features"][0]]


async def test_scope_both_reads_fail_preserves_existing_no_selection_behavior(source, selection_services):
    selection_services.snapshot.side_effect = RuntimeError("snapshot unavailable")
    selection_services.map_state.side_effect = RuntimeError("map state unavailable")
    assert await advanced_spatial._scope_source_points(source, None, "session-a") is source
    selection_services.validate.assert_not_awaited()


async def test_scope_validation_exception_is_not_swallowed(source, selection_services):
    selection_services.snapshot.return_value = {"selection": {"feature_id": "a"}}
    selection_services.validate.side_effect = RuntimeError("validator unavailable")
    with pytest.raises(RuntimeError, match="validator unavailable"):
        await advanced_spatial._scope_source_points(source, None, "session-a")


@pytest.fixture
def registry():
    result = ToolRegistry()
    advanced_spatial.register_advanced_spatial_tools(result)
    return result


def test_registered_nearest_facility_keeps_async_signature_and_schema(registry):
    function = registry._tools["nearest_facility"]
    assert inspect.iscoroutinefunction(function)
    assert list(inspect.signature(function).parameters) == [
        "source_points", "target_points", "feature_ids", "session_id",
    ]
    schema = registry.get_schemas_subset({"nearest_facility"})[0]["function"]["parameters"]
    assert set(schema["properties"]) == {"source_points", "target_points", "feature_ids"}
    assert set(schema["required"]) == {"source_points", "target_points"}


async def test_registered_nearest_facility_filters_and_discloses_distance(source, registry):
    result = await registry.dispatch("nearest_facility", {
        "source_points": source, "target_points": source, "feature_ids": ["b"],
    })
    assert result["success"] is True
    assert result["distance_method"] == "euclidean"
    assert result["disclosure"] == "straight-line metres, not road-network distance"
    assert [feature["properties"]["name"] for feature in result["data"]["features"]] == ["b"]
    assert result["data"]["features"][0]["properties"]["distance_m"] == pytest.approx(0)
    assert len(source["features"]) == 2


@pytest.mark.parametrize("ids", [["missing"], ["a"]])
async def test_registered_nearest_facility_does_not_compute_after_scope_failure(source, registry, selection_services, monkeypatch, ids):
    from app.lib.geo_analysis import network

    compute = Mock(side_effect=AssertionError("analysis must not run"))
    monkeypatch.setattr(network, "nearest_neighbor_features", compute)
    selection_services.snapshot.return_value = {"selection": {"feature_id": "a"}}
    selection_services.validate.return_value = {"status": "stale", "reason": "revision_drifted"}
    # Explicit unknown ids and a stale live selection must both fail closed.
    use_live = ids == ["a"]
    result = await registry.dispatch("nearest_facility", {
        "source_points": source, "target_points": source,
        **({} if use_live else {"feature_ids": ids}),
    }, session_id="session-a")
    assert result["success"] is False
    assert result["code"] == ("selection_unusable" if use_live else "selection_miss")
    if use_live:
        assert result["reason"] == "revision_drifted"
    compute.assert_not_called()


@pytest.mark.parametrize("session_id", [None, "trusted-session"])
async def test_registered_nearest_facility_retains_scope_patch_seam_and_trusted_session(source, registry, monkeypatch, session_id):
    failure = {"success": False, "code": "selection_unusable", "reason": "test"}
    scope = AsyncMock(return_value=failure)
    monkeypatch.setattr(advanced_spatial, "_scope_source_points", scope)
    result = await registry.dispatch("nearest_facility", {
        "source_points": source, "target_points": source,
        "feature_ids": ["a"], "session_id": "untrusted-session",
    }, session_id=session_id)
    assert result is failure
    scope.assert_awaited_once_with(source, ["a"], session_id)


async def test_registered_nearest_facility_resolves_ref_with_owning_session(source, registry):
    from app.services.session_data import session_data_manager

    session_id = "spatial-selection-ref-test"
    ref = await session_data_manager.store(session_id, source)
    result = await registry.dispatch("nearest_facility", {
        "source_points": ref, "target_points": source, "feature_ids": ["a"],
    }, session_id=session_id)
    assert result["success"] is True
    assert [feature["properties"]["name"] for feature in result["data"]["features"]] == ["a"]
    assert await session_data_manager.get(session_id, ref) == source
