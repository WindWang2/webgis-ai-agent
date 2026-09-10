"""ModelOps 集成测试共享 fixtures（隔离 blob store / registry / 服务单例）。"""
from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture()
def isolated_blobs(tmp_path, monkeypatch):
    """DataObject blob store → tmp（owner 隔离测试互不污染）。"""
    from app.services.durable_blob_store import FilesystemBlobStore
    import app.services.lakehouse.data_object as data_object

    store = FilesystemBlobStore(tmp_path / "blobs")
    monkeypatch.setattr(data_object, "_store", lambda: store)
    return store


@pytest.fixture()
def service(isolated_blobs, tmp_path, monkeypatch):
    """进程外构造的 ModelOpsService（不用全局单例；注册表在 tmp）。"""
    from app.services.modelops.config import ModelOpsSettings
    from app.services.modelops.service import ModelOpsService

    settings = ModelOpsSettings(registry_dir=tmp_path / "modelops")
    return ModelOpsService(settings)


@pytest.fixture()
def sar_raster(tmp_path):
    """双波段 SAR 形态栅格（VV 强回波块 → 检测目标）。"""
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "sar.tif"
    width, height = 64, 64
    data = np.full((2, height, width), 0.2, dtype=np.float32)
    data[0, 20:34, 20:34] = 0.98   # VV 强回波
    data[1, 20:34, 20:34] = 0.5
    profile = {
        "driver": "GTiff", "width": width, "height": height, "count": 2,
        "dtype": "float32", "crs": "EPSG:32650",
        "transform": from_origin(500000.0, 4000000.0, 10.0, 10.0),
        "nodata": -9999.0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    return path


@pytest.fixture()
def temporal_raster(tmp_path):
    """时序栈栅格：C=2, T=4 → 8 波段（time-major 布局）+ 线性趋势。"""
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "stack.tif"
    width, height = 32, 32
    bands = []
    for t in range(4):
        base = 0.1 + 0.2 * t
        bands.append(np.full((height, width), base, dtype=np.float32))
        bands.append(np.full((height, width), base * 0.5, dtype=np.float32))
    data = np.stack(bands)  # (8, H, W)
    profile = {
        "driver": "GTiff", "width": width, "height": height, "count": 8,
        "dtype": "float32", "crs": "EPSG:4326",
        "transform": from_origin(116.0, 40.0, 1.0, 1.0),
        "nodata": -9999.0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    return path


def register_remote_model(service, endpoint, *, model_id="remote-seg", version="1.0.0"):
    """注册指向 remote endpoint 的模型（provider_ref = remote@<endpoint>）。"""
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )
    from app.lib.modelops.resources import batch_for_budget  # noqa: F401

    descriptor = GeoModelDescriptor.model_validate(
        {
            "model_id": model_id,
            "model_version": version,
            "checksum": "e" * 64,
            "provider_type": "remote_endpoint",
            "provider_ref": f"remote@{endpoint}",
            "provider_semantic_version": "remote-json/1.0.0",
            "task_types": ("semantic_segmentation",),
            "input_modalities": ("optical_rgb",),
            "input_bands": 3,
            "normalization": NormalizationSpec(kind="none"),
            "output_types": ("class_raster",),
            "class_schema": ClassSchema(classes=("background", "bright", "mid")),
            "spatial": SpatialRequirements(chip_size=(32, 32), context_size=(32, 32)),
            "device_requirements": DeviceRequirements(required="cpu"),
            "license": "test",
        }
    )
    service._registry.register(
        descriptor,
        owner_scope={"global": "builtin"},
        registered_by="test",
        package_report={"synthetic": True},
        known_provider_refs=service._providers.has,
    )
    return descriptor

@pytest.fixture()
def synthetic_raster(tmp_path):
    """确定性合成 GeoTIFF（130x100, 3 波段，亮度块图案）。"""
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "synthetic.tif"
    width, height = 130, 100
    rng = np.random.default_rng(42)
    data = rng.random((3, height, width)).astype(np.float32) * 0.4
    data[:, 10:30, 20:45] = 0.9 + rng.random((3, 20, 25)).astype(np.float32) * 0.1
    data[:, 60:80, 90:120] = 0.95
    transform = from_origin(116.0, 40.0, 1.0, 1.0)
    profile = {
        "driver": "GTiff", "width": width, "height": height, "count": 3,
        "dtype": "float32", "crs": "EPSG:4326", "transform": transform,
        "nodata": -9999.0, "tiled": True, "blockxsize": 64, "blockysize": 64,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
        dst.update_tags(BANDS="red,green,blue")
    return path


@pytest.fixture()
def tiny_desc_factory():
    """可定制的 descriptor 构造器（unit conftest 同款；集成测试复用）。"""
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
