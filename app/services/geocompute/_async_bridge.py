"""geocompute 的 asyncio 桥（并发评审 C1 修复；V7 wave 21 重写）。

历史问题一（C1）：``asyncio.run`` 每次调用新建并销毁事件循环 —— Redis 后端
下每次都会错过客户端快路径、重建 client+pool，且全局 ``asyncio.Lock`` 在
跨循环争用时绑定到第一个等待者的循环。
历史问题二（V7 审计 B1）：V6 的修复用**进程级 ``_SERIAL`` 锁 + 每线程一个
循环** —— 所有 geocompute 线程对会话存储的调用完全串行；coordinator 上 N
个并发 durable 节点的载荷回取互相阻塞（串行度 1）。

V7 方案：**单一专用 event-loop 线程**（daemon，懒启动）+ supervisor +
``asyncio.run_coroutine_threadsafe``：
- 调用线程只阻塞在 ``Future.result(timeout)``（显式有界，默认 30s）——
  loop 线程死亡不会再永久挂死节点线程槽位（V6 方案的挂死面，架构
  round1 #6）；
- loop 内协程协作式并发（IO await 交错）—— 桥调用方之间不再串行
  （消除 ``_SERIAL`` 的进程级串行化）；
- 单一循环语义保持：bridge 调用之间无跨循环 asyncio.Lock 争用（C1）；
- supervisor：调度前 ``is_alive()`` 检查，死亡即整体重建（旧 loop 上的
  悬挂 future 由 result timeout 兜底）；
- 同线程护栏：调用方已在运行中的循环内 → 类型化拒绝（同步等待自己
  的循环 = 死锁面，属编程错误）。

诚实边界（架构 round1 #6 修订）：REST 事件循环 ↔ bridge loop 的跨循环
争用（session_data 的 asyncio.Lock 首用绑定 loop）依旧存在 —— ADR-0096
Deferred 既有结论；本模块只消除 ``_SERIAL`` 引入的进程级串行与挂死面。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Coroutine, Optional

#: 桥调用的默认上界（载荷回取是短 IO；超时类型化 BridgeTimeoutError，
#: 绝不永久占用节点线程槽位）。
DEFAULT_BRIDGE_TIMEOUT_S = 30.0


class BridgeTimeoutError(TimeoutError):
    """桥调用超时（loop 拥塞或协程悬挂；调用方按类型化失败处理）。"""


class _BridgeLoop:
    """专用 event-loop 线程（懒启动 + supervisor 重建）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    def _ensure(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if (
                self._loop is not None
                and self._thread is not None
                and self._thread.is_alive()
                and not self._loop.is_closed()
            ):
                return self._loop
            # supervisor：loop/线程死亡 → 整体重建（旧 future 由超时兜底）
            ready = threading.Event()
            loop_holder: list[asyncio.AbstractEventLoop] = []

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop_holder.append(loop)
                ready.set()
                try:
                    loop.run_forever()
                finally:
                    try:
                        loop.close()
                    except Exception:  # noqa: BLE001 - 关闭失败不再抛
                        pass

            thread = threading.Thread(
                target=_run, name="geocompute-bridge-loop", daemon=True,
            )
            thread.start()
            ready.wait(timeout=10.0)
            if not loop_holder:
                raise RuntimeError("geocompute bridge loop failed to start")
            self._loop = loop_holder[0]
            self._thread = thread
            return self._loop


_bridge = _BridgeLoop()


def run_coro_sync(
    coro: Coroutine[Any, Any, Any],
    timeout_s: float = DEFAULT_BRIDGE_TIMEOUT_S,
) -> Any:
    """在 geocompute 专用循环上运行协程并同步等待（有界；替代 asyncio.run）。

    与 V6 的 ``_SERIAL`` 方案不同：多个调用线程的协程在专用循环上**并发**
    交错执行（IO await 点让出），桥不再是进程级串行点。超时 →
    :class:`BridgeTimeoutError`（协程仍在循环上悬挂 —— 由超时上界把
    「loop 线程死亡 → 调用线程永久挂死」压缩为有界失败）。
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        coro.close()
        raise RuntimeError(
            "run_coro_sync called from within a running event loop; "
            "await the coroutine directly instead"
        )
    loop = _bridge._ensure()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return fut.result(timeout=max(0.1, float(timeout_s)))
    except concurrent.futures.TimeoutError as exc:
        raise BridgeTimeoutError(
            f"geocompute bridge call exceeded {timeout_s}s"
        ) from exc
