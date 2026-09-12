"""V9 P2 —— 统一画像 / H3 分布 / 增量画像 / 规则联动 / 失效钩子测试。"""
from __future__ import annotations

import copy

import pytest

from app.services.data_profile.distribution import spatial_distribution
from app.services.data_profile.incremental import (
    merge_states,
    new_state,
    state_summary,
    update_state,
)
from app.services.data_profile.unified import (
    build_unified_profile,
    install_invalidation_hook,
    suggest_rule_params,
)
from app.services.data_quality.engine import evaluate_payload
from app.services.data_quality.rules import parse_rule_defs

_FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": {"type": "Point",
                                         "coordinates": [116.0 + i * 0.01, 39.5 + i * 0.01]},
         "properties": {"temp": 20 + i, "name": f"p{i}" if i else None}}
        for i in range(12)
    ],
}


# ── H3 空间分布 ──────────────────────────────────────────────────────


def test_spatial_distribution_basic():
    out = spatial_distribution(_FC["features"], resolution=7)
    assert out["available"]
    assert out["cell_count"] >= 1
    assert out["features_scanned"] == 12
    assert 0 < out["hotspot_share"] <= 1.0
    assert all("share" in t for t in out["top_cells"])


def test_spatial_distribution_coarsens_when_cells_exceed_budget():
    many = [{"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [i * 3.0 - 90, i * 2.0 - 45]},
             "properties": {}}
            for i in range(60)]
    out = spatial_distribution(many, resolution=7, max_cells=16)
    assert out["available"]
    assert out["cell_count"] <= 16
    # 或 coarsened（降分辨率）或 truncated（保 top 截断）—— 至少其一
    assert out["coarsened"] or out["truncated"]


def test_spatial_distribution_honest_on_positionless_features():
    out = spatial_distribution([{"type": "Feature", "geometry": None,
                                 "properties": {}}])
    assert out["available"] and out["features_scanned"] == 0
    assert out["features_without_position"] == 1


# ── 增量画像 ─────────────────────────────────────────────────────────


def _batch(offset: int, n: int):
    return [{"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [116.0 + (offset + i) * 0.01,
                                          39.5 + (offset + i) * 0.01]},
             "properties": {"temp": 20 + offset + i, "name": f"p{offset + i}"}}
            for i in range(n)]


def test_incremental_matches_full_scan():
    full = update_state(new_state(), _batch(0, 10) + _batch(10, 10))
    inc = merge_states(update_state(new_state(), _batch(0, 10)),
                       update_state(new_state(), _batch(10, 10)))
    s_full, s_inc = state_summary(full), state_summary(inc)
    assert s_full["count"] == s_inc["count"] == 20
    f_full = s_full["fields"]["temp"]
    f_inc = s_inc["fields"]["temp"]
    assert f_full["mean"] == pytest.approx(f_inc["mean"], abs=1e-9)
    assert f_full["std"] == pytest.approx(f_inc["std"], abs=1e-9)
    assert f_full["min"] == f_inc["min"] and f_full["max"] == f_inc["max"]
    # H3 直方图同分辨率下可加 → cell 集一致
    assert s_full["h3_cell_count"] == s_inc["h3_cell_count"]


def test_incremental_state_is_json_serializable_and_pure():
    state = new_state()
    snapshot = copy.deepcopy(state)
    out = update_state(state, _batch(0, 5))
    assert state == snapshot, "update_state 不得就地改动入参"
    import json
    assert json.loads(json.dumps(out))["count"] == 5


def test_incremental_null_accounting():
    feats = [{"type": "Feature", "geometry": None,
              "properties": {"a": 1}}, {"type": "Feature", "geometry": None,
                                        "properties": {"a": None}}]
    s = state_summary(update_state(new_state(), feats))
    assert s["fields"]["a"]["null_rate"] == 0.5


# ── 统一画像 + 规则联动 ──────────────────────────────────────────────


def test_unified_profile_vector():
    profile = build_unified_profile({"geojson": _FC, "crs": "EPSG:4326"})
    assert profile["kind"] == "vector"
    assert profile["vector"]["row_count"] == 12
    assert "temp" in profile["vector"]["fields"]
    assert profile["distribution"]["available"]


def test_unified_profile_raster():
    stats = {"crs": "EPSG:4326", "width": 32, "height": 32,
             "resolution_x": 0.01, "resolution_y": 0.01,
             "nodata": [-9999.0, None],
             "band_stats": [{"band": 1, "mean": 1.0, "valid_pixel_ratio": 0.9},
                            {"band": 2, "mean": 2.0, "valid_pixel_ratio": 1.0}]}
    profile = build_unified_profile({"raster_stats": stats})
    assert profile["kind"] == "raster"
    assert len(profile["raster"]["bands"]) == 2
    assert profile["raster"]["bands"][0]["nodata"] == -9999.0
    assert profile["raster"]["bands"][1]["nodata"] is None


def test_unified_profile_rejects_empty_payload():
    with pytest.raises(ValueError):
        build_unified_profile({"nope": 1})


def test_suggested_rules_feed_engine():
    profile = build_unified_profile({"geojson": _FC, "crs": "EPSG:4326"})
    suggested = suggest_rule_params(profile)
    assert suggested, "有观测画像必须产出建议规则"
    specs = parse_rule_defs(suggested)  # 建议规则必须通过 DSL 校验
    report = evaluate_payload({"geojson": _FC, "crs": "EPSG:4326"}, suggested)
    assert report["rule_count"] == len(specs)
    assert all(r["rule_id"].startswith("dq.suggested.") for r in report["results"])


def test_raster_suggestions_carry_profile_baseline():
    stats = {"crs": "EPSG:4326", "resolution_x": 0.02, "resolution_y": 0.02,
             "band_stats": [{"band": 1, "mean": 1.0, "valid_pixel_ratio": 1.0},
                            {"band": 2, "mean": 1.1, "valid_pixel_ratio": 1.0},
                            {"band": 3, "mean": 0.9, "valid_pixel_ratio": 1.0}]}
    profile = build_unified_profile({"raster_stats": stats})
    suggested = {r["rule_id"]: r for r in suggest_rule_params(profile)}
    assert "dq.suggested.resolution" in suggested
    assert suggested["dq.suggested.resolution"]["params"]["expected_resolution"] == 0.02


# ── 失效钩子 ─────────────────────────────────────────────────────────


def test_invalidation_hook_clears_profiler_cache():
    from app.services.data_profile.profiler import (
        get_dataset_profiler,
        reset_dataset_profiler,
    )

    assert install_invalidation_hook()
    reset_dataset_profiler()
    profiler = get_dataset_profiler()
    profiler._cache_put(("ref", "sess-x", "ref:y", 1, False, 50000), object())
    from app.services.ref_lifecycle import invalidate_ref_caches

    invalidate_ref_caches("sess-x", ["ref:y"])
    assert profiler._cache_get(("ref", "sess-x", "ref:y", 1, False, 50000)) is None
