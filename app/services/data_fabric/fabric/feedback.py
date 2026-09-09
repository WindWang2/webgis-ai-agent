"""分布式执行反馈（ADR-0119 W11，Epic 03 Must-have G）。

捕获 → 存储 → 回路：
- **捕获**：PhysicalExecutor 的 per-source 观测（行数/页数/时延/错误类/
  限流）以 ``ExecutionFeedback`` 形态提交；R-C3 守卫 —— **只有无过滤扫描**
  的观测行数才允许回写 SourceFacts（``observe_unfiltered_count`` 契约，
  过滤后的命中数绝不冒充数据集总量）；过滤观测只进修正因子。
- **存储**：进程内有界（keys × samples 双界）+ ``data_fabric_federated_
  feedback`` 表（advisory fail-open，append-only + prune）。
- **衰减**：指数半衰期加权（半衰 30min）中位数修正因子 —— 与既有
  ``PlannerFeedbackStore`` 的 winsorized 中位数同文化；只学 outcome=ok。
- **无 secret**：feedback 只含 fingerprint/scope/计数，绝不含凭证。
"""
from __future__ import annotations

import logging
import math
import statistics
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: 修正因子夹界（与 query/feedback.py 同方向：修正永不越一个数量级）。
_FACTOR_CLAMP = (0.1, 10.0)
#: 衰减半衰期（秒）。
HALF_LIFE_S = 1800.0


class SourceObservation(BaseModel):
    """单源一次执行内的观测（全部可缺省 —— 诚实未知）。"""

    source_id: str
    dataset_fingerprint: Optional[str] = None
    estimated_rows: Optional[int] = None
    actual_rows: Optional[int] = None
    bytes_fetched: Optional[int] = None
    latency_ms: Optional[float] = None
    pages: Optional[int] = None
    error: Optional[str] = None  # typed error code（非消息）
    throttled: bool = False
    #: 该扫描是否无过滤（where/bbox 全无）—— R-C3 回写守卫依据。
    unfiltered: bool = False


