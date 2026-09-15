"""ArtifactLedger —— 专家交付物的进程内有界账本（ADR-0189 D3）。

Zero Big Data in Context 的取货位：MapSpec 载荷 / 审计单 dict 都不过
上下文，只以 ``ref:mapspec-*`` / ``ref:audit-*`` 提货券流通。账本有界
（FIFO 逐出），跨进程消费需换 ref 存储实现——取货接口不变。
"""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional

#: 账本容量上限（对抗回路单会话 ≤ 2*(compose+audit)+margin，64 足够宽）。
MAX_LEDGER_ENTRIES = 64


class ArtifactLedger:
    """线程安全的 ref→payload 有界账本。"""

    def __init__(self, *, max_entries: int = MAX_LEDGER_ENTRIES) -> None:
        self._max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()
        self._store: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._counter = 0

    # ── 发券 ────────────────────────────────────────────────

    def allocate_ref(self, prefix: str) -> str:
        """分配下一张提货券（``ref:mapspec-*`` / ``ref:audit-*``）。"""
        with self._lock:
            self._counter += 1
            return f"{prefix}-{self._counter:06d}"

    def put(self, ref_id: str, payload: Dict[str, Any]) -> None:
        """入账（同 ref 覆盖 = 原地修订，不占新槽位）。"""
        with self._lock:
            self._store[ref_id] = payload
            self._store.move_to_end(ref_id)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    # ── 取货 ────────────────────────────────────────────────

    def get(self, ref_id: str) -> Optional[Dict[str, Any]]:
        """取载荷；缺席返回 None（消费方 fail-closed，绝不虚构）。"""
        with self._lock:
            return self._store.get(ref_id)

    def contains(self, ref_id: str) -> bool:
        with self._lock:
            return ref_id in self._store

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


def payload_digest(payload: Any) -> str:
    """载荷内容摘要（sha256 前 12 位；代际比对用，非安全用途）。"""
    import json

    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
