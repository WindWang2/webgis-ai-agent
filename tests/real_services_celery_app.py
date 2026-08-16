"""Celery app for the real-services smoke lane (Issue #477).

tests/test_real_services_smoke.py 启动的 worker 子进程通过本模块拿到与
测试进程**同一个** celery app（同 broker/backend、同任务注册表）。
任务必须在这里静态定义：worker 按名字查任务，测试进程里动态装饰的任务
对 worker 是"unregistered"。

broker/backend 用真实 Redis（db 5，与会话数据隔离），由环境变量
REAL_SMOKE_BROKER_URL / REAL_SMOKE_RESULT_BACKEND 注入（默认 localhost）。
"""
import os

from celery import Celery

_default_broker = "redis://localhost:6379/5"

celery_app = Celery(
    "webgis_real_smoke",
    broker=os.environ.get("REAL_SMOKE_BROKER_URL", _default_broker),
    backend=os.environ.get("REAL_SMOKE_RESULT_BACKEND", _default_broker),
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    enable_utc=True,
    timezone="UTC",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    # 快速失败：smoke lane 不等长超时
    broker_connection_timeout=5,
    result_backend_transport_options={"socket_connect_timeout": 5, "socket_timeout": 5},
    broker_transport_options={"socket_connect_timeout": 5, "socket_timeout": 5},
)

# conftest.py 为整套套件强制 CELERY_BROKER_URL=memory://（测试进程离线），
# 而 Celery 的配置级联里环境变量优先于构造参数 —— 不显式覆盖的话本 app
# 会被静默降到 memory broker，worker 与真实 Redis 永远对不上。构造后显式
# 赋值优先级最高。
celery_app.conf.broker_url = os.environ.get("REAL_SMOKE_BROKER_URL", _default_broker)
celery_app.conf.result_backend = os.environ.get("REAL_SMOKE_RESULT_BACKEND", _default_broker)


# 兼容 fixture 命名（test 模块里 celery_worker yield smoke_celery_app）
smoke_celery_app = celery_app


@celery_app.task(name="real_smoke.add")
def add(x, y):
    return x + y


@celery_app.task(name="real_smoke.mul")
def mul(x, y):
    return x * y


@celery_app.task(name="real_smoke.slow")
def slow(seconds):
    import time

    time.sleep(seconds)
    return "done"
