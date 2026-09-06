"""Conformance tests for the Knox space-time interaction test
(Foundation V2 · Domain A3, spatiotemporal.knox).

Contract bullets (descriptor-anchored, fixed seeds — deterministic):

- hand-computable 4-point example: observed joint pairs, expected
  E = 2·S·T/(n(n−1)), spatial pair count S and temporal pair count T are
  exact;
- space-time clustered seeded data: one-sided permutation p < 0.05;
- independent space/time fixture: p > 0.05 (null calibration);
- n = 3 → InsufficientSamples; observation cap → ResourceScaleMismatch
  BEFORE allocation (monkeypatched);
- non-finite timestamps are dropped with an explicit count disclosure;
- critical_distance = 0 resolves to the median nearest-neighbour distance
  (disclosed via critical_distance_auto);
- duplicate timestamps produce a tie disclosure (Δt = 0 counts as
  time-adjacent);
- the tool layer attaches scientific evidence with MonteCarloSummary +
  StatisticalSignificance blocks exactly when permutations > 0.
"""
import asyncio

import numpy as np
import pytest
from scipy.spatial import cKDTree

import app.lib.geo_analysis.point_pattern as pp_module
from app.lib.geo_analysis.point_pattern import knox_test
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    ResourceScaleMismatch,
)

METRIC_CRS = "EPSG:32650"

pytestmark = pytest.mark.unit


def _clustered_spacetime(seed: int = 11):
    """4 space-time clusters: each spatial cluster is also a time window."""
    rs = np.random.RandomState(seed)
    centers = rs.uniform(15, 85, (4, 2))
    bases = [0, 200_000, 400_000, 600_000]
    pts, times = [], []
    for c, b in zip(centers, bases):
        pts.append(c + rs.normal(0, 3, (15, 2)))
        times.append(b + rs.normal(0, 7200, 15))
    return np.vstack(pts), np.concatenate(times)


# ── lib level ────────────────────────────────────────────────────────

def test_knox_hand_computed_4point_example():
    """4 points on a line: spatial pairs within 5 m = {(0,1),(2,3)} → S=2;
    temporal pairs within 5 s = {(0,1),(2,3)} → T=2; observed joint = 2;
    E = 2·2·2/(4·3) = 2/3. Exact match, per the descriptor tolerance."""
    xy = np.array([[0.0, 0.0], [1.0, 0.0], [100.0, 0.0], [101.0, 0.0]])
    times = np.array([0.0, 1.0, 100.0, 101.0])
    res = knox_test(xy, times, crs=METRIC_CRS, critical_distance=5,
                    critical_time=5, permutations=0)
    assert res["n"] == 4
    assert res["n_spatial_pairs"] == 2
    assert res["n_temporal_pairs"] == 2
    assert res["observed"] == 2
    assert res["expected"] == pytest.approx(2.0 / 3.0, abs=1e-6)
    assert res["observed_over_expected"] == pytest.approx(3.0, abs=1e-5)
    assert "未做显著性检验" in res["interpretation"]  # permutations=0 → descriptive


def test_knox_spacetime_clustered_significant():
    """Space-time clustered fixture: joint pairs far above the independent
    expectation → one-sided greater permutation p < 0.05."""
    xy, times = _clustered_spacetime()
    res = knox_test(xy, times, crs=METRIC_CRS, critical_distance=20,
                    critical_time=4 * 3600, permutations=199)
    assert res["observed"] > res["expected"]
    assert res["p_value"] < 0.05
    assert res["perm_quantiles"]["p50"] <= res["observed"]
    assert res["p_method"] and "199" in res["p_method"]


def test_knox_independent_not_significant():
    """Independent space/time fixture: the time-permutation null must not
    be rejected (fixed-seed calibration anchor)."""
    rs = np.random.RandomState(42)
    xy = rs.uniform(0, 100, (80, 2))
    times = rs.uniform(0, 700_000, 80)
    res = knox_test(xy, times, crs=METRIC_CRS, critical_distance=15,
                    critical_time=40_000, permutations=199)
    assert res["p_value"] > 0.05
    assert res["critical_distance_auto"] is False
    assert res["critical_time_auto"] is False


def test_knox_insufficient_and_scale_guards(monkeypatch):
    """n=3 → InsufficientSamples; the observation cap fires as
    ResourceScaleMismatch (honest rejection, not OOM)."""
    xy4 = np.array([[0, 0], [1, 0], [100, 0]], float)
    with pytest.raises(InsufficientSamples):
        knox_test(xy4, [0.0, 1.0, 2.0], crs=METRIC_CRS,
                  critical_distance=5, critical_time=5)
    monkeypatch.setattr(pp_module, "_MAX_KNOX_OBSERVATIONS", 10)
    with pytest.raises(ResourceScaleMismatch):
        knox_test(np.random.RandomState(0).uniform(0, 100, (12, 2)),
                  np.arange(12.0), crs=METRIC_CRS,
                  critical_distance=15, critical_time=5, permutations=0)


