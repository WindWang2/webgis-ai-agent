"""Tiny ONNX 测试模型构建器（V3 §B contract tests 专用 fixture）。

用 ``onnx.helper`` 构造**真实**可执行的极小计算图（1x1 Conv = 逐像素
线性层），权重确定性问题化（亮块 → 类别可断言）。产物 bytes 直接作为
``onnx-v1`` 模型包走 package_security → package_store → OnnxRuntimeProvider
全链路。

诚实边界：这不是「训练好的模型」，而是证明 ONNX Runtime 真实推理路径
（会话构造/执行/输出契约）的确定性神经网络。
"""
from __future__ import annotations

import numpy as np
import pytest


def deterministic_conv_weights(num_classes: int, bands: int, *, bright_class: int = 1) -> tuple:
    """确定性 1x1 Conv 权重：亮像素（~0.9）在 bright_class 得最高 logit，
    暗像素（~0.3）在类 0 得最高 logit（中间类衰减）。形状 (K,C,1,1)。"""
    rng = np.random.default_rng(7)
    weights = rng.uniform(-0.5, 0.5, size=(num_classes, bands)).astype(np.float32)
    # 亮度方向：bright_class 权重全正且大；类 0 反向。
    weights[bright_class] = 2.0
    weights[0] = -1.5
    weights = weights[:, :, None, None]  # Conv 1x1 要求 4D 权重 (K,C,1,1)
    bias = np.zeros((num_classes,), dtype=np.float32)
    return weights, bias


def build_onnx_segmentation_model(num_classes: int = 3, bands: int = 3) -> bytes:
    """(N,C,H,W) → 1x1 Conv → (N,K,H,W) **logits**（activation=softmax 消费）。"""
    pytest.importorskip("onnx")
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    weights, bias = deterministic_conv_weights(num_classes, bands)
    init_w = numpy_helper.from_array(weights, name="W")
    init_b = numpy_helper.from_array(bias, name="B")
    node = helper.make_node("Conv", ["pixels", "W", "B"], ["logits"], kernel_shape=[1, 1])
    graph = helper.make_graph(
        [node],
        "tiny_seg",
        [helper.make_tensor_value_info("pixels", TensorProto.FLOAT, ["N", bands, "H", "W"])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["N", num_classes, "H", "W"])],
        [init_w, init_b],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model.SerializeToString()


def build_onnx_classification_model(num_classes: int = 3, bands: int = 3) -> bytes:
    """(N,C,H,W) → GlobalAveragePool → 1x1 Conv → Reshape → (N,K) logits。"""
    pytest.importorskip("onnx")
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    weights, bias = deterministic_conv_weights(num_classes, bands)
    init_w = numpy_helper.from_array(weights, name="W")
    init_b = numpy_helper.from_array(bias, name="B")
    shape = numpy_helper.from_array(np.array([-1, num_classes], dtype=np.int64), name="shape")
    nodes = [
        helper.make_node("GlobalAveragePool", ["pixels"], ["pooled"]),
        helper.make_node("Conv", ["pooled", "W", "B"], ["logits_4d"], kernel_shape=[1, 1]),
        helper.make_node("Reshape", ["logits_4d", "shape"], ["logits"]),
    ]
    graph = helper.make_graph(
        nodes,
        "tiny_cls",
        [helper.make_tensor_value_info("pixels", TensorProto.FLOAT, ["N", bands, "H", "W"])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["N", num_classes])],
        [init_w, init_b, shape],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model.SerializeToString()


__all__ = [
    "build_onnx_classification_model",
    "build_onnx_segmentation_model",
    "deterministic_conv_weights",
]
