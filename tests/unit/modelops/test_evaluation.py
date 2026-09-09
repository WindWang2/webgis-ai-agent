"""评估平台测试（IoU/AP/ECE oracle + 泄漏防护）。"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.modelops.evaluation import (
    classification_metrics,
    detection_metrics,
    expected_calibration_error,
    leakage_guard,
    segmentation_metrics,
    spatial_blocked_split,
)


def test_iou_oracle_perfect():
    y = np.array([[0, 1], [1, 0]])
    m = segmentation_metrics(y, y, num_classes=2)
    assert m.iou_per_class == (1.0, 1.0)
    assert m.miou == 1.0


def test_iou_oracle_hand_computed():
    y_true = np.array([0, 0, 1, 1, 1])
    y_pred = np.array([0, 1, 1, 1, 0])
    m = segmentation_metrics(
        y_true.reshape(1, 5), y_pred.reshape(1, 5), num_classes=2
    )
    # class0: tp=1 fp=1 fn=1 → iou=1/3；class1: tp=2 fp=1 fn=1 → iou=1/2
    assert m.iou_per_class[0] == pytest.approx(1 / 3, abs=1e-6)
    assert m.iou_per_class[1] == pytest.approx(0.5, abs=1e-6)
    assert m.confusion[0][1] == 1 and m.confusion[1][0] == 1


def test_ignore_index_excluded():
    y_true = np.array([[0, 255], [255, 1]])
    y_pred = np.array([[0, 0], [0, 1]])
    m = segmentation_metrics(y_true, y_pred, num_classes=2, ignore_index=255)
    # 只有 (0,0) 与 (1,1) 参与 → 全对
    assert m.confusion[0][0] == 1 and m.confusion[1][1] == 1


def test_detection_pr_oracle():
    refs = [
        {"box": [0, 0, 10, 10], "label": 1, "score": 1.0},
        {"box": [50, 50, 10, 10], "label": 1, "score": 1.0},
    ]
    preds = [
        {"box": [0, 0, 10, 10], "label": 1, "score": 0.9},   # TP
        {"box": [1, 1, 10, 10], "label": 1, "score": 0.8},   # 与 TP 重叠 → FP
        {"box": [100, 100, 5, 5], "label": 1, "score": 0.7},  # FP
    ]
    m = detection_metrics(preds, refs, iou_threshold=0.5)
    assert m.true_positives == 1
    assert m.false_positives == 2
    assert m.false_negatives == 1
    assert m.precision == pytest.approx(1 / 3, abs=1e-6)
    assert m.recall == pytest.approx(0.5, abs=1e-6)


def test_detection_ap_bounds():
    refs = [{"box": [0, 0, 5, 5], "label": 1, "score": 1.0}]
    preds = [{"box": [0, 0, 5, 5], "label": 1, "score": 0.99}]
    m = detection_metrics(preds, refs)
    assert m.ap == 1.0


def test_ece_extremes():
    assert expected_calibration_error([], []) == 0.0
    perfect = expected_calibration_error([1.0] * 10, [True] * 10)
    assert perfect == pytest.approx(0.0, abs=1e-6)
    worst = expected_calibration_error([0.5] * 10, [True] * 10)
    assert worst == pytest.approx(0.5, abs=1e-6)


def test_classification_metrics():
    out = classification_metrics([0, 1, 1, 0], [0, 1, 0, 0], num_classes=2)
    assert out["accuracy"] == 0.75
    assert out["balanced_accuracy"] == 0.75


def test_spatial_blocked_split_deterministic_and_disjoint():
    s1 = spatial_blocked_split(64, 64, block_size_px=16, num_folds=3)
    s2 = spatial_blocked_split(64, 64, block_size_px=16, num_folds=3)
    assert s1.assignment == s2.assignment
    # 同 block 永远同 fold
    assert s1.fold_of(0, 0) == s1.fold_of(15, 15)
    assert s1.fold_of(16, 0) == s1.fold_of(31, 0)


def test_leakage_guard_detects_overlap():
    report = leakage_guard(train_rows=[(0, 0)], eval_rows=[(1, 1)])
    assert "overlap" in report.detail
    clean = leakage_guard(train_rows=[(0, 0)], eval_rows=[(256, 0)])
    assert "no spatial" in clean.detail


def test_leakage_guard_temporal():
    report = leakage_guard(train_rows=[], eval_rows=[],
                           train_times=["2026-01"], eval_times=["2026-01"])
    assert "temporal overlap" in report.detail
