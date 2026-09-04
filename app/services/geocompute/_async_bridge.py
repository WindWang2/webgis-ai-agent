"""geocompute 的 asyncio 桥（并发评审 C1 修复）。

问题：``asyncio.run`` 每次调用新建并销毁一个事件循环 —— Redis 后端下
每次都会错过客户端快路径、重建 client+pool，且全局 ``asyncio.Lock`` 在
跨循环争用时绑定到第一个等待者的循环（第二个循环的等待者抛
``RuntimeError: bound to a different event loop``）。

修复：每个工作线程一个**持久**事件循环（懒创建、永不关闭），协程在该
循环上 ``run_until_complete``。跨线程串行由调用方 ``_SERIAL`` 锁保证
（geocompute 线程之间绝不并发进入会话存储调用 —— 消除本模块引入的
跨循环争用；REST 主循环与本桥循环之间的理论争用是 session_data_redis
既有设计缺陷，见 ADR-0096 Deferred）。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine

_local = threading.local()
#: geocompute 线程对会话存储调用的进程级串行化（materialize/durable 回取
#: 都是短操作；串行消除跨循环 asyncio.Lock 争用）。
_SERIAL = threading.Lock()


def _get_thread_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _local.loop = loop
    return loop


def run_coro_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """在调用线程的持久事件循环上运行协程（替代 asyncio.run）。"""
    with _SERIAL:
        loop = _get_thread_loop()
        return loop.run_until_complete(coro)