class ExecutionFeedback(BaseModel):
    """一次联邦链执行的反馈载荷（绝不含 secret）。"""

    plan_hash: str
    scope_key: str
    engine: str = "v6"
    outcome: str = "ok"  # ok | error | cancelled
    per_source: List[SourceObservation] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class FabricFeedbackStore:
    """有界反馈存储：进程 LRU + durable 旁路（fail-open）。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_keys: int = 512,
        max_obs_per_key: int = 16,
        durable_max_rows: Optional[int] = None,
    ):
        self.enabled = enabled
        self._max_keys = int(max_keys)
        self._max_obs = int(max_obs_per_key)
        # key -> OrderedDict[ts -> ratio]（仅 outcome=ok 的 actual/estimated 比值）
        self._obs: "OrderedDict[str, OrderedDict[float, float]]" = OrderedDict()
        self._lock = threading.Lock()
        self._failure_count = 0
        self._warned = False
        if durable_max_rows is None:
            from app.services.data_fabric.fabric.probing import _setting

            durable_max_rows = int(_setting("DATA_FABRIC_V7_FEEDBACK_MAX_ROWS", 20_000))
        self._durable_max_rows = int(durable_max_rows)

    # ── 捕获 ─────────────────────────────────────────────────────────

    def record(self, feedback: ExecutionFeedback) -> None:
        """记录一次执行反馈（进程内更新 + durable append，全 fail-open）。"""
        if not self.enabled:
            return
        try:
            for obs in feedback.per_source:
                if (
                    feedback.outcome == "ok"
                    and obs.estimated_rows
                    and obs.actual_rows is not None
                ):
                    ratio = obs.actual_rows / max(float(obs.estimated_rows), 1.0)
                    ratio = min(max(ratio, 1 / 64.0), 64.0)
                    key = self._key(feedback.scope_key, obs.dataset_fingerprint
                                    or obs.source_id)
                    self._put_obs(key, ratio)
        except Exception as exc:  # noqa: BLE001 - advisory 层绝不阻断
            self._on_failure(exc, "record")
        self._durable_save(feedback)
        # R-C3 回路：仅无过滤观测回写 SourceFacts（行数事实）。
        if feedback.outcome == "ok":
            self._facts_loopback(feedback)

    def _facts_loopback(self, feedback: ExecutionFeedback) -> None:
        try:
            from app.services.data_fabric.fabric.source_facts import (
                get_source_facts_service,
            )

            svc = get_source_facts_service()
            for obs in feedback.per_source:
                if (
                    obs.unfiltered
                    and obs.actual_rows is not None
                    and obs.dataset_fingerprint
                ):
                    svc.observe_unfiltered_count(
                        scope_key=feedback.scope_key,
                        fingerprint=obs.dataset_fingerprint,
                        profile_id=obs.source_id,
                        source_type=None,
                        count=obs.actual_rows,
                        note="feedback: unfiltered scan row count",
                    )
        except Exception as exc:  # noqa: BLE001
            self._on_failure(exc, "facts_loopback")

    # ── 修正因子 ─────────────────────────────────────────────────────

    def correction(
        self, scope_key: str, dataset_fingerprint: str, *, min_samples: int = 3
    ) -> Dict[str, Any]:
        """衰减加权修正因子（EXPLAIN 披露 basis/samples；不足即 1.0）。"""
        if not self.enabled:
            return {"factor": 1.0, "samples": 0, "basis": "disabled"}
        key = self._key(scope_key, dataset_fingerprint)
        now = time.monotonic()
        with self._lock:
            bucket = self._obs.get(key)
            if not bucket:
                return {"factor": 1.0, "samples": 0, "basis": "insufficient_samples"}
            pairs = [
                (ts, ratio)
                for ts, ratio in bucket.items()
                if now - ts <= self._half_life_window()
            ]
            for ts in [ts for ts in bucket if now - ts > self._half_life_window()]:
                del bucket[ts]
        if len(pairs) < min_samples:
            return {"factor": 1.0, "samples": len(pairs),
                    "basis": "insufficient_samples"}
        # 指数衰减权重（半衰期）：旧样本权重减半。
        weights = [math.pow(0.5, (now - ts) / HALF_LIFE_S) for ts, _ in pairs]
        ratios = [r for _, r in pairs]
        # 衰减加权中位数：按 ratio 排序后累计权重过半处（确定性）。
        order = sorted(range(len(ratios)), key=lambda i: ratios[i])
        total_w = sum(weights)
        acc = 0.0
        median = ratios[order[-1]]
        for i in order:
            acc += weights[i]
            if acc >= total_w / 2.0:
                median = ratios[i]
                break
        factor = min(max(median, _FACTOR_CLAMP[0]), _FACTOR_CLAMP[1])
        return {
            "factor": round(factor, 4),
            "samples": len(pairs),
            "basis": "feedback_decayed",
        }

    # ── 内部 ─────────────────────────────────────────────────────────

    @staticmethod
    def _key(scope_key: str, fp_or_sid: str) -> str:
        return f"{scope_key}|{fp_or_sid}"

    @staticmethod
    def _half_life_window() -> float:
        return HALF_LIFE_S * 4.0  # 4 个半衰期后样本出窗

    def _put_obs(self, key: str, ratio: float) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._obs.get(key)
            if bucket is None:
                bucket = OrderedDict()
                self._obs[key] = bucket
            bucket[now] = ratio
            bucket.move_to_end(now)
            while len(bucket) > self._max_obs:
                bucket.popitem(last=False)
            self._obs.move_to_end(key)
            while len(self._obs) > self._max_keys:
                self._obs.popitem(last=False)

    def _durable_save(self, feedback: ExecutionFeedback) -> None:
        try:
            from datetime import datetime, timedelta, timezone

            from app.core.database import SessionLocal
            from app.models.data_fabric import FederatedFeedbackModel

            now = datetime.now(timezone.utc).replace(tzinfo=None)

            def _insert():
                with SessionLocal() as db:
                    db.add(
                        FederatedFeedbackModel(
                            plan_hash=str(feedback.plan_hash)[:64],
                            scope_key=str(feedback.scope_key)[:128],
                            engine=str(feedback.engine)[:8],
                            outcome=str(feedback.outcome)[:16],
                            payload_json=feedback.model_dump(mode="json"),
                            created_at=now,
                            expires_at=now + timedelta(seconds=self._half_life_window()),
                        )
                    )
                    db.commit()

            from concurrent.futures import ThreadPoolExecutor

            def _run():
                _insert()

            ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="feedback-db")
            try:
                ex.submit(_insert).result(timeout=3.0)
            finally:
                ex.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001 - advisory fail-open
            self._on_failure(exc, "durable_save")

    def _on_failure(self, exc: Exception, op: str) -> None:
        self._failure_count += 1
        if not self._warned:
            logger.warning("[feedback] durable %s unavailable (fail-open): %s", op, exc)
            self._warned = True

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"keys": len(self._obs), "max_keys": self._max_keys}

    def reset(self) -> None:
        with self._lock:
            self._obs.clear()


_store: Optional[FabricFeedbackStore] = None
_store_lock = threading.Lock()


def get_feedback_store() -> FabricFeedbackStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = FabricFeedbackStore()
        return _store


def reset_feedback_store() -> None:
    global _store
    with _store_lock:
        _store = None


__all__ = [
    "ExecutionFeedback",
    "FabricFeedbackStore",
    "SourceObservation",
    "get_feedback_store",
    "reset_feedback_store",
]
