"""平台观测指标（Platform V4，ADR-0131 D4/D5）。

prometheus-client 原语，注册进默认 REGISTRY，随既有 instrumentator 的
/metrics 暴露。**封闭标签词表**纪律（同 sre_metrics/auth_metrics）：
这里没有任何用户数据标签——高基数诊断走结构化日志与 status 端点。

- ``app_inflight_requests``：进行中请求计数（drain 可观测性的核心——
  shutdown 时它归零即排空完成）；
- ``jobs_worker_active_tasks``：进程内活跃 durable job 计数（与
  worker_lifecycle.active_tasks 同源；prefork 下语义为单进程）。
"""
from __future__ import annotations

import logging

from prometheus_client import Gauge

logger = logging.getLogger(__name__)

_APP_INFLIGHT_REQUESTS = Gauge(
    "app_inflight_requests",
    "In-flight HTTP requests (drain observability: zero means drained).",
)

_JOBS_WORKER_ACTIVE_TASKS = Gauge(
    "jobs_worker_active_tasks",
    "Active durable jobs in this worker process (drain progress signal).",
)


def inc_inflight() -> None:
    _APP_INFLIGHT_REQUESTS.inc()


def dec_inflight() -> None:
    _APP_INFLIGHT_REQUESTS.dec()


def set_worker_active_tasks(count: int) -> None:
    _JOBS_WORKER_ACTIVE_TASKS.set(max(0, int(count)))


class InflightGaugeMiddleware:
    """纯 ASGI 中间件：请求进入 +1，离开（含异常）-1。

    http + websocket 全 scope；计数永不为负的兜底放在 finally。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        inc_inflight()
        try:
            await self.app(scope, receive, send)
        finally:
            dec_inflight()


__all__ = [
    "inc_inflight",
    "dec_inflight",
    "set_worker_active_tasks",
    "InflightGaugeMiddleware",
]
