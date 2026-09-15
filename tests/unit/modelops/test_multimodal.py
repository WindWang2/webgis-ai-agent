"""Text/多模态 seam 测试（Platform 11 / WP-E）。

oracle 独立性：known-answer 用**手工构造的正交原型**（不经过被测
encoder）；stub 确定性用同/异文本的向量对比；typed 拒绝直接断言错误族。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.modelops.errors import MultimodalUnsupported
from app.lib.modelops.multimodal import (
    MAX_SEMANTIC_CLASSES,
    STUB_TEXT_ENCODER_VERSION,
    SemanticClassMap,
    StubTextEncoder,
)


class _OrthogonalEncoder:
    """测试专用正交 encoder：第 i 类 → e_i（手算期望的独立真值源）。"""

    def __init__(self, dimension: int = 8) -> None:
        self._dimension = dimension

    def capabilities(self):
        from app.lib.modelops.multimodal import TextEncoderCaps

        return TextEncoderCaps(
            encoder_id="orth-test", semantic_version="orth/1.0.0",
            dimension=self._dimension, stub=False,
        )

    def encode(self, texts):
        rows = np.zeros((len(texts), self._dimension), dtype=np.float32)
        for i, _ in enumerate(texts):
            rows[i, i % self._dimension] = 1.0
        return rows


def test_stub_encoder_deterministic_and_near_orthogonal():
    enc = StubTextEncoder(dimension=64)
    a1 = enc.encode(["water body"])
    a2 = enc.encode(["water body"])
    b = enc.encode(["dense forest"])
    np.testing.assert_array_equal(a1, a2)
    assert abs(float(a1[0] @ b[0])) < 0.35  # 异文本近正交（64 维；期望 ~0.1）
    norms = np.linalg.norm(enc.encode(["x", "y", "z"]), axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)
    caps = enc.capabilities()
    assert caps.stub is True
    assert caps.semantic_version == STUB_TEXT_ENCODER_VERSION


def test_register_refused_without_encoder():
    smap = SemanticClassMap(encoder=None)
    with pytest.raises(MultimodalUnsupported, match="no text encoder wired"):
        smap.register(["water"])


def test_map_refused_on_empty_index_and_dimension_mismatch():
    enc = _OrthogonalEncoder()
    smap = SemanticClassMap(encoder=enc)
    with pytest.raises(MultimodalUnsupported, match="no semantic classes"):
        smap.map_embedding([1.0] + [0.0] * 7)
    smap.register(["a", "b"])
    with pytest.raises(MultimodalUnsupported, match="dimension"):
        smap.map_embedding([1.0] * 5)


def test_zero_shot_known_answer_with_orthogonal_prototypes():
    enc = _OrthogonalEncoder(dimension=4)
    smap = SemanticClassMap(encoder=enc)
    smap.register(["water", "forest", "urban"])
    # 独立真值：encoder 把第 i 个注册名映到 e_i —— 构造 e_1 + 微扰。
    probe = np.zeros(4, dtype=np.float32)
    probe[1] = 1.0
    probe[0] = 0.05
    result = smap.map_embedding(probe.tolist(), top_k=3)
    assert result["top"][0]["class"] == "forest"
    assert result["top"][0]["score"] > 0.99
    assert [t["class"] for t in result["top"]] == ["forest", "water", "urban"]
    assert result["encoder"]["stub"] is False


def test_register_validation_and_caps_honesty():
    enc = StubTextEncoder(dimension=32)
    smap = SemanticClassMap(encoder=enc)
    with pytest.raises(MultimodalUnsupported, match="non-empty"):
        smap.register(["  "])
    with pytest.raises(MultimodalUnsupported, match="duplicate"):
        smap.register(["a", "a"])
    smap.register(["a", "b"])
    with pytest.raises(MultimodalUnsupported, match="already registered"):
        smap.register(["b", "c"])
    smap.register(["b2"], replace=True)
    assert smap.class_names() == ["a", "b", "b2"]
    with pytest.raises(MultimodalUnsupported, match="cap"):
        smap.register([f"c{i}" for i in range(MAX_SEMANTIC_CLASSES)])


def test_map_rejects_zero_vector():
    smap = SemanticClassMap(encoder=_OrthogonalEncoder())
    smap.register(["a"])
    with pytest.raises(MultimodalUnsupported, match="zero/non-finite"):
        smap.map_embedding([0.0, 0.0, 0.0, 0.0])
