"""Tool-provider 健康状态机(H05 Capability Runtime vNext)。

此前仓库有三套互不相通的健康面(Data Fabric 断路器 / LLM provider 健康 /
worker 心跳),唯独「capability → tool provider」的执行级健康无人记录 ——
resolver 排序只看静态因子 + 会话级 recovery ledger,provider 连续超时后
下一轮规划仍然排在前面。本模块补上这一面:

- **typed 失败分类**:timeout/transient/unknown 计入熔断(它们是
  provider-down 证据);credential/policy/permanent/cancelled 不计入
  (它们是上下文/语义问题,重试或换 provider 才是正解 —— 与 DF breaker
  ``is_transient`` 同纪律)。分类词表全披露,不隐藏降级。
- **断路器 + 半开恢复**:threshold 次连续瞬时失败 → OPEN(fail-fast),
  冷却到期 → HALF_OPEN(单 trial);trial 成功 → CLOSED,失败 → 重开。
  冷却**封顶指数**(30s→60s→120s→cap):反复 open 的 provider 冷却单调
  变长 —— flapping 不构成 retry storm(对抗测试钉死)。
- **latency EWMA → bucket**:排序因子与快照披露共用(500ms/2000ms 档)。
- **有界**:LRU ≤4096 entry,锁内变更(DF M1 同款并发纪律);
  clock 注入,测试确定性。
- **kill switch** ``GIS_PROVIDER_HEALTH``(默认 ON)=0 时全链路直通:
  记录与执法双关,逐位回到既有行为。

本模块不做 I/O、不探测网络 —— "probe" 是消费方(health tick / dispatch
结果)喂事实,状态机只做裁决。半开 trial 名额的释放纪律(泄漏防护)承
DF M1/R1-m9:``allow()`` 占用名额后,调用方无论成败(含 BaseException)
都必须 ``record_*`` 或 ``release_trial``。
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Optional, Tuple

__all__ = [
    "PROVIDER_HEALTH_ENV",
    "ProviderFailureClass",
    "ProviderHealthState",
    "ProviderHealthVerdict",
    "ProviderHealthRegistry",
    "classify_failure",
    "provider_health_enabled",
    "get_provider_health_registry",
    "set_provider_health_registry",
    "provider_health_factor",
    "latency_bucket",
]

PROVIDER_HEALTH_ENV = "GIS_PROVIDER_HEALTH"

#: 断路参数(DF breaker 量级;threshold 略低 —— 工具级故障证据更便宜)。
FAILURE_THRESHOLD = 3
COOL_DOWN_S = 30.0
COOL_DOWN_MAX_S = 120.0

#: 半开 trial 名额的时间兜底:grant 后超时未回填结果(调用方在 record 前
#: 异常路径崩溃/被杀)→ 名额自动失效,允许新 trial —— DF M1「名额泄漏 →
#: 熔断卡死」类问题的预防性封堵(本模块 trial 消费点在实际执行包装处,
#: 早退路径不占名额,此兜底是纵深防御)。
HALF_OPEN_TRIAL_TIMEOUT_S = 120.0

#: latency EWMA α 与 bucket 阈值(ms)。
_EWMA_ALPHA = 0.3
_FAST_MS = 500.0
_SLOW_MS = 2000.0

#: registry 容量(LRU)。
MAX_ENTRIES = 4096

#: OPEN/HALF_OPEN 在 resolver 排序里的罚分(与 degraded 0.5 / deprecated 0.5 同量级)。
HEALTH_FACTOR_OPEN = 0.75
HEALTH_FACTOR_HALF_OPEN = 0.25


class ProviderFailureClass(str, Enum):
    """typed 失败分类(证据/断路裁决/快照共用;id 级)。"""

    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    CREDENTIAL = "credential"
    POLICY = "policy"
    PERMANENT = "permanent"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


#: 只有这些分类是 provider-down 证据(计入熔断)。
_BREAKER_CLASSES = frozenset({
    ProviderFailureClass.TIMEOUT,
    ProviderFailureClass.TRANSIENT,
    ProviderFailureClass.UNKNOWN,
})

#: 已知错误码 → 分类(dispatch / registry 闸 / governor 的对外词表)。
_CODE_CLASSIFICATION = {
    "CREDENTIALS_REQUIRED": ProviderFailureClass.CREDENTIAL,
    "PERMISSION_DENIED": ProviderFailureClass.POLICY,
    "TIER3_CONFIRMATION_REQUIRED": ProviderFailureClass.POLICY,
    "CAPABILITY_INELIGIBLE": ProviderFailureClass.POLICY,
    "VALIDATION_ERROR": ProviderFailureClass.PERMANENT,
    "TOOL_NOT_EXECUTABLE": ProviderFailureClass.PERMANENT,
    "UNKNOWN_TOOL": ProviderFailureClass.PERMANENT,
    "OPERATION_CANCELLED": ProviderFailureClass.CANCELLED,
}


class ProviderHealthState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def provider_health_enabled() -> bool:
    """kill switch(默认 ON;=0 时记录与执法全链路直通)。"""
    raw = (os.getenv(PROVIDER_HEALTH_ENV) or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def classify_failure(
    exc: Optional[BaseException] = None,
    *,
    error_code: str = "",
    error_msg: str = "",
) -> ProviderFailureClass:
    """异常/错误码/文案 → typed 失败分类(确定性;不猜的归 unknown)。"""
    if exc is not None:
        if isinstance(exc, asyncio.CancelledError):
            return ProviderFailureClass.CANCELLED
        if isinstance(exc, TimeoutError):
            return ProviderFailureClass.TIMEOUT
        name = type(exc).__name__.lower()
        if "timeout" in name:
            return ProviderFailureClass.TIMEOUT
        if "cancel" in name:
            return ProviderFailureClass.CANCELLED
        code = str(getattr(exc, "code", "") or "")
        if code in _CODE_CLASSIFICATION:
            return _CODE_CLASSIFICATION[code]
        return ProviderFailureClass.TRANSIENT
    code = str(error_code or "").strip()
    if code in _CODE_CLASSIFICATION:
        return _CODE_CLASSIFICATION[code]
    msg = str(error_msg or "").lower()
    if "timeout" in msg or "timed out" in msg:
        return ProviderFailureClass.TIMEOUT
    if "credential" in msg or "凭证" in msg:
        return ProviderFailureClass.CREDENTIAL
    if "permission" in msg or "权限" in msg:
        return ProviderFailureClass.POLICY
    return ProviderFailureClass.UNKNOWN


def latency_bucket(latency_ms: Optional[float]) -> str:
    """EWMA 毫秒 → 档位词表(fast/medium/slow;无数据 = unknown)。"""
    if latency_ms is None:
        return "unknown"
    if latency_ms <= _FAST_MS:
        return "fast"
    if latency_ms <= _SLOW_MS:
        return "medium"
    return "slow"


@dataclass
class _HealthEntry:
    state: ProviderHealthState = ProviderHealthState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    cool_down_s: float = COOL_DOWN_S
    half_open_trial_inflight: bool = False
    trial_granted_at: float = 0.0
    open_cycles: int = 0
    latency_ewma_ms: Optional[float] = None
    last_failure_class: str = ""
    last_transition_at: float = 0.0
    success_count: int = 0
    failure_count: int = 0


@dataclass(frozen=True)
class ProviderHealthVerdict:
    """单 provider 健康投影(只读;快照/排序/证据共用)。"""

    provider_key: str
    state: str = "closed"
    latency_bucket: str = "unknown"
    consecutive_failures: int = 0
    last_failure_class: str = ""
    open_cycles: int = 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "provider": self.provider_key[:96],
            "state": self.state,
            "latency_bucket": self.latency_bucket,
            "consecutive_failures": self.consecutive_failures,
            "last_failure_class": self.last_failure_class,
            "open_cycles": self.open_cycles,
        }


class ProviderHealthRegistry:
    """per-provider 健康台账(锁内变更;LRU 有界;clock 注入)。"""

    def __init__(
        self,
        *,
        failure_threshold: int = FAILURE_THRESHOLD,
        cool_down_s: float = COOL_DOWN_S,
        cool_down_max_s: float = COOL_DOWN_MAX_S,
        clock: Optional[Callable[[], float]] = None,
        max_entries: int = MAX_ENTRIES,
    ):
        self._threshold = max(1, int(failure_threshold))
        self._cool_down = max(0.001, float(cool_down_s))
        self._cool_down_max = max(self._cool_down, float(cool_down_max_s))
        self._clock = clock or time.monotonic
        self._entries: "OrderedDict[str, _HealthEntry]" = OrderedDict()
        self._max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()

    # ── 内部 ─────────────────────────────────────────────────────────
    def _entry(self, key: str) -> _HealthEntry:
        """调用方必须已持锁。"""
        entry = self._entries.get(key)
        if entry is None:
            entry = _HealthEntry()
            self._entries[key] = entry
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        else:
            self._entries.move_to_end(key)
        return entry

    def _effective_state(self, entry: _HealthEntry, now: float) -> ProviderHealthState:
        if entry.state is ProviderHealthState.OPEN:
            if now - entry.opened_at >= entry.cool_down_s:
                entry.state = ProviderHealthState.HALF_OPEN
                entry.half_open_trial_inflight = False
                entry.last_transition_at = now
        return entry.state

    # ── 裁决 ─────────────────────────────────────────────────────────
    def allow(self, provider_key: str) -> bool:
        """False = OPEN fail-fast(调用方不得发起执行)。

        HALF_OPEN 下授予单 trial 名额;名额带时间兜底(超时自动失效),
        重复 allow 不会永久卡死。调用契约:allow()=True 后必须以
        ``record_success`` / ``record_failure`` 回填结果(取消路径用
        ``release_trial``)。
        """
        with self._lock:
            entry = self._entry(provider_key)
            now = self._clock()
            state = self._effective_state(entry, now)
            if state is ProviderHealthState.OPEN:
                return False
            if state is ProviderHealthState.HALF_OPEN:
                if entry.half_open_trial_inflight:
                    if now - entry.trial_granted_at < HALF_OPEN_TRIAL_TIMEOUT_S:
                        return False  # 半开只放单 trial(防 trial 风暴)
                    # 旧 trial 超时未回填:名额失效,放新 trial(纵深防御)。
                entry.half_open_trial_inflight = True
                entry.trial_granted_at = now
            return True

    def state(self, provider_key: str) -> str:
        with self._lock:
            entry = self._entry(provider_key)
            return self._effective_state(entry, self._clock()).value

    # ── 记录 ─────────────────────────────────────────────────────────
    def record_success(self, provider_key: str, latency_ms: Optional[float] = None) -> None:
        with self._lock:
            entry = self._entry(provider_key)
            now = self._clock()
            entry.consecutive_failures = 0
            if entry.state is not ProviderHealthState.CLOSED:
                entry.state = ProviderHealthState.CLOSED
                entry.open_cycles = 0
                entry.last_transition_at = now
            entry.half_open_trial_inflight = False
            entry.success_count += 1
            if latency_ms is not None and latency_ms >= 0:
                entry.latency_ewma_ms = (
                    latency_ms if entry.latency_ewma_ms is None
                    else (_EWMA_ALPHA * latency_ms
                          + (1.0 - _EWMA_ALPHA) * entry.latency_ewma_ms)
                )

    def record_failure(
        self,
        provider_key: str,
        failure_class: ProviderFailureClass = ProviderFailureClass.UNKNOWN,
    ) -> None:
        """typed 失败记账;仅瞬时类计数熔断(上下文/语义类不证据化 provider-down)。"""
        with self._lock:
            entry = self._entry(provider_key)
            now = self._clock()
            entry.last_failure_class = str(failure_class.value)
            if failure_class is ProviderFailureClass.CANCELLED:
                # 取消不是 provider 的错也不是成功:只清半开名额,不动计数。
                entry.half_open_trial_inflight = False
                return
            entry.failure_count += 1
            if failure_class not in _BREAKER_CLASSES:
                entry.half_open_trial_inflight = False
                return
            entry.consecutive_failures += 1
            entry.half_open_trial_inflight = False
            if entry.state is ProviderHealthState.HALF_OPEN:
                # trial 失败 → 重开,冷却指数升级(封顶)。
                entry.open_cycles += 1
                entry.cool_down_s = min(
                    self._cool_down_max,
                    self._cool_down * (2 ** min(entry.open_cycles, 4)),
                )
                entry.state = ProviderHealthState.OPEN
                entry.opened_at = now
                entry.last_transition_at = now
            elif entry.consecutive_failures >= self._threshold:
                # 阈值开启不计 open_cycles(那是「重开」计数 —— 冷却升级
                # 只由半开 trial 失败驱动:30→60→120→cap)。
                entry.state = ProviderHealthState.OPEN
                entry.opened_at = now
                entry.last_transition_at = now

    def release_trial(self, provider_key: str) -> None:
        """无条件释放半开 trial 名额(调用方未走到 record_* 的路径防泄漏)。"""
        with self._lock:
            entry = self._entries.get(provider_key)
            if entry is not None:
                entry.half_open_trial_inflight = False

    # ── 投影 ─────────────────────────────────────────────────────────
    def verdict(self, provider_key: str) -> ProviderHealthVerdict:
        with self._lock:
            entry = self._entry(provider_key)
            state = self._effective_state(entry, self._clock()).value
            return ProviderHealthVerdict(
                provider_key=provider_key,
                state=state,
                latency_bucket=latency_bucket(entry.latency_ewma_ms),
                consecutive_failures=entry.consecutive_failures,
                last_failure_class=entry.last_failure_class,
                open_cycles=entry.open_cycles,
            )

    def snapshot(self, max_entries: int = 64) -> Tuple[ProviderHealthVerdict, ...]:
        """有界全量投影(快照构造面;LRU 尾 = 最近使用优先)。"""
        with self._lock:
            out = []
            for key in reversed(list(self._entries.keys())[-max_entries:]):
                entry = self._entries[key]
                out.append(ProviderHealthVerdict(
                    provider_key=key,
                    state=self._effective_state(entry, self._clock()).value,
                    latency_bucket=latency_bucket(entry.latency_ewma_ms),
                    consecutive_failures=entry.consecutive_failures,
                    last_failure_class=entry.last_failure_class,
                    open_cycles=entry.open_cycles,
                ))
            return tuple(out)


# ── 进程单例 ─────────────────────────────────────────────────────────────

_REGISTRY_LOCK = threading.Lock()
_REGISTRY: Optional[ProviderHealthRegistry] = None


def get_provider_health_registry() -> ProviderHealthRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = ProviderHealthRegistry()
        return _REGISTRY


def set_provider_health_registry(
    registry: Optional[ProviderHealthRegistry],
) -> None:
    """注入进程级 registry(测试缝;None = 回落默认)。"""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = registry


def provider_health_factor(provider_key: str) -> float:
    """resolver 排序因子(open 0.75 / half_open 0.25 / 其余 0;disabled → 0)。

    因子而非资格:健康是时变运行时事实,排序披露它(可解释),不悄悄改变
    资格语义 —— 资格面的执法在 bind 拒绝支(有替代才拒,kill-switch 同源)。
    """
    if not provider_health_enabled():
        return 0.0
    try:
        state = get_provider_health_registry().state(provider_key)
    except Exception:  # noqa: BLE001 — 健康面缺席中性
        return 0.0
    if state == ProviderHealthState.OPEN.value:
        return HEALTH_FACTOR_OPEN
    if state == ProviderHealthState.HALF_OPEN.value:
        return HEALTH_FACTOR_HALF_OPEN
    return 0.0
