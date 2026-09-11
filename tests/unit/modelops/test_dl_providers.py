"""V3 §B：ONNX Runtime / TorchScript / subprocess provider 契约测试。"""
from __future__ import annotations


import numpy as np
import pytest

from app.lib.modelops.backends import BACKEND_TORCH, probe_backend, reset_probe_cache
from app.lib.modelops.errors import ProviderLoadFailed, ProviderOOM
from app.lib.modelops.package_security import sha256_of_bytes
from app.services.modelops.package_store import ModelPackageStore
from app.services.modelops.providers.base import InferenceContext, TileBatch
from app.services.modelops.providers.dl_output import apply_activation, map_dl_outputs

from .onnx_fixtures import build_onnx_segmentation_model

onnxruntime = pytest.importorskip("onnxruntime")

from app.services.modelops.providers.onnx_adapter import OnnxRuntimeProvider  # noqa: E402


# ── dl_output 映射（共享契约）────────────────────────────────────────


def _seg_descriptor():
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )

    return GeoModelDescriptor.model_validate(
        {
            "model_id": "dl-map-model",
            "model_version": "1.0.0",
            "checksum": "a" * 64,
            "provider_type": "onnx_adapter",
            "provider_ref": "onnx-runtime",
            "provider_semantic_version": "onnx-adapter/1.0.0",
            "task_types": ("semantic_segmentation",),
            "input_modalities": ("optical_rgb",),
            "input_bands": 3,
            "normalization": NormalizationSpec(kind="none"),
            "output_types": ("class_raster",),
            "class_schema": ClassSchema(classes=("background", "bright", "mid")),
            "spatial": SpatialRequirements(chip_size=(8, 8), context_size=(8, 8)),
            "device_requirements": DeviceRequirements(required="cpu"),
            "license": "test",
        }
    )


def test_apply_activation_softmax_sums_to_one():
    logits = np.array([[[[2.0, 0.0]], [[0.0, 1.0]]]], dtype=np.float32)  # (1,2,1,2)
    probs = apply_activation(logits, activation="softmax")
    np.testing.assert_allclose(probs.sum(axis=1), 1.0, atol=1e-5)
    assert probs.dtype == np.float32


def test_apply_activation_sigmoid_expands_two_class():
    logits = np.array([[[[3.0]]]], dtype=np.float32)  # (1,1,1,1)
    probs = apply_activation(logits, activation="sigmoid")
    assert probs.shape == (1, 2, 1, 1)
    np.testing.assert_allclose(probs.sum(axis=1), 1.0, atol=1e-6)


def test_map_dl_outputs_detection_conversion():
    desc = _seg_descriptor()
    batch = TileBatch(pixels=np.zeros((2, 3, 8, 8), dtype=np.float32))
    raw = [
        np.array(
            [
                [[10, 10, 20, 20, 0.9, 1], [0, 0, 5, 5, 0.4, 1]],
                [[30, 30, 40, 44, 0.7, 2], [0, 0, 0, 0, 0.0, 0]],
            ],
            dtype=np.float32,
        ),  # (N=2, M=2, 6)
    ]
    out = map_dl_outputs("object_detection", raw, desc, batch)
    assert out.task_type == "object_detection"
    boxes = {(d["batch_index"], tuple(np.round(d["box"], 3))) for d in out.detections}
    assert (0, (10.0, 10.0, 10.0, 10.0)) in boxes
    assert (1, (30.0, 30.0, 10.0, 14.0)) in boxes


def test_map_dl_outputs_rejects_wrong_shape():
    desc = _seg_descriptor()
    batch = TileBatch(pixels=np.zeros((1, 3, 8, 8), dtype=np.float32))
    with pytest.raises(Exception):  # noqa: B017 — ProviderError typed
        map_dl_outputs("semantic_segmentation", [np.zeros((1, 8, 8), dtype=np.float32)], desc, batch)


# ── OnnxRuntimeProvider（真实推理）───────────────────────────────────


@pytest.fixture()
def onnx_provider(tmp_path):
    store = ModelPackageStore(tmp_path / "packages")
    return OnnxRuntimeProvider(store), store


def test_onnx_provider_capabilities_probe_driven(onnx_provider):
    provider, _ = onnx_provider
    caps = provider.capabilities()
    assert caps.provider_type == "onnx_adapter"
    assert "cpu" in caps.devices
    assert "semantic_segmentation" in caps.tasks


