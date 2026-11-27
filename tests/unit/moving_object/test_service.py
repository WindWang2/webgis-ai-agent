"""Trajectory product service tests: preview→apply→query→staleness.

Mirrors the flow-product service test discipline: per-test session, real
session store, cross-session denial, cache reuse, source-staleness.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.moving_object.oracles import (
    bus_route_oracle,
    static_noise_oracle,
)
from app.services.moving_object.service import (
    TrajectoryProductConfig,
    TrajectoryServiceError,
    apply_trajectory_product_preview,
    inspect_moving_object_source,
    preview_trajectory_product,
    query_trajectory_product,
)
from app.services.session_data import session_data_manager


def _fc_features(features):
    return {"type": "FeatureCollection", "features": features}


async def _session_with_dataset(prefix: str = "traj-service"):
    sid = f"{prefix}-{uuid.uuid4().hex[:8]}"
    fc, _truth = bus_route_oracle()
    source_ref = await session_data_manager.store(sid, fc, prefix="geojson")
    await session_data_manager.set_alias(sid, source_ref, "bus")
    return sid, source_ref


def _config(**updates):
    values = {
        "dataset": "bus",
        "dwell_radius_m": 25.0,
        "dwell_min_dwell_s": 120.0,
        "dwell_min_points": 3,
    }
    values.update(updates)
    return TrajectoryProductConfig(**values)


@pytest.mark.asyncio
async def test_inspect_reports_health_and_fields():
    sid, _ = await _session_with_dataset()
    result = await inspect_moving_object_source(sid, "bus")
    assert result["success"] is True
    assert result["ingest_health"]["accepted"] > 0
    assert result["ingest_health"]["entity_count"] == 3
    assert "entity_id" in result["fields"]
    assert "timestamp" in result["fields"]
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_preview_build_store_and_cache_reuse():
    sid, _ = await _session_with_dataset()
    first = await preview_trajectory_product(sid, _config())
    assert first["success"] is True
    assert first["cache_hit"] == "miss"
    assert first["stats"]["entities"] == 3
    assert first["stats"]["segments"] >= 3
    assert first["can_apply"] is True
    assert first["line_ref"] and first["dwell_ref"]
    frag = first["mapspec_fragment"]
    assert f"traj-{first['family_id']}-lines" in [ly["id"] for ly in frag["layers"]]
    # quality + evidence present (tool face attaches evidence; service envelope carries quality)
    assert first["quality"]["ingest"]["accepted"] > 0

    second = await preview_trajectory_product(sid, _config())
    assert second["cache_hit"] == "preview"
    assert second["product_ref"] == first["product_ref"]

    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_region_events_inline_build():
    sid, _ = await _session_with_dataset()
    # A region covering the corridor start point.
    region_fc = _fc_features([{
        "type": "Feature",
        "properties": {"region_id": "STOP-A"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [115.99, 38.99], [116.01, 38.99], [116.01, 39.01],
            [115.99, 39.01], [115.99, 38.99],
        ]]},
    }])
    zone_ref = await session_data_manager.store(sid, region_fc, prefix="geojson")
    await session_data_manager.set_alias(sid, zone_ref, "zones")

    result = await preview_trajectory_product(sid, _config(regions_ref="zones"))
    assert result["success"] is True
    assert result["events_summary"]["n_events"] > 0
    assert result["events_summary"]["by_type"].get("enter", 0) >= 1
    assert result["region_summaries"][0]["region_id"] == "STOP-A"
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_apply_via_map_lifecycle_and_query_drilldown():
    sid, _ = await _session_with_dataset()
    built = await preview_trajectory_product(sid, _config())
    applied = await apply_trajectory_product_preview(
        sid, built["product_ref"],
        expected_revision=built["mapspec_revision"],
        mutation_id="traj-apply-1",
    )
    assert applied["success"], applied.get("error_msg")
    layer_ids = [ly["id"] for ly in applied["mapspec"]["layers"]]
    assert f"traj-{built['family_id']}-lines" in layer_ids
    assert f"traj-{built['family_id']}-dwell" in layer_ids

    product = await query_trajectory_product(sid, built["product_ref"], kind="product")
    assert product["product"]["schema_version"] == "moving_object_trajectory.v1"
    entities = await query_trajectory_product(
        sid, built["product_ref"], kind="entity", entity_id="BUS-01")
    assert entities["page"]["total"] >= 1
    dwells = await query_trajectory_product(sid, built["product_ref"], kind="dwell")
    assert dwells["page"]["total"] >= 1
    with pytest.raises(TrajectoryServiceError):
        await query_trajectory_product(sid, built["product_ref"], kind="bogus")
    # cross-session denial
    other = f"traj-other-{uuid.uuid4().hex[:8]}"
    with pytest.raises(TrajectoryServiceError):
        await query_trajectory_product(other, built["product_ref"], kind="product")
    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(other)


@pytest.mark.asyncio
async def test_apply_rejects_after_source_change():
    sid, source_ref = await _session_with_dataset()
    built = await preview_trajectory_product(sid, _config())
    # overwrite the dataset → content_revision bumps
    fc2, _ = static_noise_oracle()
    await session_data_manager.overwrite(sid, source_ref, fc2)
    applied = await apply_trajectory_product_preview(
        sid, built["product_ref"],
        expected_revision=built["mapspec_revision"],
    )
    assert applied["success"] is False
    assert applied.get("error_code") == "TRAJECTORY_SOURCE_STALE"
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_apply_family_mismatch_rejected():
    sid, _ = await _session_with_dataset()
    built = await preview_trajectory_product(sid, _config())
    from app.services.moving_object.mutation import apply_trajectory_product
    from app.services.mapspec.mutation_contracts import MutationContext
    from app.services.mapspec.intents import ApplyTrajectoryProductIntent
    from app.services.mapspec.store import MapSpecStore
    ctx = MutationContext(
        session_id=sid,
        intent=ApplyTrajectoryProductIntent(
            product_ref=built["product_ref"], family_id="traj_other"),
        origin="agent", loaded={}, pre_state={}, store=MapSpecStore(),
    )
    result = await apply_trajectory_product(ctx)
    assert result.is_error
    assert result.error_code == "TRAJECTORY_PRODUCT_FAMILY_MISMATCH"
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_empty_input_typed_rejection_not_crash():
    sid = f"traj-empty-{uuid.uuid4().hex[:8]}"
    empty_fc = {"type": "FeatureCollection", "features": []}
    empty_ref = await session_data_manager.store(sid, empty_fc, prefix="geojson")
    await session_data_manager.set_alias(sid, empty_ref, "bus")
    result = await preview_trajectory_product(sid, _config())
    # Empty input: honest zero-motion envelope, can_apply=False
    assert result["success"] is True
    assert result["can_apply"] is False
    assert "no_observable_motion" in result["blocking_reasons"]
    await session_data_manager.clear_session(sid)
