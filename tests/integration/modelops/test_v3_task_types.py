"""V3 §C 集成切片：change_detection / super_resolution / temporal_classification
/ sar_optical_fusion 四类新任务的端到端（种子模型 → engine → georef 产物）。"""
from __future__ import annotations

import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


# ── fixtures：任务专用合成栅格 ───────────────────────────────────────


def _write_raster(path, data, *, crs="EPSG:32650", transform=None, nodata=-9999.0):
    count, height, width = data.shape
    profile = {
        "driver": "GTiff", "width": width, "height": height, "count": count,
        "dtype": "float32", "crs": crs,
        "transform": transform or from_origin(500000.0, 4000000.0, 10.0, 10.0),
        "nodata": nodata, "tiled": True, "blockxsize": 64, "blockysize": 64,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    return path


@pytest.fixture()
def bitemporal_pair(tmp_path):
    """网格对齐的双时相 RGB 栅格：B 在东南角新增亮块（变化）。"""
    transform = from_origin(500000.0, 4000000.0, 10.0, 10.0)
    base = np.full((3, 96, 96), 0.25, dtype=np.float32)
    base[:, 10:26, 10:26] = 0.85
    before = _write_raster(tmp_path / "t0.tif", base, transform=transform)
    after_arr = base.copy()
    after_arr[:, 60:80, 60:80] = 0.95  # 变化区域
    after = _write_raster(tmp_path / "t1.tif", after_arr, transform=transform)
    return before, after


@pytest.fixture()
def sr_raster(tmp_path):
    """超分辨率输入：3 波段、已知常数值块（放大后值可断言）。"""
    data = np.full((3, 64, 64), 0.3, dtype=np.float32)
    data[:, 20:40, 20:40] = 0.8
    return _write_raster(tmp_path / "sr_in.tif", data)


@pytest.fixture()
def temporal_stack_raster(tmp_path):
    """C=2, T=2 → 4 波段时序栈：t1 亮度更高（类别应随时间变化）。"""
    bands = [
        np.full((32, 32), 0.2, dtype=np.float32),
        np.full((32, 32), 0.3, dtype=np.float32),
        np.full((32, 32), 1.2, dtype=np.float32),
        np.full((32, 32), 1.1, dtype=np.float32),
    ]
    data = np.stack(bands)
    profile = {
        "driver": "GTiff", "width": 32, "height": 32, "count": 4,
        "dtype": "float32", "crs": "EPSG:4326",
        "transform": from_origin(116.0, 40.0, 1.0, 1.0),
        "nodata": -9999.0,
    }
    path = tmp_path / "stack2.tif"
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    return path


@pytest.fixture()
def fused_raster(tmp_path):
    """SAR(VV,VH)+光学(R,G,B) 5 波段：高回波+高亮 = urban 类输入。"""
    data = np.full((5, 96, 96), 0.2, dtype=np.float32)
    data[0:2, 30:60, 30:60] = 0.9   # VV/VH 强回波
    data[2:5, 30:60, 30:60] = 0.85  # 光学高亮
    return _write_raster(tmp_path / "fused.tif", data)


# ── change detection ─────────────────────────────────────────────────


def test_change_detection_end_to_end(service, bitemporal_pair, isolated_blobs):
    from app.services.modelops.engine import InferenceRequest

    before, after = bitemporal_pair
    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-bitemporal-change",
            source_uri=str(before),
            source_uri_b=str(after),
            owner_scope={"session_id": "s-chg"},
        )
    )
    assert result.status == "completed"
    assert result.task_type == "change_detection"
    assert "classes" in result.outputs
    with rasterio.open(result.outputs["classes"]["path"]) as src:
        classes = src.read(1)
        assert src.crs is not None  # 产物继承 georef
    # 变化区 = change(1)；稳定亮块 = no_change(0)。
    assert (classes[60:80, 60:80] == 1).mean() > 0.9
    assert (classes[10:26, 10:26] == 0).mean() > 0.9
    assert (classes[0:5, 0:5] == 0).mean() > 0.9


