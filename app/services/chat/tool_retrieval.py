"""确定性工具检索（ADR-0101 Wave 3, §13）。

ToolCatalog 的 tier/关键词/sticky 机制是**域级**激活：命中才载入，宁严勿宽。
当用户意图落在关键词词表之外（如「泰森多边形」没进 statistics 词表时），
域不激活 → 工具不可见 → LLM 只能放弃或走 list_available_tools 两跳。

本模块提供**词法检索**补强（零网络依赖、零嵌入模型、纯确定性）：

    rank(query_terms, candidates, boosts) -> [(name, score, matched_terms)]

- 语料 = 工具名 + domains + tags + capability/algorithm id + 描述（全部来自
  ToolDescriptor —— 不引入第二真相）；
- V4（ADR-0104 决策 5）语料增补：descriptor **已声明**的语义字段值
  （scale/latency/memory class、crs/unit semantics、side_effect、
  output/input 工件类型）+ examples / failure_modes 作正证据、
  anti_examples 作负证据 —— 未声明的字段贡献为零（绝不虚构）；
- 打分：名字精确/前缀 > tags/domains/capability 词命中 > 语义字段/示例 >
  描述词命中；CJK 用 bigram 切词，ASCII 用词边界小写；
- tie-break 按工具名排序 —— 同输入必同输出；
- 索引按 registry 指纹 + **全量 descriptor 指纹**缓存，注册表变化或
  纯描述/tags 编辑自动失效（V4 修复 audit gap #9：schema 指纹对
  description-only 编辑不敏感）；
- 检索结果只是**候补补充**（fill leftover budget），绝不挤掉 tier-1 /
  前门 / 域激活工具。

Kill switch：``GIS_TOOL_RETRIEVAL_V4=0`` 恢复 V3 行为（新增语料权重
全部停用 —— 词法打分数学与 V3 完全一致；索引失效键增强保持，因其
只影响重建时机，不影响同注册表内容下的打分结果）。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from app.tools.descriptor import manifest_fingerprint

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
_W_SEMANTIC = 2.5   # V4：已声明语义字段值命中（scale/crs/unit/side_effect/…）
_W_DESC = 2.0
_W_EXAMPLE = 2.0    # V4：examples 语料命中（典型正确调用意图）
_W_FAILURE = 1.0    # V4：failure_modes 词表命中
_W_ANTI = 1.5       # V4：anti_examples 负证据（每命中扣减，总量有界）
_ANTI_CAP = 3.0
_MATCH_CAP = 3  # 单词最多计 3 次命中（防长描述刷分）
_ANTI_MATCH_CAP = 4  # 负证据匹配记录上限（可解释面有界）

# ---------------------------------------------------------------------------
# V6（ADR-0119 D1）打分判别力修复：单字 CJK token 命中是口语句的主要
# 噪声源（「给/图/层/生/成」几乎命中一切描述 → 16+ 分噪声地板，把真
# 区分信号淹没）。纪律：
# - 封闭停用字表 → 零权重（只收功能字，绝不收领域字如 河/桥/山）；
# - 其余单字 token 命中按 _SINGLE_CHAR_SCALE 折算（保召回、降话语权）；
# - anti 负证据只认多字 token（单字负证据纯噪声）。
# V3/V4-off 与 V4-on 走同一打分循环 → 「V3 数学 == V4 关闭」契约不变。
# ---------------------------------------------------------------------------
_SINGLE_CHAR_SCALE = 0.25
_CJK_STOPCHARS = frozenset(
    "的了在是和与或把给个这那有也就都被对为不没很之等每们吧呢啊嘛呀"
    "又再才只更最太非常想看下上中里外前后左右上下说请问帮我想需要"
)


def v4_retrieval_enabled() -> bool:
    """Kill switch：``GIS_TOOL_RETRIEVAL_V4=0`` 精确恢复 V3 行为（默认开）。

    每次调用读取环境变量（与 GIS_WORKFLOW_INSTANCE 同款先例），测试可
    monkeypatch 环境后即时生效，无需重载模块。
    """
    return os.getenv("GIS_TOOL_RETRIEVAL_V4", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


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
    """单个工具的检索语料（由 ToolDescriptor 派生；查询期零重建 —— PERF R1）。

    V4 增补字段全部来自 descriptor **已声明**的值；未声明 → 空元组，
    对打分贡献为零（不虚构、不猜测）。
    """

    name: str
    name_token_set: frozenset
    norm_name: str
    tags: Tuple[str, ...]
    domains: Tuple[str, ...]
    capabilities: Tuple[str, ...]
    desc_tokens: Tuple[str, ...]
    # --- V4（ADR-0104 决策 5）：声明字段的检索语料增补 ---
    semantic_tags: Tuple[str, ...] = ()   # scale/latency/memory/crs/unit/side_effect/output/input 声明值
    example_tokens: Tuple[str, ...] = ()  # examples（典型正确调用意图）
    anti_tokens: Tuple[str, ...] = ()     # anti_examples（negative retrieval 证据）
    failure_tokens: Tuple[str, ...] = ()  # failure_modes 词表

    @classmethod
    def build(cls, descriptor) -> "ToolLexicon":
        hay = " ".join([
            descriptor.name.replace("_", " "),
            descriptor.name,
        ])
        cap_text = " ".join(descriptor.capabilities) + " " + " ".join(descriptor.algorithms)
        # V4：已声明语义字段值（有界拼装；unknown/None/空 一律不计入）
        sem_parts: List[str] = []
        for v in (descriptor.scale_class, descriptor.latency_class, descriptor.memory_class):
            if v and v != "unknown":
                sem_parts.append(str(v))
        for v in (descriptor.crs_semantics, descriptor.unit_semantics,
                  descriptor.output_semantic_type):
            if v:
                sem_parts.append(str(v))
        se = getattr(descriptor.side_effect, "value", descriptor.side_effect)
        if se and se != "unclassified":
            sem_parts.append(str(se))
        sem_parts.extend(str(a) for a in descriptor.input_artifacts)
        sem_parts.extend(str(r) for r in descriptor.accepts_ref_types)
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
            semantic_tags=tuple(tokenize(" ".join(sem_parts))),
            example_tokens=tuple(tokenize(" ".join(descriptor.examples))),
            anti_tokens=tuple(tokenize(" ".join(descriptor.anti_examples))),
            failure_tokens=tuple(tokenize(" ".join(descriptor.failure_modes))),
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
    anti_matched: Tuple[str, ...] = ()   # V4：命中的 anti_example 词（负证据留痕）

    def explain(self) -> str:
        return f"{self.name} score={self.score:.1f} matched={list(self.matched[:6])}"


class ToolRetrievalIndex:
    """按 registry 指纹 + 全量 descriptor 指纹缓存的词法索引。

    V4（audit gap #9 修复）：``registry_fingerprint`` 只覆盖
    (name, schema_fingerprint)，对 description/tags/summary-only 编辑不敏感。
    索引键因此叠加全量 ``descriptor_fingerprint`` 清单指纹 —— 纯元数据编辑
    必然重建词料；registry 级指纹仍是第一道 O(1) 快路径。
    """

    def __init__(self):
        self._lexicons: Optional[Tuple[ToolLexicon, ...]] = None
        self._fingerprint: Optional[str] = None

    @staticmethod
    def _index_key(registry) -> str:
        rf = registry.registry_fingerprint()
        try:
            fps = registry.fingerprints()
        except Exception:  # noqa: BLE001 — 指纹面故障退回 registry 级键（绝不阻断检索）
            return rf
        dfp = manifest_fingerprint([(name, fp[1]) for name, fp in fps.items()])
        return f"{rf}:{dfp}"

    def build_if_stale(self, registry) -> None:
        fp = self._index_key(registry)
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
        enriched: Optional[bool] = None,
    ) -> List[RetrievalHit]:
        """返回按分数降序（tie 按名升序）的检索命中。

        min_score 过滤噪音：低于阈值宁可不给（LLM 自己有两跳自救通道）。
        V4 增补语料（semantic/examples/failure 正证据 + anti 负证据）仅在
        V4 检索生效时参与打分；关闭时打分数学与 V3 逐位一致。
        ``enriched``：None = 跟随全局 kill switch；True/False = 调用方显式
        指定（V3 selector 用它实现「无 V4 上下文证据 → 精确 V3 打分」）。
        """
        self.build_if_stale(registry)
        assert self._lexicons is not None
        # review R1 INFO：查询长度钳制 —— 多 MB 用户消息不该在事件循环上
        # 做 O(terms × tools) 检索。
        terms = tokenize((query or "")[:2048])
        if not terms:
            return []
        boosts = boosts or {}
        v4 = v4_retrieval_enabled() if enriched is None else bool(enriched)
        hits: List[RetrievalHit] = []
        for lex in self._lexicons:
            score = 0.0
            anti_penalty = 0.0
            matched: List[str] = []
            anti_matched: List[str] = []
            for t in terms:
                # V6 判别力修复：停用单字零权重、其余单字降权（见权重表注）
                if len(t) == 1:
                    if t in _CJK_STOPCHARS:
                        continue
                    w_scale = _SINGLE_CHAR_SCALE
                else:
                    w_scale = 1.0
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
                if v4 and local == 0.0:
                    # V4 正证据：语义字段值 > examples > failure_modes（与
                    # V3 的 desc 兜底同一纪律 —— 单词只计一档，不叠加刷分）
                    if t in lex.semantic_tags:
                        local = _W_SEMANTIC
                    elif t in lex.example_tokens:
                        local = _W_EXAMPLE
                    elif t in lex.failure_tokens:
                        local = _W_FAILURE
                if local == 0.0 and t in lex.desc_tokens:
                    local = _W_DESC
                if local > 0.0:
                    if matched.count(t) < _MATCH_CAP:
                        matched.append(t)
                    score += local * w_scale
                if v4 and len(t) > 1 and t in lex.anti_tokens and t not in anti_matched:
                    # V4 负证据：anti_example 命中有界扣减（永只降序，不剔除）
                    if len(anti_matched) < _ANTI_MATCH_CAP:
                        anti_matched.append(t)
                    anti_penalty = max(-_ANTI_CAP, anti_penalty - _W_ANTI)
            if score <= 0.0:
                continue
            score += boosts.get(lex.name, 0.0) + anti_penalty
            if score >= min_score:
                hits.append(RetrievalHit(
                    name=lex.name, score=score, matched=tuple(matched),
                    anti_matched=tuple(anti_matched),
                ))
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
    enriched: Optional[bool] = None,
) -> List[RetrievalHit]:
    """模块级便捷入口（默认共享索引）。"""
    return _default_index.rank(
        registry, query, boosts=boosts, top_k=top_k, min_score=min_score,
        enriched=enriched,
    )
