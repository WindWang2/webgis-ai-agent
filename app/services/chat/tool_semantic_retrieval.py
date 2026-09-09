"""Tool Retrieval V6 —— 语义检索路径（TOOL_RETRIEVAL_SEMANTIC 的生产实现）。

现状（Phase-0 审计 00-baseline §F）：``TOOL_RETRIEVAL_SEMANTIC`` hook 默认
空串，生产等价 lexical-only。本模块提供项目内可控的语义检索：

- **离线可控**：embedding 复用 RAG 既有模型约定
  （``paraphrase-multilingual-MiniLM-L12-v2``；``RAG_EMBEDDING_OFFLINE``
  开启时 ``local_files_only=True``，零网络 —— 与 faiss_store 同一约定，
  不引第二套模型管线）；
- **确定性退化**：模型加载/编码任何失败 → 异常上抛，``DynamicToolSurface``
  既有降级路径回落词法（系统永远正常；registry 仍是唯一工具事实源）；
- **索引是派生物**：文本语料全部来自 ToolDescriptor **已声明**字段
  （name/summary/description/tags/domains/examples —— 与 ToolLexicon 同
  源纪律，未声明不虚构）；索引键 = registry 指纹 + 全量 descriptor 指纹
  （复用 ToolRetrievalIndex._index_key 同一实现），registry 变更即重建；
- **有界**：工具数 ≤512（超出截断防膨胀）；向量经 numpy 归一化余弦
  （工具量级下无需 faiss，少一个重依赖面）；
- kill switch：``GIS_TOOL_SEMANTIC=0`` 或 ``TOOL_RETRIEVAL_SEMANTIC=off``
  → 语义路径整体缺席（lexical 逐位回退）。

测试：embed_fn 注入（假编码器，零模型依赖）；真模型集成测试在模型缓存
缺席时自跳过。
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.services.chat.tool_retrieval import RetrievalHit, ToolRetrievalIndex

logger = logging.getLogger(__name__)

#: 与 app/services/rag/faiss_store.py 同一模型约定（单一模型事实源）。
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

#: 语义分数 → surface 合成分的量纲（词法分约 4+ 起步；语义余弦 0..1 映射到
#: 可比区间）。常数确定性，评测钉门锁住排序效果。
_SEMANTIC_SCORE_SCALE = 6.0
#: 余弦相似度下限（低于此值的命中是噪声 —— 宁可不给，与词法 min_score 同理）。
_MIN_SIMILARITY = 0.15
_MAX_INDEXED_TOOLS = 512

#: embed_fn 类型：texts → 向量列表（注入点；生产默认 _default_embed）。
EmbedFn = Callable[[Sequence[str]], Sequence[Sequence[float]]]


def _default_embed(texts: Sequence[str]) -> List[List[float]]:
    """生产编码器（SentenceTransformer；RAG_EMBEDDING_OFFLINE 时零网络）。"""
    from sentence_transformers import SentenceTransformer

    offline = os.getenv("RAG_EMBEDDING_OFFLINE", "") not in ("", "0", "false", "False")
    model = _model_cache.get("model")
    if model is None:
        model = SentenceTransformer(
            EMBEDDING_MODEL_NAME, local_files_only=offline)
        _model_cache["model"] = model
    vectors = model.encode(list(texts), normalize_embeddings=True)
    return [list(map(float, v)) for v in vectors]


_model_cache: Dict[str, Any] = {}


def _tool_text(desc: Any) -> str:
    """工具的语义语料（全部来自 descriptor 已声明字段；截断有界）。"""
    parts = [
        desc.name.replace("_", " "),
        str(getattr(desc, "summary", "") or ""),
        str(getattr(desc, "description", "") or ""),
        " ".join(str(t) for t in (getattr(desc, "tags", None) or ())),
        " ".join(str(d) for d in (getattr(desc, "domains", None) or ())),
        " ".join(str(e) for e in (getattr(desc, "examples", None) or ())[:4]),
    ]
    return " ".join(p for p in parts if p)[:1200]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class ToolSemanticIndex:
    """按 registry 指纹缓存的语义索引（派生投影，查询期零重建）。

    ``embed_fn`` 缺省 = 生产编码器；测试注入假编码器（零模型依赖）。
    构建失败（模型不可用等）→ 状态保持未建，查询抛错由调用方降级词法。
    """

    def __init__(self, embed_fn: Optional[EmbedFn] = None) -> None:
        self._embed_fn: EmbedFn = embed_fn or _default_embed
        self._key: Optional[str] = None
        self._names: Tuple[str, ...] = ()
        self._vectors: Tuple[Tuple[float, ...], ...] = ()

    def build_if_stale(self, registry: Any) -> None:
        key = ToolRetrievalIndex._index_key(registry)
        if self._vectors and self._key == key:
            return
        names: List[str] = []
        texts: List[str] = []
        for name in registry.list_tools():
            try:
                desc = registry.descriptor(name)
            except KeyError:
                continue
            if not getattr(desc, "model_visible", True):
                continue  # hidden/planned 不进检索候选（与词法索引一致）
            names.append(name)
            texts.append(_tool_text(desc))
        names = names[:_MAX_INDEXED_TOOLS]
        texts = texts[:_MAX_INDEXED_TOOLS]
        vectors = [tuple(map(float, v)) for v in self._embed_fn(texts)]
        if len(vectors) != len(names):
            raise RuntimeError("semantic embed vector count mismatch")
        self._names = tuple(names)
        self._vectors = tuple(vectors)
        self._key = key

    def query(self, registry: Any, query: str, top_k: int) -> List[RetrievalHit]:
        # opt-in 说明：首轮 / registry 指纹变更重建时本调用同步阻塞（模型
        # encode 在查询线程内执行）—— 高频首轮场景由调用方预热 build_if_stale。
        self.build_if_stale(registry)
        if not self._names:
            return []
        qv = list(map(float, self._embed_fn([query[:400]])[0]))
        scored: List[Tuple[float, str]] = []
        for name, vec in zip(self._names, self._vectors):
            sim = _cosine(qv, vec)
            if sim >= _MIN_SIMILARITY:
                scored.append((sim, name))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [
            RetrievalHit(
                name=name,
                score=round(sim * _SEMANTIC_SCORE_SCALE, 4),
                matched=("semantic",),
            )
            for sim, name in scored[:max(1, top_k)]
        ]


#: 进程级默认索引（DynamicToolSurface 每次调用同一 registry 时命中缓存）。
_DEFAULT_INDEX: Optional[ToolSemanticIndex] = None


def _default_index() -> ToolSemanticIndex:
    global _DEFAULT_INDEX
    if _DEFAULT_INDEX is None:
        _DEFAULT_INDEX = ToolSemanticIndex()
    return _DEFAULT_INDEX


def retrieve_tools_semantic(
    registry: Any, query: str, top_k: int,
) -> List[RetrievalHit]:
    """``TOOL_RETRIEVAL_SEMANTIC`` hook 契约实现：``(registry, query, k)``。

    任何失败上抛 —— DynamicToolSurface 既有 try/except 降级词法。
    """
    return _default_index().query(registry, query, top_k)


__all__ = [
    "EMBEDDING_MODEL_NAME",
    "ToolSemanticIndex",
    "retrieve_tools_semantic",
]
