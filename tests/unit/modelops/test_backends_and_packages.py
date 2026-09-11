"""V3 §B：DL 后端探测 / 单文件模型包门 / 包存储 的契约测试。"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.modelops.backends import (
    BACKEND_IDS,
    BACKEND_ONNXRUNTIME,
    BACKEND_TORCH,
    probe_all,
    probe_backend,
    reset_probe_cache,
    onnx_execution_providers,
)
from app.lib.modelops.errors import ModelChecksumError, PackageSecurityError, ProviderError
from app.lib.modelops.package_security import (
    ARTIFACT_FORMAT_ONNX,
    inspect_model_file,
    sha256_of_bytes,
)
from app.services.modelops.package_store import ModelPackageStore

from .onnx_fixtures import build_onnx_segmentation_model


# ── backends probe ───────────────────────────────────────────────────


def test_probe_unknown_backend_rejected():
    with pytest.raises(ValueError):
        probe_backend("cuda9")


def test_probe_all_returns_closed_vocab_and_never_raises():
    reset_probe_cache()
    infos = probe_all()
    assert set(infos) == set(BACKEND_IDS)
    for info in infos.values():
        assert info.backend in BACKEND_IDS
        assert isinstance(info.available, bool)
        payload = info.as_dict()
        assert payload["backend"] == info.backend


def test_probe_onnxruntime_shape():
    reset_probe_cache()
    info = probe_backend(BACKEND_ONNXRUNTIME)
    # 本测试环境断言最小契约（available 与 providers 词表一致），不在
    # available 本身上断言真值——CI/本地差异由 probe 结果如实呈现。
    assert info.backend == BACKEND_ONNXRUNTIME
    if info.available:
        assert "CPUExecutionProvider" in info.execution_providers


def test_probe_torch_honest_when_broken(monkeypatch):
    """torch import 损坏（Windows DLL 失败常态）→ available=False + detail。"""
    import sys as _sys

    reset_probe_cache()
    # sys.modules 置 None → import 抛 ImportError（DLL 损坏的同型失败）。
    monkeypatch.setitem(_sys.modules, "torch", None)
    info = probe_backend(BACKEND_TORCH)
    assert info.available is False
    assert "torch runtime unavailable" in info.detail


def test_onnx_execution_providers_ends_with_cpu():
    eps = onnx_execution_providers()
    assert eps, "onnxruntime available in test env must yield providers"
    assert eps[-1] == "CPUExecutionProvider"


# ── inspect_model_file（单文件模型包门）──────────────────────────────


def test_inspect_model_file_accepts_real_onnx_and_verifies_checksum():
    data = build_onnx_segmentation_model()
    report = inspect_model_file(
        data, expected_checksum=sha256_of_bytes(data), allowed_suffix=".onnx"
    )
    assert report.checksum == sha256_of_bytes(data)
    assert report.entries[0].name == "model.onnx"
    assert report.metadata["format"] == "single-file.onnx"


def test_inspect_model_file_checksum_mismatch_typed():
    data = build_onnx_segmentation_model()
    with pytest.raises(ModelChecksumError):
        inspect_model_file(data, expected_checksum="0" * 64, allowed_suffix=".onnx")


def test_inspect_model_file_rejects_bad_suffix_and_non_protobuf():
    data = build_onnx_segmentation_model()
    with pytest.raises(PackageSecurityError):
        inspect_model_file(data, allowed_suffix=".pth")
    # 非空但明显不是 protobuf（文本）的载荷拒绝。
    with pytest.raises(PackageSecurityError):
        inspect_model_file(b"definitely not a protobuf payload......", allowed_suffix=".onnx")


# ── ModelPackageStore ────────────────────────────────────────────────


def _desc_with_checksum(checksum: str):
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )

    return GeoModelDescriptor.model_validate(
        {
            "model_id": "pkg-store-model",
            "model_version": "1.0.0",
            "checksum": checksum,
            "provider_type": "onnx_adapter",
            "provider_ref": "onnx-runtime",
            "provider_semantic_version": "onnx-adapter/1.0.0",
            "task_types": ("semantic_segmentation",),
            "input_modalities": ("optical_rgb",),
            "input_bands": 3,
            "normalization": NormalizationSpec(kind="none"),
            "output_types": ("class_raster",),
            "class_schema": ClassSchema(classes=("background", "bright", "mid")),
            "spatial": SpatialRequirements(chip_size=(16, 16), context_size=(16, 16)),
            "device_requirements": DeviceRequirements(required="cpu"),
            "artifact_format": ARTIFACT_FORMAT_ONNX,
            "license": "test",
        }
    )


def test_package_store_roundtrip_and_tamper_detection(tmp_path):
    store = ModelPackageStore(tmp_path / "packages")
    data = build_onnx_segmentation_model()
    desc = _desc_with_checksum(sha256_of_bytes(data))
    path = store.persist(desc, data)
    assert path.exists()
    resolved = store.resolve(desc)
    assert resolved == path
    # 篡改 → load 双验 typed 拒绝。
    path.write_bytes(path.read_bytes() + b"\x00")
    with pytest.raises(ModelChecksumError):
        store.resolve(desc)


def test_package_store_resolve_missing_typed(tmp_path):
    store = ModelPackageStore(tmp_path / "packages")
    desc = _desc_with_checksum("a" * 64)
    with pytest.raises(ProviderError):
        store.resolve(desc)


def test_package_store_persist_rejects_checksum_mismatch(tmp_path):
    store = ModelPackageStore(tmp_path / "packages")
    data = build_onnx_segmentation_model()
    desc = _desc_with_checksum("b" * 64)  # 与 data 不符
    with pytest.raises(ProviderError):
        store.persist(desc, data)


def test_descriptor_output_transform_in_fingerprint():
    from app.lib.modelops.descriptor import GeoModelDescriptor, OutputTransform

    desc = _desc_with_checksum("c" * 64)
    payload = desc.fingerprint_payload()
    assert payload["output_transform"] == {"activation": "softmax", "output_scale": 1}
    # activation 是输出语义 → 不同 activation 不同指纹（reuse 失效）。
    changed = desc.model_copy(update={"output_transform": OutputTransform(activation="none")})
    assert changed.fingerprint_payload()["output_transform"]["activation"] == "none"
    assert changed.fingerprint_payload() != payload


def test_numpy_guard_for_b64_roundtrip():
    """子进程协议的 b64 数组保真（float32 字节序不变）。"""
    import base64

    arr = np.array([[1.5, 2.5]], dtype=np.float32)
    encoded = base64.b64encode(arr.tobytes()).decode("ascii")
    decoded = np.frombuffer(base64.b64decode(encoded), dtype=np.float32).reshape(arr.shape)
    np.testing.assert_array_equal(decoded, arr)
