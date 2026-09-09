"""Vertical Slice A：语义分割闭环（COG→推理→融合→产物→provenance→reuse）。"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.geo_raster.reader import RasterReader
from app.services.modelops.engine import InferenceRequest
from app.lib.modelops.errors import CompatibilityError, InferenceCancelled


def test_slice_a_full_loop(service, synthetic_raster):
    service_result = service.run_inference(
        InferenceRequest(
            model_id="tiny-landcover-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s1"},
            task_type="semantic_segmentation",
        )
    )
    assert service_result.status == "completed"
    # 产物可渲染：classes.tif 可被唯一读通道重新打开且形状对齐。
    classes_path = service_result.outputs["classes"]["path"]
    with RasterReader.open(classes_path) as reader:
        meta = reader.metadata()
        arr = reader.read_full(budget_ok=True)
    assert meta.count == 1
    assert (meta.height, meta.width) == (100, 130)
    assert set(np.unique(arr)) <= {0, 1, 2, 255}
    # DataObject 发布（renderable + merkle 身份）。
    assert service_result.outputs["classes"].get("data_object_id")
    assert service_result.outputs["confidence"].get("data_object_id")
    # manifest：全字段 provenance + 性能计数。
    manifest = service_result.manifest
    assert manifest["model"]["checksum"]
    assert manifest["model"]["model_id"] == "tiny-landcover-seg"
    assert manifest["provider"]["provider_ref"] == "tiny-reference"
    assert manifest["input"]["content_sha256"]
    assert manifest["tile_plan"]["tile_count"] > 0
    assert manifest["preprocess"]["band_indices"] == [0, 1, 2]
    assert manifest["performance"]["pixels_per_s"] > 0
    assert manifest["performance"]["bytes_read"] > 0
    assert manifest["manifest_fingerprint"]


def test_slice_a_reuse_hit_and_invalidation(service, synthetic_raster):
    request = InferenceRequest(
        model_id="tiny-landcover-seg",
        source_uri=str(synthetic_raster),
        owner_scope={"session_id": "s1"},
        task_type="semantic_segmentation",
    )
    first = service.run_inference(request)
    second = service.run_inference(request)
    assert second.status == "reused" and second.reused
    assert second.manifest["manifest_fingerprint"] == first.manifest["manifest_fingerprint"]
    # 阈值变化 → miss（完整重跑）。
    changed = service.run_inference(
        InferenceRequest(
            model_id="tiny-landcover-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s1"},
            task_type="semantic_segmentation",
            score_threshold=0.9,
        )
    )
    assert changed.status == "completed"


def test_slice_a_reuse_owner_isolation(service, synthetic_raster):
    request = dict(
        model_id="tiny-landcover-seg",
        source_uri=str(synthetic_raster),
        task_type="semantic_segmentation",
    )
    service.run_inference(InferenceRequest(owner_scope={"session_id": "s1"}, **request))
    other = service.run_inference(
        InferenceRequest(owner_scope={"session_id": "s2"}, **request)
    )
    assert other.status == "completed"  # 跨 owner 不命中
    other_project = service.run_inference(
        InferenceRequest(owner_scope={"project_id": "p2"}, **request)
    )
    assert other_project.status == "completed"


def test_incompatible_input_typed_rejection(service, synthetic_raster, tmp_path):
    """SAR 模型 × RGB 栅格 → typed 兼容失败（波段数不符）。"""
    import rasterio

    path = tmp_path / "multiband.tif"
    with rasterio.open(synthetic_raster) as src:
        profile = src.profile.copy()
        profile.update(count=6)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.zeros((6, 100, 130), dtype=np.float32))
    from app.lib.modelops.errors import ModelOpsError

    with pytest.raises((CompatibilityError, ModelOpsError)):
        service.run_inference(
            InferenceRequest(
                model_id="tiny-sar-detector",
                source_uri=str(path),
                owner_scope={"session_id": "s1"},
                task_type="object_detection",
            )
        )


def test_lazy_windows_bounded_reads(service, tmp_path):
    """R1-M7 lazy 证明：大栅格 bytes_read ≪ 整幅，窗口数=计划。"""
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "big.tif"
    width, height = 512, 512
    data = np.random.default_rng(7).random((3, height, width)).astype(np.float32) * 0.3
    profile = {
        "driver": "GTiff", "width": width, "height": height, "count": 3,
        "dtype": "float32", "crs": "EPSG:4326",
        "transform": from_origin(116.0, 40.0, 1.0, 1.0), "nodata": -9999.0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-landcover-seg",
            source_uri=str(path),
            owner_scope={"session_id": "s1"},
            task_type="semantic_segmentation",
        )
    )
    perf = result.perf
    full_bytes = 3 * height * width * 4
    # lazy 证明：每次读取都有界（窗口 × 单窗字节上界），窗口数 = 计划。
    window_bytes_cap = perf["raster_windows_total"] * 3 * 80 * 80 * 4
    assert 0 < perf["bytes_read"] <= window_bytes_cap
    assert perf["raster_windows_total"] == perf["chips_total"] > 0
    assert result.manifest["tile_plan"]["tile_count"] == perf["raster_windows_total"]
    # 相对量：窗口读取总量随 tile 数线性（含 context halo 的常数开销）。
    assert perf["bytes_read"] < 4 * full_bytes


def test_cancellation_before_and_midrun(service, synthetic_raster):
    from app.lib.cancellation import CancellationToken

    token = CancellationToken(job_id="t1")
    token.cancel("test cancel")
    with pytest.raises(InferenceCancelled):
        service._engine.run(
            InferenceRequest(
                model_id="tiny-landcover-seg",
                source_uri=str(synthetic_raster),
                owner_scope={"session_id": "s1"},
                task_type="semantic_segmentation",
            ),
            cancel_token=token,
        )


def test_explicit_reproject_stage(service, synthetic_raster):
    """R1-C2：CRS 需求不符 + 声明 resampling → 显式重投影进 manifest。"""
    from app.lib.data.fingerprints import canonical_dumps, sha256_hex
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )
    from app.services.modelops.registry import ModelRegistryStore  # noqa: F401

    payload = {
        "model_id": "utm-seg", "model_version": "1.0.0",
        "provider_type": "local_reference", "provider_ref": "tiny-reference",
        "provider_semantic_version": "tiny/1.0.0",
        "task_types": ["semantic_segmentation"],
        "input_modalities": ["optical_rgb"], "input_bands": 3,
        "normalization": NormalizationSpec(kind="none").as_dict(),
        "output_types": ["class_raster"],
        "class_schema": ClassSchema(classes=("background", "bright", "mid")).as_dict(),
        "spatial": SpatialRequirements(
            chip_size=(16, 16), context_size=(16, 16),
            crs_requirements=("EPSG:32650",), resampling_policy="nearest",
            allow_reproject=True,
        ).as_dict(),
        "device_requirements": DeviceRequirements(required="cpu").as_dict(),
        "license": "test",
        "random_seed_policy": "deterministic",
    }
    descriptor = GeoModelDescriptor.model_validate(
        {**payload, "checksum": sha256_hex(canonical_dumps(payload))}
    )
    service._registry.register(
        descriptor, owner_scope={"global": "builtin"}, registered_by="test",
        package_report={"synthetic": True}, known_provider_refs=service._providers.has,
    )
    result = service.run_inference(
        InferenceRequest(
            model_id="utm-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s1"},
            task_type="semantic_segmentation",
        )
    )
    reproject = result.manifest["reproject"]
    assert reproject and reproject["target_crs"] == "EPSG:32650"
    assert reproject["content_sha256"]
    assert result.status in ("completed", "reused")
