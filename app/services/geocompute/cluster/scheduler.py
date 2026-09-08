"""GeoCompute V6 cluster coordinator：持久调度循环（wave 3-5/9/10）。

职责（全部经 cluster.store 的 CAS，无进程内权威状态）：
- **leadership**：epoch fencing 当选/续约；落选 → standby（只观察）；
- **reclaim**：过期 lease 回收（attempt 预算内回队重试 / 耗尽 failed
  [WORKER_LOSS]），账本精确归还；
- **dispatch**：fairness 轮转选取 → ``claim_lease``（含账本 reserve）→
  本地线程执行（``run_plan_sync`` 全链路：governor/预算/复用/分类重试）；
- **heartbeat watchdog**：每 run 心跳线程 —— fencing 失败（lease 易主）
  立即点燃本地取消（停写）；观察持久取消/抢占旗标；
- **preemption**：高优先级等待超时 → 对最低优先级的在跑 run 请求让出
  （持久旗标，跨 coordinator 生效）；执行侧只在节点边界让出。

诚实边界：
- 双 leader 极窄窗口（PG READ COMMITTED）不损坏状态 —— 所有写路径另有
  run 级 epoch CAS（store.acquire_leadership docstring）；
- 执行体在 lease 丢失后到本地取消生效前可能与新 attempt 短暂重叠 ——
  节点级幂等键 + 缓存/证据幂等覆盖使后果有界（与 Celery visibility
  timeout 同级取舍，known limitation 已记录）。
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

from app.lib.cancellation import CancellationToken, OperationCancelled
from app.services.geocompute.cluster.contracts import (
    DEFAULT_MAX_RUN_ATTEMPTS,
    MAX_PREEMPTS,
    ClusterRunStatus,
)
from app.services.geocompute.cluster.fairness import fair_pick, matches_profiles
from app.services.geocompute.cluster.store import ClusterLedger, ClusterRunStore
from app.services.geocompute.errors import GeoComputeError

logger = logging.getLogger(__name__)

#: 默认心跳间隔（取消/抢占延迟的下界 ≈ 该间隔 + checkpoint 粒度）。
DEFAULT_HEARTBEAT_INTERVAL_S = 0.5
#: 默认 leadership TTL 与 run lease TTL。
DEFAULT_LEADERSHIP_TTL_S = 15.0
DEFAULT_LEASE_TTL_S = 30.0
#: 高优先级等待多久才允许发起抢占（防抖；避免抢占风暴）。
DEFAULT_PREEMPT_WAIT_S = 5.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class _RunExecution:
    """单次执行的本地句柄（有界：随 claim 创建、随终态移除）。"""

    run_id: str
    epoch: int
    token: CancellationToken
    yield_event: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    stop_heartbeat: threading.Event = field(default_factory=threading.Event)


class ClusterCoordinator:
    """一个进程一个实例；``tick()`` 同步可测，``run_forever()`` 阻塞循环。"""

    def __init__(
        self,
        *,
        store: Optional[ClusterRunStore] = None,
        coordinator_id: Optional[str] = None,
        ledger: Optional[ClusterLedger] = None,
        local_slots: Optional[int] = None,
        leadership_ttl_s: float = DEFAULT_LEADERSHIP_TTL_S,
        lease_ttl_s: float = DEFAULT_LEASE_TTL_S,
        heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
        tick_interval_s: float = 0.25,
        batch_size: int = 32,
        max_run_attempts: int = DEFAULT_MAX_RUN_ATTEMPTS,
        preempt_wait_s: float = DEFAULT_PREEMPT_WAIT_S,
        max_preempts: int = MAX_PREEMPTS,
        governor: Optional[Any] = None,
        engine: Optional[Any] = None,
    ):
        self._store = store or ClusterRunStore()
        self._coordinator_id = coordinator_id or (
            f"coord-{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        )
        self._ledger = ledger
        self._local_slots = max(1, int(local_slots or _env_int("WEBGIS_COORDINATOR_SLOTS", 2)))
        self._leadership_ttl_s = float(leadership_ttl_s)
        self._lease_ttl_s = float(lease_ttl_s)
        self._heartbeat_interval_s = float(heartbeat_interval_s)
        self._tick_interval_s = float(tick_interval_s)
        self._batch_size = max(1, int(batch_size))
        self._max_run_attempts = max(1, int(max_run_attempts))
        self._preempt_wait_s = float(preempt_wait_s)
        self._max_preempts = int(max_preempts)
        self._governor = governor
        self._engine = engine
        self._pool = ThreadPoolExecutor(
            max_workers=self._local_slots, thread_name_prefix="geocompute-cluster"
        )
        self._inflight: dict[str, _RunExecution] = {}
        self._lock = threading.Lock()
        self._leadership_epoch: Optional[int] = None
        self._stop = threading.Event()

    @property
    def coordinator_id(self) -> str:
        return self._coordinator_id

    @property
    def is_leader(self) -> bool:
        return self._leadership_epoch is not None

    # ------------------------------------------------------------- tick

    def tick(self) -> dict[str, Any]:
        """一次调度循环（同步；测试直接调用）。返回有界统计投影。"""
        stats: dict[str, Any] = {
            "leader": False,
            "reclaimed": 0,
            "dispatched": 0,
            "preempt_requests": 0,
            "inflight": len(self._inflight),
        }
        self._renew_or_acquire_leadership()
        stats["leader"] = self.is_leader
        if not self.is_leader:
            return stats

        reclaimed = self._store.reclaim_expired(
            limit=self._batch_size,
            max_attempts=self._max_run_attempts,
            ledger=self._ledger,
        )
        stats["reclaimed"] = len(reclaimed)
        cancelled = self._store.cancel_flagged(limit=self._batch_size)
        stats["cancel_swept"] = len(cancelled)
        self._store.prune_workers()
        if not self._renew_leadership():
            # 续约失败（failover 已发生）→ 本轮起卸任；在跑 run 由心跳
            # 线程正常完成，其终态写路径自带 epoch fencing。
            self._leadership_epoch = None
            return stats

        self._dispatch(stats)
        stats["preempt_requests"] = self._maybe_preempt()
        stats["inflight"] = len(self._inflight)
        return stats

    def run_forever(self) -> None:
        """阻塞调度循环（DB 异常退避重试；``stop()`` 退出）。"""
        backoff = self._tick_interval_s
        while not self._stop.is_set():
            try:
                self.tick()
                backoff = self._tick_interval_s
            except Exception:  # noqa: BLE001 - DB 瞬断不 crash 调度者
                logger.warning(
                    "[geocompute-v6] coordinator tick failed; backing off %.2fs",
                    backoff, exc_info=True,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 5.0)
                continue
            self._stop.wait(self._tick_interval_s)

    def stop(self) -> None:
        self._stop.set()
        for exec_state in list(self._inflight.values()):
            exec_state.token.cancel("coordinator stopping")

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        """等待全部在跑 run 落定（测试/优雅停机用）。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        while self._inflight:
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(0.02)
        return True

    # ------------------------------------------------------- leadership

    def _renew_or_acquire_leadership(self) -> None:
        if self._leadership_epoch is None:
            self._leadership_epoch = self._store.acquire_leadership(
                self._coordinator_id, ttl_s=self._leadership_ttl_s
            )

    def _renew_leadership(self) -> bool:
        assert self._leadership_epoch is not None
        ok = self._store.renew_leadership(
            self._coordinator_id, epoch=self._leadership_epoch,
            ttl_s=self._leadership_ttl_s,
        )
        if not ok:
            logger.warning(
                "[geocompute-v6] leadership lost (fenced); standing by: %s",
                self._coordinator_id,
            )
        return ok

    # --------------------------------------------------------- dispatch

    def _available_profiles(self) -> Optional[frozenset[str]]:
        """存活 worker 覆盖的 profile 并集；eager 模式 → None（本地全可执行）。"""
        try:
            from app.services.task_queue import celery_app

            if celery_app.conf.task_always_eager:
                return None
        except Exception:  # noqa: BLE001 - conf 不可读按真实 broker 处理
            pass
        covered: set[str] = set()
        for w in self._store.live_workers(role="worker"):
            covered |= set(w.get("profiles") or {})
        return frozenset(covered)

    def _dispatch(self, stats: dict[str, Any]) -> None:
        free = self._local_slots - len(self._inflight)
        if free <= 0:
            return
        candidates = self._store.scan_dispatchable(limit=self._batch_size)
        available = self._available_profiles()
        # 通道能力匹配：必需 profile 无存活通道的 run 留队（消除队头阻塞
        # —— 其余 run 不被单个无通道 run 卡住）。
        candidates = [
            c for c in candidates
            if matches_profiles(c.get("required_profiles"), available)
        ]
        picked = fair_pick(
            candidates, slots=free,
            last_dispatch=self._store.tenant_last_dispatch(),
        )
        for row in picked:
            run_id = row["run_id"]
            epoch = self._store.claim_lease(
                run_id,
                coordinator_id=self._coordinator_id,
                ttl_s=self._lease_ttl_s,
                max_attempts=self._max_run_attempts,
                ledger=self._ledger,
            )
            if epoch is None:
                continue  # 竞争失败/账本拒绝（enforcing）→ 留队下轮再试
            exec_state = _RunExecution(run_id=run_id, epoch=epoch,
                                       token=CancellationToken(job_id=run_id))
            with self._lock:
                self._inflight[run_id] = exec_state
            if not self._store.mark_running(run_id, epoch=epoch):
                # CAS 失败（被并发转移）→ 不执行
                with self._lock:
                    self._inflight.pop(run_id, None)
                continue
            self._start_heartbeat(exec_state)
            self._pool.submit(self._execute_run, exec_state)
            stats["dispatched"] += 1

    # -------------------------------------------------------- execution

    def _start_heartbeat(self, exec_state: _RunExecution) -> None:
        def _loop() -> None:
            while not exec_state.stop_heartbeat.wait(self._heartbeat_interval_s):
                try:
                    ok = self._store.heartbeat_run(
                        exec_state.run_id, epoch=exec_state.epoch,
                        ttl_s=self._lease_ttl_s,
                    )
                    if not ok:
                        # fencing：lease 已易主（本执行体被 reclaim）→
                        # 立即停写停算；终态由 reclaim/new attempt 所有。
                        exec_state.token.cancel("run lease lost")
                        exec_state.stop_heartbeat.set()
                        return
                    row = self._store.get_run(exec_state.run_id)
                    if row is None:
                        continue
                    if row.get("cancel_requested_at"):
                        exec_state.token.cancel("cancelled via control plane")
                    if row.get("yield_requested_at"):
                        exec_state.yield_event.set()
                except Exception:  # noqa: BLE001 - DB 抖动由 lease TTL 容纳
                    logger.warning(
                        "[geocompute-v6] heartbeat failed run_id=%s",
                        exec_state.run_id, exc_info=True,
                    )

        threading.Thread(target=_loop, name=f"gc-v6-hb-{exec_state.run_id}",
                         daemon=True).start()

    def _execute_run(self, exec_state: _RunExecution) -> None:
        """工作线程：重建计划 → run_plan_sync 全链路 → fenced 终态落库。"""
        run_id = exec_state.run_id
        terminal_status: ClusterRunStatus = ClusterRunStatus.FAILED
        error_code: Optional[str] = None
        try:
            internal = self._store.get_run_internal(run_id)
            if internal is None:
                return
            from app.services.geocompute.api import run_plan_sync
            from app.services.geocompute.plan import ExecutionPlan, ExecutionRunStatus

            plan = ExecutionPlan.model_validate(internal["plan_snapshot"])
            caller: Optional[dict[str, Any]] = None
            if internal.get("creator_id"):
                caller = {"user_id": internal["creator_id"],
                          "org_id": internal.get("org_id")}
            run = run_plan_sync(
                plan,
                session_id=internal.get("session_id"),
                cancel_token=exec_state.token,
                caller=caller,
                governor=self._governor,
                project_id=internal.get("project_key"),
                run_id=run_id,
                yield_check=exec_state.yield_event.is_set,
                owner_scope_override=internal.get("owner_scope"),
            )
            status_map = {
                ExecutionRunStatus.COMPLETED: ClusterRunStatus.COMPLETED,
                ExecutionRunStatus.CANCELLED: ClusterRunStatus.CANCELLED,
                ExecutionRunStatus.PREEMPTED: ClusterRunStatus.PREEMPTED,
            }
            terminal_status = status_map.get(
                run.status, ClusterRunStatus.FAILED
            )
            error_code = run.error_code
        except OperationCancelled:
            terminal_status = ClusterRunStatus.CANCELLED
        except GeoComputeError as exc:
            terminal_status = ClusterRunStatus.FAILED
            error_code = exc.code
            logger.warning(
                "[geocompute-v6] run failed run_id=%s code=%s", run_id, exc.code
            )
        except Exception:  # noqa: BLE001 - 类型化收编，绝不带异常死线程
            terminal_status = ClusterRunStatus.FAILED
            error_code = "NODE_FAILED"
            logger.exception("[geocompute-v6] run crashed run_id=%s", run_id)
        finally:
            exec_state.stop_heartbeat.set()
            self._finish(exec_state, terminal_status, error_code)

    def _finish(
        self,
        exec_state: _RunExecution,
        status: ClusterRunStatus,
        error_code: Optional[str],
    ) -> None:
        run_id, epoch = exec_state.run_id, exec_state.epoch
        try:
            ok = self._store.finish_run(
                run_id, epoch=epoch, status=status, error_code=error_code,
                ledger=self._ledger,
            )
            if ok and status == ClusterRunStatus.PREEMPTED:
                # 两段：PREEMPTED 可驻留（客户端可见）→ 立即回队尾重排。
                self._store.requeue_preempted(run_id, epoch=epoch)
            if not ok:
                # lease 已易主：本地结果诚实丢弃（fencing 生效的证据）。
                logger.warning(
                    "[geocompute-v6] fenced terminal write dropped run_id=%s "
                    "status=%s", run_id, status.value,
                )
        finally:
            with self._lock:
                self._inflight.pop(run_id, None)
            exec_state.done.set()

    # -------------------------------------------------------- preemption

    def _maybe_preempt(self) -> int:
        """高优先级等待 → 对低优先级在跑 run 请求让出（持久旗标）。

        条件：存在 priority 更高的可派发 run 等待 ≥ preempt_wait_s（以
        created_at 计）且本地槽位满（有等待者）；受害者 = 优先级低于等待
        者的在跑 run 中优先级最低者（同优先级不抢）；preempts 已达保险丝
        的 run 受保护（防抢占 livelock）。
        """
        running = self._store.scan_running(limit=self._batch_size)
        waiting = self._store.scan_dispatchable(limit=self._batch_size)
        if not running or not waiting:
            return 0
        # 只有当高优先级等待者无法立即获得槽位时才抢占：本地满员即可
        # （多 coordinator 时各自决定，语义一致且单调）。
        if len(self._inflight) < self._local_slots:
            return 0
        now = time.time()
        requests = 0
        for waiter in waiting:
            wait_s = self._wait_seconds(waiter, now)
            if wait_s < self._preempt_wait_s:
                continue
            victim = next(
                (
                    r for r in running
                    if int(r.get("priority") or 0) < int(waiter.get("priority") or 0)
                    and int(r.get("preempts") or 0) < self._max_preempts
                ),
                None,
            )
            if victim is None:
                continue
            if self._store.request_yield(victim["run_id"]):
                requests += 1
                tracing_preempt(victim["run_id"], waiter["run_id"])
        return requests

    @staticmethod
    def _wait_seconds(row: dict[str, Any], now: float) -> float:
        created = row.get("created_at")
        if not created:
            return 0.0
        try:
            from datetime import datetime

            ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            return max(0.0, now - ts.timestamp())
        except ValueError:
            return 0.0


