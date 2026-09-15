"""空间记忆关联热索引（ADR-0190）：倒排 + 网格空间 + BM25 + Half-Life 衰减。

职责：把 ``gis_spatial_memories`` 的 active 行加载为进程内条目，提供
**亚毫秒级**的联想召回（查询词元 BM25 语义 + bbox 地理邻近 + 时间半衰期
+ kind/scope 先验），供 :mod:`.proactive_retriever` 组装事实卡片。

纪律（与 ADR-0183 R4 同源并加固）：
- **租户物理分区**：顶层按 ``org_id`` 分桶——跨租户查询在数据结构层面
  不可表达，而不是靠谓词过滤兜底；空 org 直接拒绝（fail-closed）；
- **确定性**：同索引状态同输入恒同序（``-score, scope, subject,
  memory_id`` 字典序），每条命中带可读理由（不透明排序不可接受）；
- **有界**：每 org 条目上限（默认 512，LRU 逐出），与 store 的作用域
  预算纪律同源；无界增长即漏洞；
- **零 SQL**：本模块不触碰数据库——加载/回写由 retriever 与 consolidator
  在非热路径完成。
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.gis_memory.contract import (
    SCOPE_PROJECT,
    SCOPE_SESSION,
    SCOPE_USER,
)

logger = logging.getLogger(__name__)

#: 打分权重（ADR-0190 §1.2）：语义 > 地理 > 时间 > 先验。
W_SEM = 0.45
W_GEO = 0.25
W_DECAY = 0.18
W_PRIOR = 0.12

#: Half-Life Decay 默认半衰期（天）。显式偏好 pinned → 半衰期无穷大。
DEFAULT_HALF_LIFE_DAYS = 14.0

_BM25_K1 = 1.5
_BM25_B = 0.75

#: 每 org 索引条目上限（LRU 逐出）。store 预算下单 org 一次同步的最坏
#: 载入 ≈ 680 行（session 80 + project 400 + user 200），上限须有余量。
MAX_ENTRIES_PER_ORG = 1024

#: 检索 top-k 硬上限。
HARD_LIMIT_CAP = 32

#: 空间网格单元格边长（度）。大 bbox（覆盖 > 64 格）进 wide 列表全查。
_GRID_CELL_DEG = 1.0
_GRID_WIDE_CELLS = 64

_SCOPE_PRIORITY = {SCOPE_SESSION: 1.0, SCOPE_PROJECT: 0.7, SCOPE_USER: 0.55}

_ASCII_WORD_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _is_cjk(ch: str) -> bool:
    return bool(_CJK_RE.match(ch))


def tokenize(text: str) -> Tuple[str, ...]:
    """索引与查询共用的词元器：CJK bigram + ASCII 小写词。

    「不对称即 bug」——索引侧与查询侧必须走同一函数。数字串任意长度
    保留（地块编号 1 vs 488 的判别信号就在数字上），纯字母仍需 ≥2。
    """
    if not text:
        return ()
    lowered = str(text).lower()
    tokens: List[str] = []
    for match in _ASCII_WORD_RE.finditer(lowered):
        word = match.group(0)
        if len(word) >= 2 or word.isdigit():
            tokens.append(word)
    i = 0
    length = len(lowered)
    while i < length:
        if _is_cjk(lowered[i]):
            j = i
            while j < length and _is_cjk(lowered[j]):
                j += 1
            run = lowered[i:j]
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[k:k + 2] for k in range(len(run) - 1))
            i = j
        else:
            i += 1
    return tuple(tokens)


def half_life_decay(
    age_days: float,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    *,
    pinned: bool = False,
) -> float:
    """Half-Life Decay：``2^(-age/half_life)``；显式偏好永不忘（恒 1.0）。"""
    if pinned:
        return 1.0
    hl = float(half_life_days or DEFAULT_HALF_LIFE_DAYS)
    if hl <= 0:
        hl = DEFAULT_HALF_LIFE_DAYS
    age = max(float(age_days), 0.0)
    return float(math.pow(2.0, -age / hl))


def bbox_proximity(
    query_bbox: Optional[Sequence[float]],
    entry_bbox: Optional[Sequence[float]],
) -> float:
    """地理邻近度 ∈ [0,1]：交叠归一（inter/query_area）；无交叠走中心距
    衰减；任一缺 bbox → 0.5（无空间信息不奖不罚）。"""
    if not query_bbox or not entry_bbox:
        return 0.5
    try:
        qx0, qy0, qx1, qy1 = (float(v) for v in query_bbox[:4])
        ex0, ey0, ex1, ey1 = (float(v) for v in entry_bbox[:4])
    except (TypeError, ValueError):
        return 0.5
    inter_w = min(qx1, ex1) - max(qx0, ex0)
    inter_h = min(qy1, ey1) - max(qy0, ey0)
    if inter_w > 0 and inter_h > 0:
        q_area = max((qx1 - qx0) * (qy1 - qy0), 1e-12)
        return float(min(1.0, (inter_w * inter_h) / q_area))
    qcx, qcy = (qx0 + qx1) / 2.0, (qy0 + qy1) / 2.0
    ecx, ecy = (ex0 + ex1) / 2.0, (ey0 + ey1) / 2.0
    dist = math.hypot(qcx - ecx, qcy - ecy)
    return float(min(0.5, 1.0 / (1.0 + max(dist, 0.0))))


@dataclass
class MemoryIndexEntry:
    """热索引条目（可变面：hit_count / last_access 由索引维护）。"""

    memory_id: str
    org_id: str
    scope: str
    scope_id: str
    kind: str
    subject: str
    name: str
    tokens: Tuple[str, ...]
    tf: Dict[str, int]
    dl: int
    bbox: Optional[Tuple[float, float, float, float]]
    confidence: float
    pinned: bool
    half_life_days: float
    kind_weight: float
    scope_priority: float
    last_validated_ts: float
    value: Dict = field(default_factory=dict)
    hit_count: int = 0
    last_access: float = 0.0


@dataclass(frozen=True)
class IndexHit:
    """一次索引命中：条目 + 分量分数 + 可读理由。"""

    entry: MemoryIndexEntry
    score: float
    sem: float
    geo: float
    decay: float
    reasons: Tuple[str, ...] = ()


class _OrgIndex:
    """单租户索引：倒排 postings + 网格空间 + BM25 语料统计。"""

    def __init__(self) -> None:
        self.entries: "OrderedDict[str, MemoryIndexEntry]" = OrderedDict()
        self.postings: Dict[str, set] = {}
        self.grid: Dict[Tuple[int, int], set] = {}
        self.wide: set = set()
        self.total_tokens = 0

    def _detach(self, entry: MemoryIndexEntry) -> None:
        """把条目从倒排/网格/语料统计中摘除（不动 entries 字典）。"""
        self.total_tokens -= entry.dl
        for token in set(entry.tokens):
            bucket = self.postings.get(token)
            if bucket is not None:
                bucket.discard(entry.memory_id)
                if not bucket:
                    self.postings.pop(token, None)
        if entry.bbox is not None:
            cells = _bbox_cells(entry.bbox)
            if cells is None:
                self.wide.discard(entry.memory_id)
            else:
                for cell in cells:
                    bucket = self.grid.get(cell)
                    if bucket is not None:
                        bucket.discard(entry.memory_id)
                        if not bucket:
                            self.grid.pop(cell, None)

    def remove(self, memory_id: str) -> None:
        entry = self.entries.pop(memory_id, None)
        if entry is not None:
            self._detach(entry)

    def upsert(self, entry: MemoryIndexEntry) -> None:
        self.remove(entry.memory_id)
        self.entries[entry.memory_id] = entry
        self.total_tokens += entry.dl
        for token in set(entry.tokens):
            self.postings.setdefault(token, set()).add(entry.memory_id)
        if entry.bbox is not None:
            cells = _bbox_cells(entry.bbox)
            if cells is None:
                self.wide.add(entry.memory_id)
            else:
                for cell in cells:
                    self.grid.setdefault(cell, set()).add(entry.memory_id)
        while len(self.entries) > MAX_ENTRIES_PER_ORG:
            _stale_id, stale_entry = self.entries.popitem(last=False)
            self._detach(stale_entry)

    def spatial_candidates(
        self, bbox: Sequence[float]
    ) -> set:
        cells = _query_cells(bbox)
        ids: set = set(self.wide)
        for cell in cells:
            ids |= self.grid.get(cell, set())
        return ids

    def idf(self, token: str) -> float:
        n = len(self.entries)
        df = len(self.postings.get(token, ()))
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def avgdl(self) -> float:
        n = len(self.entries)
        return (self.total_tokens / n) if n else 0.0


def _bbox_cells(
    bbox: Sequence[float],
) -> Optional[List[Tuple[int, int]]]:
    """bbox 覆盖的网格单元；过大（> ``_GRID_WIDE_CELLS`` 格）返回 None
    （调用方收进 wide 全查列表，避免巨型 AOI 撑爆网格）。"""
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return []
    cx0, cx1 = int(math.floor(x0 / _GRID_CELL_DEG)), int(
        math.floor(x1 / _GRID_CELL_DEG))
    cy0, cy1 = int(math.floor(y0 / _GRID_CELL_DEG)), int(
        math.floor(y1 / _GRID_CELL_DEG))
    nx, ny = cx1 - cx0 + 1, cy1 - cy0 + 1
    if nx * ny > _GRID_WIDE_CELLS:
        return None
    return [(cx, cy) for cx in range(cx0, cx1 + 1)
            for cy in range(cy0, cy1 + 1)]


def _query_cells(
    bbox: Sequence[float],
) -> List[Tuple[int, int]]:
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return []
    cx0 = int(math.floor(min(x0, x1) / _GRID_CELL_DEG)) - 1
    cx1 = int(math.floor(max(x0, x1) / _GRID_CELL_DEG)) + 1
    cy0 = int(math.floor(min(y0, y1) / _GRID_CELL_DEG)) - 1
    cy1 = int(math.floor(max(y0, y1) / _GRID_CELL_DEG)) + 1
    return [(cx, cy) for cx in range(cx0, cx1 + 1)
            for cy in range(cy0, cy1 + 1)]


def _epoch(ts: float) -> float:
    try:
        return float(ts)
    except (TypeError, ValueError):
        return time.time()


def parse_validated_ts(iso_text: Optional[str]) -> float:
    """ISO 时间 → epoch 秒（naive 按 UTC）；解析失败回退当前。"""
    if not iso_text:
        return time.time()
    try:
        dt = datetime.fromisoformat(str(iso_text))
    except ValueError:
        return time.time()
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)
    return dt.timestamp()


class AssociativeIndex:
    """多租户关联热索引（线程安全）：org → :class:`_OrgIndex` 物理分区。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._orgs: Dict[str, _OrgIndex] = {}

    # ── 写入面 ────────────────────────────────────────────────────────

    def upsert(self, entry: MemoryIndexEntry) -> None:
        if not entry or not entry.org_id or not entry.memory_id:
            return
        with self._lock:
            org = self._orgs.setdefault(entry.org_id, _OrgIndex())
            entry.last_access = time.time()
            org.upsert(entry)

    def remove(self, org_id: str, memory_id: str) -> None:
        with self._lock:
            org = self._orgs.get(org_id)
            if org is not None:
                org.remove(memory_id)

    def clear(self, org_id: Optional[str] = None) -> None:
        with self._lock:
            if org_id is None:
                self._orgs.clear()
            else:
                self._orgs.pop(org_id, None)

    # ── 查询面 ────────────────────────────────────────────────────────

    def get(self, org_id: str, memory_id: str) -> Optional[MemoryIndexEntry]:
        if not org_id:
            return None
        with self._lock:
            org = self._orgs.get(org_id)
            return org.entries.get(memory_id) if org else None

    def touch(self, org_id: str, memory_ids: Iterable[str]) -> None:
        """命中计数回写（consolidator 晋升判定的输入；进程内，零 SQL）。"""
        if not org_id:
            return
        now = time.time()
        with self._lock:
            org = self._orgs.get(org_id)
            if org is None:
                return
            for mid in memory_ids:
                entry = org.entries.get(mid)
                if entry is not None:
                    entry.hit_count += 1
                    entry.last_access = now

    def search(
        self,
        org_id: str,
        query_text: str,
        *,
        bbox: Optional[Sequence[float]] = None,
        kinds: Sequence[str] = (),
        limit: int = 8,
        now: Optional[float] = None,
    ) -> List[IndexHit]:
        """联想召回：BM25 语义 + 地理邻近 + 半衰期 + 先验，有界 top-k。"""
        if not org_id:
            return []
        now_ts = _epoch(now) if now is not None else time.time()
        with self._lock:
            org = self._orgs.get(org_id)
            if org is None or not org.entries:
                return []
            query_tokens = tokenize(query_text)
            unique_tokens = sorted(set(query_tokens))
            candidates: set = set()
            if unique_tokens:
                for token in unique_tokens:
                    candidates |= org.postings.get(token, set())
            if bbox is not None:
                candidates |= org.spatial_candidates(bbox)
            if not candidates:
                return []
            avgdl = org.avgdl() or 1.0
            # 热循环外提：IDF 每查询算一次（1000 候选时省 ~3k 次 dict/对数）。
            # 理由字符串只在排序后对 top-k 构建（per-candidate 只留算术）。
            idfs = {t: org.idf(t) for t in unique_tokens}
            scored: List[Tuple[float, float, float, float, float,
                               MemoryIndexEntry]] = []
            for mid in candidates:
                entry = org.entries.get(mid)
                if entry is None:
                    continue
                if kinds and entry.kind not in kinds:
                    continue
                total = 0.0
                for token in unique_tokens:
                    tf = entry.tf.get(token)
                    if not tf:
                        continue
                    norm = tf * (_BM25_K1 + 1.0) / (
                        tf + _BM25_K1
                        * (1.0 - _BM25_B + _BM25_B * entry.dl / avgdl)
                    )
                    total += idfs[token] * norm
                sem = (total / (total + 1.0)) if total > 0.0 else 0.0
                geo = bbox_proximity(bbox, entry.bbox)
                age_days = max((now_ts - entry.last_validated_ts) / 86400.0,
                               0.0)
                decay = half_life_decay(
                    age_days, entry.half_life_days, pinned=entry.pinned)
                prior = (
                    entry.kind_weight / 1.2
                ) * entry.confidence * entry.scope_priority
                score = W_SEM * sem + W_GEO * geo + W_DECAY * decay + (
                    W_PRIOR * prior)
                if score > 1e-6:
                    scored.append(
                        (score, sem, geo, decay, prior, entry))
            scored.sort(key=lambda t: (
                -t[0],
                t[5].scope,
                t[5].subject,
                t[5].memory_id,
            ))
            hits: List[IndexHit] = []
            for score, sem, geo, decay, prior, entry in scored[
                    : max(1, min(int(limit or 8), HARD_LIMIT_CAP))]:
                hits.append(IndexHit(
                    entry=entry, score=score, sem=sem, geo=geo,
                    decay=decay,
                    reasons=_reasons_for(
                        entry, unique_tokens, idfs, avgdl,
                        geo, decay, prior, bbox),
                ))
            return hits

    def recent_entries(
        self,
        org_id: str,
        *,
        kinds: Sequence[str] = (),
        limit: int = 4,
        now: Optional[float] = None,
    ) -> List[MemoryIndexEntry]:
        """某租户下按（半衰期+先验）最近的条目——模糊指代兜底通道。

        与 :meth:`search` 互补：回指/惯用简称天然缺词元重叠，本方法给
        retriever 提供有界（≤16）的候选入口，唯一性余量仍由调用方把关。
        """
        if not org_id:
            return []
        now_ts = _epoch(now) if now is not None else time.time()
        with self._lock:
            org = self._orgs.get(org_id)
            if org is None or not org.entries:
                return []
            entries = [
                e for e in org.entries.values()
                if not kinds or e.kind in kinds
            ]

            def _rank(e: MemoryIndexEntry) -> Tuple[float, str, str, str]:
                age_days = max((now_ts - e.last_validated_ts) / 86400.0, 0.0)
                decay = half_life_decay(
                    age_days, e.half_life_days, pinned=e.pinned)
                prior = (e.kind_weight / 1.2) * e.confidence * (
                    e.scope_priority)
                return (-(decay + prior), e.scope, e.subject, e.memory_id)

            entries.sort(key=_rank)
            return entries[: max(1, min(int(limit or 4), 16))]

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "orgs": len(self._orgs),
                "entries": sum(len(o.entries) for o in self._orgs.values()),
            }


