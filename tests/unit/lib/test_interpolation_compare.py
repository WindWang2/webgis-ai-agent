"""Interpolation model-compare conformance (Foundation V2 · A2).

Contract bullets:

* deterministic: identical input → byte-identical comparison table, fixed
  method order, RMSE ranking with deterministic method-name tie-break;
* seeded accuracy anchor: on a stationary field with nugget noise, ordinary
  kriging ranks above (lower RMSE than) IDW, and the recommendation is the
  row-minimum RMSE with evidence text;
* honest skips: sample floors produce per-method rows with machine-usable
  skipped_reason (never silent absence); the CV budget cap skips later
  methods in the fixed order with ``cv_budget_exhausted``;
* eligible rows carry per-method validation evidence blocks
  (uncertainty_type=validation_metrics) matching their library metrics.
"""
import numpy as np
import pytest

from app.lib.geo_analysis.interpolation_compare import (
    COMPARE_METHODS,
    METHOD_SAMPLE_FLOORS,
    compare_interpolation_models,
)

pytestmark = pytest.mark.unit


def _stationary_fc(n=80, seed=42, noise=0.35):
    """Seeded stationary field with nugget noise over an H3-sample grid.

    seed=42 is pinned: the auto variogram selects the spherical family on
    this layout and kriging CV beats IDW (the anchor property)."""

    rng = np.random.default_rng(seed)
    feats = []
    for _ in range(n):
        lat = rng.uniform(29.9, 30.1)
        lon = rng.uniform(120.0, 120.2)
        x = (lon - 120.0) * 95000.0 * np.cos(np.deg2rad(30.0))
        y = (lat - 30.0) * 111000.0
        v = 5.0 * np.exp(-((x - 8000.0) ** 2 + (y - 6000.0) ** 2)
                         / (2 * 3500.0 ** 2)) + 10.0 + rng.normal(0, noise)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            "properties": {"v": float(v)},
        })
    return {"type": "FeatureCollection", "features": feats}


# ── determinism ─────────────────────────────────────────────────────────────

def test_compare_deterministic_repeat():
    fc = _stationary_fc()
    a = compare_interpolation_models(fc, "v")
    b = compare_interpolation_models(fc, "v")
    assert a["comparison"] == b["comparison"]
    assert a["metadata"] == b["metadata"]
    assert a["recommended"] == b["recommended"]
    # fixed method order in the table
    assert [r["method"] for r in a["comparison"]] == list(COMPARE_METHODS)


# ── accuracy anchor ─────────────────────────────────────────────────────────

def test_compare_recommendation_beats_idw_on_stationary_field():
    fc = _stationary_fc()
    out = compare_interpolation_models(fc, "v")
    rows = {r["method"]: r for r in out["comparison"] if r["eligible"]}
    assert "ordinary_kriging" in rows and "idw" in rows
    assert rows["ordinary_kriging"]["rmse"] < rows["idw"]["rmse"], (
        "OK must rank above IDW on a stationary field with nugget noise"
    )
    # recommendation = row-minimum RMSE with deterministic tie-break + evidence
    rec = out["recommended"]
    assert rec is not None
    best = min(rows.values(), key=lambda r: (r["rmse"], r["method"]))
    assert rec["method"] == best["method"]
    assert rec["rmse"] == pytest.approx(best["rmse"])
    assert "RMSE" in rec["evidence_note"]


# ── honest skips ────────────────────────────────────────────────────────────

def test_compare_sample_floor_skips_honest():
    fc = _stationary_fc(n=5)
    out = compare_interpolation_models(fc, "v", cv_budget=10_000)
    by_method = {r["method"]: r for r in out["comparison"]}
    # floors: idw≥2, tin≥4, trend≥6, rbf≥3, kriging≥20
    for method, floor in METHOD_SAMPLE_FLOORS.items():
        row = by_method[method]
        if 5 < floor:
            assert row["eligible"] is False
            assert row["skipped_reason"] and str(floor) in row["skipped_reason"]
            assert row["rmse"] is None
        else:
            assert row["eligible"] is True
            assert row["skipped_reason"] is None
    assert out["metadata"]["n_eligible"] == sum(
        1 for m, f in METHOD_SAMPLE_FLOORS.items() if 5 >= f
    )


def test_compare_budget_cap_skips_disclosed():
    fc = _stationary_fc()
    out = compare_interpolation_models(fc, "v", cv_budget=30)
    skipped = [r for r in out["comparison"] if not r["eligible"]]
    assert skipped, "a tiny budget must skip at least one method"
    assert all("cv_budget_exhausted" in r["skipped_reason"] for r in skipped)
    # budget walk is prefix-closed: once exhausted, ALL later methods skip
    methods = [r["method"] for r in out["comparison"]]
    first_skip = next(i for i, r in enumerate(out["comparison"]) if not r["eligible"])
    assert all(not out["comparison"][i]["eligible"]
               for i in range(first_skip, len(methods)))
    assert out["metadata"]["budget_used"] <= 30


# ── evidence blocks ─────────────────────────────────────────────────────────

def test_compare_rows_carry_validation_evidence():
    fc = _stationary_fc()
    out = compare_interpolation_models(fc, "v")
    for row in out["comparison"]:
        if row["eligible"]:
            v = row["validation"]
            assert v["uncertainty_type"] == "validation_metrics"
            assert v["rmse"] == pytest.approx(row["rmse"], abs=1e-6)
            assert v["sample_count"] == row["n_used"]


def test_compare_metadata_and_input_guard():
    fc = _stationary_fc()
    out = compare_interpolation_models(fc, "v")
    m = out["metadata"]
    assert m["algorithm"] == "interpolation.model_compare"
    assert m["n_samples"] == 80
    assert m["method_order"] == list(COMPARE_METHODS)
    assert m["trend_order"] in (1, 2, 3)
    assert m["budget_used"] > 0
    # invalid inputs are structured rejections (shared parse contract)
    with pytest.raises(ValueError):
        compare_interpolation_models(fc, "missing_field")
    with pytest.raises(ValueError):
        compare_interpolation_models(fc, "")
