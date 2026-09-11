"""V3 §G：边界指标 / 分区指标 / 漂移指标 的确定性契约测试。"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.modelops.evaluation import (
    boundary_band,
    boundary_metrics,
    class_distribution_psi,
    per_region_metrics,
    population_stability_index,
)


def test_boundary_band_extraction():
    mask = np.zeros((8, 8), dtype=bool)
    mask[2:6, 2:6] = True
    band = boundary_band(mask)
    # 内部 (3:5,3:5) 非边界；边界环存在。
    assert not band[3, 3] and not band[4, 4]
    assert band[2, 2] and band[5, 5]


def test_boundary_metrics_perfect_vs_shifted():
    truth = np.zeros((32, 32), dtype=np.uint8)
    truth[8:24, 8:24] = 1  # 紧凑方块（边界明确）
    perfect = boundary_metrics(truth, truth, tolerance_px=1)
    assert perfect["f1"] == 1.0
    # 平移 4 像素：容差 1 内边界无匹配 → 低 F1。
    shifted = np.roll(truth, 4, axis=1)
    far = boundary_metrics(truth, shifted, tolerance_px=1)
    assert far["f1"] < 0.5
    # 容差 4 内可高度匹配。
    tolerant = boundary_metrics(truth, shifted, tolerance_px=4)
    assert tolerant["f1"] > 0.9


def test_per_region_metrics_separates_regions():
    truth = np.zeros((16, 16), dtype=np.uint8)
    pred = truth.copy()
    truth[0:8, 0:8] = 1
    pred[0:8, 0:8] = 1          # region 1：完美
    truth[8:, 8:] = 1
    pred[8:, 8:] = 0            # region 2：全错
    regions = np.zeros((16, 16), dtype=np.int64)
    regions[0:8, :] = 1
    regions[8:, :] = 2
    report = per_region_metrics(truth, pred, regions, num_classes=2)
    assert report["regions"]["1"]["miou"] > 0.99
    # region 2：一半真负例正确 + 一半漏检 → miou = 0.25（可手算的 oracle）。
    assert report["regions"]["2"]["miou"] == pytest.approx(0.25, abs=1e-6)


def test_psi_stable_vs_drifted():
    rng = np.random.default_rng(7)
    base = rng.normal(0.0, 1.0, size=5000).tolist()
    same = rng.normal(0.0, 1.0, size=5000).tolist()
    shifted = rng.normal(3.0, 1.0, size=5000).tolist()
    assert population_stability_index(base, same) < 0.1
    assert population_stability_index(base, shifted) > 0.25


def test_class_distribution_psi_detects_flip():
    psi_stable = class_distribution_psi([900, 100], [890, 110])
    psi_flip = class_distribution_psi([900, 100], [100, 900])
    assert psi_stable < 0.1
    assert psi_flip > 0.5


# ── service 漂移评估（端到端）────────────────────────────────────────


def test_evaluation_report_has_boundary_and_folds(service, tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    from app.services.modelops.evaluation_service import EvaluationRequest

    truth = (np.random.default_rng(5).random((64, 64)) > 0.5).astype(np.uint8)
    pred = truth.copy()
    pred[30, :] = 0
    for name, data in (("ref.tif", truth), ("prd.tif", pred)):
        with rasterio.open(
            tmp_path / name, "w", driver="GTiff", width=64, height=64,
            count=1, dtype="uint8", crs="EPSG:4326",
            transform=from_origin(0, 64, 1, 1), nodata=255,
        ) as dst:
            dst.write(data, 1)
    report = service.evaluate(
        EvaluationRequest(
            owner_scope={"session_id": "s-ev"},
            task_type="segmentation",
            predictions_path=tmp_path / "prd.tif",
            references_path=tmp_path / "ref.tif",
            num_classes=2,
            num_folds=4,
            model_id="tiny-landcover-seg",
            model_version="1.0.0",
        )
    )
    assert report["boundary"]["f1"] > 0.9
    assert report["per_fold"], "4 folds requested → at least one scored"
    # 自动 lineage：评估事件已记录。
    lineage = service.model_history("tiny-landcover-seg", model_version="1.0.0")
    events = lineage["versions"]["1.0.0"]["events"]
    assert any(e["event_type"] == "evaluation" for e in events)


def test_drift_report_regions_and_psi(service, tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    from app.services.modelops.evaluation_service import DriftEvaluationRequest

    def _write(name, data):
        with rasterio.open(
            tmp_path / name, "w", driver="GTiff", width=32, height=32,
            count=1, dtype="uint8", crs="EPSG:4326",
            transform=from_origin(0, 32, 1, 1), nodata=255,
        ) as dst:
            dst.write(data, 1)
        return tmp_path / name

    base = np.zeros((32, 32), dtype=np.uint8)
    base[0:16, :] = 1
    cur = base.copy()
    cur[0:16, :] = 0  # 类别整体翻转 → 显著漂移
    regions = np.zeros((32, 32), dtype=np.int64)
    regions[:, 0:16] = 1
    regions[:, 16:] = 2

    report = service.evaluate_drift(
        DriftEvaluationRequest(
            owner_scope={"session_id": "s-drift"},
            baseline_path=_write("base.tif", base),
            current_path=_write("cur.tif", cur),
            regions_path=_write("regions.tif", regions.astype(np.uint8)),
            baseline_tags={"time": "2025-01", "sensor": "S2"},
            current_tags={"time": "2025-06", "sensor": "S2"},
            num_classes=2,
            model_id="tiny-landcover-seg",
            model_version="1.0.0",
        )
    )
    assert report["class_distribution_psi"] > 0.5
    assert report["baseline_tags"]["time"] == "2025-01"
    assert "per_region" in report
    # lineage 自动记录漂移评估。
    lineage = service.model_history("tiny-landcover-seg", model_version="1.0.0")
    events = lineage["versions"]["1.0.0"]["events"]
    assert any(e["event_type"] == "evaluation" and
               "agreement_miou" in (e.get("payload", {}).get("metrics_summary") or {})
               for e in events)
