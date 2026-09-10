"""GPU/资源规划与 OOM 降批测试（mock_gpu；04-test-matrix §3）。

平台门：全部 in-process mock（win32 无 RLIMIT 不影响）；不断言绝对时序。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.modelops.descriptor import (
    ClassSchema,
    DeviceRequirements,
    GeoModelDescriptor,
    NormalizationSpec,
    SpatialRequirements,
)
from app.services.modelops.engine import InferenceEngine, InferenceRequest
from app.lib.modelops.errors import ProviderOOM, ResourceUnavailable
from app.lib.modelops.resources import batch_for_budget
from app.services.modelops.config import ModelOpsSettings
from app.services.modelops.loaded_cache import LoadedModelCache
from app.services.modelops.providers.base import ProviderRegistry
from app.services.modelops.providers.mock_gpu import MockGPUProvider
from app.services.modelops.providers.tiny_reference import TinyReferenceProvider
from app.services.modelops.registry import ModelRegistryStore


def _descriptor(provider_ref, *, device="cpu", fallback=True, provider_type="local_reference"):
    return GeoModelDescriptor.model_validate(
        {
            "model_id": f"m-{provider_ref}", "model_version": "1.0.0",
            "checksum": "a" * 64, "provider_type": provider_type,
            "provider_ref": provider_ref, "provider_semantic_version": "v1",
            "task_types": ["semantic_segmentation"],
            "input_modalities": ["optical_rgb"], "input_bands": 3,
            "normalization": NormalizationSpec(kind="none"),
            "output_types": ["class_raster"],
            "class_schema": ClassSchema(classes=("background", "bright", "mid")),
            "spatial": SpatialRequirements(chip_size=(16, 16), context_size=(16, 16)),
            "device_requirements": DeviceRequirements(required=device, allow_cpu_fallback=fallback),
            "license": "t",
        }
    )


def _raster(tmp_path, w=64, h=64):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / f"r{w}x{h}.tif"
    data = np.random.default_rng(3).random((3, h, w)).astype(np.float32) * 0.5
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=3, dtype="float32",
        crs="EPSG:4326", transform=from_origin(116.0, 40.0, 1.0, 1.0), nodata=-9999.0,
    ) as dst:
        dst.write(data)
    return path


def _engine(tmp_path, providers, *, cache=None, **settings_kw):
    settings = ModelOpsSettings(registry_dir=tmp_path / "mo", **settings_kw)
    registry = ModelRegistryStore(settings)
    return (
        InferenceEngine(registry, providers, settings, loaded_cache=cache),
        registry,
    )


def test_no_gpu_and_no_fallback_rejected(tmp_path):
    providers = ProviderRegistry()
    providers.register(TinyReferenceProvider())  # 只声明 cpu
    engine, registry = _engine(tmp_path, providers)
    d = _descriptor("tiny-reference", device="cuda", fallback=False)
    registry.register(d, owner_scope={"global": "x"}, known_provider_refs=providers.has)
    with pytest.raises(ResourceUnavailable):
        engine.run(
            InferenceRequest(
                model_id="m-tiny-reference", source_uri=str(_raster(tmp_path)),
                owner_scope={"session_id": "s1"},
            )
        )


def test_gpu_required_with_fallback_serves_cpu(tmp_path):
    providers = ProviderRegistry()
    providers.register(TinyReferenceProvider())
    engine, registry = _engine(tmp_path, providers)
    d = _descriptor("tiny-reference", device="cuda", fallback=True)
    registry.register(d, owner_scope={"global": "x"}, known_provider_refs=providers.has)
    result = engine.run(
        InferenceRequest(
            model_id="m-tiny-reference", source_uri=str(_raster(tmp_path)),
            owner_scope={"session_id": "s1"},
        )
    )
    assert result.manifest["device_plan"]["device"] == "cpu"


def test_simulated_oom_downshift_and_recovery(tmp_path):
    """batch>2 首批 OOM → 降批 → 成功；计数可见。"""
    providers = ProviderRegistry()
    providers.register(MockGPUProvider(oom_on_batch_gt=2, max_batch=4))
    engine, registry = _engine(tmp_path, providers)
    d = _descriptor("mock-gpu")
    registry.register(d, owner_scope={"global": "x"}, known_provider_refs=providers.has)
    result = engine.run(
        InferenceRequest(
            model_id="m-mock-gpu", source_uri=str(_raster(tmp_path)),
            owner_scope={"session_id": "s1"},
        )
    )
    assert result.status == "completed"
    assert result.perf["oom_downshifts"] >= 1
    assert result.perf["batch_size_final"] <= 2


def test_vram_exhaustion_at_load_is_typed(tmp_path):
    providers = ProviderRegistry()
    providers.register(MockGPUProvider(vram_limit_bytes=1))  # 装不下任何权重
    engine, registry = _engine(tmp_path, providers)
    d = _descriptor("mock-gpu")
    registry.register(d, owner_scope={"global": "x"}, known_provider_refs=providers.has)
    with pytest.raises(ProviderOOM):
        engine.run(
            InferenceRequest(
                model_id="m-mock-gpu", source_uri=str(_raster(tmp_path)),
                owner_scope={"session_id": "s1"},
            )
        )


def test_batch_budget_math():
    n = batch_for_budget(
        chip_hw=(16, 16), input_channels=3, output_channels=4,
        bytes_budget=10_000_000, max_batch=8,
    )
    assert 1 <= n <= 8
    tiny = batch_for_budget(
        chip_hw=(16, 16), input_channels=3, output_channels=4,
        bytes_budget=100, max_batch=8,
    )
    assert tiny == 1  # 下限保护


def test_loaded_cache_eviction_between_two_models(tmp_path):
    """两模型竞争：max_models=1 → 先加载者被驱逐，后者成功。"""
    cache = LoadedModelCache(max_models=1)
    providers = ProviderRegistry()
    providers.register(TinyReferenceProvider())
    engine, registry = _engine(tmp_path, providers, cache=cache)
    for ver in ("1", "2"):
        d = _descriptor("tiny-reference")
        d2 = d.model_copy(update={"model_version": ver, "checksum": ("a" * 63) + ver})
        from app.lib.data.fingerprints import canonical_dumps, sha256_hex  # noqa: F401

        registry.register(d2, owner_scope={"global": "x"}, known_provider_refs=providers.has)
    result = engine.run(
        InferenceRequest(
            model_id="m-tiny-reference", model_version="1",
            source_uri=str(_raster(tmp_path)), owner_scope={"session_id": "s1"},
        )
    )
    result2 = engine.run(
        InferenceRequest(
            model_id="m-tiny-reference", model_version="2",
            source_uri=str(_raster(tmp_path)), owner_scope={"session_id": "s1"},
        )
    )
    assert result.status == "completed" and result2.status == "completed"
    assert cache.stats()["evictions"] >= 1


def test_unseeded_model_excluded_from_reuse(tmp_path):
    """M3-2：unseeded provider 不进 reuse（重复运行不产生 reuse 命中）。"""
    providers = ProviderRegistry()
    providers.register(TinyReferenceProvider())
    settings = ModelOpsSettings(registry_dir=tmp_path / "mo")
    from app.services.modelops.reuse import ReuseStore

    reuse = ReuseStore(tmp_path / "reuse")
    engine = InferenceEngine(
        ModelRegistryStore(settings), providers, settings, reuse_store=reuse
    )
    d = _descriptor("tiny-reference")
    d = d.model_copy(update={"random_seed_policy": "unseeded"})
    engine._registry.register(d, owner_scope={"global": "x"}, known_provider_refs=providers.has)
    path = _raster(tmp_path)
    r1 = engine.run(InferenceRequest(model_id="m-tiny-reference", source_uri=str(path),
                                     owner_scope={"session_id": "s1"}))
    r2 = engine.run(InferenceRequest(model_id="m-tiny-reference", source_uri=str(path),
                                     owner_scope={"session_id": "s1"}))
    assert r1.status == r2.status == "completed"
    assert r2.reused is False  # unseeded → reuse 不适用
    assert reuse.stats(owner_scope={"session_id": "s1"})["entries"] == 0
