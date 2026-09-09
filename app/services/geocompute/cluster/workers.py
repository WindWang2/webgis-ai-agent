"""GeoCompute V6 worker 注册与心跳（wave 7）。

celery worker 进程经 celery signals 接入集群：
- ``worker_ready``：注册 ``geocompute_workers`` 行（profile → 槽位，env
  ``WEBGIS_WORKER_PROFILE_SLOTS`` 可配）+ 启动心跳 daemon 线程；
- ``worker_shutdown``：停线程 + 注销（主动离开）；
- 失联（心跳停止）由 coordinator tick 的 ``prune_workers`` 收敛 ——
  集群容量随之收缩（通道匹配不再向死通道派发）。

诚实边界：心跳线程是 worker 主进程内的 daemon —— 与 jobs 子系统的
``CancelWatchdog`` 同一进程内线程惯例；eager（无真实 worker）下信号
永不触发，零行为影响。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
from typing import Optional

from app.services.geocompute.cluster.contracts import WorkerCapability
from app.services.geocompute.cluster.store import ClusterRunStore

logger = logging.getLogger(__name__)

#: 心跳间隔（容量可见性的上界延迟；prune cutoff 是它的 3 倍）。
WORKER_HEARTBEAT_INTERVAL_S = 10.0
_WORKER_TTL_S = WORKER_HEARTBEAT_INTERVAL_S * 3

_signals_connected = False


def worker_profile_slots() -> dict[str, int]:
    """worker 消费的 profile → 槽位（env JSON；默认全部 profile 各 1）。

    与 docker-compose 单 worker 消费全部队列的部署对齐；按 profile 拆
    worker 时运维只改 env（通道隔离随之真实生效）。
    """
    from app.services.geocompute.durable import EXECUTION_QUEUE_PROFILES

    raw = os.environ.get("WEBGIS_WORKER_PROFILE_SLOTS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {
                    str(k): max(0, int(v)) for k, v in parsed.items()
                    if int(v) > 0
                }
        except (ValueError, TypeError):
            logger.warning("[geocompute-v6] invalid WEBGIS_WORKER_PROFILE_SLOTS; "
                           "using default all-profiles=1")
    return {profile: 1 for profile in EXECUTION_QUEUE_PROFILES}


def celery_worker_id() -> str:
    return f"celery-{socket.gethostname()}:{os.getpid()}"


class WorkerHeartbeatThread(threading.Thread):
    """daemon 心跳线程（worker 进程内；异常退避，绝不 crash worker）。"""

    def __init__(self, worker_id: str, profiles: dict[str, int],
                 interval_s: float = WORKER_HEARTBEAT_INTERVAL_S,
                 store: Optional[ClusterRunStore] = None):
        super().__init__(name=f"gc-v6-worker-hb-{worker_id}", daemon=True)
        self._worker_id = worker_id
        self._profiles = profiles
        self._interval_s = max(1.0, float(interval_s))
        self._store = store or ClusterRunStore()
        self._stop = threading.Event()

    def run(self) -> None:
        backoff = self._interval_s
        while not self._stop.wait(self._interval_s):
            try:
                self._store.upsert_worker(
                    self._worker_id, role="worker", profiles=self._profiles,
                    ttl_s=_WORKER_TTL_S,
                )
                backoff = self._interval_s
            except Exception:  # noqa: BLE001 - DB 抖动不 crash worker
                logger.warning("[geocompute-v6] worker heartbeat failed: %s",
                               self._worker_id, exc_info=True)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)

    def stop(self) -> None:
        self._stop.set()


_thread: Optional[WorkerHeartbeatThread] = None
_thread_lock = threading.Lock()


def connect_celery_signals() -> bool:
    """注册 celery 生命周期信号（幂等；eager/测试导入零副作用）。"""
    global _signals_connected, _thread
    if _signals_connected:
        return True
    try:
        from celery.signals import worker_ready, worker_shutdown
    except Exception:  # noqa: BLE001 - celery 不可用 → 不接入
        return False

    def _on_ready(**_):
        global _thread
        worker_id = celery_worker_id()
        profiles = worker_profile_slots()
        capability = _probe_capability_safe()
        cap = WorkerCapability(worker_id=worker_id, role="worker",
                               profiles=profiles)
        try:
            ClusterRunStore().upsert_worker(
                cap.worker_id, role=cap.role, profiles=cap.profiles,
                capability=capability,
                ttl_s=_WORKER_TTL_S,
            )
        except Exception:  # noqa: BLE001 - 注册失败不影响 worker 启动
            logger.warning("[geocompute-v6] worker register failed: %s",
                           worker_id, exc_info=True)
        with _thread_lock:
            if _thread is None or not _thread.is_alive():
                _thread = WorkerHeartbeatThread(worker_id, profiles)
                _thread.start()
        logger.info("[geocompute-v6] worker registered: %s profiles=%s",
                    worker_id, sorted(profiles))

    def _on_shutdown(**_):
        global _thread
        with _thread_lock:
            if _thread is not None:
                _thread.stop()
                _thread = None
        try:
            worker_id = celery_worker_id()
            ClusterRunStore().remove_worker(worker_id)
            # V7 级联：清掉本 worker 的对象缓存位置声明（防幽灵位置）
            try:
                from app.services.geocompute.cluster.locality import (
                    WorkerCacheRegistry,
                )

                WorkerCacheRegistry().drop_worker(worker_id)
            except Exception:  # noqa: BLE001 - 级联失败 = miss 方向安全
                pass
        except Exception:  # noqa: BLE001 - 尽力注销；失联 prune 兜底
            pass

    # V7 P1 修复（V6 潜伏缺陷）：kombu Signal.connect 默认 ``weak=True``，
    # 闭包 handler 的唯一引用是弱引用 —— connect_celery_signals 返回后
    # handler 即被 GC，``worker_ready`` 永不触发。后果：真实 worker 的
    # 注册/心跳从未生效（集群容量恒为空视图），而 V6 全部测试都是 eager，
    # real-services lane 也从未跑过 geocompute worker —— 直到 V7
    # real-broker E2E 才暴露。必须显式 ``weak=False`` 强引用注册。
    worker_ready.connect(_on_ready, weak=False)
    worker_shutdown.connect(_on_shutdown, weak=False)
    _signals_connected = True
    return True


def _probe_capability_safe() -> Optional[dict]:
    """V7：能力剖面探针（worker_ready 一次；失败 → None = V6 语义）。"""
    try:
        from app.services.geocompute.cluster.capabilities import (
            capability_json,
            probe_capability,
        )

        return capability_json(probe_capability())
    except Exception:  # noqa: BLE001 - 探针绝不阻断注册
        logger.warning("[geocompute-v7] capability probe failed; "
                       "registering profiles-only", exc_info=True)
        return None
