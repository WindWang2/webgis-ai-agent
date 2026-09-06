"""计划-估计反馈环（ADR-0101 D6）：有界、可解释、可关闭、绝不自学习失控。

设计边界（V4 §12）：
- 反馈按 **(dataset 指纹, 操作类别)** 作用域化；TTL + 每键样本数双界；
- 只从 **成功（outcome=ok）** 的执行学习 —— 失败/部分执行绝不进入；
- 修正因子是「winsorized 中位数比值」：单样本夹在 [1/64, 64]，聚合因子
  夹在 [0.1, 10]，样本 < min_samples 时不修正（回到无反馈基线）；
- 每一次修正都携带可解释的 basis/samples —— planner 把它写进
  ``assumptions``，EXPLAIN 诚实披露；
- 进程内有界存储（不建第二持久真相）；``enabled=False`` 一键关闭回到
  V3 行为（位级不变）。
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import OrderedDict
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: 单样本比值夹界（防单次异常观测毒化修正）。
_OBS_RATIO_CLAMP = (1.0 / 64.0, 64.0)
#: 聚合修正因子夹界（修正永不把估计推离真实量级一个数量级以上）。
_FACTOR_CLAMP = (0.1, 10.0)


class PlannerCorrection(BaseModel):
    """一次可解释的估计修正。"""

    factor: float
    samples: int
    basis: str = "feedback"          # feedback | disabled | insufficient_samples
    drift: Optional[str] = None      # 持续偏移披露（写入 assumptions）


class PlannerFeedbackStore:
    """进程内有界反馈存储。``record`` 在执行后调用（携带实际值），
    ``correction`` 在规划期调用（产出修正因子）。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        ttl_s: float = 3600.0,
        max_keys: int = 512,
        max_obs_per_key: int = 8,
        min_samples: int = 3,
    ):
        self.enabled = enabled
        self._ttl_s = ttl_s
        self._max_keys = max_keys
        self._max_obs = max_obs_per_key
        self._min_samples = min_samples
        # key -> OrderedDict[ts -> ratio]（只存成功观测的 actual/estimated）
        self._obs: "OrderedDict[str, OrderedDict[float, float]]" = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(dataset_fingerprint: str, operator_class: str) -> str:
        return f"{operator_class}:{dataset_fingerprint}"

    def record(
        self,
        *,
        dataset_fingerprint: str,
        operator_class: str,
        estimated_rows: Optional[int],
        actual_rows: Optional[int],
        outcome: str = "ok",
    ) -> None:
        """记录一次执行反馈。只有 outcome=ok 且两侧行数已知才进入样本。"""
        if not self.enabled or outcome != "ok":
            return
        if not dataset_fingerprint or not estimated_rows or actual_rows is None:
            return
        ratio = actual_rows / max(float(estimated_rows), 1.0)
        ratio = min(max(ratio, _OBS_RATIO_CLAMP[0]), _OBS_RATIO_CLAMP[1])
        now = time.monotonic()
        key = self._key(str(dataset_fingerprint), operator_class)
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

    def correction(
        self, dataset_fingerprint: str, operator_class: str = "query"
    ) -> PlannerCorrection:
        """规划期修正因子（可解释；样本不足/关闭时给显式 basis）。"""
        if not self.enabled:
            return PlannerCorrection(factor=1.0, samples=0, basis="disabled")
        key = self._key(str(dataset_fingerprint), operator_class)
        now = time.monotonic()
        with self._lock:
            bucket = self._obs.get(key)
            if not bucket:
                return PlannerCorrection(factor=1.0, samples=0, basis="insufficient_samples")
            # TTL 内的样本（访问期惰性过期；过期样本不参与）。
            ratios = [r for ts, r in bucket.items() if now - ts <= self._ttl_s]
            for ts in [ts for ts in bucket if now - ts > self._ttl_s]:
                del bucket[ts]
        if len(ratios) < self._min_samples:
            return PlannerCorrection(
                factor=1.0, samples=len(ratios), basis="insufficient_samples")
        factor = statistics.median(ratios)
        factor = min(max(factor, _FACTOR_CLAMP[0]), _FACTOR_CLAMP[1])
        drift = None
        if factor < 1 / 3:
            drift = "planner_feedback_overestimate"
        elif factor > 3:
            drift = "planner_feedback_underestimate"
        return PlannerCorrection(
            factor=round(factor, 4), samples=len(ratios), basis="feedback", drift=drift)

    def reset(self) -> None:
        with self._lock:
            self._obs.clear()


#: 进程级反馈单例（engine-local 性能提示；跨进程持久化是显式 Deferred）。
feedback_store = PlannerFeedbackStore()
