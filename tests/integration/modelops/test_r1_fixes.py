"""Review Round 1 修复的回归测试（C-2..C-6, M-*, m-*）。"""
from __future__ import annotations

import json

import numpy as np
import pytest

from pathlib import Path

from app.lib.geo_raster.reader import RasterReader
from app.services.modelops.engine import InferenceRequest
from app.lib.modelops.errors import DescriptorError
from app.lib.modelops.promptable import PromptSpec
from app.services.modelops.registry import ModelRegistryStore
from app.services.modelops.config import ModelOpsSettings


# ── C-2：prompt/temporal 进 reuse key ────────────────────────────────


def test_reuse_key_prompt_invalidation(service, synthetic_raster):
    """同输入异 prompt → 必 miss（R1-C2）。"""
    r1 = service.run_inference(InferenceRequest(
        model_id="tiny-promptable-seg", source_uri=str(synthetic_raster),
        owner_scope={"session_id": "s1"},
        prompt=PromptSpec(points=((70.0, 50.0),)),
    ))
    assert r1.status == "completed"
    r2 = service.run_inference(InferenceRequest(
        model_id="tiny-promptable-seg", source_uri=str(synthetic_raster),
        owner_scope={"session_id": "s1"},
        prompt=PromptSpec(points=((10.0, 10.0),)),
    ))
    assert r2.status == "completed"  # 不同 prompt 不命中
    r3 = service.run_inference(InferenceRequest(
        model_id="tiny-promptable-seg", source_uri=str(synthetic_raster),
        owner_scope={"session_id": "s1"},
        prompt=PromptSpec(points=((70.0, 50.0),)),
    ))
    assert r3.status == "reused"  # 同 prompt 精确命中


def test_reuse_key_temporal_invalidation(service, temporal_raster):
    from app.lib.modelops.temporal import TemporalStackSpec

    spec = TemporalStackSpec(times=("2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"),
                             max_length=8, missing_policy="flag")
    r1 = service.run_inference(InferenceRequest(
        model_id="tiny-temporal-forecast", source_uri=str(temporal_raster),
        owner_scope={"session_id": "s-t"}, temporal=spec))
    assert r1.status == "completed"
    spec2 = TemporalStackSpec(times=("2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"),
                              max_length=8, missing_policy="error")
    r2 = service.run_inference(InferenceRequest(
        model_id="tiny-temporal-forecast", source_uri=str(temporal_raster),
        owner_scope={"session_id": "s-t"}, temporal=spec2))
    assert r2.status == "completed"  # missing_policy 变化 → miss


# ── C-4：detection 多 tile oracle（真坐标）──────────────────────────


def test_detection_multitile_global_oracle(service, tmp_path):
    """内部多 tile + 人工亮块：全局 box 必须落在亮块上（R1-C4 oracle）。"""
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "det.tif"
    w, h = 200, 100
    data = np.full((2, h, w), 0.2, dtype=np.float32)
    data[:, 40:60, 100:130] = 0.98  # 两波段同亮（provider 按波段均值打分）
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=2,
                       dtype="float32", crs="EPSG:32650",
                       transform=from_origin(500000.0, 4000000.0, 1.0, 1.0),
                       nodata=-9999.0) as dst:
        dst.write(data)
    result = service.run_inference(InferenceRequest(
        model_id="tiny-sar-detector", source_uri=str(path),
        owner_scope={"session_id": "s-det"}, task_type="object_detection",
        score_threshold=0.6))
    detections = json.loads(
        Path(result.outputs["detections"]["path"]).read_text(encoding="utf-8")
    )
    assert detections["features"], "必须检出亮块"
    xs, ys = [], []
    for feat in detections["features"]:
        ring = feat["geometry"]["coordinates"][0]
        xs.extend(p[0] for p in ring)
        ys.extend(p[1] for p in ring)
    # 手算 oracle：全局 box 与亮块（像素 x∈[100,130], y∈[40,60]）相交。
    with rasterio.open(path) as src:
        gx0, gy1 = src.transform * (100, 40)
        gx1, gy0 = src.transform * (130, 60)
    assert max(xs) > gx0 and min(xs) < gx1
    assert max(ys) > gy0 and min(ys) < gy1


# ── C-5：warmup 失败不泄漏 refcount ─────────────────────────────────


