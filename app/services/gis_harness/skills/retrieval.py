"""retrieve_skills —— 技能检索（ADR-0182 §2.6；goal S18）。

确定性 BM25-lite：对 name/description/when_to_use/intent_patterns 做
词频打分 + 结构化过滤（domain/pack/capability 前缀），**不重新开发**
Data Supply 的检索引擎、不引入 embeddings 依赖（若仓库未来有通用
semantic retrieval 可在其上复用，本模块接口不变）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from app.services.gis_harness.skills.contract import SkillContract

#: zh 2-gram + en 词干的轻量索引（不引入分词依赖）。
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    """en 小写词 + zh 二元组（确定性；无外部分词器）。"""
    tokens: List[str] = []
    for m in _TOKEN_RE.finditer(text.lower()):
        tokens.append(m.group(0))
    zh = re.sub(r"[^\u4e00-\u9fff]+", "", text)
    for i in range(len(zh) - 1):
        tokens.append(zh[i:i + 2])
    return tokens


class SkillRetriever:
    """BM25-lite 检索器（构建一次，查询纯内存）。"""

    def __init__(self, skills: List[SkillContract]) -> None:
        self._skills = {s.id: s for s in skills}
        self._docs: Dict[str, List[str]] = {}
        for sid, s in self._skills.items():
            text = " ".join([
                s.id.replace("_", " "), s.name, s.description, s.when_to_use,
                s.when_not_to_use, " ".join(s.intent_patterns),
            ])
            self._docs[sid] = _tokens(text)
        # IDF（平滑）
        df: Dict[str, int] = {}
        for toks in self._docs.values():
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        n = max(len(self._skills), 1)
        import math
        self._idf: Dict[str, float] = {
            t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 8,
        domain: str = "",
        pack: str = "",
    ) -> List[Tuple[str, float]]:
        """query → [(skill_id, score)] 降序；同分按 id。纯确定性。"""
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        scores: Dict[str, float] = {}
        for sid, toks in self._docs.items():
            s = self._skills[sid]
            if domain and s.domain != domain:
                continue
            if pack and s.pack != pack:
                continue
            tf: Dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            score = 0.0
            for qt in q_tokens:
                count = tf.get(qt, 0)
                if count:
                    score += self._idf.get(qt, 1.0) * (count * 2.0) / (count + 1.2)
            if score > 0:
                scores[sid] = score
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:limit]


def retrieve_skills(library, query: str, **filters) -> List[Tuple[str, float]]:
    """门面函数：SkillLibrary → 检索结果。"""
    return SkillRetriever(list(library.skills)).retrieve(query, **filters)


__all__ = [
    "SkillRetriever",
    "retrieve_skills",
]
