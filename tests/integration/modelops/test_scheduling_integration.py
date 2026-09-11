"""V3 §E 集成：service 级 warm pool + VRAM 账本观测（无 GPU 机器同样有效）。"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


def test_service_warm_pool_pin_and_status(service):
    # 钉一个种子模型（CPU；确定性 tiny provider，加载零成本）。
    state = service.pin_warm_pool("tiny-landcover-seg")
    assert state["pinned"] is True
    status = service.warm_pool_status()
    assert status["pinned"]["tiny-landcover-seg"]["pinned"] is True
    assert "vram_ledger" in status
    # 账本观测面完整（无 GPU 机器：devices 为空是合法的诚实结果）。
    assert isinstance(status["vram_ledger"]["devices"], list)
    assert "used" in status["vram_ledger"]


def test_service_run_with_ledger_records_reservation(service, synthetic_raster):
    from app.services.modelops.engine import InferenceRequest

    service.pin_warm_pool("tiny-landcover-seg")
    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-landcover-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s-sched"},
        )
    )
    assert result.status in ("completed", "reused")
    manifest = result.manifest
    assert manifest["device_plan"]["device"] == "cpu"
    assert manifest["device_plan"]["device_index"] == 0
    # 推理完成后预订已归还（无泄漏）。
    assert service.warm_pool_status()["vram_ledger"]["used"].get("cpu:0", 0) == 0


def test_ledger_released_when_provider_load_fails(service, tmp_path, isolated_blobs):
    """provider load 永久失败 → VRAM 预订必须归还（账本无泄漏）。"""




    from app.lib.modelops.descriptor import GeoModelDescriptor
    from app.services.modelops.engine import InferenceRequest

    # onnx 模型（真实包注册成功），但 load 抛 typed 错 → 账本泄漏路径。
    from tests.unit.modelops.onnx_fixtures import build_onnx_segmentation_model

    path = tmp_path / "tiny.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=32, height=32, count=3,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(0.0, 32.0, 1.0, 1.0), nodata=-9999.0,
    ) as dst:
        dst.write(np.zeros((3, 32, 32), dtype=np.float32))
    onnx_bytes = build_onnx_segmentation_model()
    descriptor = GeoModelDescriptor.model_validate(
        {
            "model_id": "leaky-load-onnx", "model_version": "1.0.0",
            "checksum": hashlib.sha256(onnx_bytes).hexdigest(),
            "provider_type": "onnx_adapter", "provider_ref": "onnx-runtime",
            "provider_semantic_version": "onnx-adapter/1.0.0",
            "task_types": ("semantic_segmentation",),
            "input_modalities": ("optical_rgb",), "input_bands": 3,
            "output_types": ("class_raster",),
            "class_schema": {"classes": ("background", "bright", "mid")},
            "spatial": {"chip_size": [16, 16], "context_size": [16, 16]},
            "artifact_format": "onnx-v1", "license": "test",
        }
    )
    service.register_model(
        descriptor, owner_scope={"session_id": "s-leak"},
        package_bytes=onnx_bytes, registered_by="test",
    )
    # load 阶段 typed 失败（loaded_cache.acquire 抛错路径）。
    from app.lib.modelops.errors import ProviderLoadFailed

    provider = service._providers.get("onnx-runtime")

    def _boom(descriptor, *, device):
        raise ProviderLoadFailed("simulated runtime failure")

    original_load = provider.load
    provider.load = _boom
    # 让预订非零（onnx CPU 估计 vram=0 → 0 字节预订测不出泄漏）。
    from app.lib.modelops.resources import ResourceEstimate as _RE

    original_estimate = provider.estimate_resources
    provider.estimate_resources = (
        lambda d, *, batch, device: _RE(vram_bytes=4096, host_ram_bytes=8192,
                                        recommended_batch=1)
    )
    # spy：验证 release 被调用且带非零字节（否则断言空洞——onnx CPU
    # 估计 vram=0 时 release(0) 无法区分泄漏）。
    ledger = service._ledger
    released = []
    original_release = ledger.release
    ledger.release = lambda res: (released.append(res.bytes_reserved),
                                  original_release(res))
    try:
        with pytest.raises(ProviderLoadFailed):
            service.run_inference(
                InferenceRequest(
                    model_id="leaky-load-onnx", source_uri=str(path),
                    owner_scope={"session_id": "s-leak"},
                )
            )
    finally:
        provider.load = original_load
        provider.estimate_resources = original_estimate
        ledger.release = original_release
    assert released and released[0] > 0, "load 失败路径必须释放非零预订"
    # 预订已归还（失败路径无泄漏）。
    assert service.warm_pool_status()["vram_ledger"]["used"].get("cpu:0", 0) == 0
