"""共享 sync→async 桥（audit ISSUE-056，#1350）。

散落各处的 ``asyncio.run()`` 每次新建/销毁事件循环——连接池
（httpx / asyncpg / SQLAlchemy async engine）无法跨调用复用，且
循环重建本身有成本。本模块提供**每线程持久 loop** 的 ``run_sync``：
FastAPI sync 路由运行在 Starlette 线程池上，同一 worker 线程复用
同一 loop，其上的连接池因此跨请求存活。

纪律：仅 sync 上下文调用；已运行 loop 内调用直接 RuntimeError
（与 asyncio.run 语义一致，杜绝嵌套 loop）。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine

_local = threading.local()


def _get_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _local.loop = loop
    return loop


def run_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """在当前线程的持久事件循环中执行 coroutine 并返回结果。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "run_sync() must not be called from a running event loop; "
            "await the coroutine directly instead"
        )
    return _get_loop().run_until_complete(coro)
