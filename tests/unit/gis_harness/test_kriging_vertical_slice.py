"""Kriging vertical slice — service-seam + comparative benchmark evidence.

Separate from test_kriging_interpolation.py (lib/tool/resolver contract) —
these exercise the ToolDispatchService artifact seam (uncertainty_ref is
minted AND registered in ArtifactRegistry as its own artifact) and the
deterministic IDW-vs-kriging accuracy comparison the benchmark gate needs.
"""

import numpy as np
import pytest

from app.evaluation.fixtures import FIXTURE_BUILDERS


# ── dispatch seam: uncertainty is a first-class registered artifact ─────────


@pytest.mark.asyncio
async def test_kriging_dispatch_mints_and_registers_uncertainty_ref():
    """The service seam must store ``uncertainty`` as its own ref AND
    register BOTH surfaces in the ArtifactRegistry (prediction via
    geojson_ref, uncertainty via uncertainty_ref)."""
    from app.services.artifact_registry import list_artifacts
    from app.services.session_data import session_data_manager
    from app.services.tool_dispatch_service import ToolDispatchService
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    svc = ToolDispatchService(registry=reg)
    session_id = "kriging-seam-test"
    doc = FIXTURE_BUILDERS["pm25_stations"]()
    ref = await session_data_manager.store(session_id, doc, prefix="bench")
    await session_data_manager.set_alias(session_id, ref, "pm25_stations")

    tc = {"id": "tc-krig-1", "function": {"name": "kriging_interpolation", "arguments": {
        "geojson": "pm25_stations", "value_field": "pm25",
        "resolution": 6, "cross_validate": False,
    }}}
    result = await svc.dispatch(tc, session_id, set())
    assert result.status == "ok", result.error_msg

    artifacts = await list_artifacts(session_id)
    by_producer = [a for a in artifacts if a.producer_tool == "kriging_interpolation"]
    # two distinct artifacts: the prediction surface + the uncertainty surface
    assert len(by_producer) >= 2, (
        f"prediction + uncertainty must both be registered, got {len(by_producer)}"
    )
    refs = {a.artifact_id for a in by_producer}
    assert result.geojson_ref in refs
    uncertainty_refs = [a.artifact_id for a in by_producer if a.artifact_id != result.geojson_ref]
    uncertainty_ref = uncertainty_refs[0]
    assert uncertainty_ref.startswith("ref:")
    # the uncertainty payload is retrievable and carries kriging_stddev cells
    unc = await session_data_manager.get(session_id, uncertainty_ref)
    assert unc is not None
    feats = unc.get("features") if isinstance(unc, dict) else None
    assert feats and "kriging_stddev" in feats[0]["properties"]
    # cleanup
    await session_data_manager.clear_session(session_id)


@pytest.mark.asyncio
async def test_kriging_artifact_typed_as_surface_via_capability():
    """With capability context (plan-apply seam), the prediction artifact is
    typed terrain_surface — the raster_surface map model's vocabulary."""
    from app.services.artifact_registry import list_artifacts, register_artifact
    from app.services.session_data import session_data_manager

    session_id = "kriging-type-test"
    doc = FIXTURE_BUILDERS["pm25_stations"]()
    ref = await session_data_manager.store(session_id, doc, prefix="bench")
    rec = await register_artifact(
        session_id,
        artifact_id=ref,
        artifact_type="terrain_surface",
        producer_capability="spatial_interpolation",
        producer_tool="kriging_interpolation",
    )
    assert rec is not None and rec.artifact_type == "terrain_surface"
    arts = [a for a in await list_artifacts(session_id) if a.artifact_id == ref]
    assert arts and arts[0].artifact_type == "terrain_surface"
    assert arts[0].producer_capability == "spatial_interpolation"
    await session_data_manager.clear_session(session_id)


# ── IDW vs kriging on deterministic fixtures (honest comparison) ────────────


def _fixture_arrays(n=240, seed=23):
    import random

    rng = random.Random(seed)
    lonlat, _vals = [], []
    for _ in range(n):
        lon = 103.95 + rng.random() * 0.25
        lat = 30.55 + rng.random() * 0.2
        d2 = (lon - 104.06) ** 2 + (lat - 30.66) ** 2
        pm = (
            42.0 * np.exp(-d2 / (2 * 0.05 ** 2))
            + 18.0 * np.exp(-((lon - 104.15) ** 2 + (lat - 30.58) ** 2) / (2 * 0.04 ** 2))
            + 8.0
        )
        lonlat.append((lon, lat, pm))  # noise-free truth field
    return lonlat


