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
from app.services.geocompute.cluster.fairness import fair_pick
from app.services.geocompute.cluster.metrics import (
    record_gpu_fallback,
    record_oom_avoided,
    record_queue_wait_s,
    record_resource_rejection,
)
from app.services.geocompute.cluster.store import ClusterLedger, ClusterRunStore
from app.services.geocompute.errors import GeoComputeError

logger = logging.getLogger(__name__)


def _utcnow_naive():
    """repo 约定的 naive UTC now（store 同款；绝不吃服务器本地时区）。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_naive(value: Any):
    """DB 时刻字符串/对象 → naive datetime（解析失败 → None）。"""
    from datetime import datetime

    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return None


def _created_ts(row: dict[str, Any]) -> float:
    """扫描投影的 created_at → time.time() 域（解析失败 → now，计 0 等待）。"""
    from datetime import timezone

    parsed = _parse_naive(row.get("created_at"))
    if parsed is None:
        return time.time()
    return parsed.replace(tzinfo=timezone.utc).timestamp()

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
        retention_s: float = 24 * 3600.0,
        gpu_fallback_wait_s: Optional[float] = None,
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
        self._retention_s = float(retention_s)
        self._governor = governor
        self._engine = engine
        # V7：分布式事件（fail-open）+ 对象缓存注册表（placement 局部性）
        from app.services.geocompute.cluster.events import RunEventStore
        from app.services.geocompute.cluster.locality import WorkerCacheRegistry

        self._events = RunEventStore()
        self._cache_registry = WorkerCacheRegistry()
        #: worker 缓存位置声明 TTL（默认 24h；env 可调）。
        self._cache_ttl_s = _env_float("WEBGIS_WORKER_CACHE_TTL_S", 24 * 3600.0)
        #: waiting_resource 事件存在性去重的进程内缓存（有界：≤ batch；
        #: 跨进程由 events.exists 兜底）。
        self._waiting_noted: set[str] = set()
        #: V8：GPU fallback 等待窗（构造参数优先；缺省 env
        #: ``WEBGIS_GPU_FALLBACK_WAIT_S``=30s。GPU run 声明 fallback_cpu 且
        #: 窗内一直没有 GPU worker → 剥离 gpu 要求改派 CPU。0 = 立即回退；
        #: 负值 = 禁用回退 —— GPU run 无限期留队等待 GPU worker（V7 语义）。
        self._gpu_fallback_wait_s = float(
            gpu_fallback_wait_s
            if gpu_fallback_wait_s is not None
            else _env_float("WEBGIS_GPU_FALLBACK_WAIT_S", 30.0)
        )
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
        # M6：续约校验必须先于一切破坏性 sweep —— 过期 leader 不带权威跑
        # reclaim/cancel（虽有 run 级 epoch CAS 兜底，语义上先自证再动手）。
        if not self._renew_leadership():
            # 续约失败（failover 已发生）→ 本轮起卸任；在跑 run 由心跳
            # 线程正常完成，其终态写路径自带 epoch fencing。
            self._leadership_epoch = None
            return stats

        reclaimed = self._store.reclaim_expired(
            limit=self._batch_size,
            max_attempts=self._max_run_attempts,
            ledger=self._ledger,
        )
        stats["reclaimed"] = len(reclaimed)
        cancelled = self._store.cancel_flagged(limit=self._batch_size)
        stats["cancel_swept"] = len(cancelled)
        for run_id in cancelled:
            record_cancellation_latency(run_id, self._store)
            self._emit_run_event(run_id, "run_cancelled", status="swept")
        # V7：straggler 探测（run 级心跳滞后；一次性事件）
        stats["stragglers"] = self._detect_stragglers()
        pruned = self._store.prune_workers(ttl_s=self._worker_ttl_s())
        if pruned:
            stats["pruned_workers"] = len(pruned)
            self._drop_worker_caches(pruned)
        # M1：终态行 retention（分帧删除，每 tick 有界批）+ V7 级联 events
        self._store.purge_terminal(older_than_s=self._retention_s, limit=64)
        # V7：孤儿事件 TTL 兜底（不依赖 run 行存活）+ 缓存位置声明 TTL
        self._events.purge_older_than(older_than_s=self._retention_s, limit=256)
        if self._cache_registry is not None:
            self._cache_registry.purge_older_than(
                older_than_s=self._cache_ttl_s, limit=64
            )

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

    def _worker_ttl_s(self) -> float:
        """worker 容量单一 TTL 来源（与心跳线程同一真相，审计 M1）。"""
        try:
            from app.services.geocompute.cluster import workers as _w

            return _w._WORKER_TTL_S
        except Exception:  # noqa: BLE001
            return 30.0

    def _eligible_workers(self) -> Optional[list[dict[str, Any]]]:
        """存活 worker 投影；eager 模式 → None（本地全可执行，免放置）。"""
        try:
            from app.services.task_queue import celery_app

            if celery_app.conf.task_always_eager:
                return None
        except Exception:  # noqa: BLE001 - conf 不可读按真实 broker 处理
            pass
        return self._store.live_workers(role="worker")

    def _dispatch(self, stats: dict[str, Any]) -> None:
        free = self._local_slots - len(self._inflight)
        if free <= 0:
            return
        candidates = self._store.scan_dispatchable(limit=self._batch_size)
        workers = self._eligible_workers()
        picked: list[dict[str, Any]] = []
        within_tenant_key = None
        if workers is None:
            # eager：本地全可执行（V6 语义 —— 逐字节兼容）
            picked = fair_pick(
                candidates, slots=free,
                last_dispatch=self._store.tenant_last_dispatch(),
            )
        else:
            from app.services.geocompute.cluster.contracts import ResourceRequest
            from app.services.geocompute.cluster.placement import (
                eligible_workers,
                request_from_run_row,
                scarcity_rank_key,
            )

            # V8 GPU fallback：声明 fallback_cpu 的 GPU run 等过等待窗且
            # 集群里**一个存活 GPU worker 都没有** → 持久剥离 gpu 要求
            # （下一 tick 走 CPU 路径）。诚实可见：事件 + metric。
            if self._gpu_fallback_wait_s >= 0:
                has_gpu_worker = any(
                    int(((w.get("capability") or {}).get("gpu_count")) or 0) > 0
                    for w in workers
                )
                if not has_gpu_worker:
                    now_ts = time.time()
                    for c in candidates:
                        req = request_from_run_row(c)
                        if req.gpu <= 0 or not req.fallback_cpu:
                            continue
                        if (now_ts - _created_ts(c)) < self._gpu_fallback_wait_s:
                            continue
                        if self._store.downgrade_gpu_request(c["run_id"]):
                            self._events.append(
                                c["run_id"], "gpu_fallback",
                                status=f"gpu={req.gpu}", worker_id=self._coordinator_id,
                            )
                            record_gpu_fallback()

            gated: list[dict[str, Any]] = []
            eligible_counts: dict[str, int] = {}
            for c in candidates:
                req = request_from_run_row(c)
                if req == ResourceRequest():
                    # V6 语义保留：无 profile、无 envelope 要求的 run 恒可
                    # 派发（本地直跑路径不依赖 worker 存活 —— 逐字节兼容）
                    gated.append(c)
                    continue
                eligible = eligible_workers(req, workers)
                if eligible:
                    gated.append(c)
                    eligible_counts[c["run_id"]] = len(eligible)
                else:
                    self._note_waiting(c["run_id"], self._waiting_reason(req))
            # V8：同租户内稀缺资源优先（GPU/高内存 run 在槽位紧张时先占位，
            # 防同租户饿死）；跨租户公平环不受影响。
            within_tenant_key = lambda r: (  # noqa: E731 - 派发循环内的小适配器
                scarcity_rank_key(
                    request_from_run_row(r),
                    eligible_counts.get(r["run_id"], 0),
                )
                + (-int(r.get("priority") or 0), int(r.get("id") or 0))
            )
            picked = fair_pick(
                gated, slots=free,
                last_dispatch=self._store.tenant_last_dispatch(),
                within_tenant_key=within_tenant_key,
            )
        for row in picked:
            run_id = row["run_id"]
            epoch, reject_dim = self._store.claim_lease(
                run_id,
                coordinator_id=self._coordinator_id,
                ttl_s=self._lease_ttl_s,
                max_attempts=self._max_run_attempts,
                ledger=self._ledger,
                return_detail=True,
            )
            if epoch is None:
                if reject_dim is not None:
                    # V8：enforcing 账本按维拒绝 → 可观测留队（维度事件 +
                    # 拒绝计数；内存维即「OOM 避免」量化）。
                    self._note_waiting(run_id, f"resource:{reject_dim}")
                    record_resource_rejection(reject_dim)
                    if reject_dim == "mem_mb":
                        record_oom_avoided()
                continue  # 竞争失败/账本拒绝（enforcing）→ 留队下轮再试
            self._events.append(run_id, "run_started",
                                worker_id=self._coordinator_id)
            self._waiting_noted.discard(run_id)
            record_queue_wait_s(
                max(0.0, time.time() - _created_ts(row))
            )
            exec_state = _RunExecution(run_id=run_id, epoch=epoch,
                                       token=CancellationToken(job_id=run_id))
            with self._lock:
                self._inflight[run_id] = exec_state
            if not self._store.mark_running(run_id, epoch=epoch):
                # CAS 失败（被并发转移）→ 诚实收敛为 FAILED（同事务归还
                # 账本预留），不留 leased 僵尸等 30s reclaim 白记一次 attempt。
                self._store.finish_run(
                    run_id, epoch=epoch, status=ClusterRunStatus.FAILED,
                    error_code="CLAIM_RACE", ledger=self._ledger,
                )
                self._events.append(run_id, "run_failed",
                                    error_code="CLAIM_RACE")
                with self._lock:
                    self._inflight.pop(run_id, None)
                continue
            self._start_heartbeat(exec_state)
            self._pool.submit(self._execute_run, exec_state)
            stats["dispatched"] += 1

    @staticmethod
    def _waiting_reason(req: Any) -> str:
        """无合格 worker 的短原因词表（status 列 ≤20 字符，round1 n2）。"""
        if req is not None and getattr(req, "gpu", 0):
            return "no_worker_gpu"
        if req is not None and getattr(req, "required_profiles", None):
            return "no_worker_profiles"
        if req is not None and getattr(req, "min_mem_mb", 0):
            return "no_worker_mem"
        return "no_worker"

    def _note_waiting(self, run_id: str, reason: str) -> None:
        """资源不满足 → waiting_resource 事件（一次性；有界去重）。

        V8 语义扩展：reason 词表 = no_worker[_gpu|_profiles|_mem] ∪
        ``resource:<dim>``（enforcing 账本按维拒绝）。每 run 只发**第一条**
        （events.exists 跨进程兜底）—— 留队 run 不被重复事件刷屏。
        """
        if run_id in self._waiting_noted:
            return
        self._waiting_noted.add(run_id)
        # 有界防御：进程内集合异常膨胀时清空重来（词表外路径不存在，
        # 集合规模上界 = 累计留队 run 数）
        if len(self._waiting_noted) > 4096:
            self._waiting_noted.clear()
        if self._events.exists(run_id, "waiting_resource"):
            return
        self._events.append(run_id, "waiting_resource", status=(reason or "no_worker")[:20])

    def _detect_stragglers(self) -> int:
        """run 级 straggler：**真实心跳**滞后 > 3× 心跳间隔的在跑 run
        （一次性事件；round1 M4 —— 此前误用永不更新的 started_at，任何
        健康运行超过阈值都会被误报）。处置仍交给 lease TTL reclaim
        （不发明新状态机）；事件只做可见性。
        """
        from datetime import timedelta

        threshold = max(self._heartbeat_interval_s * 3, 1.0)
        cutoff = _utcnow_naive() - timedelta(seconds=threshold)
        lagging = self._store.scan_running(limit=self._batch_size)
        detected = 0
        for row in lagging:
            hb = row.get("heartbeat_at")
            if not hb:
                continue  # claim 后必写心跳；NULL 只在理论窗口出现
            if _parse_naive(hb) > cutoff:
                continue
            run_id = row["run_id"]
            if self._events.exists(run_id, "straggler_detected"):
                continue
            if self._events.append(run_id, "straggler_detected",
                                   status="heartbeat_lag"):
                detected += 1
        return detected

    def _emit_run_event(self, run_id: str, event: str, **kw: Any) -> None:
        self._events.append(run_id, event, worker_id=self._coordinator_id, **kw)

    def _drop_worker_caches(self, worker_ids: list[str]) -> None:
        """prune 级联：删除失联 worker 的缓存位置声明（防幽灵位置）。"""
        if self._cache_registry is None:
            return
        for worker_id in worker_ids[:64]:
            try:
                self._cache_registry.drop_worker(worker_id)
            except Exception:  # noqa: BLE001 - 级联失败 = 幽灵声明（miss 方向安全）
                pass

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
                        try:
                            from datetime import datetime

                            from app.services.geocompute.cluster.metrics import (
                                record_cancellation_latency as _record,
                            )

                            flag_ts = datetime.fromisoformat(
                                str(row["cancel_requested_at"]).replace("Z", "+00:00")
                            ).timestamp()
                            _record(max(0.0, time.time() - flag_ts))
                        except Exception:  # noqa: BLE001 - 观测失败不阻断
                            pass
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
                project_id=internal.get("project_id"),
                run_id=run_id,
                yield_check=exec_state.yield_event.is_set,
                owner_scope_override=internal.get("owner_scope"),
                resource_envelope=internal.get("resource_request"),
                emit_events=True,
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
        run_id = exec_state.run_id
        try:
            self._finish_locked(exec_state, status, error_code)
        except Exception:  # noqa: BLE001 - 终态写失败绝不带走线程：
            # run 行滞留由 lease 过期 reclaim 自愈（round1 M1）。
            logger.exception(
                "[geocompute-v6] terminal write crashed run_id=%s", run_id
            )
        finally:
            with self._lock:
                self._inflight.pop(run_id, None)
            exec_state.done.set()

    def _finish_locked(
        self,
        exec_state: _RunExecution,
        status: ClusterRunStatus,
        error_code: Optional[str],
    ) -> None:
        run_id, epoch = exec_state.run_id, exec_state.epoch
        ok = self._store.finish_run(
            run_id, epoch=epoch, status=status, error_code=error_code,
            ledger=self._ledger,
        )
        if ok and status == ClusterRunStatus.PREEMPTED:
            # 两段：PREEMPTED 可驻留（客户端可见）→ 立即回队尾重排。
            self._store.requeue_preempted(run_id, epoch=epoch)
        if ok:
            # V7：run 级终态事件（豁免节点级预算 —— 终态可见性不因洪泛丢失）
            event = {
                ClusterRunStatus.COMPLETED: "run_completed",
                ClusterRunStatus.FAILED: "run_failed",
                ClusterRunStatus.CANCELLED: "run_cancelled",
                ClusterRunStatus.PREEMPTED: "run_preempted",
            }.get(status)
            if event:
                self._emit_run_event(run_id, event, error_code=error_code)
        if not ok:
            # lease 已易主：本地结果诚实丢弃（fencing 生效的证据）。
            logger.warning(
                "[geocompute-v6] fenced terminal write dropped run_id=%s "
                "status=%s", run_id, status.value,
            )

    # -------------------------------------------------------- preemption

    def _maybe_preempt(self) -> int:
        """高优先级等待 → 对低优先级在跑 run 请求让出（持久旗标）。

        条件：存在 priority 更高的可派发 run 等待 ≥ preempt_wait_s（以
        created_at 计）且本地槽位满（有等待者）；受害者 = 优先级低于等待
        者的在跑 run 中优先级最低者（同优先级不抢）；preempts 已达保险丝
        的 run 受保护（防抢占 livelock）。
        """
        waiting = self._store.scan_dispatchable(limit=self._batch_size)
        if not waiting:
            return 0
        # 只有当本地槽位满员（有等待者拿不到槽位）时才抢占。
        if len(self._inflight) < self._local_slots:
            return 0
        # M4：受害者仅限**本 coordinator** 的在跑 run —— 抢占的目的是给
        # 本地等待者腾槽位；对远端 run 让出不释放本地槽位，只是浪费一次
        # 完整重执行。
        running = [
            r for r in self._store.scan_running(limit=self._batch_size)
            if r.get("coordinator_id") == self._coordinator_id
        ]
        if not running:
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
            from datetime import datetime, timezone

            text = str(created).replace("Z", "+00:00")
            ts = datetime.fromisoformat(text)
            # DB 列是 naive UTC（repo 约定）—— 显式按 UTC 解释，绝不吃
            # 服务器本地时区偏移（round2 m2）。
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return max(0.0, now - ts.timestamp())
        except ValueError:
            return 0.0


def record_cancellation_latency(run_id: str, store: ClusterRunStore) -> None:
    """取消旗标 → 终态 的可观测延迟（cancel sweep 路径；心跳路径在 _loop）。

    有界样本（metrics 侧 deque ≤128）；失败静默 —— 观测绝不倒灌控制面。
    """
    try:
        from datetime import datetime

        row = store.get_run(run_id)
        if not row or not row.get("cancel_requested_at") or not row.get("terminal_at"):
            return

        def _parse(ts: str) -> float:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()

        from app.services.geocompute.cluster.metrics import (
            record_cancellation_latency as _record,
        )

        _record(max(0.0, _parse(row["terminal_at"]) - _parse(row["cancel_requested_at"])))
    except Exception:  # noqa: BLE001 - 观测失败不阻断控制面
        pass


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
                    preempt_wait_s=_env_float(
                        "WEBGIS_CLUSTER_PREEMPT_WAIT_S", DEFAULT_PREEMPT_WAIT_S
                    ),
                    local_slots=_env_int("WEBGIS_COORDINATOR_SLOTS", 2),
                    retention_s=_env_float(
                        "WEBGIS_CLUSTER_RUN_RETENTION_H", 24.0
                    ) * 3600.0,
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