def _reasons_for(
    entry: MemoryIndexEntry,
    unique_tokens: List[str],
    idfs: Dict[str, float],
    avgdl: float,
    geo: float,
    decay: float,
    prior: float,
    bbox: Optional[Sequence[float]],
) -> Tuple[str, ...]:
    """为 top-k 命中构建可读理由（排序后调用，不进热循环）。"""
    contributions: List[Tuple[float, str]] = []
    for token in unique_tokens:
        tf = entry.tf.get(token)
        if not tf:
            continue
        norm = tf * (_BM25_K1 + 1.0) / (
            tf + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * entry.dl / avgdl)
        )
        contributions.append((idfs[token] * norm, token))
    contributions.sort(reverse=True)
    reasons = [f"bm25:{token}" for _score, token in contributions[:2]]
    reasons.append(f"geo:{geo:.2f}")
    if bbox is not None and entry.bbox is not None:
        reasons.append("bbox_overlap" if geo >= 0.999 else "bbox_near")
    reasons.append(f"decay:{decay:.2f}")
    reasons.append(f"prior:{prior:.2f}")
    if entry.pinned:
        reasons.append("pinned_preference")
    return tuple(reasons[:6])


__all__ = [
    "W_SEM",
    "W_GEO",
    "W_DECAY",
    "W_PRIOR",
    "DEFAULT_HALF_LIFE_DAYS",
    "MAX_ENTRIES_PER_ORG",
    "HARD_LIMIT_CAP",
    "MemoryIndexEntry",
    "IndexHit",
    "AssociativeIndex",
    "tokenize",
    "half_life_decay",
    "bbox_proximity",
    "parse_validated_ts",
]
