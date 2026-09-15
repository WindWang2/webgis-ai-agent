"""Text/多模态 seam —— 类别原型与 zero-shot 语义映射（Platform 11 / WP-E）。

诚实边界（GOAL Oracle 2 的多模态面）：
- 本模块只定义 **seam**（TextEncoder 协议 + 语义类映射）；不捆绑任何
  真实文本/多模态模型权重；
- 没有接线 encoder 时，原型注册与 zero-shot 映射**typed 拒绝**
  （:class:`MultimodalUnsupported`），绝不以哈希 stub 冒充语义能力；
- ``StubTextEncoder`` 是显式标注的确定性参考实现（``capabilities().stub
  == True``，semantic_version ``text-stub/1.0.0``）——同文本同向量、
  异文本近正交（高维伪随机投影），仅用于离线管道验证/测试。

数学：原型与输入向量统一 L2 归一；映射 = 余弦相似排序（等价于内积），
分数 ∈ [-1,1]；top_k 有界。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence

import numpy as np

from app.lib.modelops.errors import MultimodalUnsupported

#: stub encoder 的语义版本（进入结果 provenance，绝不与真实 encoder 混淆）。
STUB_TEXT_ENCODER_VERSION = "text-stub/1.0.0"
DEFAULT_STUB_DIMENSION = 64

MAX_SEMANTIC_CLASSES = 512
MAX_ZERO_SHOT_TOP_K = 16


@dataclass(frozen=True)
class TextEncoderCaps:
    """文本编码器能力声明（stub 位是诚实性硬字段）。"""

    encoder_id: str
    semantic_version: str
    dimension: int
    stub: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "encoder_id": self.encoder_id,
            "semantic_version": self.semantic_version,
            "dimension": int(self.dimension),
            "stub": bool(self.stub),
        }


class TextEncoder(Protocol):
    """文本 → 单位向量 encoder 协议（services 层接线；lib 层只此契约）。"""

    def capabilities(self) -> TextEncoderCaps: ...

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """(N, D) float32，行向量 L2 归一（确定性：同文本同向量）。"""
        ...


class StubTextEncoder:
    """确定性参考 encoder（sha256 种子伪随机投影；**非真实语义**）。

    同文本 → 同种子 → 同向量；异文本在高维下近正交。仅当 operator 显式
    配置（``MODELOPS_TEXT_ENCODER=stub``）才接线——离线测试/管道验证用。
    """

    def __init__(self, dimension: int = DEFAULT_STUB_DIMENSION) -> None:
        if dimension < 4:
            # 下限 4：与既有 chip-embedding 模型（如 6 维）可组合；正交性
            # 随维度下降而退化（stub 语义下可接受，测试断言只在高维做）。
            raise ValueError("stub text encoder dimension must be >= 4")
        self._dimension = int(dimension)

    def capabilities(self) -> TextEncoderCaps:
        return TextEncoderCaps(
            encoder_id="text-stub",
            semantic_version=STUB_TEXT_ENCODER_VERSION,
            dimension=self._dimension,
            stub=True,
        )

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        rows = []
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise MultimodalUnsupported(
                    "text encoder requires non-empty strings",
                    correction_hint="drop empty class names",
                )
            seed = int.from_bytes(
                hashlib.sha256(text.encode("utf-8")).digest()[:8], "big"
            )
            rng = np.random.default_rng(seed)
            vec = rng.standard_normal(self._dimension).astype(np.float32)
            norm = float(np.linalg.norm(vec))
            rows.append(vec / max(norm, 1e-12))
        return np.stack(rows) if rows else np.zeros((0, self._dimension), np.float32)


def _unit(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if not np.isfinite(norm) or norm <= 0.0:
        raise MultimodalUnsupported(
            "embedding vector has zero/non-finite norm",
            correction_hint="pass a finite non-zero embedding",
        )
    return (vec / norm).astype(np.float32)


class SemanticClassMap:
    """类别原型索引 + zero-shot 余弦映射（有界、确定性、typed 拒绝）。

    - 无 encoder：``register`` typed 拒绝（不注册假原型）；
    - 维度失配/空索引/空向量：``map_embedding`` typed 拒绝；
    - 结果携带 encoder 能力（含 stub 标注）——provenance 如实。
    """

    def __init__(self, encoder: Optional[TextEncoder] = None) -> None:
        self._encoder = encoder
        self._prototypes: Dict[str, np.ndarray] = {}

    @property
    def encoder_caps(self) -> Optional[TextEncoderCaps]:
        return self._encoder.capabilities() if self._encoder is not None else None

    def register(self, class_names: Sequence[str], *, replace: bool = False) -> Dict[str, object]:
        if self._encoder is None:
            raise MultimodalUnsupported(
                "no text encoder wired: semantic class registration refused "
                "(this platform does not fake text understanding)",
                correction_hint="configure a text encoder "
                "(MODELOPS_TEXT_ENCODER=stub for offline verification only)",
            )
        names = [str(n).strip() for n in class_names]
        if not names or any(not n for n in names):
            raise MultimodalUnsupported(
                "class names must be non-empty strings",
            )
        if len(set(names)) != len(names):
            raise MultimodalUnsupported("duplicate class names in registration")
        if not replace and (set(names) & set(self._prototypes)):
            overlap = sorted(set(names) & set(self._prototypes))
            raise MultimodalUnsupported(
                f"classes already registered (pass replace=True): {overlap[:8]}",
            )
        if len(self._prototypes) + len(names) > MAX_SEMANTIC_CLASSES:
            raise MultimodalUnsupported(
                f"semantic class count exceeds cap {MAX_SEMANTIC_CLASSES}"
            )
        vectors = self._encoder.encode(names)
        for name, vec in zip(names, vectors):
            self._prototypes[name] = _unit(vec)
        return {
            "registered": names,
            "total": len(self._prototypes),
            "encoder": self._encoder.capabilities().as_dict(),
        }

    def map_embedding(
        self, embedding: Sequence[float], *, top_k: int = 1
    ) -> Dict[str, object]:
        if not self._prototypes:
            raise MultimodalUnsupported(
                "no semantic classes registered: zero-shot mapping refused",
                correction_hint="register class prototypes first",
            )
        if self._encoder is None:  # pragma: no cover — register 已拦截
            raise MultimodalUnsupported("no text encoder wired")
        k = max(1, min(int(top_k), MAX_ZERO_SHOT_TOP_K, len(self._prototypes)))
        vec = _unit(np.asarray(list(embedding), dtype=np.float32))
        dimension = self._encoder.capabilities().dimension
        if vec.shape[0] != dimension:
            raise MultimodalUnsupported(
                f"embedding dimension {vec.shape[0]} != encoder dimension "
                f"{dimension}",
                correction_hint="use an embedding from the same encoder family",
            )
        names = sorted(self._prototypes)
        matrix = np.stack([self._prototypes[n] for n in names])
        scores = matrix @ vec
        order = np.argsort(-scores, kind="stable")[:k]
        return {
            "top": [
                {"class": names[int(i)], "score": float(scores[int(i)])}
                for i in order
            ],
            "encoder": self._encoder.capabilities().as_dict(),
            "class_count": len(names),
        }

    def class_names(self) -> List[str]:
        return sorted(self._prototypes)


__all__ = [
    "DEFAULT_STUB_DIMENSION",
    "MAX_SEMANTIC_CLASSES",
    "MAX_ZERO_SHOT_TOP_K",
    "STUB_TEXT_ENCODER_VERSION",
    "SemanticClassMap",
    "StubTextEncoder",
    "TextEncoder",
    "TextEncoderCaps",
]