def test_onnx_provider_end_to_end_segmentation(tmp_path, onnx_provider):
    provider, store = onnx_provider
    data = build_onnx_segmentation_model()
    # checksum 必须等于包内容 sha256 —— 注册面同一口径。
    from app.lib.modelops.descriptor import GeoModelDescriptor

    desc = GeoModelDescriptor.model_validate(
        {
            **_seg_descriptor().model_dump(mode="json"),
            "checksum": sha256_of_bytes(data),
            "artifact_format": "onnx-v1",
        }
    )
    store.persist(desc, data)
    model = provider.load(desc, device="cpu")
    warm = provider.warmup(model)
    assert warm["warmed"] is True

    pixels = np.full((2, 3, 8, 8), 0.9, dtype=np.float32)  # 亮块 → bright_class
    batch = TileBatch(pixels=pixels, chip_hw=(8, 8))
    out = provider.infer(model, batch, InferenceContext(run_id="t1"))
    out.validate_for(batch)
    assert out.class_probabilities is not None
    assert out.class_probabilities.shape == (2, 3, 8, 8)
    # 全亮像素 → argmax = bright_class（类 1）。
    assert int(out.class_probabilities[0].argmax(axis=0)[0, 0]) == 1
    # 确定性：同输入两次逐位一致。
    out2 = provider.infer(model, batch, InferenceContext(run_id="t2"))
    np.testing.assert_array_equal(out.class_probabilities, out2.class_probabilities)
    provider.unload(model)


def test_onnx_provider_missing_package_typed(onnx_provider):
    provider, _ = onnx_provider
    desc = _seg_descriptor()
    with pytest.raises(ProviderLoadFailed):
        provider.load(desc, device="cpu")


def test_onnx_provider_rejects_unserved_task(onnx_provider):
    provider, _ = onnx_provider
    from app.lib.modelops.descriptor import GeoModelDescriptor

    desc = _seg_descriptor()
    desc = GeoModelDescriptor.model_validate(
        {**desc.model_dump(mode="json"), "task_types": ("temporal_forecast",)}
    )
    with pytest.raises(ProviderLoadFailed):
        provider.load(desc, device="cpu")


# ── TorchScriptProvider（探测门；不依赖 torch 可用性）────────────────


def test_torch_provider_typed_when_runtime_unavailable(tmp_path, monkeypatch):
    """torch 缺席/损坏 → load typed ProviderLoadFailed（V3 降级验收）。"""
    from app.services.modelops.providers.torch_adapter import TorchScriptProvider

    reset_probe_cache()
    info = probe_backend(BACKEND_TORCH)
    provider = TorchScriptProvider(ModelPackageStore(tmp_path / "packages"))
    caps = provider.capabilities()
    assert caps.provider_type == "torch_adapter"
    if not info.available:
        # 本机/CI 无可用 torch：load 必须 typed（绝不裸抛 ImportError）。
        with pytest.raises(ProviderLoadFailed):
            provider.load(_seg_descriptor(), device="cpu")
    else:
        # torch 可用的环境：包缺失必须 typed ProviderLoadFailed。
        with pytest.raises(ProviderLoadFailed):
            provider.load(_seg_descriptor(), device="cpu")


def test_torch_provider_cuda_request_typed_when_unavailable(tmp_path, monkeypatch):
    from app.services.modelops.providers.torch_adapter import TorchScriptProvider

    reset_probe_cache()
    info = probe_backend(BACKEND_TORCH)
    provider = TorchScriptProvider(ModelPackageStore(tmp_path / "packages"))
    if not info.cuda_available:
        with pytest.raises(ProviderLoadFailed):
            provider.load(_seg_descriptor(), device="cuda")


# ── SubprocessWorkerProvider ─────────────────────────────────────────