def test_kriging_beats_or_matches_idw_on_stationary_field():
    """Hold-out comparison on the deterministic PM2.5 fixture (noise-free):
    ordinary kriging must not be grossly worse than IDW — a regression that
    breaks the OK solver shows up here long before users do. We do NOT
    require kriging to always win; the assertion is a generous band."""
    import geopandas as gpd
    from scipy.spatial import cKDTree

    from app.lib.geo_analysis.interpolation import _pick_metric_crs
    from app.lib.geo_analysis.kriging import cross_validate_kriging

    lonlat = _fixture_arrays()
    pts = np.array([(a, b) for a, b, _ in lonlat])
    vals = np.array([v for _, _, v in lonlat])
    crs = _pick_metric_crs(pts)
    g = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(pts[:, 0], pts[:, 1]), crs="EPSG:4326"
    ).to_crs(crs)
    xy = np.asarray([(p.x, p.y) for p in g.geometry], dtype=float)

    cv = cross_validate_kriging(xy, vals, model="spherical")
    assert cv.rmse is not None

    # IDW on identical deterministic folds
    n = len(vals)
    fold_id = np.arange(n) % 5
    errs = []
    for f in range(5):
        test = fold_id == f
        tr_xy, tr_v = xy[~test], vals[~test]
        tree = cKDTree(tr_xy)
        d, idx = tree.query(xy[test], k=5)
        w = 1.0 / np.maximum(d, 1e-9) ** 2
        pred = (w * tr_v[idx]).sum(axis=1) / w.sum(axis=1)
        errs.extend((pred - vals[test]).tolist())
    idw_rmse = float(np.sqrt(np.mean(np.asarray(errs) ** 2)))

    # kriging should be within 2.5x of IDW on this well-posed field, and the
    # absolute level must be small relative to the field's dynamic range
    dynamic_range = float(vals.max() - vals.min())
    assert cv.rmse < 2.5 * idw_rmse + 1e-9, (
        f"kriging rmse {cv.rmse:.3f} blew past idw {idw_rmse:.3f}"
    )
    assert cv.rmse < 0.25 * dynamic_range, (
        f"kriging rmse {cv.rmse:.3f} too large vs field range {dynamic_range:.1f}"
    )


@pytest.mark.asyncio
async def test_kriging_repeat_call_reuses_prediction_surface_not_uncertainty():
    """Review F1 regression: a repeat identical kriging call must reuse the
    PREDICTION surface. Both refs register under the same analysis reuse
    machinery; an unsuffixed uncertainty key would shadow the prediction
    (max(updated_at) picks the last-registered) and the wrong surface would
    be canonized as the capability's latest artifact."""
    from app.services.session_data import session_data_manager
    from app.services.tool_dispatch_service import ToolDispatchService
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    svc = ToolDispatchService(registry=reg)
    session_id = "kriging-reuse-test"
    doc = FIXTURE_BUILDERS["pm25_stations"]()
    ref = await session_data_manager.store(session_id, doc, prefix="bench")
    await session_data_manager.set_alias(session_id, ref, "pm25_stations")

    args = {"geojson": "pm25_stations", "value_field": "pm25",
            "resolution": 6, "cross_validate": False}
    tc1 = {"id": "tc-krig-r1", "function": {"name": "kriging_interpolation", "arguments": args}}
    r1 = await svc.dispatch(tc1, session_id, set())
    assert r1.status == "ok"

    tc2 = {"id": "tc-krig-r2", "function": {"name": "kriging_interpolation", "arguments": args}}
    r2 = await svc.dispatch(tc2, session_id, set())
    assert r2.status == "ok"
    assert r2.status == "ok" and r2.geojson_ref == r1.geojson_ref, (
        "repeat call must reuse the SAME prediction ref"
    )
    # and the reused payload is the prediction surface (value cells), not
    # the stddev surface
    reused = await session_data_manager.get(session_id, r2.geojson_ref)
    props = reused["features"][0]["properties"]
    assert "pm25" in props, f"reused surface must be the prediction, got keys {list(props)}"
    await session_data_manager.clear_session(session_id)
