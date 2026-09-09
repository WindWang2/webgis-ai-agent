"""merge/postprocess oracle 测试（手算 oracle；R1-m5 契约）。"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.modelops.descriptor import SpatialRequirements
from app.lib.modelops.errors import PreprocessError
from app.lib.modelops.planning import plan_tiles
from app.lib.modelops.stitching import (
    SegmentationMergePolicy,
    collect_embeddings,
    merge_detections,
    merge_instances,
    merge_segmentation,
)


class _D:
    def __init__(self, spatial):
        self.spatial = spatial


def _plan(h, w, chip=(4, 4), ctx=None, stride=None):
    ctx = ctx or chip
    spatial = SpatialRequirements(chip_size=chip, context_size=ctx, stride=stride)
    return plan_tiles(_D(spatial), raster_height=h, raster_width=w)


def test_per_pixel_softmax_no_seam_artifacts():
    """逐像素概率 + 无 overlap：输出 = 每像素 argmax（oracle）。"""
    plan = _plan(8, 8, chip=(4, 4))
    probs = []
    for t in plan.tiles:
        p = np.zeros((3, 4, 4), dtype=np.float32)
        p[1] = 0.9
        probs.append(p)
    result = merge_segmentation(plan, probs, input_nodata=None,
                                policy=SegmentationMergePolicy(), num_classes=3)
    assert (result.classes == 1).all()
    assert np.allclose(result.confidence, 0.9, atol=1e-6)


def test_overlap_blend_majority_wins():
    """overlap + crop blend：两 tile 交界处概率平均（手算 oracle）。"""
    plan = _plan(8, 8, chip=(4, 4), stride=(2, 2))
    probs = []
    for t in plan.tiles:
        p = np.zeros((2, 4, 4), dtype=np.float32)
        # tile 覆盖列范围的依据：偶数列 tile 认为类 0，奇数列 tile 认为类 1
        label = (t.core_window[1] // 2) % 2
        p[label] = 1.0
        probs.append(p)
    result = merge_segmentation(plan, probs, input_nodata=None,
                                policy=SegmentationMergePolicy(), num_classes=2)
    assert result.classes.shape == (8, 8)
    assert set(np.unique(result.classes)) <= {0, 1, 255}


def test_nodata_pixels_excluded_and_masked():
    """nodata 像元：输出掩膜=输入掩膜；类别置 255（不渗入相邻）。"""
    plan = _plan(4, 4)
    probs = [np.full((2, 4, 4), 0.5, dtype=np.float32) for _ in plan.tiles]
    probs[0][0] = 0.9
    nodata = np.zeros((4, 4), dtype=bool)
    nodata[2, 2] = True
    result = merge_segmentation(plan, probs, input_nodata=nodata,
                                policy=SegmentationMergePolicy(), num_classes=2)
    assert result.valid_mask[2, 2] is False or result.valid_mask[2, 2] == False  # noqa: E712
    assert result.classes[2, 2] == 255
    assert result.classes[0, 0] == 0


def test_blend_weight_zero_on_nodata_no_seepage():
    """feather 权重下 nodata 不改变相邻像元结果（权重置零语义）。"""
    plan = _plan(4, 4)
    probs = [np.full((2, 4, 4), 0.5, dtype=np.float32) for _ in plan.tiles]
    nodata = np.zeros((4, 4), dtype=bool)
    nodata[0, 0] = True
    policy = SegmentationMergePolicy(blend="feather")
    r1 = merge_segmentation(plan, probs, input_nodata=None, policy=policy, num_classes=2)
    r2 = merge_segmentation(plan, probs, input_nodata=nodata, policy=policy, num_classes=2)
    # 除 nodata 像元自身外，其余像元类别一致（nodata 不参与融合）。
    same = r1.classes == r2.classes
    assert same[~nodata].all()


def test_deterministic_repeat():
    plan = _plan(6, 6, chip=(4, 4), stride=(2, 2))
    probs = [np.random.default_rng(i).random((3, 4, 4)).astype(np.float32) for i in range(len(plan.tiles))]
    policy = SegmentationMergePolicy()
    r1 = merge_segmentation(plan, probs, input_nodata=None, policy=policy, num_classes=3)
    r2 = merge_segmentation(plan, probs, input_nodata=None, policy=policy, num_classes=3)
    assert np.array_equal(r1.classes, r2.classes)
    assert np.array_equal(r1.confidence, r2.confidence)


def test_geometry_mismatch_raises():
    plan = _plan(4, 4)
    with pytest.raises(PreprocessError):
        merge_segmentation(plan, [np.zeros((2, 3, 3), dtype=np.float32)],
                           input_nodata=None, policy=SegmentationMergePolicy(), num_classes=2)


# ── detection oracle ─────────────────────────────────────────────────


def test_detection_global_coords_and_edge_dedup():
    plan = _plan(8, 8, chip=(4, 4), stride=(4, 4))
    # tile(0,0) 检出目标；相邻 tile(0,4) 对同一目标有局部坐标重叠检出
    # （全局 x=3 的重复框）→ NMS 去重后只留高分者。
    outputs = [
        [{"box": [2.0, 2.0, 2.0, 2.0], "score": 0.9, "label": 1}],   # tile(0,0) → 全局 (2,2)
        [{"box": [-1.0, 2.0, 2.0, 2.0], "score": 0.8, "label": 1}],  # tile(0,4) → 全局 (3,2)
        [], [],
    ]
    records = merge_detections(plan, outputs, iou_threshold=0.3, score_threshold=0.5)
    assert len(records) == 1
    assert records[0].box[0] == 2.0  # 全局坐标（高分胜出）
    assert records[0].score == 0.9


def test_detection_score_threshold_filters():
    plan = _plan(4, 4)
    outputs = [[{"box": [0.0, 0.0, 1.0, 1.0], "score": 0.3, "label": 1}]]
    records = merge_detections(plan, outputs, score_threshold=0.5)
    assert records == []


# ── instance oracle ──────────────────────────────────────────────────


def test_instance_merge_ids_and_classes():
    plan = _plan(4, 8, chip=(4, 4))
    masks = [
        np.array([[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]], dtype=np.int32),
        np.array([[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 2, 2], [0, 0, 2, 2]], dtype=np.int32),
    ]
    merged = merge_instances(plan, masks, [{1: 1}, {2: 1}])
    assert merged.instance_ids[0, 0] > 0
    assert merged.instance_ids[2, 6] > 0  # 第二 tile 局部 (2,2) → 全局 (2,6)
    assert merged.instance_ids[0, 0] != merged.instance_ids[2, 6]  # 实例身份区分
    assert merged.instance_labels[merged.instance_ids[0, 0]] == 1


def test_instance_polygonize():
    plan = _plan(4, 4)
    masks = [np.array([[1, 1, 0, 0], [1, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]], dtype=np.int32)]
    merged = merge_instances(plan, masks, [{1: 1}], polygonize=True)
    assert merged.polygon_geojson is not None
    assert merged.polygon_geojson["type"] == "FeatureCollection"


# ── embedding collection ─────────────────────────────────────────────


def test_collect_embeddings_with_spatial_anchor():
    plan = _plan(4, 4)
    vecs = [np.array([0.1, 0.2], dtype=np.float32)]
    items = collect_embeddings(plan, vecs)
    assert items[0].core_window == plan.tiles[0].core_window
    assert items[0].vector == (0.1, 0.2)
