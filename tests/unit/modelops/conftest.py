"""ModelOps 单元测试共享 fixtures（确定性、无网络、无大模型下载）。"""
from __future__ import annotations

import pytest


@pytest.fixture()
def tiny_desc_factory():
    """可定制的 descriptor 构造器（默认合法；按需覆写字段）。"""
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        MemoryEstimate,
        NormalizationSpec,
        ResolutionRange,
        SpatialRequirements,
    )

    def make(**overrides):
        payload = {
            "model_id": "unit-model",
            "model_version": "1.0.0",
            "provider_type": "local_reference",
            "provider_ref": "unit-provider",
            "provider_semantic_version": "unit/1.0.0",
            "task_types": ("semantic_segmentation",),
            "input_modalities": ("optical_rgb",),
            "input_bands": 3,
            "band_order": ("red", "green", "blue"),
            "normalization": NormalizationSpec(kind="none"),
            "output_types": ("class_raster", "confidence_raster"),
            "class_schema": ClassSchema(classes=("background", "bright", "mid")),
            "spatial": SpatialRequirements(
                chip_size=(16, 16),
                context_size=(16, 16),
                resolution_range=ResolutionRange(min_m_per_px=0.1, max_m_per_px=10.0),
            ),
            "device_requirements": DeviceRequirements(required="cpu"),
            "memory_estimate": MemoryEstimate(weights_bytes=1024),
            "license": "test",
            "checksum": "a" * 64,
        }
        payload.update(overrides)
        return GeoModelDescriptor.model_validate(payload)

    return make
