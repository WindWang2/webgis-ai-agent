"""统一执行器（ADR-0096 D2/D5）：波次并行、预算准入、复用、取消、deadline。

协调者而非第二真相：
- 重活穿透既有 durable-job 运行时（M5 接线 ``durable_job`` 策略）；
- 取消复用 ``CancellationToken``（lib 叶子原语 + checkpoint 协作点）；
- 节点结果按语义指纹复用（结果存储可插拔，默认进程内有界 LRU）；
- 失败类型化（errors.py），重试只针对 transient-safe 失败；
- 后代失效：上游失败/取消 → 后代 skipped；指纹变化 → invalidation_set。

进程内模式是第一公民：同步执行核心，REST 层用 to_thread 卸载（与
project workflow 路由同一模式），绝不阻塞事件循环。
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from typing import Any, Optional

from app.lib.cancellation import CancellationToken, OperationCancelled, use_token
from app.services.geocompute import graph, ops, tracing
from app.services.geocompute.errors import (
    BudgetExceededError,
    GeoComputeError,
    NodeExecutionError,
)
from app.services.geocompute.plan import (
    ExecutionNode,
    ExecutionPlan,
    ExecutionRun,
    ExecutionRunStatus,
    NodeEvidence,
    NodeReusePolicy,
)

#: 计划级最大并行度：独立于工具注册表信号量，小而有界（防线程池饥饿）。
DEFAULT_MAX_WORKERS = 2


class NodeResultStore:
    """进程内有界节点结果存储（LRU，双重界：条目数 + 字节预算）。"""

    def __init__(self, max_entries: int = 256, max_bytes: int = 128 * 1024 * 1024):
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def _measure(payload: dict[str, Any]) -> int:
        """近似字节量（有界测量：逐 feature 计量，超预算即停，不做全量序列化）。"""
        total = 0
        feats = payload.get("features") or []
        for f in feats[:50_000]:
            total += len(str(f)) * 2  # 粗粒度近似，避免 O(全量) JSON 序列化
        rows = payload.get("rows") or []
        for r in rows[:50_000]:
            total += len(str(r)) * 2
        return total

    def get(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: str, payload: dict[str, Any]) -> None:
        size = min(self._measure(payload), self._max_bytes + 1)
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= old.get("__size__", 0)
            if size > self._max_bytes:
                return  # 超预算的大结果不入复用存储（仍可作为本 run 内节点输出）
            self._entries[key] = {"__size__": size, **payload}
            self._bytes += size
            while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._bytes -= evicted.get("__size__", 0)
                if not self._entries:
                    break


class GeoExecutionEngine:
    """执行计划协调器。一个进程一个实例即可（run 之间无共享可变状态，
    结果存储除外）。"""

    def __init__(
        self,
        *,
        result_store: Optional[NodeResultStore] = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        run_cache_size: int = 128,
    ):
        self._store = result_store or NodeResultStore()
        self._max_workers = max(1, min(int(max_workers), 8))
        self._runs: OrderedDict[str, ExecutionRun] = OrderedDict()
        self._run_outputs: dict[str, dict[str, dict[str, Any]]] = {}
        self._run_lock = threading.Lock()
        self._run_cache_size = run_cache_size

    # ------------------------------------------------------------- public

    def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        session_id: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> ExecutionRun:
        """执行整个计划（同步；调用方负责卸载到线程）。"""
        graph.validate_plan(plan)
        self._admission_check(plan)

        run_id = f"gexec-{uuid.uuid4().hex[:12]}"
        plan_fp = plan.graph_fingerprint()
        run = ExecutionRun(
            run_id=run_id, plan_id=plan.plan_id, plan_fingerprint=plan_fp,
            status=ExecutionRunStatus.RUNNING,
        )
        run.evidence = {
            n.node_id: NodeEvidence(status="pending", fingerprint=n.semantic_fingerprint(),
                                    policy=n.policy.value)
            for n in plan.nodes
        }
        deadline_ts = time.monotonic() + plan.budget.deadline_s
        outputs: dict[str, dict[str, Any]] = {}
        with self._run_lock:
            self._runs[run_id] = run
            self._run_outputs[run_id] = outputs
            while len(self._runs) > self._run_cache_size:
                old = next(iter(self._runs))
                self._runs.pop(old)
                self._run_outputs.pop(old, None)

        tracing.emit("run_started", run_id=run_id, plan_fingerprint=plan_fp,
                     nodes=len(plan.nodes), status="running")
        started = time.monotonic()
        try:
            self._run_waves(run, plan, outputs, session_id=session_id,
                            cancel_token=cancel_token, deadline_ts=deadline_ts)
        finally:
            run.wall_time_s = round(time.monotonic() - started, 6)

        failed = [nid for nid, ev in run.evidence.items() if ev.status == "failed"]
        if any(ev.status == "cancelled" for ev in run.evidence.values()):
            run.status = ExecutionRunStatus.CANCELLED
        elif failed:
            run.status = ExecutionRunStatus.FAILED
            first = run.evidence[sorted(
                failed, key=lambda nid: run.evidence[nid].attempts or 0
            )[0]]
            run.error_code = first.error_code
            run.error_message = first.error_message
        else:
            run.status = ExecutionRunStatus.COMPLETED
        tracing.emit("run_finished", run_id=run_id, plan_fingerprint=plan_fp,
                     status=run.status.value, duration_s=run.wall_time_s,
                     error_code=run.error_code)
        return run

    def get_run(self, run_id: str) -> Optional[ExecutionRun]:
        with self._run_lock:
            return self._runs.get(run_id)

    def get_node_output(self, run_id: str, node_id: str) -> Optional[dict[str, Any]]:
        with self._run_lock:
            return (self._run_outputs.get(run_id) or {}).get(node_id)

    # ------------------------------------------------------------ internal

    def _admission_check(self, plan: ExecutionPlan) -> None:
        """预算准入：已知估计的总和不得超过计划预算（未知不阻塞，诚实估计）。"""
        total_rows = 0
        total_bytes = 0
        unknown = False
        for node in plan.nodes:
            est = node.estimate
            if est is None or (est.rows is None and est.bytes is None):
                unknown = True
                continue
            if est.confidence == "assumption":
                unknown = True
            if est.rows is not None:
                total_rows += est.rows
            if est.bytes is not None:
                total_bytes += est.bytes
        over: list[str] = []
        if total_rows > plan.budget.max_rows:
            over.append(f"rows {total_rows} > budget {plan.budget.max_rows}")
        if total_bytes > plan.budget.max_bytes:
            over.append(f"bytes {total_bytes} > budget {plan.budget.max_bytes}")
        if over:
            raise BudgetExceededError(
                "plan admission rejected: " + "; ".join(over),
                suggestions=[
                    "push down filters/aggregation to sources",
                    "use statistics-aware planning (data fabric V3 optimizer)",
                    "materialize per-source subsets before joining",
                    "explicitly raise the budget for approved heavy paths",
                ],
                details={"estimated_rows": total_rows, "estimated_bytes": total_bytes,
                         "estimates_unknown_for_some_nodes": unknown},
            )

    def _run_waves(
        self,
        run: ExecutionRun,
        plan: ExecutionPlan,
        outputs: dict[str, dict[str, Any]],
        *,
        session_id: Optional[str],
        cancel_token: Optional[CancellationToken],
        deadline_ts: float,
    ) -> None:
        node_map = plan.node_map()
        for wave in graph.topo_wave_order(plan):
            if cancel_token is not None and cancel_token.cancelled:
                self._mark_remaining(run, wave, "cancelled", reason=cancel_token.reason)
                continue
            remaining = deadline_ts - time.monotonic()
            if remaining <= 0:
                self._mark_remaining(run, wave, "cancelled", reason="deadline exceeded")
                continue

            runnable: list[str] = []
            for nid in wave:
                node = node_map[nid]
                if any(run.evidence[s].status in {"failed", "cancelled", "skipped"}
                       for s in node.inputs if s in run.evidence):
                    run.evidence[nid].status = "skipped"
                    tracing.emit("node_skipped", run_id=run.run_id, node_id=nid,
                                 status="skipped", reason="ancestor_not_completed")
                    continue
                runnable.append(nid)
            if not runnable:
                continue

            if len(runnable) == 1:
                self._execute_one(run, node_map[runnable[0]], outputs,
                                  session_id=session_id, cancel_token=cancel_token,
                                  deadline_ts=deadline_ts, budget=plan.budget)
            else:
                with ThreadPoolExecutor(
                    max_workers=min(self._max_workers, len(runnable)),
                    thread_name_prefix="geocompute-node",
                ) as pool:
                    futures = {
                        nid: pool.submit(
                            self._execute_one, run, node_map[nid], outputs,
                            session_id=session_id, cancel_token=cancel_token,
                            deadline_ts=deadline_ts, budget=plan.budget,
                        )
                        for nid in runnable
                    }
                    for nid, fut in futures.items():
                        try:
                            fut.result(timeout=max(0.1, deadline_ts - time.monotonic()))
                        except Exception:  # noqa: BLE001 - _execute_one 已类型化收编
                            continue

    def _execute_one(
        self,
        run: ExecutionRun,
        node: ExecutionNode,
        outputs: dict[str, dict[str, Any]],
        *,
        session_id: Optional[str],
        cancel_token: Optional[CancellationToken],
        deadline_ts: float,
        budget: Any = None,
    ) -> None:
        ev = run.evidence[node.node_id]
        if node.policy.value == "durable_job":
            ev.status = "failed"
            ev.error_code = "OPERATION_UNSUPPORTED"
            ev.error_message = (
                "durable_job policy dispatch lands with distributed execution "
                "policies (ADR-0096 D5 follow-up); use in_process today"
            )
            tracing.emit("node_unsupported_policy", run_id=run.run_id,
                         node_id=node.node_id, status="failed", error_code=ev.error_code)
            return

        node_deadline = deadline_ts
        if node.deadline_s is not None:
            node_deadline = min(node_deadline, time.monotonic() + node.deadline_s)

        # 复用：语义指纹命中 + 策略允许 → 跳过执行
        if node.reuse == NodeReusePolicy.ALLOW:
            cached = self._store.get(graph.node_reuse_key(run.plan_fingerprint, node))
            if cached is not None and "__size__" in cached:
                payload = {k: v for k, v in cached.items() if k != "__size__"}
                outputs[node.node_id] = payload
                ev.status = "reused"
                ev.rows_emitted = self._count_rows(payload)
                tracing.emit("node_reused", run_id=run.run_id, node_id=node.node_id,
                             status="reused", rows=ev.rows_emitted)
                return

        attempts_allowed = node.retry.max_attempts
        started = time.monotonic()
        last_err: Optional[GeoComputeError] = None
        for attempt in range(1, attempts_allowed + 1):
            ev.attempts = attempt
            if cancel_token is not None and cancel_token.cancelled:
                ev.status = "cancelled"
                return
            try:
                ctx = ops.OperatorContext(
                    run_id=run.run_id,
                    node_id=node.node_id,
                    session_id=session_id,
                    budget=budget,
                    deadline_ts=node_deadline,
                    cancel_token=cancel_token,
                )
                token = cancel_token if node.cancellable else None
                with use_token(token):
                    payload = ops.execute_node(ctx, node, outputs)
                outputs[node.node_id] = payload
                self._store.put(graph.node_reuse_key(run.plan_fingerprint, node), payload)
                ev.status = "completed"
                ev.rows_emitted = self._count_rows(payload)
                ev.duration_s = round(time.monotonic() - started, 6)
                ev.output_ref = payload.get("ref_id")
                ev.output_summary = {
                    k: v for k, v in (payload.get("metadata") or {}).items()
                    if isinstance(v, (str, int, float, bool, type(None)))
                }
                tracing.emit("node_completed", run_id=run.run_id, node_id=node.node_id,
                             status="completed", rows=ev.rows_emitted,
                             duration_s=ev.duration_s, attempts=attempt)
                return
            except OperationCancelled as exc:
                ev.status = "cancelled"
                ev.error_code = "CANCELLED"
                ev.error_message = str(exc)
                ev.duration_s = round(time.monotonic() - started, 6)
                tracing.emit("node_cancelled", run_id=run.run_id, node_id=node.node_id,
                             status="cancelled", duration_s=ev.duration_s)
                return
            except GeoComputeError as exc:
                last_err = exc
                retryable = isinstance(exc, NodeExecutionError) and exc.retry_safe
                tracing.emit("node_attempt_failed", run_id=run.run_id,
                             node_id=node.node_id, status="failed",
                             error_code=exc.code, attempts=attempt)
                if not retryable or attempt >= attempts_allowed:
                    break
                time.sleep(0.05 * attempt)
            except Exception as exc:  # noqa: BLE001 - 类型化收编，绝不让线程带异常死掉
                last_err = NodeExecutionError(
                    f"{type(exc).__name__}: {exc}", retry_safe=False, node_id=node.node_id
                )
                tracing.emit("node_attempt_failed", run_id=run.run_id,
                             node_id=node.node_id, status="failed",
                             error_code="NODE_FAILED", attempts=attempt)
                break

        ev.status = "failed"
        ev.error_code = last_err.code if last_err else "NODE_FAILED"
        ev.error_message = str(last_err) if last_err else "unknown failure"
        if isinstance(last_err, NodeExecutionError):
            ev.retry_safe = last_err.retry_safe
        ev.duration_s = round(time.monotonic() - started, 6)
        tracing.emit("node_failed", run_id=run.run_id, node_id=node.node_id,
                     status="failed", error_code=ev.error_code,
                     duration_s=ev.duration_s)

    @staticmethod
    def _count_rows(payload: dict[str, Any]) -> Optional[int]:
        if "features" in payload:
            return len(payload["features"])
        if "rows" in payload:
            return len(payload["rows"])
        return None

    def _mark_remaining(
        self, run: ExecutionRun, wave: list[str], status: str, *, reason: str
    ) -> None:
        for nid in wave:
            ev = run.evidence[nid]
            if ev.status == "pending":
                ev.status = status
                ev.error_message = reason
                tracing.emit("node_marked", run_id=run.run_id, node_id=nid,
                             status=status, reason=reason)


#: 进程级默认引擎（与 spatial_catalog_service 同一单例惯例）。
engine = GeoExecutionEngine()