def tracing_preempt(victim_run_id: str, waiter_run_id: str) -> None:
    """抢占决策的结构化日志（有界；trace ring 不承载控制面事件）。"""
    logger.info(
        "[geocompute-v6] preemption requested victim=%s waiter=%s",
        victim_run_id, waiter_run_id,
    )


#: 进程级默认 coordinator（懒构建；WEBGIS_CLUSTER_COORDINATOR=1 时由
#: lifespan 启动 —— 默认关闭，单进程部署行为与 V5 一致）。
_coordinator: Optional[ClusterCoordinator] = None
_coordinator_lock = threading.Lock()


def get_coordinator() -> Optional[ClusterCoordinator]:
    global _coordinator
    if _coordinator is None:
        with _coordinator_lock:
            if _coordinator is None:
                if os.environ.get("WEBGIS_CLUSTER_COORDINATOR", "").strip() != "1":
                    return None
                enforcing = (
                    os.environ.get("WEBGIS_CLUSTER_LEDGER_ENFORCING", "").strip() == "1"
                )
                _coordinator = ClusterCoordinator(
                    ledger=ClusterLedger(enforcing=enforcing),
                    governor=_production_governor(),
                )
    return _coordinator


def _production_governor() -> Any:
    """生产 GOVERNOR（与 api.run_plan_sync 默认一致；测试可注入替身）。"""
    try:
        from app.services.geocompute.api import GOVERNOR

        return GOVERNOR
    except Exception:  # noqa: BLE001 - governor 不可用 → 无治理直跑（诚实日志）
        logger.warning("[geocompute-v6] GOVERNOR unavailable; executing ungoverned")
        return None


def coordinator_id_now() -> str:
    return f"coord-{socket.gethostname()}:{os.getpid()}"