SEGMENTATION_WORKER = r'''
"""测试用子进程推理 worker：stdin JSON → stdout JSON（确定性阈值分割）。"""
import base64, json, sys
import numpy as np

def main():
    request = json.loads(sys.stdin.readline())
    shape = request["shape"]
    pixels = np.frombuffer(base64.b64decode(request["pixels_b64"]), dtype=np.float32).reshape(shape)
    mean = pixels.mean(axis=1, keepdims=True)                     # (N,1,H,W)
    bright = (mean > 0.5).astype(np.float32)
    p_obj = np.clip(bright, 0.02, 0.98)
    probs = np.concatenate([1.0 - p_obj, p_obj], axis=1).astype(np.float32)
    weights = request.get("weights_b64")
    if weights is not None:
        w = np.frombuffer(base64.b64decode(weights), dtype=np.float32)
        probs = probs * float(w[0] if w.size else 1.0)
        s = probs.sum(axis=1, keepdims=True)
        probs = np.where(s == 0, 0.5, probs / np.where(s == 0, 1.0, s)).astype(np.float32)
    response = {
        "class_probabilities_b64": base64.b64encode(probs.tobytes()).decode("ascii"),
        "class_probabilities_b64_shape": list(probs.shape),
    }
    sys.stdout.write(json.dumps(response))

if __name__ == "__main__":
    main()
'''


@pytest.fixture()
def subprocess_provider(tmp_path):
    from app.services.modelops.providers.subprocess_adapter import SubprocessWorkerProvider

    script = tmp_path / "worker.py"
    script.write_text(SEGMENTATION_WORKER, encoding="utf-8")
    return SubprocessWorkerProvider("fixture", str(script), deadline_s=60.0)


def _subprocess_descriptor(tmp_path):
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )

    return GeoModelDescriptor.model_validate(
        {
            "model_id": "sub-model",
            "model_version": "1.0.0",
            "checksum": "d" * 64,
            "provider_type": "local_subprocess",
            "provider_ref": "subprocess@fixture",
            "provider_semantic_version": "subprocess/fixture/1.0.0",
            "task_types": ("semantic_segmentation",),
            "input_modalities": ("optical_rgb",),
            "input_bands": 3,
            "normalization": NormalizationSpec(kind="none"),
            "output_types": ("class_raster",),
            "class_schema": ClassSchema(classes=("background", "object")),
            "spatial": SpatialRequirements(chip_size=(8, 8), context_size=(8, 8)),
            "device_requirements": DeviceRequirements(required="cpu"),
            "license": "test",
        }
    )


def test_subprocess_roundtrip_segmentation(subprocess_provider, tmp_path):
    provider = subprocess_provider
    caps = provider.capabilities()
    assert caps.provider_id == "subprocess@fixture"
    assert caps.provider_type == "local_subprocess"
    model = provider.load(_subprocess_descriptor(tmp_path), device="cpu")
    pixels = np.zeros((2, 3, 8, 8), dtype=np.float32)
    pixels[0] = 0.9
    batch = TileBatch(pixels=pixels, chip_hw=(8, 8))
    out = provider.infer(model, batch, InferenceContext(run_id="sp1"))
    out.validate_for(batch)
    # 亮 chip → object；暗 chip → background。
    assert int(out.class_probabilities[0].argmax(axis=0)[0, 0]) == 1
    assert int(out.class_probabilities[1].argmax(axis=0)[0, 0]) == 0
    provider.unload(model)


def test_subprocess_deadline_typed(tmp_path):
    """deadline 超时 → kill + ProviderOOM（有界执行的验收）。"""
    from app.services.modelops.providers.subprocess_adapter import SubprocessWorkerProvider

    script = tmp_path / "slow_worker.py"
    script.write_text(
        "import time, sys\nsys.stdin.readline()\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    provider = SubprocessWorkerProvider("slow", str(script), deadline_s=2.0)
    model = provider.load(_subprocess_descriptor(tmp_path), device="cpu")
    batch = TileBatch(pixels=np.zeros((1, 3, 8, 8), dtype=np.float32), chip_hw=(8, 8))
    with pytest.raises(ProviderOOM):
        provider.infer(model, batch, InferenceContext(run_id="slow1"))


def test_parse_worker_allowlist_validation(tmp_path):
    from app.services.modelops.providers.subprocess_adapter import parse_worker_allowlist

    script = tmp_path / "w.py"
    script.write_text("pass", encoding="utf-8")
    allowlist = parse_worker_allowlist([f"thermal={script}"])
    assert allowlist == {"thermal": str(script.resolve())}
    with pytest.raises(Exception):  # noqa: B017 — 缺 '=' 的条目 typed
        parse_worker_allowlist(["thermal-no-path"])
    with pytest.raises(Exception):  # noqa: B017 — 路径不存在的条目 typed
        parse_worker_allowlist([f"ghost={tmp_path / 'missing.py'}"])