def test_change_detection_requires_b_raster(service, bitemporal_pair):
    from app.lib.modelops.errors import ModelOpsError
    from app.services.modelops.engine import InferenceRequest

    before, _ = bitemporal_pair
    with pytest.raises(ModelOpsError):
        service.run_inference(
            InferenceRequest(
                model_id="tiny-bitemporal-change",
                source_uri=str(before),
                owner_scope={"session_id": "s-chg2"},
            )
        )


def test_change_detection_rejects_misaligned_grid(service, bitemporal_pair, tmp_path):
    from app.lib.modelops.errors import PlanningError
    from app.services.modelops.engine import InferenceRequest

    before, after = bitemporal_pair
    shifted = _write_raster(
        tmp_path / "shifted.tif",
        np.full((3, 96, 96), 0.3, dtype=np.float32),
        transform=from_origin(500100.0, 4000000.0, 10.0, 10.0),  # 平移一个像元
    )
    with pytest.raises(PlanningError):
        service.run_inference(
            InferenceRequest(
                model_id="tiny-bitemporal-change",
                source_uri=str(before),
                source_uri_b=str(shifted),
                owner_scope={"session_id": "s-chg3"},
            )
        )
    assert after.exists()  # noqa: B018 — 语义占位（B 未被误用）


def test_change_detection_b_image_in_reuse_key(service, bitemporal_pair, tmp_path, isolated_blobs):
    """不同 B 影像 → 不同 reuse key（B1 身份纪律的双时相扩展）。"""
    from app.services.modelops.engine import InferenceRequest

    before, after = bitemporal_pair
    other_b = _write_raster(
        tmp_path / "other_b.tif",
        np.full((3, 96, 96), 0.25, dtype=np.float32),
    )
    r1 = service.run_inference(
        InferenceRequest(model_id="tiny-bitemporal-change", source_uri=str(before),
                         source_uri_b=str(after), owner_scope={"session_id": "s-chg4"})
    )
    r2 = service.run_inference(
        InferenceRequest(model_id="tiny-bitemporal-change", source_uri=str(before),
                         source_uri_b=str(other_b), owner_scope={"session_id": "s-chg4"})
    )
    assert r1.status == "completed" and r2.status == "completed"
    assert r1.reuse_key != r2.reuse_key


# ── super resolution ─────────────────────────────────────────────────


def test_super_resolution_output_georef(service, sr_raster, isolated_blobs):
    from app.services.modelops.engine import InferenceRequest

    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-superres-x2",
            source_uri=str(sr_raster),
            owner_scope={"session_id": "s-sr"},
        )
    )
    assert result.task_type == "super_resolution"
    assert "superres" in result.outputs
    with rasterio.open(sr_raster) as src:
        src_transform = src.transform
    with rasterio.open(result.outputs["superres"]["path"]) as out:
        assert out.width == 128 and out.height == 128  # 64x64 ×2
        assert out.count == 3
        # 同一地理范围 → 像元尺寸减半（输出坐标正确性的直接验收）。
        assert abs(out.transform.a - src_transform.a / 2) < 1e-9
        assert abs(out.transform.e - src_transform.e / 2) < 1e-9
        band1 = out.read(1)
    # 双线性放大常数块：块内值保持 0.8（确定性 oracle）。
    assert abs(float(band1[50:70, 50:70].mean()) - 0.8) < 0.02


# ── temporal classification ──────────────────────────────────────────


def test_temporal_classification_sequence(service, temporal_stack_raster, isolated_blobs):
    from app.lib.modelops.temporal import TemporalStackSpec
    from app.services.modelops.engine import InferenceRequest

    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-temporal-classifier",
            source_uri=str(temporal_stack_raster),
            owner_scope={"session_id": "s-tcls"},
            temporal=TemporalStackSpec(times=("2025-01-01", "2025-06-01"), max_length=8),
        )
    )
    assert result.task_type == "temporal_classification"
    out = result.outputs["temporal_classification"]
    payload = json.loads(open(out["path"], encoding="utf-8").read())
    assert [c["time"] for c in payload["classes"]] == ["2025-01-01", "2025-06-01"]
    labels = [c["label"] for c in payload["classes"]]
    assert labels[0] != labels[1]  # 亮度跃迁应改变类别（确定性 oracle）
    assert all(c["label_name"] for c in payload["classes"])


