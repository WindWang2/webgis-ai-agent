"""P1 review 回归：modelops engine 三条发现（实例 polygonize reader / ROI plan / scope 路径穿越）。

每条用例对应一个独立 finding 的修复语义，oracle 独立于被测实现：
手导期望值/调用记录/互斥构造。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.lib.modelops.descriptor import (
    ClassSchema,
    DeviceRequirements,
    GeoModelDescriptor,
    NormalizationSpec,
    SpatialRequirements,
)
from app.lib.modelops.errors import ModelOpsError, ResourceUnavailable
from app.services.modelops.config import ModelOpsSettings
from app.services.modelops.engine import InferenceEngine, InferenceRequest
from app.services.modelops.providers.base import ProviderRegistry
from app.services.modelops.providers.tiny_instance import TinyInstanceProvider
from app.services.modelops.providers.tiny_reference import TinyReferenceProvider
from app.services.modelops.registry import ModelRegistryStore


# ── 基础设施 ────────────────────────────────────────────────────────


def _engine(tmp_path, providers):
    settings = ModelOpsSettings(registry_dir=tmp_path / "mo")
    registry = ModelRegistryStore(settings)
    return InferenceEngine(registry, providers, settings), registry


def _descriptor(*, model_id, provider_ref, semantic_version, task, bands, chip):
    return GeoModelDescriptor.model_validate(
        {
            "model_id": model_id,
            "model_version": "1.0.0",
            "checksum": "a" * 64,
            "provider_type": "local_reference",
            "provider_ref": provider_ref,
            "provider_semantic_version": semantic_version,
            "task_types": [task],
            "input_modalities": ["optical_rgb"],
            "input_bands": bands,
            "normalization": NormalizationSpec(kind="none"),
            "output_types": ["class_raster"],
            "class_schema": ClassSchema(classes=("background", "bright", "mid")),
            "spatial": SpatialRequirements(chip_size=chip, context_size=chip),
            "device_requirements": DeviceRequirements(required="cpu"),
            "license": "t",
        }
    )


def _seg_engine(tmp_path, *, chip=(4, 4)):
    providers = ProviderRegistry()
    providers.register(TinyReferenceProvider())
    engine, registry = _engine(tmp_path, providers)
    registry.register(
        _descriptor(model_id="seg-poly", provider_ref="tiny-reference",
                    semantic_version="tiny/1.0.0", task="semantic_segmentation",
                    bands=3, chip=chip),
        owner_scope={"global": "x"},
        known_provider_refs=providers.has,
    )
    return engine


def _raster(tmp_path, *, name, w, h, bands=3, dtype="float32"):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / name
    data = np.random.default_rng(7).random((bands, h, w)).astype(dtype) * 0.5
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=bands, dtype=dtype,
        crs="EPSG:4326", transform=from_origin(116.0, 40.0, 1.0, 1.0), nodata=-9999.0,
    ) as dst:
        dst.write(data)
    return path


# ── P1-1：instance polygonize 读已关闭 reader ───────────────────────


def test_instance_polygonize_uses_open_reader_georef(tmp_path):
    """polygonize 分支必须从**打开中**的 reader 取 transform/crs。

    未修复时该分支引用 infer 循环 with 块外已关闭的 reader →
    ``RasterReaderError: reader ... is closed``，整次
    instance_segmentation + polygonize_instances run 失败。
    """
    providers = ProviderRegistry()
    providers.register(TinyInstanceProvider())
    engine, registry = _engine(tmp_path, providers)
    registry.register(
        _descriptor(model_id="inst-poly", provider_ref="tiny-instance",
                    semantic_version="tiny-instance/1.0.0",
                    task="instance_segmentation", bands=1, chip=(16, 16)),
        owner_scope={"global": "x"},
        known_provider_refs=providers.has,
    )
    path = tmp_path / "inst.tif"
    data = np.full((1, 64, 64), 0.2, dtype=np.float32)
    data[0, 20:32, 20:32] = 0.95          # 单个实例
    import rasterio
    from rasterio.transform import from_origin

    with rasterio.open(
        path, "w", driver="GTiff", width=64, height=64, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(116.0, 40.0, 1.0, 1.0), nodata=-9999.0,
    ) as dst:
        dst.write(data)

    result = engine.run(InferenceRequest(
        model_id="inst-poly", source_uri=str(path),
        owner_scope={"session_id": "s-inst-poly"},
        polygonize_instances=True,
    ))
    assert result.status == "completed"
    poly = result.outputs["instance_polygons"]
    fc = json.loads(Path(poly["path"]).read_text(encoding="utf-8"))
    assert fc["features"], "polygonize 产出空 FeatureCollection"
    # georef oracle：源 transform 原点 (116,40)、1°/px、64px → 多边形必须
    # 落在 [116,180]×[-24,40] 的地理范围内（transform 来自真实 reader）。
    xs = [pt[0] for f in fc["features"]
          for ring in f["geometry"]["coordinates"] for pt in ring]
    ys = [pt[1] for f in fc["features"]
          for ring in f["geometry"]["coordinates"] for pt in ring]
    assert min(xs) >= 116.0 - 1e-6 and max(xs) <= 180.0 + 1e-6
    assert min(ys) >= -24.0 - 1e-6 and max(ys) <= 40.0 + 1e-6
    assert fc["crs"]["properties"]["name"] == "EPSG:4326"


# ── P1-2：ROI 请求不得物化全幅 TileSpec ─────────────────────────────


def test_roi_run_plans_only_roi_tiles(tmp_path, monkeypatch):
    """ROI 请求的 tile 计划必须在 ROI 尺寸上物化（C-1 守门的语义）。

    未修复时代码先按全幅 meta 调 plan_tiles 再裁剪 → 大栅格 + 小 ROI
    即物化全幅 TileSpec，MAX_TILES_PER_RUN 守门被绕过。
    """
    import app.services.modelops.engine as engine_mod

    real_plan_tiles = engine_mod.plan_tiles
    seen: list = []

    def spy(descriptor, *, raster_height, raster_width):
        seen.append((raster_height, raster_width))
        return real_plan_tiles(
            descriptor, raster_height=raster_height, raster_width=raster_width
        )

    monkeypatch.setattr(engine_mod, "plan_tiles", spy)

    engine = _seg_engine(tmp_path)
    # 1100px / chip 4 → 275×275 = 75625 tiles > 65536 上限；ROI 16×16 → 16。
    source = _raster(tmp_path, name="big.tif", w=1100, h=1100)

    result = engine.run(InferenceRequest(
        model_id="seg-poly", source_uri=str(source),
        owner_scope={"session_id": "s-roi-plan"},
        roi_bbox=(100, 60, 116, 76),
    ))
    assert result.status == "completed"
    assert seen == [(16, 16)], f"plan_tiles 调用了全幅尺寸: {seen}"
    assert result.manifest["tile_plan"]["tile_count"] == 16

    # 同栅格全幅请求仍被 C-1 守门拒绝（ROI 是唯一可行路径，不是放行的借口）。
    with pytest.raises(ResourceUnavailable):
        engine.run(InferenceRequest(
            model_id="seg-poly", source_uri=str(source),
            owner_scope={"session_id": "s-roi-plan-full"},
        ))


# ── P1-3：scope 值目录穿越 ──────────────────────────────────────────


def test_owner_scope_value_traversal_rejected(tmp_path):
    """scope 值含 "/" / ".." → typed 拒绝，绝不拼进 output 根路径。

    未修复时 ``registry_dir/outputs/session_id-../../../pwned`` 会
    mkdir(parents=True) 到注册表根之外。
    """
    engine = _seg_engine(tmp_path)
    source = _raster(tmp_path, name="small.tif", w=32, h=32)
    for evil in ("../../../pwned", "a/b", "/etc", ""):
        with pytest.raises(ModelOpsError):
            engine.run(InferenceRequest(
                model_id="seg-poly", source_uri=str(source),
                owner_scope={"session_id": evil},
            ))
    # 注册表根之外不得出现任何逃逸目录。
    assert not (tmp_path / "pwned").exists()
    assert not (tmp_path / "mo" / "pwned").exists()
    outputs_root = tmp_path / "mo" / "outputs"
    assert not outputs_root.exists() or not any(outputs_root.iterdir())
