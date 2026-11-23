"""P2 全量逐行审查回归：modelops 模块 16 条发现（第二波 fix wave）。

每条用例对应一个独立 finding 的修复语义，oracle 独立于被测实现：
手导期望值 / 调用记录 / 互斥构造 / 竞态终局手工布置。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.lib.modelops.errors import ModelOpsError, RemoteInferenceError, ResourceUnavailable
from app.services.modelops.config import ModelOpsSettings
from app.services.modelops.engine import InferenceEngine, InferenceRequest
from app.services.modelops.lineage import ModelLineageStore
from app.services.modelops.loaded_cache import LoadedModelCache
from app.services.modelops.providers.base import ProviderRegistry
from app.services.modelops.providers.tiny_reference import TinyReferenceProvider
from app.services.modelops.registry import ModelRegistryStore
from app.services.modelops.reuse import ReuseStore


# ── 基础设施 ───────────────────────────────────────────────────────


def _descriptor(*, model_id, provider_ref, semantic_version, task, bands, chip):
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )

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
    settings = ModelOpsSettings(registry_dir=tmp_path / "mo")
    registry = ModelRegistryStore(settings)
    engine = InferenceEngine(registry, providers, settings)
    registry.register(
        _descriptor(model_id="seg-poly", provider_ref="tiny-reference",
                    semantic_version="tiny/1.0.0", task="semantic_segmentation",
                    bands=3, chip=chip),
        owner_scope={"global": "x"},
        known_provider_refs=providers.has,
    )
    return engine


def _raster(tmp_path, *, name, w, h, bands=3, dtype="float32", crs="EPSG:4326",
            origin=(116.0, 40.0), pixel=1.0):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / name
    data = np.random.default_rng(7).random((bands, h, w)).astype(dtype) * 0.5
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=bands, dtype=dtype,
        crs=crs, transform=from_origin(origin[0], origin[1], pixel, pixel),
        nodata=-9999.0,
    ) as dst:
        dst.write(data)
    return path


def _label_raster(tmp_path, *, name, data, crs="EPSG:4326", origin=(0.0, 64.0),
                  pixel=1.0):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / name
    with rasterio.open(
        path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0],
        count=1, dtype="uint8", crs=crs,
        transform=from_origin(origin[0], origin[1], pixel, pixel), nodata=255,
    ) as dst:
        dst.write(data, 1)
    return path


# ── artifacts.py:205 —— 投影 CRS 栅格的检测 GeoJSON 丢失 crs 成员 ────


def test_detection_geojson_keeps_crs_for_projected_grid():
    """to_geographic=True 的输出仍是**源 CRS 单位**（投影 = 米）。

    未修复时 crs 仅在 to_geographic=False 时写入，engine（:1123）恒用默认
    True → 投影 CRS 栅格产出无 crs 成员的米制坐标，下游按 GeoJSON 默认
    WGS84 解读成经纬度。
    """
    from rasterio.transform import from_origin

    from app.services.modelops.artifacts import build_geojson_from_detections

    transform = from_origin(500000.0, 4000000.0, 10.0, 10.0)
    fc = build_geojson_from_detections(
        [{"box": [0, 0, 4, 4], "score": 0.9, "label": 1, "batch_index": 0}],
        crs="EPSG:3857",
        transform=transform,
    )
    assert fc["crs"]["properties"]["name"] == "EPSG:3857"
    # oracle：像素角点 → 目标 CRS 像元中心（from_origin(500000,4000000,10,10)
    # 下 box[0,0,4,4] 的 x∈[500005,500045]、y∈[3999955,3999995]）。
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert min(p[0] for p in ring) == pytest.approx(500005.0)
    assert max(p[0] for p in ring) == pytest.approx(500045.0)
    assert min(p[1] for p in ring) == pytest.approx(3999955.0)
    assert max(p[1] for p in ring) == pytest.approx(3999995.0)


def test_detection_geojson_omits_crs_for_wgs84_grid():
    """WGS84 地理栅格不写 crs 成员（GeoJSON 默认坐标系；行为不回退）。"""
    from rasterio.transform import from_origin

    from app.services.modelops.artifacts import build_geojson_from_detections

    fc = build_geojson_from_detections(
        [{"box": [0, 0, 4, 4], "score": 0.9, "label": 1, "batch_index": 0}],
        crs="EPSG:4326",
        transform=from_origin(116.0, 40.0, 1.0, 1.0),
    )
    assert "crs" not in fc
    # to_geographic=False（原生 CRS 单位）时 crs 成员照旧写入。
    fc2 = build_geojson_from_detections(
        [{"box": [0, 0, 4, 4], "score": 0.9, "label": 1, "batch_index": 0}],
        crs="EPSG:3857",
        transform=from_origin(500000.0, 4000000.0, 10.0, 10.0),
        to_geographic=False,
    )
    assert fc2["crs"]["properties"]["name"] == "EPSG:3857"


# ── engine.py:763 / :1939 —— run_key 目录穿越 ───────────────────────


def test_engine_run_key_traversal_rejected(tmp_path):
    """run_key 进 output 目录名与重投影产物名，含 ".." 即写出 registry_dir。

    未修复时 ``outputs/<scope>/<run_id>`` 的 is_relative_to 检查走的是
    **未 resolve** 的路径（".." 组件按字面比较恒为真）→ mkdir(parents=True)
    落到注册表根之外；重投影产物路径则完全无检查。
    """
    engine = _seg_engine(tmp_path)
    source = _raster(tmp_path, name="small.tif", w=32, h=32)
    for evil in ("../../../pwned", "..", ".", "a/b", "/etc", "x" * 200):
        with pytest.raises(ModelOpsError):
            engine.run(InferenceRequest(
                model_id="seg-poly", source_uri=str(source),
                owner_scope={"session_id": "s-runkey"},
                run_key=evil,
            ))
    assert not (tmp_path / "pwned").exists()
    assert not (tmp_path / "mo" / "pwned").exists()
    outputs_root = tmp_path / "mo" / "outputs"
    assert not outputs_root.exists() or not any(outputs_root.iterdir())


def test_engine_empty_run_key_falls_back_to_uuid(tmp_path):
    """空 run_key 回退到 uuid（falsy），不得成为穿越面。"""
    engine = _seg_engine(tmp_path)
    source = _raster(tmp_path, name="small.tif", w=32, h=32)
    result = engine.run(InferenceRequest(
        model_id="seg-poly", source_uri=str(source),
        owner_scope={"session_id": "s-runkey"},
        run_key="",
    ))
    assert result.status == "completed"
    assert len(result.run_id) == 16


def test_engine_accepts_path_safe_run_key(tmp_path):
    """合法 run_key 不受影响（uuid hex / 常规调用方键）。"""
    engine = _seg_engine(tmp_path)
    source = _raster(tmp_path, name="small.tif", w=32, h=32)
    result = engine.run(InferenceRequest(
        model_id="seg-poly", source_uri=str(source),
        owner_scope={"session_id": "s-runkey"},
        run_key="run-2026-11-23_01",
    ))
    assert result.status == "completed"
    assert result.run_id == "run-2026-11-23_01"


# ── engine.py:2059 —— PostGIS 发布异常不得毁掉已完成推理 ────────────


def test_engine_postgis_publish_failure_does_not_fail_run(tmp_path, monkeypatch):
    """推理完成后的 PostGIS 发布异常必须降级为 reason，不让整次 run 失败。"""
    import app.services.modelops.geo_output as geo_output

    def boom(*args, **kwargs):
        raise RuntimeError("driver exploded")

    monkeypatch.setattr(geo_output, "publish_geojson_to_postgis", boom)

    engine = _seg_engine(tmp_path)
    source = _raster(tmp_path, name="small.tif", w=32, h=32)
    result = engine.run(InferenceRequest(
        model_id="seg-poly", source_uri=str(source),
        owner_scope={"session_id": "s-postgis"},
        vectorize_classes=True,
        postgis_table="class_polygons",
    ))
    assert result.status == "completed"
    assert result.outputs["class_polygons"]["path"]
    note = result.outputs["class_polygons"]["postgis"]
    assert note["published"] is False
    assert "postgis publish raised" in note["reason"]


# ── geo_output.py:54 —— 表名/if_exists 校验不得 raise ───────────────


def test_postgis_validation_is_honest_skip_not_raise(monkeypatch):
    """函数注释承诺"发布失败不抛"：非法表名 / 非法 if_exists 都走 reason。"""
    from app.services.modelops.geo_output import publish_geojson_to_postgis

    features = [{"type": "Feature", "properties": {},
                 "geometry": {"type": "Point", "coordinates": [1, 2]}}]
    fc = {"type": "FeatureCollection", "features": features}
    for bad_table in ("UPPER", "x" * 64, "a;b", ""):
        out = publish_geojson_to_postgis(fc, table=bad_table)
        assert out["published"] is False
        assert "table name" in out["reason"]
    out = publish_geojson_to_postgis(fc, table="t", if_exists="replace")
    assert out["published"] is False
    assert "if_exists" in out["reason"]


# ── evaluation_service.py:103 / :159 —— 辅助栅格同网格校验 ──────────


def _eval_service():
    from app.services.modelops.evaluation_service import EvaluationService

    return EvaluationService()


def test_confidence_raster_grid_mismatch_rejected(tmp_path):
    """confidence 与 pred 同元素数异形 → typed 拒绝（未修复时裸 IndexError）。"""
    from app.services.modelops.evaluation_service import EvaluationRequest

    # 32x16 网格：pred/refs 同网格；confidence 为 16x32（转置，元素数相同）。
    truth = (np.random.default_rng(5).random((32, 16)) > 0.5).astype(np.uint8)
    pred = truth.copy()
    pred_path = _label_raster(tmp_path, name="prd.tif", data=pred)
    ref_path = _label_raster(tmp_path, name="ref.tif", data=truth)
    conf_path = _label_raster(tmp_path, name="conf.tif",
                              data=np.full((16, 32), 200, dtype=np.uint8))
    with pytest.raises(ModelOpsError, match="shape"):
        _eval_service().evaluate(EvaluationRequest(
            owner_scope={"session_id": "s-ev"},
            task_type="segmentation",
            predictions_path=pred_path,
            references_path=ref_path,
            confidence_path=conf_path,
            num_classes=2,
            output_dir=tmp_path / "out",
        ))


def test_confidence_raster_crs_mismatch_rejected(tmp_path):
    """形状相同但 CRS 不同的 confidence 栅格同样拒绝（对齐 _read_pair 口径）。"""
    from app.services.modelops.evaluation_service import EvaluationRequest

    truth = (np.random.default_rng(5).random((32, 16)) > 0.5).astype(np.uint8)
    pred_path = _label_raster(tmp_path, name="prd.tif", data=truth)
    ref_path = _label_raster(tmp_path, name="ref.tif", data=truth)
    conf_path = _label_raster(tmp_path, name="conf.tif",
                              data=np.full((32, 16), 200, dtype=np.uint8),
                              crs="EPSG:3857")
    with pytest.raises(ModelOpsError, match="grid differs"):
        _eval_service().evaluate(EvaluationRequest(
            owner_scope={"session_id": "s-ev"},
            task_type="segmentation",
            predictions_path=pred_path,
            references_path=ref_path,
            confidence_path=conf_path,
            num_classes=2,
            output_dir=tmp_path / "out",
        ))


def test_confidence_raster_same_grid_still_evaluates(tmp_path):
    """同网格的 confidence 栅格照旧产出 ECE（不回退既有能力）。"""
    from app.services.modelops.evaluation_service import EvaluationRequest

    truth = (np.random.default_rng(5).random((32, 16)) > 0.5).astype(np.uint8)
    pred = truth.copy()
    pred[3, :] = 0
    pred_path = _label_raster(tmp_path, name="prd.tif", data=pred)
    ref_path = _label_raster(tmp_path, name="ref.tif", data=truth)
    conf_path = _label_raster(tmp_path, name="conf.tif",
                              data=np.full((32, 16), 200, dtype=np.uint8))
    report = _eval_service().evaluate(EvaluationRequest(
        owner_scope={"session_id": "s-ev"},
        task_type="segmentation",
        predictions_path=pred_path,
        references_path=ref_path,
        confidence_path=conf_path,
        num_classes=2,
        output_dir=tmp_path / "out",
    ))
    assert report["ece"] is not None


def test_regions_raster_grid_mismatch_rejected(tmp_path):
    """region 栅格与 pred/refs 不同网格 → typed 拒绝（逐 region 指标不误导）。"""
    from app.services.modelops.evaluation_service import EvaluationRequest

    truth = np.zeros((32, 16), dtype=np.uint8)
    truth[0:16, :] = 1
    pred_path = _label_raster(tmp_path, name="prd.tif", data=truth)
    ref_path = _label_raster(tmp_path, name="ref.tif", data=truth)
    # 16x32：同元素数异形 → per_region_metrics 的布尔切片错位。
    regions_path = _label_raster(tmp_path, name="regions.tif",
                                 data=np.zeros((16, 32), dtype=np.uint8))
    with pytest.raises(ModelOpsError, match="shape"):
        _eval_service().evaluate(EvaluationRequest(
            owner_scope={"session_id": "s-ev"},
            task_type="segmentation",
            predictions_path=pred_path,
            references_path=ref_path,
            regions_path=regions_path,
            num_classes=2,
            output_dir=tmp_path / "out",
        ))


def test_regions_raster_same_grid_still_evaluates(tmp_path):
    from app.services.modelops.evaluation_service import EvaluationRequest

    truth = np.zeros((32, 16), dtype=np.uint8)
    truth[0:16, :] = 1
    regions = np.zeros((32, 16), dtype=np.uint8)
    regions[:, 0:8] = 1
    pred_path = _label_raster(tmp_path, name="prd.tif", data=truth)
    ref_path = _label_raster(tmp_path, name="ref.tif", data=truth)
    regions_path = _label_raster(tmp_path, name="regions.tif", data=regions)
    report = _eval_service().evaluate(EvaluationRequest(
        owner_scope={"session_id": "s-ev"},
        task_type="segmentation",
        predictions_path=pred_path,
        references_path=ref_path,
        regions_path=regions_path,
        num_classes=2,
        output_dir=tmp_path / "out",
    ))
    assert report["per_region"]["region_count"] == 1


# ── evaluation_service.py:215 —— 漂移评估缺 owner_scope 一维校验 ────


def test_drift_owner_scope_must_be_one_dimensional(tmp_path):
    """空 / 双键 scope 会并入 output_dir 路径并以非法 scope 发布 artifact。"""
    from app.services.modelops.evaluation_service import (
        DriftEvaluationRequest,
        EvaluationService,
    )

    truth = np.zeros((16, 16), dtype=np.uint8)
    base_path = _label_raster(tmp_path, name="base.tif", data=truth)
    cur_path = _label_raster(tmp_path, name="cur.tif", data=truth)
    for bad in ({}, {"session_id": "s", "project_id": "p"}, {"tenant": "t"}):
        with pytest.raises(ModelOpsError, match="owner_scope"):
            EvaluationService().evaluate_drift(DriftEvaluationRequest(
                owner_scope=bad,
                baseline_path=base_path,
                current_path=cur_path,
                num_classes=2,
                output_dir=tmp_path / "drift-out",
            ))


# ── lineage.py:52 —— 重复查询不得每次全量重读 ───────────────────────


def test_lineage_repeated_queries_do_not_reread_the_file(tmp_path, monkeypatch):
    """latest_metrics/deployment_state 每次调用都重读+排序全文件（线性增长）。"""
    from pathlib import Path as _Path

    store = ModelLineageStore(tmp_path)
    for i in range(6):
        store.append("m", "1", event_type="evaluation", payload={"i": i})

    reads = {"n": 0}
    real_read_text = _Path.read_text

    def counting(self, *args, **kwargs):
        reads["n"] += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "read_text", counting)
    for _ in range(25):
        store.latest_metrics("m", "1")
        store.deployment_state("m", "1")
        store.history("m", "1")
    # 未修复时 = 75 次全量读入 + 75 次排序。
    assert reads["n"] == 1
    assert store.latest_metrics("m", "1") == {"i": 5}


def test_lineage_cache_sees_cross_process_appends(tmp_path):
    """memo 键含 size/mtime：另一 store 实例的追加必须可见。"""
    store = ModelLineageStore(tmp_path)
    store.append("m", "1", event_type="training_metrics", payload={"miou": 0.1})
    assert store.latest_metrics("m", "1") == {"miou": 0.1}
    other = ModelLineageStore(tmp_path)
    other.append("m", "1", event_type="training_metrics", payload={"miou": 0.9})
    assert store.latest_metrics("m", "1") == {"miou": 0.9}


# ── loaded_cache.py:136 —— 失败键锁不得在等待者在途时移除 ───────────


class _Model:
    def __init__(self, name):
        self.name = name


_DESC = SimpleNamespace(model_id="m", model_version="1", checksum="c")


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return False


def test_transient_load_failure_keeps_single_flight():
    """load 失败即 pop per-key 锁 → 后续 acquirer 新建锁，与等待者并发 load。"""
    cache = LoadedModelCache(max_models=4, idle_ttl_s=3600)
    in_flight = 0
    peak = 0
    guard = threading.Lock()
    second_in_load = threading.Event()

    def load_fail():
        # 等到第二个 acquirer 已登记为 key lock 使用者（等待者就位）再失败。
        assert _wait_for(lambda: cache._key_lock_users.get("k", 0) >= 2)
        raise ResourceUnavailable("oom")

    def load_ok():
        nonlocal in_flight, peak
        with guard:
            in_flight += 1
            peak = max(peak, in_flight)
        second_in_load.set()
        # 给"失败后被放走的第三个 acquirer"留出抢锁窗口。
        time.sleep(0.3)
        with guard:
            in_flight -= 1
        return _Model("ok")

    errors = []

    def run(fn):
        try:
            cache.acquire("k", _DESC, provider_ref="p", device="cpu",
                          load_fn=fn, unload_fn=lambda m: None)
        except ResourceUnavailable as exc:
            errors.append(exc)

    t1 = threading.Thread(target=run, args=(load_fail,))
    t1.start()
    assert _wait_for(lambda: cache._key_lock_users.get("k", 0) >= 1)
    t2 = threading.Thread(target=run, args=(load_ok,))
    t2.start()
    assert _wait_for(lambda: cache._key_lock_users.get("k", 0) >= 2)
    t1.join(5.0)
    # 第二个 acquirer 已进入 load_fn（持有 per-key 锁）后才放第三个进来。
    assert second_in_load.wait(5.0)
    t3 = threading.Thread(target=run, args=(load_ok,))
    t3.start()
    t2.join(10.0)
    t3.join(10.0)

    assert len(errors) == 1
    # 未修复时第三个 acquirer 拿到新锁 → 与第二个并发 load（peak == 2）。
    assert peak == 1, "per-key single-flight 被失败键锁的 pop 破坏"
    assert cache.size() == 1
    # 全部使用者退出后键锁不驻留。
    assert "k" not in cache._key_locks
    assert "k" not in cache._key_lock_users


# ── loaded_cache.py:169 —— release 先查孤儿桶再查条目 ───────────────


def test_release_prefers_live_entry_over_orphan_handle():
    """同 key 并存孤儿句柄与 live 条目时，条目持有者的 release 必须递减条目。"""
    cache = LoadedModelCache(max_models=4, idle_ttl_s=3600)
    unloaded = []
    orphan_model, entry_model = _Model("orphan"), _Model("entry")
    # 竞态终局手工布置：invalidate 撞在载入中（孤儿）+ 同 key 再次加载成功（条目）。
    from app.services.modelops.loaded_cache import _Entry

    cache._orphans["k"] = [(orphan_model, lambda m: unloaded.append(m.name))]
    cache._entries["k"] = _Entry(model=entry_model, refcount=1,
                                 unload_cb=lambda m: unloaded.append(m.name))
    # 条目持有者先 release（finding 的触发次序）。
    cache.release("k")
    assert cache._entries["k"].refcount == 0, "条目 refcount 未递减 → 永不被驱逐"
    assert unloaded == [], "卸载了他人仍在用的孤儿 model"
    # 孤儿持有者随后 release → 卸载自己的 model。
    cache.release("k")
    assert unloaded == ["orphan"]


# ── dl_output.py:91 —— NaN/inf 检测行 ──────────────────────────────


def test_map_dl_outputs_drops_nonfinite_detection_rows():
    """NaN 比较全为 False → NaN score 静默进 detections，int(NaN) 裸崩。"""
    from app.services.modelops.providers.dl_output import map_dl_outputs

    desc = _descriptor(model_id="dl-map", provider_ref="tiny-reference",
                       semantic_version="tiny/1.0.0",
                       task="object_detection", bands=3, chip=(8, 8))
    from app.services.modelops.providers.base import TileBatch

    batch = TileBatch(pixels=np.zeros((1, 3, 8, 8), dtype=np.float32))
    raw = [np.array(
        [
            [[10, 10, 20, 20, 0.9, 1],
             [np.nan, 1, 5, 5, np.nan, 1],
             [0, 0, 5, 5, 0.8, np.inf],
             [30, 30, 40, 44, 0.7, 2]],
        ],
        dtype=np.float32,
    )]  # (N=1, M=4, 6)
    out = map_dl_outputs("object_detection", raw, desc, batch)
    boxes = {(d["batch_index"], tuple(np.round(d["box"], 3))) for d in out.detections}
    assert (0, (10.0, 10.0, 10.0, 10.0)) in boxes
    assert (0, (30.0, 30.0, 10.0, 14.0)) in boxes
    assert len(out.detections) == 2, "NaN/inf 行必须被显式过滤"
    for det in out.detections:
        assert np.isfinite(det["score"])
        assert np.isfinite(det["box"]).all()


# ── remote_client.py:311 —— 深嵌套 JSON 的 RecursionError ───────────


def test_remote_deeply_nested_json_is_typed_error():
    """RecursionError 是 RuntimeError 子类而非 ValueError → 绕过 except 逃逸。"""
    import httpx

    from app.services.modelops.providers.remote_client import (
        RemoteEndpointPolicy,
        RemoteInferenceProvider,
    )

    provider = RemoteInferenceProvider(
        "http://10.0.0.1:9000",
        policy=RemoteEndpointPolicy(allowlist=("http://10.0.0.1:9000",)),
    )
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )
    from app.services.modelops.providers.base import InferenceContext, TileBatch

    descriptor = GeoModelDescriptor.model_validate({
        "model_id": "remote-seg", "model_version": "1.0.0", "checksum": "e" * 64,
        "provider_type": "remote_endpoint", "provider_ref": "remote@http://10.0.0.1:9000",
        "provider_semantic_version": "remote-json/1.0.0",
        "task_types": ("semantic_segmentation",),
        "input_modalities": ("optical_rgb",), "input_bands": 1,
        "normalization": NormalizationSpec(kind="none"),
        "output_types": ("class_raster",),
        "class_schema": ClassSchema(classes=("background", "bright")),
        "spatial": SpatialRequirements(chip_size=(4, 4), context_size=(4, 4)),
        "device_requirements": DeviceRequirements(required="cpu"),
        "license": "test",
    })
    model = provider.load(descriptor, device="cpu")
    batch = TileBatch(pixels=np.zeros((1, 1, 4, 4), dtype=np.float32))
    ctx = InferenceContext(run_id="r-remote")
    # 64MiB 字节预算内即可构造的超限嵌套深度。
    deep = ("[" * 20000 + "]" * 20000).encode("ascii")
    provider._request = lambda *a, **kw: httpx.Response(200, content=deep)
    with pytest.raises(RemoteInferenceError, match="not valid JSON"):
        provider.infer(model, batch, ctx)


# ── reuse.py:120 / :121 —— staging 同名覆盖 + 清理 ──────────────────


_OWNER = {"tenant": "acme"}


def test_reuse_store_stages_same_named_artifacts_distinctly(tmp_path):
    """不同目录的同名 artifact 在 staging 内互相覆盖 → 两条记录指向同一文件。"""
    src_a = tmp_path / "dir_a" / "classes.tif"
    src_b = tmp_path / "dir_b" / "classes.tif"
    src_a.parent.mkdir(parents=True)
    src_b.parent.mkdir(parents=True)
    src_a.write_bytes(b"AAAA")
    src_b.write_bytes(b"BBBB")
    store = ReuseStore(tmp_path / "reuse", clock=lambda: 1000.0)
    ok = store.store(
        "abcdef1234567890", owner_scope=_OWNER, manifest={"m": 1},
        artifacts=[{"role": "classes", "file": str(src_a)},
                   {"role": "instances", "file": str(src_b)}],
    )
    assert ok
    hit = store.lookup("abcdef1234567890", owner_scope=_OWNER)
    assert hit is not None
    files = [a["file"] for a in hit["artifacts"]]
    assert len(set(files)) == 2, f"staging 内同名覆盖: {files}"
    entry_dir = Path(hit["entry_dir"])
    contents = {(entry_dir / f).read_bytes() for f in files}
    assert contents == {b"AAAA", b"BBBB"}


def test_reuse_store_cleans_staging_on_copy_failure(tmp_path, monkeypatch):
    """copy2 抛异常时必须回收 staging（glob 只认 entry.json → 永不回收）。"""
    import shutil as _shutil

    src = tmp_path / "classes.tif"
    src.write_bytes(b"AAAA")
    store = ReuseStore(tmp_path / "reuse", clock=lambda: 1000.0)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(_shutil, "copy2", boom)
    with pytest.raises(OSError):
        store.store("abcdef1234567890", owner_scope=_OWNER, manifest={"m": 1},
                    artifacts=[{"role": "classes", "file": str(src)}])
    leftovers = list((tmp_path / "reuse").glob("*/*/*.tmp.*"))
    assert leftovers == [], f"staging 目录遗留: {leftovers}"


def test_reuse_evict_reclaims_stale_staging_dirs(tmp_path):
    """被 kill 的 store 留下的 staging 目录：远超 TTL 后由 _evict 回收。"""
    import json as _json
    import os

    store = ReuseStore(tmp_path / "reuse", ttl_s=10.0)
    stale = (tmp_path / "reuse" / "tenant-acme" / "ab" / "deadbeef.tmp.1.cafe")
    stale.mkdir(parents=True)
    # 真实 staging 目录里已有 entry.json（created_at 很新）→ 条目 TTL  sweep
    # 放行；只有 mtime 暴露"进程早已死去"。
    (stale / "entry.json").write_text(
        _json.dumps({"created_at": time.time(), "total_bytes": 8}), encoding="utf-8"
    )
    (stale / "0000-classes.tif").write_bytes(b"AAAA")
    old = time.time() - 3600.0
    os.utime(stale, (old, old))
    store._evict()
    assert not stale.exists()


def test_reuse_evict_keeps_fresh_staging_dirs(tmp_path):
    """进行中的 store（mtime 新）不得被误删。"""
    store = ReuseStore(tmp_path / "reuse", ttl_s=10.0)
    live = tmp_path / "reuse" / "tenant-acme" / "ab" / "deadbeef.tmp.1.cafe"
    live.mkdir(parents=True)
    store._evict()
    assert live.exists()


# ── scheduling.py:230 —— 并发 pin 同键只留一条记录 ──────────────────


class _CountingProvider:
    def __init__(self):
        self.loads = 0

    def load(self, descriptor, *, device):
        self.loads += 1
        return _Model(f"m{self.loads}")

    def warmup(self, model):
        return {"warmed": True}

    def unload(self, model):
        return None


class _FakeRecord:
    def __init__(self, descriptor):
        self.descriptor = descriptor


class _FakeRegistry:
    def __init__(self, descriptor):
        self._descriptor = descriptor

    def resolve(self, model_id, model_version=None):
        return _FakeRecord(self._descriptor)


def test_concurrent_pin_same_key_keeps_single_refcount():
    """并发 pin 同 (model_id,version,device)：两次 acquire 只留一条记录。"""
    from app.services.modelops.scheduling import WarmPoolManager

    gate = threading.Barrier(2)

    class _RecordingCache:
        def __init__(self):
            self.acquires = 0
            self.releases = 0
            self.refcount = 0

        def acquire(self, key, desc, **kw):
            # 两个 pin 必须都通过 _pinned 预检后才放行（否则后到者直接命中
            # 既有记录，根本构造不出并发双 acquire）。
            gate.wait(timeout=10.0)
            self.acquires += 1
            self.refcount += 1
            return _Model("m"), 0.0

        def release(self, key):
            self.releases += 1
            self.refcount -= 1

    cache = _RecordingCache()
    descriptor = SimpleNamespace(
        model_id="m1", model_version="1.0.0", checksum="c", provider_ref="p",
    )
    manager = WarmPoolManager(cache, _FakeRegistry(descriptor), {"p": _CountingProvider()})

    results = []

    def pin():
        results.append(manager.pin("m1", model_version="1.0.0", device="cpu"))

    threads = [threading.Thread(target=pin) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15.0)

    assert cache.acquires == 2
    # 未修复时两条记录只留一条但 refcount 永置 2 → release_all 后仍不可驱逐。
    assert cache.refcount == 1
    assert len(manager.status()) == 1
    assert manager.release_all() == 1
    assert cache.refcount == 0


# ── service.py:819 —— refine sidecar 名必须含网格身份 ───────────────


def _promptable_raster(tmp_path, *, name, crs, origin):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / name
    rng = np.random.default_rng(42)
    data = rng.random((3, 64, 64)).astype(np.float32) * 0.4
    data[:, 10:30, 20:45] = 0.95
    with rasterio.open(
        path, "w", driver="GTiff", width=64, height=64, count=3, dtype="float32",
        crs=crs, transform=from_origin(origin[0], origin[1], 1.0, 1.0),
        nodata=-9999.0,
    ) as dst:
        dst.write(data)
    return path


def test_refine_sidecar_keyed_by_grid_identity(service, tmp_path):
    """相同掩膜字节 + 不同 CRS 的源栅格不得复用同一 sidecar（georef 错）。"""
    import rasterio

    artifact = {
        "crs": "EPSG:4326",
        "polygons": [[[22.0, 36.0], [45.0, 36.0], [45.0, 10.0], [22.0, 10.0]]],
    }
    source_a = _promptable_raster(tmp_path, name="a.tif", crs="EPSG:4326", origin=(20.0, 38.0))
    compiled = service.compile_geo_prompt(
        artifact, str(source_a), allowed_roots=[tmp_path]
    )
    first = service.run_inference(_refine_request(source_a, compiled, session="p2-refine"))
    assert "prompt_candidates" in first.outputs

    # 同尺寸、同 transform、不同 CRS 的第二张源栅格：同一候选几何栅格化出的
    # 掩膜**字节相同**（bug 的前提条件），但 georef 不同。
    source_b = _promptable_raster(tmp_path, name="b.tif", crs="EPSG:3857", origin=(20.0, 38.0))

    _, meta_a = service.run_prompt_refine(
        "tiny-promptable-seg", str(source_a),
        first.outputs["prompt_candidates"]["path"], 0,
        session_id="p2-refine", allowed_roots=[tmp_path],
    )
    _, meta_b = service.run_prompt_refine(
        "tiny-promptable-seg", str(source_b),
        first.outputs["prompt_candidates"]["path"], 0,
        session_id="p2-refine", allowed_roots=[tmp_path],
    )
    sidecar_a, sidecar_b = Path(meta_a["sidecar"]), Path(meta_b["sidecar"])
    assert sidecar_a != sidecar_b, "相同掩膜字节 + 不同 CRS 复用了前者 sidecar"
    # 掩膜内容确实相同（证明复用的确是纯命名碰撞，而非几何差异）。
    with rasterio.open(sidecar_a) as ra, rasterio.open(sidecar_b) as rb:
        assert np.array_equal(ra.read(1), rb.read(1))
        assert str(ra.crs) == "EPSG:4326"
        assert str(rb.crs) == "EPSG:3857"


def _refine_request(source, compiled, *, session):
    from app.services.modelops.engine import InferenceRequest

    return InferenceRequest(
        model_id="tiny-promptable-seg",
        source_uri=str(source),
        owner_scope={"session_id": session},
        prompt=compiled["prompt"],
        prompt_artifact_id=compiled["artifact_id"],
        prompt_audit=compiled["audit"],
        return_candidates=True,
    )