def test_knox_nan_times_dropped_with_disclosure():
    """Non-finite time rows are dropped and the count is disclosed; the
    test runs on the remaining valid rows only."""
    xy = np.array([[0.0, 0.0], [1.0, 0.0], [100.0, 0.0], [101.0, 0.0],
                   [50.0, 50.0]])
    times = np.array([0.0, 1.0, 100.0, 101.0, np.nan])
    res = knox_test(xy, times, crs=METRIC_CRS, critical_distance=5,
                    critical_time=5, permutations=0)
    assert res["n_time_dropped"] == 1
    assert res["n"] == 4
    assert "time_dropped_note" in res
    assert res["observed"] == 2  # identical to the clean 4-point example


def test_knox_auto_critical_distance():
    """critical_distance=0 resolves to the median NN distance and is
    disclosed via critical_distance_auto + note."""
    rs = np.random.RandomState(42)
    xy = rs.uniform(0, 100, (80, 2))
    times = rs.uniform(0, 700_000, 80)
    res = knox_test(xy, times, crs=METRIC_CRS, critical_distance=0,
                    critical_time=40_000, permutations=0)
    median_nn = float(np.median(cKDTree(xy).query(xy, k=2)[0][:, 1]))
    assert res["critical_distance_auto"] is True
    assert res["critical_distance"] == pytest.approx(median_nn, abs=1e-6)
    assert "critical_distance_note" in res


def test_knox_duplicate_timestamp_ties_disclosed():
    """Identical timestamps on a spatial pair: Δt=0 counts as time-adjacent
    and the tie is disclosed (permutation resolution caveat)."""
    xy = np.array([[0.0, 0.0], [1.0, 0.0], [100.0, 0.0], [101.0, 0.0]])
    times = np.array([7.0, 7.0, 100.0, 100.0])  # spatial pairs are also ties
    res = knox_test(xy, times, crs=METRIC_CRS, critical_distance=5,
                    critical_time=5, permutations=0)
    assert res["observed"] == 2
    assert "tie_note" in res


def test_knox_degenerate_thresholds():
    """Degenerate auto thresholds (all times identical / zero NN distance)
    are typed failures, not silent zeros."""
    xy = np.array([[0.0, 0.0], [1.0, 0.0], [100.0, 0.0], [101.0, 0.0]])
    with pytest.raises(DegenerateData):
        knox_test(xy, [5.0, 5.0, 5.0, 5.0], crs=METRIC_CRS,
                  critical_distance=5, critical_time=0)  # auto time → 0
    with pytest.raises(DegenerateData):
        knox_test(np.zeros((4, 2)), [0.0, 1.0, 2.0, 3.0], crs=METRIC_CRS,
                  critical_distance=0, critical_time=5)  # auto dist → 0


# ── tool level ───────────────────────────────────────────────────────

def test_knox_tool_evidence_blocks():
    """knox_analysis tool: evidence attached, uncertainty blocks = Monte-
    Carlo summary + statistical significance (permutations > 0), and the
    null-draw disclosure survives the tool boundary."""
    from app.tools.point_pattern_tools import register_point_pattern_tools
    from app.tools.registry import ToolRegistry

    rs = np.random.RandomState(42)
    features = []
    for i in range(40):
        lo, la = float(rs.uniform(116.3, 116.4)), float(rs.uniform(39.85, 39.95))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lo, la]},
            "properties": {"timestamp": 1_700_000_000 + int(i * 900)},
        })
    fc = {"type": "FeatureCollection", "features": features}

    registry = ToolRegistry()
    register_point_pattern_tools(registry)
    payload = asyncio.run(registry.dispatch("knox_analysis", {
        "geojson": fc, "time_field": "timestamp",
        "critical_distance": 500.0, "critical_time": 3600.0,
        "permutations": "99",
    }))
    assert payload["success"] is True, payload.get("message")
    ev = payload["scientific_evidence"]
    kinds = [u["uncertainty_type"] for u in ev["uncertainty"]]
    assert "monte_carlo_summary" in kinds and "statistical_significance" in kinds
    sig = next(u for u in ev["uncertainty"]
               if u["uncertainty_type"] == "statistical_significance")
    assert sig["method"] == "permutation" and sig["alternative"] == "greater"
    assert sig["permutations"] == 99
    assert ev["reproducibility"]["seed"] == 42
    assert payload["data"]["time_field"] == "timestamp"
