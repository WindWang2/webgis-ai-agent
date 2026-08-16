"""Regression tests for #445: detect_vegetation_change pixel-level classification.

`run_change_detection` advertised "变化统计、分类图和预览" but compared two
scene-level means from independent STAC acquisitions (possibly different
footprints) and returned a single scalar category — no pixel-level
classification anywhere. These tests pin the pixel-level behavior on the
common footprint.
"""

import numpy as np
import pytest

from app.services import spatial_tasks
from app.services.rs.band_math import compute_raster_stats
from app.services.rs.spectral_engine import SpectralRasterEngine


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    """Pin Celery to eager + no backend (direct task-body calls use update_state)."""
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


def _index_result(array, bounds):
    return {
        "status": "ok",
        "index_type": "NDVI",
        "stats": compute_raster_stats(array),
        "bbox": bounds,
        "raster_source": {"array": array, "bounds": bounds, "suggested_palette": "Viridis"},
    }


def _patch_engine(monkeypatch, t1, t2):
    async def fake_cvi(self, bbox, date_from, date_to, index_type="ndvi"):
        return t1 if date_from.startswith("2025") else t2

    monkeypatch.setattr(SpectralRasterEngine, "compute_vegetation_index", fake_cvi)


def _run(spatial_tasks_mod, **kw):
    return spatial_tasks_mod.run_change_detection(
        bbox=kw.get("bbox", [0.0, 0.0, 1.0, 1.0]),
        t1_from=kw.get("t1_from", "2025-06-01"),
        t1_to=kw.get("t1_to", "2025-06-30"),
        t2_from=kw.get("t2_from", "2026-06-01"),
        t2_to=kw.get("t2_to", "2026-06-30"),
        index_type="ndvi",
        change_threshold=kw.get("change_threshold", 0.1),
    )


def test_pixel_level_classification_on_common_footprint(monkeypatch):
    """Same footprint, 6×6 grids: a 2×2 patch degrades by −0.6, another 2×2
    patch improves by +0.2, the rest is unchanged. The response must carry
    per-pixel class counts over the COMMON footprint, not a scene-mean scalar."""
    base = np.full((6, 6), 0.3)
    a1 = base.copy()
    a1[1:3, 1:3] = 0.6  # vegetation patch that will be lost
    a2 = base.copy()
    a2[1:3, 1:3] = 0.0  # −0.6 → significant degradation
    a2[4:6, 4:6] = 0.5  # +0.2 → significant improvement
    bounds = [0.0, 0.0, 1.0, 1.0]
    _patch_engine(monkeypatch, _index_result(a1, bounds), _index_result(a2, bounds))

    result = _run(spatial_tasks)
    assert result["success"] is True, result

    change = result["data"]["change"]
    assert change["method"] == "pixel_common_footprint"
    cls = change["classification"]
    assert cls["class_counts"]["significant_degradation"] == 4
    assert cls["class_counts"]["significant_improvement"] == 4
    assert cls["class_counts"]["no_change"] == 28
    assert cls["valid_pixels"] == 36
    assert cls["class_percentages"]["significant_degradation"] == round(100 * 4 / 36, 1)
    # delta_mean over the common grid (not scene means of different extents)
    assert change["delta_mean"] == pytest.approx((4 * 0.2 - 4 * 0.6) / 36)
    # A preview image is produced (promised by the tool description).
    assert cls["preview_png_b64"]


def test_different_footprints_compare_only_overlap(monkeypatch):
    """T1 and T2 cover different (overlapping) footprints — the two scene
    means are not comparable. The pixel comparison must run on the
    intersection only: here the change lives INSIDE the overlap, and the
    T2-only half is uniformly offset (which scene means would wrongly mix in)."""
    a1 = np.full((6, 6), 0.3)
    a1[1:3, 1:3] = 0.6
    a2 = np.full((6, 6), 0.3)
    a2[1:3, 1:3] = 0.0  # the −0.6 patch is inside the overlap [0.5, 1.0]
    # T2's non-overlapping half differs uniformly — must NOT enter the stats.
    a2[:, 3:] += 0.05

    t1_bounds = [0.0, 0.0, 1.0, 1.0]
    t2_bounds = [0.5, 0.0, 1.5, 1.0]  # overlap = [0.5, 0.0, 1.0, 1.0] → 3 cols
    _patch_engine(
        monkeypatch,
        _index_result(a1, t1_bounds),
        _index_result(a2, t2_bounds),
    )

    result = _run(spatial_tasks)
    assert result["success"] is True

    change = result["data"]["change"]
    assert change["method"] == "pixel_common_footprint"
    cls = change["classification"]
    assert cls["total_pixels"] == 18  # 6×3 overlap window only
    # Overlap columns of a1: cols 3..5 (values 0.3, patch col 3 only has 0.3);
    # a1's patch at cols 1..2 lies OUTSIDE the overlap → only −0.05 diffs from
    # a2's uniform offset... wait: a2 overlap cols are 0..2 (bounds 0.5..1.5),
    # where a2 has the patch → diff = 0.3-0.6 = -0.3 for rows 1..2 col 0..1.
    # Assert the stats are computed over exactly that overlap diff grid:
    diff_overlap = a2[:, 0:3] - a1[:, 3:6]
    assert cls["valid_pixels"] == 18
    assert change["delta_mean"] == pytest.approx(float(np.mean(diff_overlap)))


def test_disjoint_footprints_fall_back_with_note(monkeypatch):
    """Adversarial: no common footprint → no pixel comparison is possible;
    the response must degrade explicitly (fallback method), never present
    scene-mean numbers as a pixel verdict."""
    a1 = np.full((4, 4), 0.3)
    a2 = np.full((4, 4), 0.9)
    _patch_engine(
        monkeypatch,
        _index_result(a1, [0.0, 0.0, 1.0, 1.0]),
        _index_result(a2, [5.0, 5.0, 6.0, 6.0]),  # disjoint
    )

    result = _run(spatial_tasks)
    assert result["success"] is True
    change = result["data"]["change"]
    assert change["method"] == "scene_mean_fallback"
    assert change["classification"] is None
    assert change["delta_mean"] == pytest.approx(0.6)
    assert change["category"] == "significant_improvement"


def test_all_nodata_common_window(monkeypatch):
    """Adversarial: entirely-NaN common window → no valid pixels, category
    unknown, no crash."""
    a1 = np.full((4, 4), np.nan)
    a2 = np.full((4, 4), np.nan)
    bounds = [0.0, 0.0, 1.0, 1.0]
    _patch_engine(monkeypatch, _index_result(a1, bounds), _index_result(a2, bounds))

    result = _run(spatial_tasks)
    assert result["success"] is True
    cls = result["data"]["change"]["classification"]
    assert cls["valid_pixels"] == 0
    assert result["data"]["change"]["category"] == "unknown"


def test_partial_nodata_masked_per_pixel(monkeypatch):
    """NaN cells in either epoch are excluded per-pixel (cloud in T2 must not
    fabricate a change)."""
    a1 = np.full((4, 4), 0.3)
    a2 = np.full((4, 4), 0.3)
    a2[0:2, 0:2] = 0.9   # +0.6 improvement patch
    a2[2:4, 2:4] = np.nan  # cloud in T2 → excluded
    bounds = [0.0, 0.0, 1.0, 1.0]
    _patch_engine(monkeypatch, _index_result(a1, bounds), _index_result(a2, bounds))

    result = _run(spatial_tasks)
    cls = result["data"]["change"]["classification"]
    assert cls["valid_pixels"] == 12
    assert cls["class_counts"]["significant_improvement"] == 4
    assert cls["class_counts"]["no_change"] == 8