# ── SAR+optical fusion ───────────────────────────────────────────────


def test_sar_optical_fusion_segmentation(service, fused_raster, isolated_blobs):
    from app.services.modelops.engine import InferenceRequest
    from app.services.modelops.providers.fusion_reference import derive_fusion_params
    from app.services.modelops.seeds import seed_descriptors

    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-sar-optical-fusion",
            source_uri=str(fused_raster),
            owner_scope={"session_id": "s-fus"},
        )
    )
    assert result.task_type == "sar_optical_fusion"
    assert "classes" in result.outputs
    with rasterio.open(result.outputs["classes"]["path"]) as src:
        classes = src.read(1)
    # 确定性 oracle：用 provider 的派生参数重算期望类别（权重=身份函数）。
    desc = next(d for d in seed_descriptors() if d.model_id == "tiny-sar-optical-fusion")
    sar_w, anchors = derive_fusion_params(desc.checksum, 3)

    def expected(sar, opt):
        fused = sar_w * sar + (1 - sar_w) * opt
        return int(np.abs(anchors - fused).argmin())

    assert (classes[40:50, 40:50] == expected(0.9, 0.85)).mean() > 0.9
    assert (classes[0:5, 0:5] == expected(0.2, 0.2)).mean() > 0.9


# ── 新任务可见于工具面 ───────────────────────────────────────────────


def test_new_task_types_in_service_listing(service):
    models = service.list_models(session_id=None, project_id=None)
    by_id = {m["model_id"]: m for m in models}
    assert "change_detection" in by_id["tiny-bitemporal-change"]["task_types"]
    assert "super_resolution" in by_id["tiny-superres-x2"]["task_types"]
    assert "temporal_classification" in by_id["tiny-temporal-classifier"]["task_types"]
    assert "sar_optical_fusion" in by_id["tiny-sar-optical-fusion"]["task_types"]


# ── V3 §D：ROI + 矢量化端到端 ────────────────────────────────────────


def test_segmentation_with_roi_and_vectorize(service, synthetic_raster, isolated_blobs):
    """ROI 裁剪推理 + 类别多边形：产物 georef 落在 ROI 范围。"""
    from app.services.modelops.engine import InferenceRequest

    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-landcover-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s-roi"},
            roi_bbox=(20, 10, 84, 74),       # 64x64 ROI
            vectorize_classes=True,
        )
    )
    assert result.status in ("completed", "reused")
    assert "class_polygons" in result.outputs
    import rasterio

    with rasterio.open(result.outputs["classes"]["path"]) as src:
        # 产物只覆盖 ROI（64x64），且 transform 平移到 ROI 原点。
        assert src.width == 64 and src.height == 64
        assert src.crs == "EPSG:4326"
        # 源 transform 原点 (116, 40)，1°/px；ROI (20,10) → 平移后原点。
        assert abs(src.transform.c - (116.0 + 20 * 1.0)) < 1e-9
        assert abs(src.transform.f - (40.0 - 10 * 1.0)) < 1e-9
    poly = result.outputs["class_polygons"]
    assert poly.get("data_object_id")
    payload = result.manifest["postprocess"]
    assert payload["roi"] == [20, 10, 84, 74]
    assert payload["vectorize"]["enabled"] is True


def test_roi_rejected_for_promptable(service, synthetic_raster):
    from app.lib.modelops.errors import ModelOpsError
    from app.services.modelops.engine import InferenceRequest

    with pytest.raises(ModelOpsError):
        service.run_inference(
            InferenceRequest(
                model_id="tiny-promptable-seg",
                source_uri=str(synthetic_raster),
                owner_scope={"session_id": "s-roi2"},
                roi_bbox=(0, 0, 64, 64),
            )
        )
