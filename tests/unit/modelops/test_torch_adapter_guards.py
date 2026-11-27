"""P3 review 回归：TorchScript adapter 的 typed 错误与设备迁移原子性。"""
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
from app.lib.modelops.capabilities import DEVICE_CUDA
from app.lib.modelops.errors import ProviderError
from app.services.modelops.package_store import ModelPackageStore
from app.services.modelops.providers.base import (
    InferenceContext,
    LoadedModel,
    TileBatch,
)
from app.services.modelops.providers.torch_adapter import TorchScriptProvider


def _descriptor():
    return GeoModelDescriptor.model_validate(
        {
            "model_id": "p3-torch-model",
            "model_version": "1.0.0",
            "checksum": "d" * 64,
            "provider_type": "torch_adapter",
            "provider_ref": "torch-script",
            "provider_semantic_version": "torch-adapter/1.0.0",
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


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _InputTensor:
    def __init__(self, arr):
        self.arr = arr
        self.device = "cpu"

    def to(self, device):
        self.device = device
        return self


class _OutputTensor:
    def __init__(self, arr):
        self.arr = arr

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.arr


class _FakeTorch:
    def no_grad(self):
        return _NoGrad()

    def from_numpy(self, arr):
        return _InputTensor(arr)


def _batch():
    return TileBatch(
        pixels=np.zeros((1, 3, 8, 8), dtype=np.float32), chip_hw=(8, 8)
    )


def test_non_tensor_module_output_is_typed_provider_error(tmp_path):
    """dict 等非张量输出：_to_numpy 的 AttributeError 必须分类为 ProviderError。"""

    class _DictModule:
        def __call__(self, _x):
            return {"logits": _x}

    provider = TorchScriptProvider(ModelPackageStore(tmp_path / "packages"))
    model = LoadedModel(
        descriptor=_descriptor(),
        provider_id="torch-script",
        device="cpu",
        handle_id="h",
        state={"torch": _FakeTorch(), "module": _DictModule()},
    )
    with pytest.raises(ProviderError, match="torch inference failed"):
        provider.infer(model, _batch(), InferenceContext(run_id="p3-torch"))


def test_cuda_module_migration_and_call_are_atomic(tmp_path):
    """CUDA 分支：模块迁移与调用必须在同一把锁内（并发不同 device_index）。"""
    observed = []

    class _ProbeModule:
        def __init__(self):
            self.device = "cpu"

        def to(self, device):
            self.device = device
            return self

        def __call__(self, x):
            observed.append(provider._lock.locked() and x.device == self.device)
            return _OutputTensor(np.zeros((1, 2, 8, 8), dtype=np.float32))

    provider = TorchScriptProvider(ModelPackageStore(tmp_path / "packages"))
    model = LoadedModel(
        descriptor=_descriptor(),
        provider_id="torch-script",
        device=DEVICE_CUDA,
        handle_id="h",
        state={"torch": _FakeTorch(), "module": _ProbeModule()},
    )
    ctx = InferenceContext(run_id="p3-torch-cuda", extras={"device_index": 1})
    out = provider.infer(model, _batch(), ctx)
    assert observed == [True]
    assert out.class_probabilities.shape == (1, 2, 8, 8)
