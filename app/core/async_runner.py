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
from contextlib import asynccontextmanager
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


def get_thread_async_engine():
    """当前线程持久 loop 专属的 AsyncEngine（NullPool，#1437 跨 loop 隔离）。

    全局 AsyncEngine 生产为 QueuePool：asyncpg 连接绑定创建它的 loop，池化
    复用会把主 loop 创建的连接交给线程 loop 上的 run_sync 协程（反之亦然）
    → 间歇性 'Future attached to a different loop' / 挂死（dev/CI 用
    NullPool 永远暴露不了）。经 run_sync 桥接的协程凡需 async DB 会话，
    必须用本工厂：engine 按线程缓存，连接生命周期完全落在当前线程 loop
    内，NullPool 保证绝不跨 loop 复用。主 loop 的 async 路由不要用它
    （连接零复用，热路径性能差——那边用全局 AsyncSessionLocal）。
    """
    eng = getattr(_local, "async_engine", None)
    if eng is None:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import settings
        from app.core.database import _to_async_url

        url = _to_async_url(settings.DATABASE_URL)
        connect_args = (
            {"check_same_thread": False}
            if url.startswith("sqlite+aiosqlite")
            else {}
        )
        eng = create_async_engine(
            url, poolclass=NullPool, connect_args=connect_args,
        )
        _local.async_engine = eng
    return eng


@asynccontextmanager
async def thread_async_session():
    """``async with thread_async_session() as db:`` —— run_sync 协程专用会话。"""
    from sqlalchemy.ext.asyncio import AsyncSession

    session = AsyncSession(get_thread_async_engine())
    try:
        yield session
    finally:
        await session.close()