def test_engine_release_on_warmup_failure(service, synthetic_raster, monkeypatch):
    """warmup 抛错 → engine finally 释放 refcount → 条目可驱逐。"""
    calls = {"n": 0}
    real = service._providers.get("tiny-reference")

    def boom_warmup(model):
        calls["n"] += 1
        raise RuntimeError("warmup boom")

    monkeypatch.setattr(real, "warmup", boom_warmup)
    with pytest.raises(RuntimeError):
        service.run_inference(InferenceRequest(
            model_id="tiny-landcover-seg", source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s-warm"}))
    assert calls["n"] == 1
    # refcount 已释放（finally 覆盖 warmup 抛错路径）：恢复后重跑正常。
    monkeypatch.setattr(real, "warmup", lambda m: {"warmed": True})
    ok = service.run_inference(InferenceRequest(
        model_id="tiny-landcover-seg", source_uri=str(synthetic_raster),
        owner_scope={"session_id": "s-warm"}))
    assert ok.status in ("completed", "reused")


# ── C-6：跨 owner 同身份 + scope 值穿越 ─────────────────────────────


def test_cross_owner_same_identity_no_collision(tmp_path, tiny_desc_factory):
    """owner A/B 各自注册同名同版本（不同 checksum）→ 互不干扰（R1-C6b）。"""
    store = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    store.register(tiny_desc_factory(checksum="a" * 64),
                   owner_scope={"session_id": "alice"}, known_provider_refs=lambda r: True)
    store.register(tiny_desc_factory(checksum="b" * 64),
                   owner_scope={"session_id": "bob"}, known_provider_refs=lambda r: True)
    a = store.resolve("unit-model", session_id="alice")
    b = store.resolve("unit-model", session_id="bob")
    assert a.descriptor.checksum == "a" * 64
    assert b.descriptor.checksum == "b" * 64
    assert store.validate_parity() == []
    # 重载后仍隔离
    store2 = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    assert store2.resolve("unit-model", session_id="alice").descriptor.checksum == "a" * 64


def test_scope_value_traversal_rejected(tmp_path, tiny_desc_factory):
    """scope 值路径穿越 → typed 拒绝（R1-C6a）。"""
    store = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    with pytest.raises(DescriptorError):
        store.register(tiny_desc_factory(),
                       owner_scope={"session_id": "../../evil"},
                       known_provider_refs=lambda r: True)


# ── m-3：最新版本按注册序 ────────────────────────────────────────────


def test_latest_version_by_registration_order(tmp_path, tiny_desc_factory):
    store = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    for ver in ("9.0", "10.0"):
        store.register(tiny_desc_factory(model_version=ver),
                       owner_scope={"global": "x"}, known_provider_refs=lambda r: True)
    rec = store.resolve("unit-model", session_id="s1")
    assert rec.descriptor.model_version == "10.0"  # 字典序 "9.0" > "10.0"，禁之


# ── M-2：promptable 产物 georef oracle ──────────────────────────────


def test_promptable_mask_georef(service, synthetic_raster):
    prompt = PromptSpec(points=((70.0, 50.0),))
    result = service.run_inference(InferenceRequest(
        model_id="tiny-promptable-seg", source_uri=str(synthetic_raster),
        owner_scope={"session_id": "s-geo"}, prompt=prompt))
    with RasterReader.open(result.outputs["prompt_mask"]["path"]) as reader:
        meta = reader.metadata()
    # V3 §H：promptable 产物现为整幅画布（tile 策略统一），georef 必须与
    # 源栅格网格精确对齐（transform 原点 = 全幅 (116,40)）。
    assert meta.transform is not None
    assert abs(meta.transform[2] - 116.0) < 1e-6
    assert abs(meta.transform[5] - 40.0) < 1e-6
    assert (meta.width, meta.height) == (130, 100)


def test_mask_prompt_zero_prior_yields_empty(service, synthetic_raster):
    """R1-M3：全 False prior mask → 输出无目标（mask prompt 真实生效）。"""
    mask = np.zeros((100, 130), dtype=bool)
    prompt = PromptSpec(points=((70.0, 50.0),), prior_masks=(mask,))
    result = service.run_inference(InferenceRequest(
        model_id="tiny-promptable-seg", source_uri=str(synthetic_raster),
        owner_scope={"session_id": "s-prior"}, prompt=prompt))
    with RasterReader.open(result.outputs["prompt_mask"]["path"]) as reader:
        arr = reader.read_full(budget_ok=True)
    assert arr.sum() == 0


# ── M-4：生产分割路径兑现 nodata/概率契约 ────────────────────────────


def test_nodata_pixels_never_classified(service, tmp_path):
    """nodata 像元 → 类别 255（生产 accumulator 路径，R1-m5/M-4）。"""
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "nodata.tif"
    data = np.random.default_rng(5).random((3, 40, 40)).astype(np.float32) * 0.4
    with rasterio.open(path, "w", driver="GTiff", width=40, height=40, count=3,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_origin(116.0, 40.0, 1.0, 1.0),
                       nodata=-9999.0) as dst:
        dst.write(data)
        mask_band = np.ones((40, 40), dtype=np.uint8) * 255
        mask_band[:10, :] = 0
        dst.write_mask(mask_band)  # GDAL mask band：前 10 行 nodata
    result = service.run_inference(InferenceRequest(
        model_id="tiny-landcover-seg", source_uri=str(path),
        owner_scope={"session_id": "s-nd"}))
    with RasterReader.open(result.outputs["classes"]["path"]) as reader:
        arr = reader.read_full(budget_ok=True)[0]
    assert (arr[:10, :] == 255).all()  # nodata 区无预测类别


# ── M-7：静态架构测试 ────────────────────────────────────────────────


def test_extension_adapter_isolation():
    """R1-M6 静态测试：adapter 不 import worker/client 内部件。"""
    import app.services.modelops.providers.extension_adapter as adapter_mod
    import inspect

    src = inspect.getsource(adapter_mod)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("from app.extensions_platform.worker",
                                "import app.extensions_platform.worker")):
            pytest.fail(f"adapter imports worker internals: {stripped}")
        if "host._records" in stripped or "ExtensionHost(" in stripped:
            pytest.fail(f"adapter touches host privates: {stripped}")


def test_classic_iterator_untouched():
    """R1-M7 静态测试：modelops 不导入/不改动经典分区权威。"""
    import app.lib.modelops.planning as planning
    import app.services.modelops.engine as engine
    import inspect

    for mod in (planning, engine):
        src = inspect.getsource(mod)
        assert "iter_bounded_windows" not in src
        assert "from app.lib.geo_raster.chunk" not in src


# ── m-1：padding modes ───────────────────────────────────────────────


def test_padding_mode_reflect_applied(tiny_desc_factory):
    from app.lib.modelops.descriptor import SpatialRequirements
    from app.lib.modelops.preprocess import build_plan, preprocess_window
    from app.lib.modelops.planning import plan_tiles

    d = tiny_desc_factory(spatial=SpatialRequirements(
        chip_size=(4, 4), context_size=(8, 8), padding_mode="replicate"))
    plan = plan_tiles(d.spatial and d, raster_height=4, raster_width=4)
    plan_ = build_plan(d, source_band_count=3)
    assert plan_.pad_mode == "replicate"
    window = np.arange(3 * 4 * 4, dtype=np.float32).reshape(3, 4, 4)
    chip, valid = preprocess_window(plan_, d, window, None, plan.tiles[0])
    assert chip.shape == (3, 8, 8)
    # edge replicate：pad 区第一行 = 数据第一行
    assert np.allclose(chip[0, 0, :4], window[0, 0, :])


# ── m-2：band_order 按语义名解析 ────────────────────────────────────


def test_band_order_resolved_by_names(tiny_desc_factory):
    from app.lib.modelops.preprocess import build_plan

    d = tiny_desc_factory(input_bands=3, band_order=("blue", "green", "red"))
    plan = build_plan(d, source_band_count=3, source_band_names=("red", "green", "blue"))
    assert plan.band_indices == (2, 1, 0)


# ── M-1：instance 批粒度端到端 ──────────────────────────────────────


def test_instance_end_to_end_multitile(service, tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "inst.tif"
    w, h = 100, 100
    data = np.full((1, h, w), 0.2, dtype=np.float32)
    data[0, 10:25, 10:25] = 0.95    # 实例 1
    data[0, 60:80, 60:85] = 0.95    # 实例 2（不同 tile）
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_origin(116.0, 40.0, 1.0, 1.0),
                       nodata=-9999.0) as dst:
        dst.write(data)
    result = service.run_inference(InferenceRequest(
        model_id="tiny-instance-seg", source_uri=str(path),
        owner_scope={"session_id": "s-inst"}))
    assert result.task_type == "instance_segmentation"
    with RasterReader.open(result.outputs["instances"]["path"]) as reader:
        arr = reader.read_full(budget_ok=True)[0]
    # 两个亮块中心都有实例 id（跨 tile 的同一物理实例按 tile 序合并语义，
    # 数量不做精确断言——契约是身份存在与位置正确）。
    assert arr[17, 17] > 0
    assert arr[70, 70] > 0
    assert set(np.unique(arr)) != {0, 255}

