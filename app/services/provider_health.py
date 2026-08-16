"""Provider 级别的速率限制 + 电路保险丝，防止频繁失败时仍然持续调用已宕机的 provider。"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PROVIDER_NAMES = frozenset({"amap", "baidu", "tianditu", "overpass", "nominatim"})


@dataclass
class _State:
    consecutive_errors: int = 0
    last_failure_ts: float = 0.0
    # 60s 滑动窗口（issue #433）：deque 按 append 顺序（≈时间升序）存放被
    # **放行**的调用时间戳；过期项从左端弹出，均摊 O(1)，长度恒 ≤ 限流阈值，
    # 失败/被拒路径不再无界增长。
    call_timestamps: deque = field(default_factory=deque)
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
    _WINDOW_SECONDS: int = 60  # 速率窗口宽度（calls_per_minute 对应的秒数）

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

    # ── Internal helpers（调用方必须已持有 self._lock）───────────────────────

    def _prune_window(self, s: _State, now: float) -> None:
        """弹出窗外时间戳。append 顺序 ≈ 时间升序 → 左端弹出，均摊 O(1)。"""
        cutoff = now - self._WINDOW_SECONDS
        while s.call_timestamps and s.call_timestamps[0] <= cutoff:
            s.call_timestamps.popleft()

    def _circuit_gates(self, s: _State, now: float) -> bool:
        """熔断检查；冷静期已过则悄悄复位（原 can_call 语义）。"""
        if not s.circuit_open:
            return True
        if now - s.last_failure_ts < self._recovery_seconds:
            return False
        s.circuit_open = False
        s.consecutive_errors = 0
        return True

    # ── Public API ────────────────────────────────────────────────────────────

    async def can_call(self, provider: str) -> bool:
        """如果被速率限制或熔断（仍在冷静期内）则返回 False。"""
        async with self._lock:
            s = self._state.get(provider)
            if s is None:
                return True
            now = time.time()
            if not self._circuit_gates(s, now):
                return False
            self._prune_window(s, now)
            return len(s.call_timestamps) < self._calls_per_minute

    async def record_attempt(self, provider: str) -> bool:
        """记录一次调用意图，返回 True 表示允许调用，False 代表被限制。

        被拒（熔断/限流）的尝试**不写入**窗口：写入会让持续打点把限流窗
        无限向后推、且窗口随失败路径无界增长（issue #433）。窗口在每次
        检查前按 60s 剪枝，长度恒 ≤ calls_per_minute。
        """
        ts = time.time()
        async with self._lock:
            s = self._state.setdefault(provider, _State())
            if not self._circuit_gates(s, ts):
                return False
            self._prune_window(s, ts)
            if len(s.call_timestamps) >= self._calls_per_minute:
                return False
            s.call_timestamps.append(ts)
            return True

    async def record_success(self, provider: str) -> None:
        """请求成功：重置错误计数器，清除熔断标记，并剪枝窗口。"""
        async with self._lock:
            s = self._state.setdefault(provider, _State())
            s.consecutive_errors = 0
            s.circuit_open = False
            self._prune_window(s, time.time())

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
            out: dict[str, dict] = {}
            for p, s in self._state.items():
                self._prune_window(s, now)
                out[p] = {
                    "consecutive_errors": s.consecutive_errors,
                    "last_failure_ts": s.last_failure_ts,
                    "circuit_open": s.circuit_open,
                    "calls_last_minute": len(s.call_timestamps),
                }
            return out


from typing import Callable, Any

# 全局单例，chinese_maps.py 直接 import 使用
health_tracker = ProviderHealthTracker()


# ─── Standard Provider Business Status Checkers ─────────────────────────────


def check_amap_status(data: dict) -> tuple[bool, str]:
    """Check Amap JSON response for status == '1' or infocode == '10000'."""
    if not isinstance(data, dict):
        return False, "Invalid Amap response format"
    if data.get("status") != "1" and data.get("infocode") != "10000":
        return False, f"Amap: {data.get('info', 'unknown error')}"
    return True, ""


def check_baidu_status(data: dict) -> tuple[bool, str]:
    """Check Baidu JSON response for status == 0."""
    if not isinstance(data, dict):
        return False, "Invalid Baidu response format"
    status = data.get("status")
    if status is not None and status != 0:
        return False, f"Baidu: {data.get('message', f'error status {status}')}"
    return True, ""


def check_tianditu_status(data: dict) -> tuple[bool, str]:
    """Check Tianditu JSON response for error presence."""
    if not isinstance(data, dict):
        return False, "Invalid Tianditu response format"
    if "error" in data:
        return False, f"Tianditu: {data.get('error')}"
    return True, ""


def check_overpass_status(data: Any) -> tuple[bool, str]:
    """Overpass 响应业务校验：应含 elements 数组（查询错误时可能只含 remark）。"""
    if not isinstance(data, dict) or "elements" not in data:
        return False, "Overpass 响应缺少 elements"
    return True, ""


def check_nominatim_status(data: Any) -> tuple[bool, str]:
    """Nominatim 响应业务校验：search 返回 list，reverse 返回 dict（无 error 键）。"""
    if isinstance(data, dict) and "error" in data:
        return False, str(data["error"])
    if isinstance(data, list) or isinstance(data, dict):
        return True, ""
    return False, "Nominatim 响应格式异常"


async def tracked_provider_get(
    provider: str,
    url: str,
    params: dict,
    *,
    method: str = "GET",
    data: dict | None = None,
    business_checker: Callable[[dict], tuple[bool, str]] | None = None,
    tracker: ProviderHealthTracker | None = None,
    ssl_context: Any = None,
    proxy: str | None = None,
    timeout: float = 10.0,
) -> dict:
    """Execute a tracked HTTP request using get_shared_client() with circuit breaker protection.

    `method` 支持 "GET"（默认，用 `params`）与 "POST"（用 `data`）——Overpass 走
    POST 提交查询体，其余第三方 API 均为 GET。
    """
    ht = tracker or health_tracker
    if not await ht.record_attempt(provider):
        return {"error": f"{provider.capitalize()} 暂时不可用（频率限制或服务故障），请稍后重试"}

    from app.core.network import get_shared_client, get_ssl_context
    from app.core.config import settings

    actual_ssl = ssl_context or get_ssl_context()
    actual_proxy = proxy if proxy is not None else (settings.HTTPS_PROXY or settings.HTTP_PROXY)

    try:
        session = await get_shared_client()
        is_post = method.upper() == "POST"
        request = session.post if is_post else session.get
        kwargs: dict = {"data": data} if is_post else {"params": params}
        async with request(
            url,
            ssl=actual_ssl,
            proxy=actual_proxy,
            timeout=timeout,
            **kwargs,
        ) as resp:
            if resp.status != 200:
                await ht.record_error(provider)
                return {"error": f"{provider.capitalize()} API HTTP {resp.status}"}

            import json
            try:
                data = await resp.json()
            except (json.JSONDecodeError, TypeError, Exception) as e:
                await ht.record_error(provider, e)
                return {"error": f"{provider.capitalize()} JSON decode error: {e}"}

            if business_checker is not None:
                ok, err_msg = business_checker(data)
                if not ok:
                    await ht.record_error(provider)
                    return {"error": err_msg}

            await ht.record_success(provider)
            return data
    except Exception as e:
        await ht.record_error(provider, e)
        raise