"""LLM Provider/Model 健康跟踪（ADR-0102 Wave 4, §21）。

与 geodata ProviderHealthTracker（ADR-0027，app/services/provider_health.py）
的关系：**复用其断路器原语语义**（连续失败开门 + 冷却 + 半开），但不混用
实例 —— LLM 健康按 (provider, model) 键控，geodata 健康按外部数据源键控，
二者故障域不同，绝不通押。

有界指标（§21 白名单）：可用性、连续失败数、近期限流、时延桶、最近成功、
冷却到期、能力不匹配。**绝不存 prompt/响应内容。**
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# 冷却策略（与 geodata tracker 的 5 连错/300s 语义同族，按模型粒度收紧）
_FAILURE_THRESHOLD = 3
_BASE_COOLDOWN_S = 30.0
_MAX_COOLDOWN_S = 300.0
_RATE_LIMIT_RECENT_S = 120.0
_MAX_TRACKED_KEYS = 256

_LATENCY_BUCKETS_S = ((1.0, "fast"), (5.0, "medium"), (20.0, "slow"))


def latency_bucket(seconds: float) -> str:
    for threshold, label in _LATENCY_BUCKETS_S:
        if seconds < threshold:
            return label
    return "very_slow"


@dataclass
class _ModelState:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_success_ts: float = 0.0
    last_failure_ts: float = 0.0
    last_failure_kind: str = ""
    last_latency_bucket: str = ""
    recent_rate_limit_ts: float = 0.0
    capability_mismatch: bool = False
    total_success: int = 0
    total_failure: int = 0

    def snapshot(self) -> Dict[str, object]:
        now = time.monotonic()
        return {
            "available": now >= self.cooldown_until,
            "cooldown_remaining_s": max(0.0, round(self.cooldown_until - now, 1)),
            "consecutive_failures": self.consecutive_failures,
            "last_failure_kind": self.last_failure_kind or None,
            "last_latency_bucket": self.last_latency_bucket or None,
            "rate_limited_recently": (now - self.recent_rate_limit_ts) < _RATE_LIMIT_RECENT_S
            if self.recent_rate_limit_ts else False,
            "capability_mismatch": self.capability_mismatch,
            "last_success_age_s": round(now - self.last_success_ts, 1)
            if self.last_success_ts else None,
            "total_success": self.total_success,
            "total_failure": self.total_failure,
        }


class LLMProviderHealth:
    """进程内 (provider_id, model_id) 健康表。线程安全、有界、无内容存储。"""

    def __init__(
        self,
        failure_threshold: int = _FAILURE_THRESHOLD,
        base_cooldown_s: float = _BASE_COOLDOWN_S,
        max_cooldown_s: float = _MAX_COOLDOWN_S,
    ) -> None:
        self._lock = threading.Lock()
        self._states: Dict[Tuple[str, str], _ModelState] = {}
        self._failure_threshold = failure_threshold
        self._base_cooldown_s = base_cooldown_s
        self._max_cooldown_s = max_cooldown_s

    # -- 写入 -----------------------------------------------------------

    def record_success(self, provider_id: str, model_id: str, latency_s: float = 0.0) -> None:
        with self._lock:
            st = self._state(provider_id, model_id)
            st.consecutive_failures = 0
            st.cooldown_until = 0.0
            st.last_success_ts = time.monotonic()
            st.last_latency_bucket = latency_bucket(latency_s)
            st.total_success += 1

    def record_failure(
        self,
        provider_id: str,
        model_id: str,
        kind: str = "transport",
        *,
        capability_mismatch: bool = False,
    ) -> None:
        """记录失败；连续失败达阈值时按指数退避开启冷却。

        kind 语义见 provider.FailureKind：rate_limit 单独记近期限流位；
        capability_mismatch/context_too_large/invalid_tool_schema 不开冷却
        （换模型/改请求才有用，等再久也不会自愈）但计入能力不匹配标记。
        """
        now = time.monotonic()
        with self._lock:
            st = self._state(provider_id, model_id)
            st.last_failure_ts = now
            st.last_failure_kind = kind
            st.total_failure += 1
            if kind == "rate_limit":
                st.recent_rate_limit_ts = now
            if capability_mismatch or kind in (
                "capability_mismatch", "context_too_large", "invalid_tool_schema",
            ):
                st.capability_mismatch = True
                st.consecutive_failures = 0  # 不自愈类失败不进退避计数
                return
            st.consecutive_failures += 1
            if st.consecutive_failures >= self._failure_threshold:
                cooldown = min(
                    self._max_cooldown_s,
                    self._base_cooldown_s * (2 ** max(0, st.consecutive_failures - self._failure_threshold)),
                )
                st.cooldown_until = now + cooldown

    # -- 查询 -----------------------------------------------------------

    def available(self, provider_id: str, model_id: str) -> bool:
        with self._lock:
            st = self._states.get((provider_id, model_id))
            if st is None:
                return True
            return time.monotonic() >= st.cooldown_until

    def cooldown_remaining(self, provider_id: str, model_id: str) -> float:
        with self._lock:
            st = self._states.get((provider_id, model_id))
            if st is None:
                return 0.0
            return max(0.0, st.cooldown_until - time.monotonic())

    def rate_limited_recently(self, provider_id: str, model_id: str) -> bool:
        with self._lock:
            st = self._states.get((provider_id, model_id))
            if st is None or not st.recent_rate_limit_ts:
                return False
            return (time.monotonic() - st.recent_rate_limit_ts) < _RATE_LIMIT_RECENT_S

    def has_capability_mismatch(self, provider_id: str, model_id: str) -> bool:
        with self._lock:
            st = self._states.get((provider_id, model_id))
            return bool(st and st.capability_mismatch)

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        with self._lock:
            return {
                f"{p}/{m}": st.snapshot() for (p, m), st in self._states.items()
            }

    def reset(self) -> None:
        with self._lock:
            self._states.clear()

    # -- 内部 -----------------------------------------------------------

    def _state(self, provider_id: str, model_id: str) -> _ModelState:
        key = (provider_id, model_id)
        st = self._states.get(key)
        if st is None:
            if len(self._states) >= _MAX_TRACKED_KEYS:
                # 有界：逐出最旧的 last_failure（无失败的最先）
                oldest = min(
                    self._states,
                    key=lambda k: self._states[k].last_failure_ts or 0.0,
                )
                self._states.pop(oldest, None)
            st = _ModelState()
            self._states[key] = st
        return st


_health = LLMProviderHealth()


def get_llm_provider_health() -> LLMProviderHealth:
    return _health
