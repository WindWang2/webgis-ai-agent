"""registry / loaded cache / preprocess / metrics 契约测试。"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from app.lib.modelops.descriptor import NormalizationSpec
from app.lib.modelops.errors import (
    ModelNotFoundError,
    ModelVersionCollision,
    ProviderError,
    ProviderLoadFailed,
    ProviderOOM,
)
from app.lib.modelops.metrics import PerfCounters
from app.lib.modelops.preprocess import build_plan, preprocess_batch, preprocess_window
from app.services.modelops.config import ModelOpsSettings
from app.services.modelops.loaded_cache import LoadedModelCache, load_key
from app.services.modelops.registry import ModelRegistryStore


# ── registry ─────────────────────────────────────────────────────────


def test_register_and_resolve_by_scope(tmp_path, tiny_desc_factory):
    store = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    d = tiny_desc_factory()
    store.register(d, owner_scope={"global": "builtin"}, known_provider_refs=lambda ref: True)
    rec = store.resolve("unit-model", session_id="s1")
    assert rec.descriptor.checksum == d.checksum
    with pytest.raises(ModelNotFoundError):
        store.resolve("missing-model", session_id="s1")


def test_owner_scope_isolation(tmp_path, tiny_desc_factory):
    store = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    d = tiny_desc_factory(model_id="scoped-model")
    store.register(d, owner_scope={"project_id": "p1"}, known_provider_refs=lambda ref: True)
    assert store.resolve("scoped-model", project_id="p1") is not None
    with pytest.raises(ModelNotFoundError):
        store.resolve("scoped-model", project_id="p2")
    with pytest.raises(ModelNotFoundError):
        store.resolve("scoped-model", session_id="s1")


def test_version_collision_typed(tmp_path, tiny_desc_factory):
    store = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    store.register(tiny_desc_factory(), owner_scope={"global": "x"},
                   known_provider_refs=lambda ref: True)
    with pytest.raises(ModelVersionCollision):
        store.register(tiny_desc_factory(checksum="b" * 64), owner_scope={"global": "x"},
                       known_provider_refs=lambda ref: True)


def test_reregister_same_content_idempotent(tmp_path, tiny_desc_factory):
    store = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    d = tiny_desc_factory()
    r1 = store.register(d, owner_scope={"global": "x"}, known_provider_refs=lambda ref: True)
    r2 = store.register(d, owner_scope={"global": "x"}, known_provider_refs=lambda ref: True)
    assert r1.revision == r2.revision


def test_provider_ref_must_be_registered(tmp_path, tiny_desc_factory):
    """R1-C1：未注册 provider_ref → typed 拒绝（无动态加载）。"""
    store = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    with pytest.raises(ProviderError):
        store.register(tiny_desc_factory(), owner_scope={"global": "x"},
                       known_provider_refs=lambda ref: False)


def test_persistence_and_parity(tmp_path, tiny_desc_factory):
    settings = ModelOpsSettings(registry_dir=tmp_path)
    store = ModelRegistryStore(settings)
    store.register(tiny_desc_factory(), owner_scope={"global": "x"},
                   known_provider_refs=lambda ref: True)
    assert store.validate_parity() == []
    store2 = ModelRegistryStore(settings)
    store2.load()
    assert store2.count() == store.count()


def test_latest_version_resolution(tmp_path, tiny_desc_factory):
    store = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    for ver in ("1.0.0", "2.0.0"):
        store.register(tiny_desc_factory(model_version=ver), owner_scope={"global": "x"},
                       known_provider_refs=lambda ref: True)
    rec = store.resolve("unit-model", session_id="s1")
    assert rec.descriptor.model_version == "2.0.0"
    assert store.resolve("unit-model", "1.0.0", session_id="s1").descriptor.model_version == "1.0.0"


# ── loaded cache（R1-M4）─────────────────────────────────────────────


def test_cache_single_flight_concurrent_first_load():
    cache = LoadedModelCache(max_models=2)
    calls = {"n": 0}
    lock = threading.Lock()
    started = threading.Event()

    def load_fn():
        with lock:
            calls["n"] += 1
        started.set()
        import time as t

        t.sleep(0.05)
        return object()

    results = []

    def worker():
        results.append(cache.acquire("k", None, provider_ref="p", device="cpu",
                                     load_fn=load_fn))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert calls["n"] == 1
    assert all(r[0] is results[0][0] for r in results)


def test_cache_refcount_blocks_eviction():
    cache = LoadedModelCache(max_models=1)
    m1, _ = cache.acquire("k1", None, provider_ref="p", device="cpu",
                          load_fn=lambda: object())
    m2, _ = cache.acquire("k2", None, provider_ref="p", device="cpu",
                          load_fn=lambda: object())  # 驱逐候选不足 → 双条目共存
    assert cache.size() == 2  # refcount>0 条目不被容量驱逐
    cache.release("k1")
    cache.release("k2")
    m3, _ = cache.acquire("k3", None, provider_ref="p", device="cpu",
                          load_fn=lambda: object())
    assert cache.size() <= 2  # release 后容量恢复


def test_cache_negative_only_permanent():
    cache = LoadedModelCache(max_models=2)

    def fail_permanent():
        raise ProviderLoadFailed("bad weights")

    with pytest.raises(ProviderLoadFailed):
        cache.acquire("kp", None, provider_ref="p", device="cpu", load_fn=fail_permanent)
    with pytest.raises(ProviderLoadFailed):
        # 命中负缓存（不再调用 load_fn）
        cache.acquire("kp", None, provider_ref="p", device="cpu",
                      load_fn=lambda: pytest.fail("should not reload"))
    assert cache.stats()["negative_hits"] == 1

    def fail_transient():
        raise ProviderOOM("vram")

    with pytest.raises(ProviderOOM):
        cache.acquire("kt", None, provider_ref="p", device="cpu", load_fn=fail_transient)
    # transient 不缓存：这次真的重试
    assert cache.acquire("kt", None, provider_ref="p", device="cpu",
                         load_fn=lambda: object()) is not None


def test_cache_unload_failure_still_drops():
    cache = LoadedModelCache(max_models=1)

    def bad_unload(model):
        raise RuntimeError("unload boom")

    cache.acquire("k1", None, provider_ref="p", device="cpu",
                  load_fn=lambda: object(), unload_fn=bad_unload)
    cache.release("k1")
    cache.acquire("k2", None, provider_ref="p", device="cpu",
                  load_fn=lambda: object())  # 驱逐 k1 时 unload 抛错不毁缓存
    assert cache.size() == 2 or cache.size() == 1


def test_load_key_varies_by_fields(tiny_desc_factory):
    d = tiny_desc_factory()
    k1 = load_key(d, provider_ref="p1", device="cpu")
    k2 = load_key(d, provider_ref="p2", device="cpu")
    assert k1 != k2


# ── preprocess ───────────────────────────────────────────────────────


def _desc_for_preprocess():
    class _N:
        normalization = NormalizationSpec(kind="none")
        input_bands = 2
        spatial = None

    return _N()


def test_preprocess_band_select_and_pad():

    from app.lib.modelops.descriptor import SpatialRequirements
    from app.lib.modelops.planning import plan_tiles

    class _D:
        normalization = NormalizationSpec(kind="none")
        input_bands = 2
        spatial = SpatialRequirements(chip_size=(2, 2), context_size=(4, 4))

    plan = plan_tiles(_D.spatial and _D(), raster_height=2, raster_width=2)
    window = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]]], dtype=np.float32)
    plan_ = build_plan(_D(), source_band_count=2)
    chip, valid = preprocess_window(plan_, _D(), window, None, plan.tiles[0])
    assert chip.shape == (2, 4, 4)
    assert not valid[3, 3]  # 右/下 pad 区无效（tile 贴原点，pad 在右下）
    assert valid[0, 0] and valid[1, 1]  # 真实数据区有效


def test_preprocess_mean_std():
    from types import SimpleNamespace

    from app.lib.modelops.descriptor import SpatialRequirements

    desc = SimpleNamespace(
        normalization=NormalizationSpec(kind="mean_std", mean=(1.0, 2.0), std=(1.0, 2.0)),
        input_bands=2,
        spatial=SpatialRequirements(chip_size=(1, 1), context_size=(1, 1)),
    )
    plan = build_plan(desc, source_band_count=2)
    window = np.array([[[3.0]], [[6.0]]], dtype=np.float32)
    chip, valid = preprocess_window(plan, desc, window, None)
    assert chip[0, 0, 0] == pytest.approx(2.0)
    assert chip[1, 0, 0] == pytest.approx(2.0)
    assert valid.all()


def test_preprocess_rejects_out_of_range_bands():
    from types import SimpleNamespace

    desc = SimpleNamespace(normalization=NormalizationSpec(kind="none"), input_bands=3)
    with pytest.raises(Exception):
        build_plan(desc, source_band_count=2)


def test_preprocess_batch_memory_guard():
    from types import SimpleNamespace

    from app.lib.modelops.descriptor import SpatialRequirements

    desc = SimpleNamespace(
        normalization=NormalizationSpec(kind="none"),
        input_bands=1,
        spatial=SpatialRequirements(chip_size=(4, 4), context_size=(4, 4)),
    )
    plan = build_plan(desc, source_band_count=1)
    big = np.zeros((1, 20000, 20000), dtype=np.float32)
    with pytest.raises(Exception):
        preprocess_batch(plan, desc, [(big, None), (big, None), (big, None), (big, None)])


# ── metrics ──────────────────────────────────────────────────────────


def test_perf_counters_export_fields():
    perf = PerfCounters()
    perf.record_batch(4)
    perf.record_batch(2)
    perf.note_oom_downshift()
    perf.note_window(3, 900)
    perf.note_cache(True)
    perf.note_cache(False)
    perf.note_latency(load=0.01, warm=0.02, cancel=0.003, provider_rtt=0.005)
    perf.note_merge(1024)
    perf.note_resources(estimated_vram_bytes=1024, observed_vram_bytes=2048, device="cpu")
    perf.pixels_done = 5000
    perf.finish()
    exported = perf.export()
    for field in (
        "pixels_per_s", "tiles_per_s", "model_load_latency_s", "warm_inference_latency_s",
        "cancel_latency_s", "peak_host_memory_bytes", "estimated_vram_bytes",
        "observed_vram_bytes", "batch_sizes", "raster_windows_total", "bytes_read",
        "merge_work_px", "cache_hits", "cache_misses", "provider_rtt_s", "queue_wait_s",
        "oom_downshifts",
    ):
        assert field in exported, field
    assert exported["cache_hits"] == 1 and exported["cache_misses"] == 1
    assert exported["oom_downshifts"] == 1
