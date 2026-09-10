"""V6→V5 引擎回退的进程级熔断（ADR-0130，收口 ADR-0120 已知限制 R2-Mi-4）。

问题：V6 非 typed 异常回退 V5 是诚实降级，但持续故障的请求**每一次**都付
V6+V5 双执行成本（V6 跑到崩 → V5 重跑），且无进程级记忆。

语义（与 per-source circuit breaker 同文化，但键是**引擎**而非源 —— 崩溃
面是 V6 引擎栈，不是某个远端）：
- 连续 V6 崩溃 ≥ ``failure_threshold``（默认 3）→ OPEN：后续 engine=v6 请求
  直接走 V5（跳过 V6 整个规划+执行栈），warning 披露熔断开启；
- ``cool_down_s``（默认 60s）后 HALF_OPEN：放行**一次** V6 尝试 —— 成功回
  CLOSED，失败重开；
- 夹界有界：单实例无集合（进程级单计数器），无 LRU 需求；
- 时钟可注入（测试确定性）；状态可披露（EXPLAIN/fabric 段如实渲染）。

红线：熔断只影响**引擎选择**，绝不改变结果契约 —— V5 回退路径的
warnings/engine 标注与既有语义一致。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class EngineBreakerState:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class EngineFallbackBreaker:
    """进程级 V6→V5 回退熔断（线程安全；无集合 = 无界增长面为零）。"""

    def __init__(
        self,
        *,
        failure_threshold: Optional[int] = None,
        cool_down_s: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if failure_threshold is None:
            failure_threshold = _setting("DATA_FABRIC_V8_ENGINE_BREAKER_THRESHOLD", 3)
        if cool_down_s is None:
            cool_down_s = _setting("DATA_FABRIC_V8_ENGINE_BREAKER_COOLDOWN_S", 60.0)
        self.failure_threshold = max(1, int(failure_threshold))
        self.cool_down_s = max(0.0, float(cool_down_s))
        self._clock = clock
        self._consecutive_failures = 0
        self._state = EngineBreakerState.CLOSED
        self._opened_at = 0.0
        self._half_open_trial_inflight = False
        self._total_fallbacks = 0
        # RLock：state()/disclosure() 内部复用（disclosure 持锁调 state）。
        self._lock = threading.RLock()

    # ── 决策 ─────────────────────────────────────────────────────────

    def allow_v6(self) -> bool:
        """engine=v6 请求是否允许进入 V6 栈（False = 直接走 V5）。"""
        with self._lock:
            if self._state == EngineBreakerState.OPEN:
                if self._clock() - self._opened_at >= self.cool_down_s:
                    self._state = EngineBreakerState.HALF_OPEN
                    self._half_open_trial_inflight = False
                else:
                    return False
            if self._state == EngineBreakerState.HALF_OPEN:
                if self._half_open_trial_inflight:
                    return False  # 单 trial：其余请求继续 V5
                self._half_open_trial_inflight = True
            return True

    def record_v6_crash(self, exc: BaseException) -> None:
        """V6 非 typed 崩溃（已回退 V5）→ 记账并按阈值开闸。"""
        with self._lock:
            self._half_open_trial_inflight = False
            self._consecutive_failures += 1
            self._total_fallbacks += 1
            if self._state == EngineBreakerState.HALF_OPEN or (
                self._consecutive_failures >= self.failure_threshold
            ):
                if self._state != EngineBreakerState.OPEN:
                    logger.warning(
                        "[engine_breaker] V6 fallback breaker OPEN after %d "
                        "consecutive crashes (last: %s); engine=v6 requests "
                        "will run V5 directly for %.0fs",
                        self._consecutive_failures,
                        type(exc).__name__,
                        self.cool_down_s,
                    )
                self._state = EngineBreakerState.OPEN
                self._opened_at = self._clock()

    def record_v6_success(self) -> None:
        """V6 成功执行 → 归零并回 CLOSED（含 half-open trial 成功）。"""
        with self._lock:
            self._consecutive_failures = 0
            self._state = EngineBreakerState.CLOSED
            self._half_open_trial_inflight = False

    # ── 披露 ─────────────────────────────────────────────────────────

    def state(self) -> str:
        with self._lock:
            if self._state == EngineBreakerState.OPEN:
                if self._clock() - self._opened_at >= self.cool_down_s:
                    self._state = EngineBreakerState.HALF_OPEN
                    self._half_open_trial_inflight = False
            return self._state

    def disclosure(self) -> dict:
        """EXPLAIN/fabric 段的诚实投影（无 secret 面）。"""
        with self._lock:
            return {
                "state": self.state(),
                "consecutive_failures": self._consecutive_failures,
                "failure_threshold": self.failure_threshold,
                "cool_down_s": self.cool_down_s,
                "total_fallbacks": self._total_fallbacks,
            }

    def reset(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._state = EngineBreakerState.CLOSED
            self._opened_at = 0.0
            self._half_open_trial_inflight = False
            self._total_fallbacks = 0


def _setting(name: str, default):
    try:
        from app.core.config import settings

        return getattr(settings, name, default)
    except Exception:  # noqa: BLE001 - 配置层不可用时用默认值
        return default


_breaker: Optional[EngineFallbackBreaker] = None
_breaker_lock = threading.Lock()


def get_engine_breaker() -> EngineFallbackBreaker:
    global _breaker
    with _breaker_lock:
        if _breaker is None:
            _breaker = EngineFallbackBreaker()
        return _breaker


def reset_engine_breaker() -> None:
    """测试隔离用（生产路径禁止调用）。"""
    global _breaker
    with _breaker_lock:
        _breaker = None


__all__ = [
    "EngineBreakerState",
    "EngineFallbackBreaker",
    "get_engine_breaker",
    "reset_engine_breaker",
]
