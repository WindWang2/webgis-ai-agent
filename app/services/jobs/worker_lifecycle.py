"""Worker 生命周期注册面（Platform V4，ADR-0131 D4）。

worker 下线的既有兜底是被动 stale sweep（60s tick + 300s 阈值）。本模块
补上**主动**一侧：

- 进程内注册表：worker 身份（hostname:pid）、状态
  online|draining|offline、活跃任务计数；
- 有界 drain：``deregister_worker`` 先停接新任务的意愿标记，再等在跑
  任务清零，受 ``WORKER_DRAIN_TIMEOUT_S``（默认 20s）deadline 约束——
  到点诚实记录并继续退出，绝不挂死进程；
- Celery signal 挂接：``install_celery_lifecycle_signals`` 把
  worker_ready / worker_shutting_down / worker_shutdown 接到上面三点，
  全部防御性（无信号环境/eager 模式 no-op）。

真相源边界（诚实披露）：durable 真相仍在 job 行 + 心跳 + stale sweep
（跨进程/崩溃恢复唯一权威）；本注册表是**进程内观测与编排面**，崩溃
后状态随进程消失——这正是设计意图（进程没了 = offline，无需清理）。
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: drain deadline（秒）。k8s terminationGracePeriod 是最后防线，进程自己先自觉。
DEFAULT_DRAIN_TIMEOUT_S = 20.0
#: drain 等待轮询步长（有界小步长，chaos 纪律同款）
_DRAIN_POLL_INTERVAL_S = 0.05

WORKER_STATES = ("online", "draining", "offline")


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass
class WorkerLifecycleState:
    worker_id: str
    status: str = "offline"
    started_at: Optional[float] = None
    draining_since: Optional[float] = None
    offline_at: Optional[float] = None
    active_tasks: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "status": self.status,
            "started_at": self.started_at,
            "draining_since": self.draining_since,
            "offline_at": self.offline_at,
            "active_tasks": self.active_tasks,
        }


class WorkerLifecycle:
    """单 worker 进程的生命周期状态（线程安全；Celery prefork 下主进程持有）。"""

    def __init__(self, worker_id: Optional[str] = None,
                 drain_timeout_s: float = DEFAULT_DRAIN_TIMEOUT_S):
        self._lock = threading.Lock()
        self._state = WorkerLifecycleState(
            worker_id=worker_id or default_worker_id()
        )
        self._drain_timeout_s = max(0.0, float(drain_timeout_s))

    # ── 查询 ────────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state.as_dict())

    @property
    def drain_timeout_s(self) -> float:
        return self._drain_timeout_s

    # ── 迁移 ────────────────────────────────────────────────────────

    def register(self) -> Dict[str, Any]:
        """worker 上线（worker_ready）。重复注册幂等（重启同一进程语义）。"""
        with self._lock:
            self._state.status = "online"
            self._state.started_at = time.time()
            self._state.draining_since = None
            self._state.offline_at = None
            snapshot = self._state.as_dict()
        logger.info("[worker-lifecycle] online worker=%s", snapshot["worker_id"])
        return snapshot

    def begin_drain(self) -> Dict[str, Any]:
        """进入 draining（worker_shutting_down）：停接新任务的意愿标记。"""
        with self._lock:
            self._state.status = "draining"
            self._state.draining_since = time.time()
            snapshot = self._state.as_dict()
        logger.info(
            "[worker-lifecycle] draining worker=%s active=%s deadline_s=%s",
            snapshot["worker_id"], snapshot["active_tasks"], self._drain_timeout_s,
        )
        return snapshot

    def mark_task_started(self) -> None:
        with self._lock:
            self._state.active_tasks += 1
            count = self._state.active_tasks
        try:
            from app.lib.observability.metrics import set_worker_active_tasks

            set_worker_active_tasks(count)
        except Exception:  # noqa: BLE001 — 计量失败不影响任务执行
            pass

    def mark_task_finished(self) -> None:
        with self._lock:
            self._state.active_tasks = max(0, self._state.active_tasks - 1)
            count = self._state.active_tasks
        try:
            from app.lib.observability.metrics import set_worker_active_tasks

            set_worker_active_tasks(count)
        except Exception:  # noqa: BLE001
            pass

    def deregister(
        self,
        *,
        reason: str = "shutdown",
        wait_active: bool = True,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> Dict[str, Any]:
        """有界 drain + 注销（worker_shutdown）。

        Args:
            reason: 日志/返回值里的注销原因。
            wait_active: 是否等待活跃任务清零（测试可关）。
            sleep/monotonic: 可注入时钟（测试确定性；生产用默认）。

        Returns:
            摘要 dict：drained（是否在 deadline 内清零）、waited_s、
            active_left、status（恒 offline）。
        """
        started = monotonic()
        self.begin_drain()
        drained = True
        if wait_active:
            while True:
                with self._lock:
                    remaining = self._state.active_tasks
                if remaining <= 0:
                    break
                if monotonic() - started >= self._drain_timeout_s:
                    drained = False
                    logger.warning(
                        "[worker-lifecycle] drain deadline hit worker=%s "
                        "active_left=%s waited_s=%.2f reason=%s",
                        self._state.worker_id, remaining,
                        monotonic() - started, reason,
                    )
                    break
                sleep(_DRAIN_POLL_INTERVAL_S)
        with self._lock:
            self._state.status = "offline"
            self._state.offline_at = time.time()
            waited = monotonic() - started
            summary = {
                "worker_id": self._state.worker_id,
                "reason": reason,
                "drained": drained,
                "waited_s": round(waited, 3),
                "active_left": self._state.active_tasks,
                "status": self._state.status,
            }
        if drained:
            logger.info(
                "[worker-lifecycle] offline worker=%s waited_s=%.3f reason=%s",
                summary["worker_id"], summary["waited_s"], reason,
            )
        return summary


#: 进程级单例（Celery prefork 主进程持有；eager/测试可直接操作）
_lifecycle = WorkerLifecycle()


def get_worker_lifecycle() -> WorkerLifecycle:
    return _lifecycle


def reset_worker_lifecycle_for_tests(worker_id: Optional[str] = None,
                                     drain_timeout_s: float = 1.0) -> WorkerLifecycle:
    global _lifecycle
    _lifecycle = WorkerLifecycle(worker_id=worker_id, drain_timeout_s=drain_timeout_s)
    return _lifecycle


# ── Celery signal 挂接（防御性：无 celery/无信号环境一律 no-op）─────────────


def install_celery_lifecycle_signals(
    celery_app: Any = None,
    lifecycle: Optional[WorkerLifecycle] = None,
) -> bool:
    """把生命周期接到 Celery 信号；成功挂接返回 True（重复挂接幂等）。

    worker_ready → register；worker_shutting_down → begin_drain；
    worker_shutdown → deregister（有界 drain）。任何 import/注册失败都
    诚实返回 False——eager 模式与测试环境没有 worker 信号，属正常路径。
    """
    lifecycle = lifecycle or get_worker_lifecycle()
    try:
        from celery.signals import (
            worker_ready,
            worker_shutdown,
            worker_shutting_down,
        )

        @worker_ready.connect
        def _on_worker_ready(**_kwargs):
            try:
                lifecycle.register()
            except Exception:  # noqa: BLE001
                logger.warning("[worker-lifecycle] register failed", exc_info=True)

        @worker_shutting_down.connect
        def _on_worker_shutting_down(**_kwargs):
            try:
                lifecycle.begin_drain()
            except Exception:  # noqa: BLE001
                logger.warning("[worker-lifecycle] begin_drain failed", exc_info=True)

        @worker_shutdown.connect
        def _on_worker_shutdown(**_kwargs):
            try:
                lifecycle.deregister(reason="celery worker_shutdown")
            except Exception:  # noqa: BLE001
                logger.warning("[worker-lifecycle] deregister failed", exc_info=True)

        return True
    except Exception:  # noqa: BLE001 — 无 celery 环境（理论不可达，防御）
        logger.info("[worker-lifecycle] celery signals unavailable; skipped")
        return False


__all__ = [
    "DEFAULT_DRAIN_TIMEOUT_S",
    "WORKER_STATES",
    "WorkerLifecycle",
    "WorkerLifecycleState",
    "get_worker_lifecycle",
    "reset_worker_lifecycle_for_tests",
    "install_celery_lifecycle_signals",
    "default_worker_id",
]
