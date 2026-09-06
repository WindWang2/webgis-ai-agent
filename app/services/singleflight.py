"""通用 per-key single-flight 协调（ADR-0101 D11，V4 §28）。

防止多个 worker 同时重建同一个昂贵缓存条目（cache stampede）：
- 同 key 并发调用 → 只有 leader 真正执行 builder，其余等待共享结果；
- **builder 崩溃**：异常传播给所有等待者（诚实失败，无人拿到半成品）；
- **超时**：等待者超过 ``wait_timeout`` 放弃等待并自行重建（诚实降级，
  绝不永久卡死）；
- **构建期间失效**：builder 产出后由调用方做权威校验（fingerprint/
  revision 检查在本模块之外 —— 缓存寿命永远不是正确性机制）；
- 有界：max_inflight 之上的 key 直接并发计算（过载降级，不排队）。

与 mvt.SingleFlightManager（asyncio 版）互补：本模块面向线程上下文
（data plane 缓存构建都在线程/同步路径）。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional


class _Entry:
    __slots__ = ("event", "result", "error", "done")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.done = False


class SingleFlight:
    """线程上下文的 per-key single-flight。"""

    def __init__(self, *, max_inflight: int = 1024, wait_timeout: Optional[float] = 30.0):
        self._max_inflight = max_inflight
        self._wait_timeout = wait_timeout
        self._inflight: Dict[Any, _Entry] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._inflight)

    def run(self, key: Any, builder: Callable[[], Any],
            *, version: Optional[Any] = None) -> Any:
        """执行或共享同 key 构建。

        ``version``（可选）：构建完成时的权威版本快照 —— 返回前由调用方
        比较当前版本，不一致则弃用结果重建（失效during-build 的处理
        接口；本模块只负责把它传递出来，不作判定）。
        返回 ``(value, version)`` 元组当 version 参数给出时，否则返回值。
        """
        with self._lock:
            entry = self._inflight.get(key)
            leader = entry is None
            if leader and len(self._inflight) >= self._max_inflight:
                # 过载：直接计算，不注册（无界等待防护）。
                leader = False
                direct = True
            elif leader:
                self._inflight[key] = _Entry()
                direct = False
            else:
                direct = False

        if not leader and not direct:
            wait_ok = entry.event.wait(self._wait_timeout)
            if not wait_ok:
                # 超时：诚实降级为自行重建（不共享未知状态）。
                return self._build_direct(builder, version)
            if entry.error is not None:
                raise entry.error
            return self._wrap(entry.result, version)

        result, error = self._build(builder)
        if leader:
            with self._lock:
                e = self._inflight.pop(key, None)
            if e is not None:
                e.result = result
                e.error = error
                e.done = True
                e.event.set()
        if error is not None:
            raise error
        return self._wrap(result, version)

    @staticmethod
    def _build(builder: Callable[[], Any]) -> tuple:
        try:
            return builder(), None
        except BaseException as exc:  # noqa: BLE001 - 原样传播给等待者
            return None, exc

    def _build_direct(self, builder: Callable[[], Any], version: Optional[Any]) -> Any:
        result, error = self._build(builder)
        if error is not None:
            raise error
        return self._wrap(result, version)

    @staticmethod
    def _wrap(result: Any, version: Optional[Any]) -> Any:
        if version is not None:
            return (result, version)
        return result
