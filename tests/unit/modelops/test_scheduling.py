"""V3 §E：GPU 调度对接契约测试（账本/亲和/warm pool/engine 集成）。"""
from __future__ import annotations

import threading

import pytest

from app.lib.modelops.errors import ResourceUnavailable
from app.services.modelops.scheduling import (
    GpuDeviceInfo,
    VramLedger,
    WarmPoolManager,
    model_affinity_index,
    probe_gpu_devices,
)


# ── GPU 探测（本机多半无 GPU——空清单是合法且常见的诚实结果）────────


def test_probe_gpu_devices_never_raises():
    devices = probe_gpu_devices()
    for dev in devices:
        assert dev.index >= 0
        assert dev.total_mem_bytes >= 0


# ── VRAM 账本 ────────────────────────────────────────────────────────


def test_ledger_acquire_release_roundtrip():
    ledger = VramLedger(devices=(), fallback_budget_bytes=1000)
    res = ledger.acquire("cpu", 0, bytes_needed=600, run_id="r1")
    assert res.bytes_reserved == 600
    snap = ledger.snapshot()
    assert snap["used"]["cpu:0"] == 600
    ledger.release(res)
    assert ledger.snapshot()["used"]["cpu:0"] == 0


def test_ledger_exhaustion_typed():
    ledger = VramLedger(devices=(), fallback_budget_bytes=1000)
    r1 = ledger.acquire("cpu", 0, bytes_needed=900, run_id="r1")
    with pytest.raises(ResourceUnavailable):
        ledger.acquire("cpu", 0, bytes_needed=200, run_id="r2")
    ledger.release(r1)
    # 释放后可再预订。
    ledger.acquire("cpu", 0, bytes_needed=200, run_id="r3")


def test_ledger_oom_feedback_converges():
    ledger = VramLedger(devices=(), fallback_budget_bytes=1000)
    factor = ledger.report_oom("cuda", 0)
    assert factor == 0.5
    # 有效容量 500：600 被 typed 拒绝。
    with pytest.raises(ResourceUnavailable):
        ledger.acquire("cuda", 0, bytes_needed=600, run_id="r1")
    ledger.acquire("cuda", 0, bytes_needed=400, run_id="r2")
    assert ledger.report_oom("cuda", 0) == 0.25  # 下限


def test_ledger_per_device_isolation():
    ledger = VramLedger(
        devices=(GpuDeviceInfo(index=0, total_mem_bytes=1000),
                 GpuDeviceInfo(index=1, total_mem_bytes=1000)),
        fallback_budget_bytes=10_000,
    )
    ledger.acquire("cuda", 0, bytes_needed=800, run_id="a")
    # 设备 0 接近满；设备 1 独立账本不受影响。
    with pytest.raises(ResourceUnavailable):
        ledger.acquire("cuda", 0, bytes_needed=100, run_id="b")
    ledger.acquire("cuda", 1, bytes_needed=100, run_id="c")


def test_ledger_thread_safety():
    ledger = VramLedger(devices=(), fallback_budget_bytes=10_000_000)
    errors = []

    def worker(i):
        try:
            for _ in range(50):
                res = ledger.acquire("cpu", 0, bytes_needed=100, run_id=f"r{i}")
                ledger.release(res)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert ledger.snapshot()["used"]["cpu:0"] == 0


# ── 多 GPU 亲和 ──────────────────────────────────────────────────────


def test_affinity_deterministic_and_bounded():
    assert model_affinity_index("model-a", 0) == 0
    assert model_affinity_index("model-a", 1) == 0
    idx_a = model_affinity_index("model-a", 4)
    assert idx_a == model_affinity_index("model-a", 4)  # 确定性
    assert 0 <= idx_a < 4
    spread = {model_affinity_index(f"m{i}", 4) for i in range(16)}
    assert len(spread) > 1  # 不同模型能落到不同卡


# ── Warm pool ────────────────────────────────────────────────────────


class _CountingProvider:
    """load 计数 provider（warm pool 钉扎效果的观测量）。"""

    def __init__(self):
        self.loads = 0
        self._lock = threading.Lock()

    def capabilities(self):
        from app.lib.modelops.capabilities import (
            DEVICE_CPU,
            TASK_SEMANTIC_SEGMENTATION,
            ProviderCapabilities,
        )

        return ProviderCapabilities(
            provider_id="counting", provider_type="local_reference",
            semantic_version="counting/1.0.0",
            tasks=frozenset({TASK_SEMANTIC_SEGMENTATION}),
            devices=frozenset({DEVICE_CPU}), max_batch=2,
        )

    def load(self, descriptor, *, device):
        with self._lock:
            self.loads += 1
        from app.services.modelops.providers.base import LoadedModel

        return LoadedModel(
            descriptor=descriptor, provider_id="counting", device=device,
            handle_id=f"{descriptor.model_id}#{self.loads}", state=None,
        )

    def warmup(self, model):
        return {"warmed": True}

    def unload(self, model):
        pass


class _FakeRecord:
    def __init__(self, descriptor):
        self.descriptor = descriptor


class _FakeRegistry:
    def __init__(self, descriptors):
        self._descriptors = descriptors

    def resolve(self, model_id, model_version=None, **kwargs):
        from app.lib.modelops.errors import ModelNotFoundError

        if model_id not in self._descriptors:
            raise ModelNotFoundError(f"model {model_id!r} not found")
        return _FakeRecord(self._descriptors[model_id])


def test_warm_pool_pin_prevents_reload_and_reports_error(tmp_path):
    from app.lib.modelops.descriptor import GeoModelDescriptor
    from app.services.modelops.loaded_cache import LoadedModelCache

    provider = _CountingProvider()
    descriptor = GeoModelDescriptor.model_validate(
        {
            "model_id": "warm-model", "model_version": "1.0.0", "checksum": "a" * 64,
            "provider_type": "local_reference", "provider_ref": "counting",
            "provider_semantic_version": "counting/1.0.0",
            "task_types": ("semantic_segmentation",),
            "input_modalities": ("optical_rgb",), "input_bands": 3,
            "output_types": ("class_raster",),
            "class_schema": {"classes": ("background", "bright", "mid")},
            "spatial": {"chip_size": [8, 8], "context_size": [8, 8]},
            "license": "test",
        }
    )
    cache = LoadedModelCache(max_models=4)
    registry = _FakeRegistry({"warm-model": descriptor})
    providers = {"counting": provider}
    warm = WarmPoolManager(cache, registry, providers)

    state = warm.pin("warm-model")
    assert state["pinned"] is True
    assert provider.loads == 1
    # 幂等：重复 pin 不重载。
    warm.pin("warm-model")
    assert provider.loads == 1

    # 不存在的模型 → honest 失败记录（不抛）。
    bad = warm.pin("no-such-model")
    assert bad["pinned"] is False
    assert bad["error"]

    status = warm.status()
    assert status["warm-model"]["pinned"] is True
    released = warm.release_all()
    assert released == 1
