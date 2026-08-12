"""取消传播原语（ADR-0052）。

原状态：``TaskTracker.cancel()`` 只翻一个内存 bool，chat 引擎在「每轮之间」和
「每个工具完成之后」轮询它。后果是用户点 Cancel 后，正在跑的 NDVI / 缓冲区分析
会一路算完（30–60s）才停 —— 取消只是 UI 状态，CPU 并没有释放。

本模块提供贯穿式取消：

    Task API → CancellationRegistry → CancellationToken
                                        ├─ asyncio 侧：await token.wait() 立即唤醒，
                                        │   执行引擎据此 preemptively cancel 在飞的
                                        │   asyncio.Task（真正的抢占）
                                        └─ 线程/CPU 侧：contextvar 携带 token，
                                            GIS 循环在 checkpoint() 处协作退出

为什么用 contextvar 而不是给每个工具加参数：``asyncio.to_thread``（registry 的同步
工具执行路径）会 ``contextvars.copy_context()``，所以 token 能自动穿到工具线程里，
不需要改动几十个 GIS 工具签名。token 用 ``threading.Event`` 承载状态，因此从 worker
线程读写都是安全的。
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import threading
import time
from collections import OrderedDict
from typing import Callable, Iterable, Iterator, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class OperationCancelled(Exception):
    """协作式取消：工具/算法在 checkpoint 处观察到取消后抛出。

    与 ``asyncio.CancelledError`` 区别开来 —— 后者是 asyncio 抢占，会被 asyncio
    机制特殊对待（BaseException，不应被 ``except Exception`` 吞掉）。
    OperationCancelled 是普通 Exception，允许中间层用 finally 清理临时文件，
    再由 job 运行时统一收敛为 cancelled 终态。
    """

    def __init__(self, message: str = "operation cancelled", job_id: str | int | None = None):
        self.job_id = job_id
        super().__init__(message)


class CancellationToken:
    """线程安全 + asyncio 友好的取消信号。

    ``cancel()`` 幂等：重复取消返回 False 且不重复触发回调（规范 §5 双重取消幂等）。
    """

    __slots__ = ("_job_id", "_event", "_reason", "_lock", "_callbacks", "_waiters", "_cancelled_at")

    def __init__(self, job_id: str | int | None = None):
        self._job_id = job_id
        self._event = threading.Event()
        self._reason: str | None = None
        self._cancelled_at: float | None = None
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[CancellationToken], None]] = []
        # (loop, asyncio.Event) 对：跨线程 cancel 时用 call_soon_threadsafe 唤醒
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []

    @property
    def job_id(self) -> str | int | None:
        return self._job_id

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def cancelled_at(self) -> float | None:
        """取消请求被观察到的单调时钟时刻，用于取消延迟度量。"""
        return self._cancelled_at

    def cancel(self, reason: str = "cancelled") -> bool:
        """请求取消。首次调用返回 True，重复调用返回 False（幂等）。"""
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = reason
            self._cancelled_at = time.monotonic()
            self._event.set()
            callbacks = list(self._callbacks)
            waiters = list(self._waiters)

        for loop, ev in waiters:
            try:
                if loop.is_closed():
                    continue
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:
                # loop 已停止：等待者本身也活不下去了，忽略
                continue
        for cb in callbacks:
            try:
                cb(self)
            except Exception:
                logger.warning("[jobs] cancellation callback failed", exc_info=True)
        return True

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise OperationCancelled(self._reason or "operation cancelled", job_id=self._job_id)

    def add_callback(self, callback: Callable[[CancellationToken], None]) -> None:
        """注册取消回调。若已取消则立即同步执行。"""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        try:
            callback(self)
        except Exception:
            logger.warning("[jobs] cancellation callback failed", exc_info=True)

    async def wait(self) -> None:
        """等待取消发生。执行引擎用它抢占式打断在飞的工具任务。"""
        if self._event.is_set():
            return
        loop = asyncio.get_running_loop()
        ev = asyncio.Event()
        with self._lock:
            if self._event.is_set():
                return
            self._waiters.append((loop, ev))
        try:
            await ev.wait()
        finally:
            with self._lock:
                self._waiters = [w for w in self._waiters if w[1] is not ev]

    def link(self, child: "CancellationToken") -> None:
        """父 token 取消时级联取消 child（agent task → 其派生的 durable job）。"""
        self.add_callback(lambda parent: child.cancel(parent.reason or "parent cancelled"))

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return f"<CancellationToken job={self._job_id} cancelled={self.cancelled}>"


#: 当前执行上下文的取消 token。asyncio.to_thread 会复制 context，因此同步 GIS
#: 工具在工作线程里也能读到它。
CURRENT_TOKEN: contextvars.ContextVar[CancellationToken | None] = contextvars.ContextVar(
    "webgis_current_cancellation_token", default=None
)


def current_token() -> CancellationToken | None:
    return CURRENT_TOKEN.get()


@contextlib.contextmanager
def use_token(token: CancellationToken | None) -> Iterator[CancellationToken | None]:
    """在 with 作用域内把 token 绑定到当前执行上下文。"""
    reset = CURRENT_TOKEN.set(token)
    try:
        yield token
    finally:
        CURRENT_TOKEN.reset(reset)


def is_cancelled() -> bool:
    """当前上下文是否已被取消。无 token 时恒为 False。"""
    token = CURRENT_TOKEN.get()
    return bool(token and token.cancelled)


def checkpoint() -> None:
    """协作式取消检查点。已取消则抛 OperationCancelled，否则零开销返回。

    放在长循环的安全边界（chunk / tile / feature batch / raster window 之间）：

        for window in windows:
            checkpoint()
            process(window)

    没有 token 时（普通同步请求路径）这就是一次 ContextVar.get()，可以放心撒。
    """
    token = CURRENT_TOKEN.get()
    if token is not None and token.cancelled:
        raise OperationCancelled(token.reason or "operation cancelled", job_id=token.job_id)


def cancellable(iterable: Iterable[T], every: int = 1) -> Iterator[T]:
    """包装迭代器，在每 ``every`` 个元素处插入 checkpoint()。

    用于把已有的 ``for feature in features:`` 循环变成可取消循环而不改动循环体。
    ``every > 1`` 用于单次迭代极短、checkpoint 相对开销不可忽略的场合。
    """
    if every < 1:
        every = 1
    for index, item in enumerate(iterable):
        if index % every == 0:
            checkpoint()
        yield item


class CancellationRegistry:
    """进程内 job_id → token 映射。

    这是「传信」通道而不是「事实源」：取消的持久事实是
    ``analysis_tasks.cancel_requested_at``（见 store.request_cancel），registry 只负责
    让本进程在飞的执行体立刻感知。因此 API 重启后 registry 为空并不丢取消语义 ——
    worker 通过 DurableCancellationProbe 从 DB 读取。

    LRU 有界，避免长期运行的 API 进程无限增长。
    """

    def __init__(self, max_entries: int = 2048):
        self._max_entries = max_entries
        self._tokens: OrderedDict[str, CancellationToken] = OrderedDict()
        self._lock = threading.Lock()

    def register(self, key: str | int, token: CancellationToken | None = None) -> CancellationToken:
        """获取或创建 key 对应的 token。已存在则返回原 token（保持幂等）。"""
        skey = str(key)
        with self._lock:
            existing = self._tokens.get(skey)
            if existing is not None:
                self._tokens.move_to_end(skey)
                return existing
            tok = token or CancellationToken(job_id=skey)
            self._tokens[skey] = tok
            while len(self._tokens) > self._max_entries:
                # 丢最旧的：它对应的执行体要么已结束，要么会在 DB 探针上观察取消
                self._tokens.popitem(last=False)
            return tok

    def get(self, key: str | int) -> CancellationToken | None:
        with self._lock:
            return self._tokens.get(str(key))

    def cancel(self, key: str | int, reason: str = "cancelled") -> bool:
        """取消 key 对应的 token。key 未注册时返回 False。"""
        token = self.get(key)
        if token is None:
            return False
        return token.cancel(reason)

    def is_cancelled(self, key: str | int) -> bool:
        token = self.get(key)
        return bool(token and token.cancelled)

    def discard(self, key: str | int) -> None:
        with self._lock:
            self._tokens.pop(str(key), None)

    def __len__(self) -> int:  # pragma: no cover - 诊断用
        with self._lock:
            return len(self._tokens)

    def clear(self) -> None:
        """清空 registry。用于测试里模拟进程重启。"""
        with self._lock:
            self._tokens.clear()


#: 全局 registry。Task API 与执行引擎共用同一个实例。
registry = CancellationRegistry()
