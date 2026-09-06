"""远端栅格窗口读取策略（ADR-0101 D9，V6 §22）。

V3 事实：远端读取完全委托 GDAL ``/vsi*``，``GDAL_HTTP_TIMEOUT=5`` 且
``GDAL_HTTP_MAX_RETRY=0`` —— 零重试、零预算、零健康感知（forensic 审计
§7）。V6 在**应用层**补齐，且绝不改变既有的 env 语义（测试锁定）：

- **有界瞬态重试**：超时/连接重置/5xx 类失败按策略重试（默认 3 次、
  全抖动指数退避）；确定性失败（校验和、结构错误）绝不重试；
- **请求预算**：每次执行会话累计的窗口读次数与近似字节有硬上界 ——
  超预算 typed 失败，绝不静默继续下载；
- **提供方健康**：按主机键接入 data_fabric 既有断路器（可选：导入
  失败 = 无健康检查，功能照常）；
- **取消协作**：重试之间检查取消令牌（窗口间取消由 execute_windowed
  的 ``cancellable`` 迭代器承担）。

红线不变：**窗口够用时绝不整幅下载远端栅格**；重试只发生在窗口级。
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

import numpy as np
from rasterio.windows import Window

from app.lib.cancellation import OperationCancelled

logger = logging.getLogger(__name__)

#: 默认远端窗口读预算（单次 execute 会话）。
DEFAULT_MAX_REQUESTS = 2_000
DEFAULT_MAX_BYTES = 512 * 1024 * 1024


class RemoteReadBudgetExceeded(RuntimeError):
    """远端读取请求/字节预算耗尽（typed；调用方转为预算错误语义）。"""


def is_transient_remote_error(exc: BaseException) -> bool:
    """GDAL/rasterio 远端失败的瞬态分类（保守：只有明确网络类才重试）。"""
    text = str(exc).lower()
    transient_markers = (
        "timed out", "timeout", "connection reset", "connection refused",
        "temporarily unavailable", "http response code: 5",
        "http response code: 429", "couldn't connect", "curl error 7",
        "curl error 28", "curl error 56", "server down",
    )
    permanent_markers = (
        "not recognized", "not found", "http response code: 404",
        "http response code: 401", "http response code: 403",
        "checksum", "corrupt", "invalid", "unsupported",
    )
    if any(m in text for m in permanent_markers):
        return False
    return any(m in text for m in transient_markers)


@dataclass
class RemoteReadPolicy:
    """远端窗口读策略（预算 + 重试 + 退避）。"""

    max_attempts: int = 3
    backoff_s: float = 0.25
    backoff_multiplier: float = 2.0
    max_backoff_s: float = 4.0
    jitter: bool = True
    max_requests: int = DEFAULT_MAX_REQUESTS
    max_bytes: int = DEFAULT_MAX_BYTES
    use_provider_health: bool = True


@dataclass
class RemoteReadSession:
    """一次远端执行会话的累计计数（请求/字节预算载体）。"""

    policy: RemoteReadPolicy = field(default_factory=RemoteReadPolicy)
    requests: int = 0
    bytes_read: int = 0
    retries: int = 0

    def approx_bytes(self, arr: np.ndarray) -> int:
        return int(arr.dtype.itemsize * arr.size) if arr is not None else 0


def remote_uri(uri: Optional[str]) -> bool:
    if not uri:
        return False
    return uri.startswith(("http://", "https://", "/vsi"))


def _host_key(uri: str) -> str:
    try:
        if uri.startswith("/vsicurl/"):
            return urlsplit(uri[len("/vsicurl/"):]).netloc
        return urlsplit(uri).netloc or uri[:64]
    except Exception:  # noqa: BLE001
        return "unknown"


def remote_read_window(
    session: RemoteReadSession,
    uri: str,
    ds: Any,
    band_or_bands: Any,
    window: Window,
    *,
    cancel_token: Optional[Any] = None,
) -> np.ndarray:
    """带预算/重试/健康感知的远端窗口读（本地源也可用，零开销路径）。"""
    from rasterio.errors import RasterioError

    policy = session.policy
    if session.requests + 1 > policy.max_requests:
        raise RemoteReadBudgetExceeded(
            f"remote read budget exceeded: {session.requests} requests "
            f"(max {policy.max_requests})"
        )

    breaker_registry = None
    host = _host_key(uri)
    if policy.use_provider_health:
        try:
            from app.services.data_fabric.circuit_breaker import get_breaker_registry

            breaker_registry = get_breaker_registry()
        except Exception:  # noqa: BLE001 - 健康检查是增值，不是依赖
            breaker_registry = None

    delay = policy.backoff_s
    last_exc: Optional[BaseException] = None
    for attempt in range(1, policy.max_attempts + 1):
        if cancel_token is not None and cancel_token.cancelled:
            raise OperationCancelled(cancel_token.reason or "cancelled during remote read")
        session.requests += 1
        try:
            if breaker_registry is not None:
                arr = breaker_registry.call(
                    f"raster:{host}", lambda: ds.read(band_or_bands, window=window))
            else:
                arr = ds.read(band_or_bands, window=window)
            session.bytes_read += int(arr.dtype.itemsize * arr.size)
            if session.bytes_read > policy.max_bytes:
                raise RemoteReadBudgetExceeded(
                    f"remote read budget exceeded: ~{session.bytes_read} bytes "
                    f"(max {policy.max_bytes})"
                )
            return arr
        except RemoteReadBudgetExceeded:
            raise
        except (RasterioError, OSError, RuntimeError) as exc:
            last_exc = exc
            if not is_transient_remote_error(exc):
                raise
            if attempt >= policy.max_attempts:
                raise
            session.retries += 1
            sleep_s = delay if not policy.jitter else random.uniform(0.0, delay)
            logger.info(
                "[geo_raster.remote] transient read failure from %s (attempt %d/%d): %s",
                host, attempt, policy.max_attempts, exc,
            )
            if cancel_token is not None:
                # 取消等待而非裸 sleep：令牌取消立即可见（有界粒度 0.1s）。
                deadline = time.monotonic() + sleep_s
                while time.monotonic() < deadline:
                    if cancel_token.cancelled:
                        raise OperationCancelled(cancel_token.reason or "cancelled")
                    time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            else:
                time.sleep(sleep_s)
            delay = min(delay * policy.backoff_multiplier, policy.max_backoff_s)
    raise last_exc if isinstance(last_exc, BaseException) else RuntimeError("unreachable")  # pragma: no cover
