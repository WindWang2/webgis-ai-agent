"""Provider 级别的速率限制 + 电路保险丝，防止频繁失败时仍然持续调用已宕机的 provider。"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PROVIDER_NAMES = frozenset({"amap", "baidu", "tianditu"})


@dataclass
class _State:
    consecutive_errors: int = 0
    last_failure_ts: float = 0.0
    call_timestamps: list[float] = field(default_factory=list)
    circuit_open: bool = False


class ProviderHealthTracker:
    """对每个 provider 独立维护错误计数 / 速率窗口 / 熔断状态的 tracker。

    用法::

        from app.services.provider_health import health_tracker as ht

        # 在各 provider helper 函数开头：
        if not await ht.record_attempt("amap"):
            return {"error": "Amap 暂时不可用，请稍后重试"}
        try:
            result = await _do_amap_request(...)
            await ht.record_success("amap")
            return result
        except Exception as e:
            await ht.record_error("amap", e)
            raise  # 异常交给上层 failover 逻辑处理
    """

    # Amap 免费配额约 60 次/分钟；取保守值防超限
    DEFAULT_RATE_LIMIT: int = 60
    DEFAULT_ERROR_THRESHOLD: int = 5  # 连续这么多次错误后短路
    DEFAULT_RECOVERY_SECONDS: int = 300  # 5 分钟冷静期后恢复尝试

    def __init__(
        self,
        *,
        calls_per_minute: int = DEFAULT_RATE_LIMIT,
        error_threshold: int = DEFAULT_ERROR_THRESHOLD,
        recovery_seconds: int = DEFAULT_RECOVERY_SECONDS,
    ) -> None:
        self._calls_per_minute = calls_per_minute
        self._error_threshold = error_threshold
        self._recovery_seconds = recovery_seconds
        self._state: dict[str, _State] = {}
        self._lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    async def can_call(self, provider: str) -> bool:
        """如果被速率限制或熔断（仍在冷静期内）则返回 False。"""
        async with self._lock:
            s = self._state.get(provider, _State())
            if s.circuit_open:
                if time.time() - s.last_failure_ts < self._recovery_seconds:
                    return False
                # 冷静期已过，悄悄复位
                s.circuit_open = False
                s.consecutive_errors = 0
            recent = [ts for ts in s.call_timestamps if time.time() - ts < 60]
            return len(recent) < self._calls_per_minute

    async def record_attempt(self, provider: str) -> bool:
        """记录一次调用意图，返回 True 表示允许调用，False 代表被限制。"""
        ts = time.time()
        async with self._lock:
            s = self._state.setdefault(provider, _State())
            s.call_timestamps.append(ts)
        return await self.can_call(provider)

    async def record_success(self, provider: str) -> None:
        """请求成功：重置错误计数器，清除熔断标记。"""
        async with self._lock:
            s = self._state.setdefault(provider, _State())
            s.consecutive_errors = 0
            s.circuit_open = False
            cutoff = time.time() - 60
            s.call_timestamps = [ts for ts in s.call_timestamps if ts >= cutoff]

    async def record_error(self, provider: str, exc: Exception | None = None) -> None:
        """请求出错：累加连续错误计数，达到阈值后打开熔断。"""
        async with self._lock:
            s = self._state.setdefault(provider, _State())
            s.consecutive_errors += 1
            s.last_failure_ts = time.time()
            if s.consecutive_errors >= self._error_threshold and not s.circuit_open:
                s.circuit_open = True
                p = provider.upper() if provider in PROVIDER_NAMES else provider
                msg = (
                    f"[ProviderHealth] {p} 连续 {s.consecutive_errors} 次错误，"
                    f"打开熔断（{self._recovery_seconds}s 内不再尝试）"
                )
                if exc:
                    msg += f"｜{type(exc).__name__}: {exc}"
                logger.warning(msg)

    # ── 诊断用 ────────────────────────────────────────────────────────────────

    async def snapshot(self) -> dict[str, dict]:
        """返回可读快照（仅供监控/日志）。

        审计 M3：之前 snapshot 不加锁，并发 record_attempt 可能修改 self._state
        导致 RuntimeError 'dictionary changed size during iteration' 或重复/缺失。
        snapshot 现在加锁（与 record_* 一致），用 copy 防 release 后 mutate。
        """
        now = time.time()
        async with self._lock:
            return {
                p: {
                    "consecutive_errors": s.consecutive_errors,
                    "last_failure_ts": s.last_failure_ts,
                    "circuit_open": s.circuit_open,
                    "calls_last_minute": sum(1 for ts in s.call_timestamps if now - ts < 60),
                }
                for p, s in list(self._state.items())  # list() copy 防迭代中 mutate
            }


# 全局单例，chinese_maps.py 直接 import 使用
health_tracker = ProviderHealthTracker()