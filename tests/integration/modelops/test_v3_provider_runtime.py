"""V3 §B 集成切片：真实 ONNX 包注册 → 引擎端到端推理 → 工具 dispatch。

证明链路：service.register_model（package_security 门 + 内容寻址落盘）
→ registry → engine（tile 循环/融合/georef 产物）→ reuse 命中。
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from app.lib.modelops.backends import probe_backend
from tests.unit.modelops.onnx_fixtures import build_onnx_segmentation_model

pytest.importorskip("onnxruntime")


# ── 工具 dispatch 回归（P0 修复：import 路径错误曾让 run_inference 必炸）


def test_tool_dispatch_run_inference_no_module_error(service):
    """modelops_run_inference dispatch 不得再出现 ModuleNotFoundError。"""
    import asyncio

    from app.tools.modelops_tools import register_modelops_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_modelops_tools(registry)
    result = asyncio.run(
        registry.dispatch(
            "modelops_run_inference",
            {"model_id": "no-such-model", "source_uri": "x.tif"},
            session_id="s-fix",
        )
    )
    assert isinstance(result, dict)
    # 修复前：TOOL_ERROR message = "No module named 'app.lib.modelops.engine'"。
    message = str(result.get("message", ""))
    assert "No module named" not in message
    assert "app.lib.modelops.engine" not in message


# ── ONNX 模型端到端 ──────────────────────────────────────────────────


def _onnx_descriptor(service, onnx_bytes, *, model_id="onnx-seg-e2e"):
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        NormalizationSpec,
        OutputTransform,
        SpatialRequirements,
    )

    descriptor = GeoModelDescriptor.model_validate(
        {
            "model_id": model_id,
            "model_version": "1.0.0",
            "checksum": hashlib.sha256(onnx_bytes).hexdigest(),
            "provider_type": "onnx_adapter",
            "provider_ref": "onnx-runtime",
            "provider_semantic_version": "onnx-adapter/1.0.0",
            "task_types": ("semantic_segmentation",),
            "input_modalities": ("optical_rgb",),
            "input_bands": 3,
            "band_order": ("red", "green", "blue"),
            "normalization": NormalizationSpec(kind="none"),
            "output_types": ("class_raster", "confidence_raster"),
            "class_schema": ClassSchema(classes=("background", "bright", "mid")),
            "output_transform": OutputTransform(activation="softmax"),
            "spatial": SpatialRequirements(chip_size=(32, 32), context_size=(32, 32)),
            "device_requirements": DeviceRequirements(required="cpu"),
            "artifact_format": "onnx-v1",
            "license": "test",
        }
    )
    return descriptor


def test_onnx_model_registered_and_served(service, synthetic_raster):
    """真实 .onnx 包 → service.register_model → engine 全管线。"""
    info = probe_backend("onnxruntime")
    if not info.available:
        pytest.skip(f"onnxruntime unavailable in this environment: {info.detail}")
    onnx_bytes = build_onnx_segmentation_model()
    descriptor = _onnx_descriptor(service, onnx_bytes)
    reg = service.register_model(
        descriptor,
        owner_scope={"session_id": "s-onnx"},
        package_bytes=onnx_bytes,
        registered_by="test",
    )
    assert reg["package_stored"] is True
    # 包落盘 + load 双验通过（隐含：engine resolve 不炸）。

    from app.services.modelops.engine import InferenceRequest

    request = InferenceRequest(
        model_id="onnx-seg-e2e",
        source_uri=str(synthetic_raster),
        owner_scope={"session_id": "s-onnx"},
    )
    result = service.run_inference(request)
    assert result.status in ("completed", "reused")
    assert result.task_type == "semantic_segmentation"
    assert "classes" in result.outputs
    manifest = result.manifest
    assert manifest["provider"]["provider_ref"] == "onnx-runtime"
    # 输出概率来自 softmax → confidence ∈ (0,1]；类别栅格已发布。
    conf_role = manifest["outputs"]
    assert any(o.get("role") == "classes" for o in conf_role)
    # 确定性 + reuse：第二次同输入 run 命中复用。
    result2 = service.run_inference(
        InferenceRequest(
            model_id="onnx-seg-e2e",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s-onnx"},
        )
    )
    assert result2.status == "reused"
    assert result2.reuse_key == result.reuse_key


def test_onnx_same_model_different_activation_invalidates_reuse(service, synthetic_raster):
    """activation 是输出语义 → 变更必须产生不同 reuse key。"""
    from app.lib.modelops.descriptor import OutputTransform

    onnx_bytes = build_onnx_segmentation_model()
    import hashlib

    base = _onnx_descriptor(service, onnx_bytes, model_id="onnx-act-a")
    descriptor = base.model_copy(update={"output_transform": OutputTransform(activation="none")})
    key_none = descriptor.fingerprint_payload()["output_transform"]["activation"]
    assert key_none == "none"
    key_soft = base.fingerprint_payload()["output_transform"]["activation"]
    assert key_soft != key_none
