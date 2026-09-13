"""Deterministic keyword retrieval: CJK-aware tokenizer + BM25 (DS2, ADR-0172).

The keyword index is the mandatory base of the hybrid retrieval — it is
deterministic, dependency-free and works offline (task book: 禁止只做向量).

Tokenizer: lowercase ASCII words + CJK character bigrams (no jieba dep);
queries are tokenized identically. BM25 (k1=1.5, b=0.75) over the cards'
``searchable_text()`` with a small field boost: keyword-tag hits weigh more
than body hits because tags are curated.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Tuple

_TOKEN_ASCII = re.compile(r"[a-z0-9][a-z0-9._-]*")
_TOKEN_CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> List[str]:
    """ASCII words + CJK bigrams (+ unigram fallback for single chars)."""
    lowered = (text or "").lower()
    tokens = _TOKEN_ASCII.findall(lowered)
    # build CJK runs first: findall gives chars; group them
    runs: List[str] = []
    cur: List[str] = []
    for ch in lowered:
        if _TOKEN_CJK.match(ch):
            cur.append(ch)
        else:
            if cur:
                runs.append("".join(cur))
                cur = []
    if cur:
        runs.append("".join(cur))
    for run in runs:
        if len(run) == 1:
            tokens.append(run)
            continue
        for i in range(len(run) - 1):
            tokens.append(run[i: i + 2])
    return tokens


class BM25Index:
    """In-memory BM25 over pre-built card texts. Deterministic; rebuilt from
    scratch on card refresh (cards are cheap to rebuild)."""

    K1 = 1.5
    B = 0.75
    #: hits on curated keyword tags count extra (tags are declarations).
    TAG_BOOST = 1.6

    def __init__(self, cards: List[Tuple[str, str, List[str]]]):
        """cards: [(card_id, searchable_text, curated_tags)]"""
        self.ids: List[str] = []
        self._tag_sets: Dict[str, set] = {}
        self._tf: List[Dict[str, float]] = []
        self._doc_len: List[float] = []
        df: Dict[str, int] = {}
        for card_id, text, tags in cards:
            self.ids.append(card_id)
            tokens = tokenize(text)
            tf: Dict[str, float] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0.0) + 1.0
            tag_tokens = set()
            for tag in tags or []:
                for t in tokenize(tag):
                    tag_tokens.add(t)
            self._tag_sets[card_id] = tag_tokens
            for t in tag_tokens:
                if t in tf:
                    tf[t] *= self.TAG_BOOST
            self._tf.append(tf)
            self._doc_len.append(sum(tf.values()) or 1.0)
            for t in tf:
                df[t] = df.get(t, 0) + 1
        self._df = df
        self._n = len(self.ids)
        self._avg_len = (sum(self._doc_len) / self._n) if self._n else 0.0

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """Deterministic BM25 scoring; ties broken by card_id for stability."""
        if self._n == 0:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores: Dict[int, float] = {}
        for doc_idx in range(self._n):
            tf = self._tf[doc_idx]
            score = 0.0
            for t in q_tokens:
                if t not in tf:
                    continue
                idf = math.log(1.0 + (self._n - self._df.get(t, 0) + 0.5) / (self._df.get(t, 0) + 0.5))
                freq = tf[t]
                denom = freq + self.K1 * (1.0 - self.B + self.B * self._doc_len[doc_idx] / (self._avg_len or 1.0))
                score += idf * (freq * (self.K1 + 1.0)) / denom
            if score > 0.0:
                scores[doc_idx] = score
        ranked = sorted(
            ((self.ids[i], s) for i, s in scores.items()),
            key=lambda kv: (-kv[1], kv[0]),
        )
        return ranked[:top_k]

    def __len__(self) -> int:
        return self._n


__all__ = ["BM25Index", "tokenize"]
