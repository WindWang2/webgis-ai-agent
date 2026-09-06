"""确定性工具检索（ADR-0101 Wave 3, §13）。

ToolCatalog 的 tier/关键词/sticky 机制是**域级**激活：命中才载入，宁严勿宽。
当用户意图落在关键词词表之外（如「泰森多边形」没进 statistics 词表时），
域不激活 → 工具不可见 → LLM 只能放弃或走 list_available_tools 两跳。

本模块提供**词法检索**补强（零网络依赖、零嵌入模型、纯确定性）：

    rank(query_terms, candidates, boosts) -> [(name, score, matched_terms)]

- 语料 = 工具名 + domains + tags + capability/algorithm id + 描述（全部来自
  ToolDescriptor —— 不引入第二真相）；
- 打分：名字精确/前缀 > tags/domains/capability 词命中 > 描述词命中；
  CJK 用 bigram 切词，ASCII 用词边界小写；
- tie-break 按工具名排序 —— 同输入必同输出；
- 索引按 registry 指纹缓存，注册表变化自动失效（§24/§43）；
- 检索结果只是**候补补充**（fill leftover budget），绝不挤掉 tier-1 /
  前门 / 域激活工具。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ASCII_WORD = re.compile(r"[a-zA-Z0-9_]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]")

# 权重表（有界、可解释）
_W_NAME_EXACT = 12.0
_W_NAME_PREFIX = 6.0
_W_NAME_SUBSTR = 4.0
_W_TAG = 5.0
_W_DOMAIN = 4.0
_W_CAPABILITY = 3.5
_W_DESC = 2.0
_W_BIGRAM = 1.0
_MATCH_CAP = 3  # 单词最多计 3 次命中（防长描述刷分）


def tokenize(text: str) -> Tuple[str, ...]:
    """确定性切词：ASCII 词（小写）+ CJK bigram（单字 CJK 保留）。"""
    if not text:
        return ()
    tokens: List[str] = []
    for m in _ASCII_WORD.finditer(text):
        tokens.append(m.group(0).lower())
    for ch in text:
        if _CJK.match(ch):
            tokens.append(ch)
    # CJK bigram
    cjk_runs: List[str] = []
    run: List[str] = []
    for ch in text:
        if _CJK.match(ch):
            run.append(ch)
        else:
            if run:
                cjk_runs.append("".join(run))
                run = []
    if run:
        cjk_runs.append("".join(run))
    for r in cjk_runs:
        if len(r) == 1:
            continue
        for i in range(len(r) - 1):
            tokens.append(r[i:i + 2])
    # 去重保序 + 丢弃超长噪声
    seen: Dict[str, None] = {}
    for t in tokens:
        if len(t) <= 32:
            seen.setdefault(t, None)
    return tuple(seen)


@dataclass(frozen=True)
class ToolLexicon:
    """单个工具的检索语料（由 ToolDescriptor 派生；查询期零重建 —— PERF R1）。"""

    name: str
    name_token_set: frozenset
    norm_name: str
    tags: Tuple[str, ...]
    domains: Tuple[str, ...]
    capabilities: Tuple[str, ...]
    desc_tokens: Tuple[str, ...]

    @classmethod
    def build(cls, descriptor) -> "ToolLexicon":
        hay = " ".join([
            descriptor.name.replace("_", " "),
            descriptor.name,
        ])
        cap_text = " ".join(descriptor.capabilities) + " " + " ".join(descriptor.algorithms)
        return cls(
            name=descriptor.name,
            name_token_set=frozenset(tokenize(hay)),
            norm_name=descriptor.name.lower(),
            tags=tuple(t.lower() for t in descriptor.tags),
            domains=tuple(d.lower() for d in descriptor.domains),
            capabilities=tuple(tokenize(cap_text)),
            desc_tokens=tokenize(
                f"{descriptor.summary} {descriptor.description}"
            ),
        )


def _count_hits(terms: Iterable[str], corpus: Iterable[str]) -> int:
    corpus_set = corpus if isinstance(corpus, (set, frozenset)) else set(corpus)
    hits = 0
    for t in terms:
        if t in corpus_set:
            hits += 1
    return hits


@dataclass(frozen=True)
class RetrievalHit:
    name: str
    score: float
    matched: Tuple[str, ...]

    def explain(self) -> str:
        return f"{self.name} score={self.score:.1f} matched={list(self.matched[:6])}"


class ToolRetrievalIndex:
    """按 registry 指纹缓存的词法索引。"""

    def __init__(self):
        self._lexicons: Optional[Tuple[ToolLexicon, ...]] = None
        self._fingerprint: Optional[str] = None

    def build_if_stale(self, registry) -> None:
        fp = registry.registry_fingerprint()
        if self._lexicons is not None and self._fingerprint == fp:
            return
        lexicons = []
        for name in registry.list_tools():
            try:
                desc = registry.descriptor(name)
            except KeyError:  # noqa: PERF203
                continue
            if not desc.model_visible:
                # hidden/planned 不进入检索候选（与 Surface 投影一致）
                continue
            lexicons.append(ToolLexicon.build(desc))
        self._lexicons = tuple(lexicons)
        self._fingerprint = fp

    def rank(
        self,
        registry,
        query: str,
        boosts: Optional[Dict[str, float]] = None,
        top_k: int = 8,
        min_score: float = 4.0,
    ) -> List[RetrievalHit]:
        """返回按分数降序（tie 按名升序）的检索命中。

        min_score 过滤噪音：低于阈值宁可不给（LLM 自己有两跳自救通道）。
        """
        self.build_if_stale(registry)
        assert self._lexicons is not None
        # review R1 INFO：查询长度钳制 —— 多 MB 用户消息不该在事件循环上
        # 做 O(terms × tools) 检索。
        terms = tokenize((query or "")[:2048])
        if not terms:
            return []
        boosts = boosts or {}
        hits: List[RetrievalHit] = []
        for lex in self._lexicons:
            score = 0.0
            matched: List[str] = []
            for t in terms:
                local = 0.0
                if t in lex.name_token_set:
                    local = max(local, _W_NAME_EXACT if len(t) > 3 else _W_NAME_PREFIX)
                elif len(t) > 3 and t in lex.norm_name:
                    local = max(local, _W_NAME_SUBSTR)
                if t in lex.tags:
                    local = max(local, _W_TAG)
                if t in lex.domains:
                    local = max(local, _W_DOMAIN)
                if t in lex.capabilities:
                    local = max(local, _W_CAPABILITY)
                if local == 0.0 and t in lex.desc_tokens:
                    local = _W_DESC
                if local > 0.0:
                    if matched.count(t) < _MATCH_CAP:
                        matched.append(t)
                    score += local
            if score <= 0.0:
                continue
            score += boosts.get(lex.name, 0.0)
            if score >= min_score:
                hits.append(RetrievalHit(name=lex.name, score=score, matched=tuple(matched)))
        hits.sort(key=lambda h: (-h.score, h.name))
        return hits[:top_k]


# 进程级默认索引（build_if_stale 按 registry 指纹自愈，无跨注册表污染）
_default_index = ToolRetrievalIndex()


def rank_tools(
    registry,
    query: str,
    boosts: Optional[Dict[str, float]] = None,
    top_k: int = 8,
    min_score: float = 4.0,
) -> List[RetrievalHit]:
    """模块级便捷入口（默认共享索引）。"""
    return _default_index.rank(registry, query, boosts=boosts, top_k=top_k, min_score=min_score)
