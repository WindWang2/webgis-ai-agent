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

import re
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

#: 错误证据的字符上界（评审 MINOR：error_message 不承载无限文本）。
_MAX_ERROR_MESSAGE_CHARS = 300

#: 绝对路径段（≥2 段，如 /home/kevin/projects/…）→ "<path>"：错误证据不得
#: 泄漏执行主机的目录拓扑；类型化 error_code 不受影响。
_ABSOLUTE_PATH_RE = re.compile(r"(?:/[^/\s]+){2,}")


def owner_scope_for(
    caller: Optional[dict[str, Any]], session_id: Optional[str] = None
) -> str:
    """从调用者身份派生 owner 域（复用键 / run 归属共用）。

    user id 优先（跨 run 稳定），回退 session id，都没有 → "anonymous"。
    哈希而非原文：owner 域出现在复用键/注册表中，不落明文身份。
    匿名哨兵（"anonymous"）经 auth.actor_ids 折叠为 None → 不与真实
    用户共享任何域。
    """
    import hashlib

    uid: Optional[str] = None
    try:
        from app.core.auth import actor_ids

        uid, _ = actor_ids(caller)
    except Exception:  # noqa: BLE001 - 身份解析失败按匿名处理（fail closed）
        uid = None
    if uid:
        return "u:" + hashlib.sha1(uid.encode()).hexdigest()[:16]
    if session_id:
        return "s:" + hashlib.sha1(str(session_id).encode()).hexdigest()[:16]
    return "anonymous"


def _scrub_error_message(message: Any) -> str:
    """证据 error_message 只保留有界、去本地拓扑的文本。

    截断到 300 字符；绝对路径（≥2 段）替换为 "<path>"。类型化
    ``error_code`` 单独存列，不受清洗影响。
    """
    text = str(message or "")
    scrubbed = _ABSOLUTE_PATH_RE.sub("<path>", text)
    if len(scrubbed) > _MAX_ERROR_MESSAGE_CHARS:
        scrubbed = scrubbed[:_MAX_ERROR_MESSAGE_CHARS]
    return scrubbed


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
        """近似字节量：采样 64 条外推（评审 m2 —— 全量 str() 是大节点上的
        瞬时垃圾源；采样估计对字节预算足够）。"""
        total = 0
        for key in ("features", "rows"):
            items = payload.get(key) or []
            if not items:
                continue
            sample = items[:64]
            avg = sum(len(str(f)) for f in sample) / len(sample)
            total += int(avg * len(items))
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
        retain_outputs: bool = False,
    ):
        self._store = result_store or NodeResultStore()
        self._max_workers = max(1, min(int(max_workers), 8))
        # 评审 M3 的逃生门：基准/测试需要在 run 终态后读取载荷做确定性
        # 断言。生产路径保持默认 False（终态即清除，证据/摘要为准）。
        self._retain_outputs = bool(retain_outputs)
        self._runs: OrderedDict[str, ExecutionRun] = OrderedDict()
        self._run_outputs: dict[str, dict[str, dict[str, Any]]] = {}
        # SEC：run 归属域（owner_scope_for 派生）；REST 读路径按它做
        # 读隔离（他人 run 一律 404，避免存在性预言机）。
        self._run_owners: dict[str, str] = {}
        self._run_tokens: dict[str, CancellationToken] = {}
        self._run_lock = threading.Lock()
        self._run_cache_size = run_cache_size

    # ------------------------------------------------------------- public

    def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        session_id: Optional[str] = None,
        caller: Optional[dict[str, Any]] = None,
        cancel_token: Optional[CancellationToken] = None,
        governor: Optional[Any] = None,
        governor_parent_path: Optional[str] = None,
    ) -> ExecutionRun:
        """执行整个计划（同步；调用方负责卸载到线程）。

        ``governor``（可选，ResourceGovernor）：给定后在
        ``governor_parent_path``（默认根）下创建 execution 作用域，准入
        与逐节点记账沿层级链生效（ADR-0096 D6）。

        ``caller``（可选，auth user dict）：贯穿到算子上下文 —— 目录项
        准入、复用键 owner 域、run 归属都以它为准；缺省按匿名隔离。
        """
        graph.validate_plan(plan)
        self._admission_check(plan)
        gov_path: Optional[str] = None
        if governor is not None:
            from app.services.geocompute.budgets import BudgetLimits, ScopeKind

            parent = governor_parent_path or "global:root"
            limits = BudgetLimits(
                max_rows=plan.budget.max_rows,
                max_bytes=plan.budget.max_bytes,
                max_nodes=plan.budget.max_nodes,
            )
            gov_path = governor.create_scope(
                parent, ScopeKind.EXECUTION, f"gexec-{uuid.uuid4().hex[:8]}",
                limits=limits,
            )
            total_rows = sum(
                (n.estimate.rows or 0) for n in plan.nodes if n.estimate
            )
            # 原子预留（评审 M2：admit→charge TOCTOU 修复）；估计值先行
            # 预配，节点完成后的实际记账叠加 —— 保守方向（宁可多记）。
            governor.reserve(gov_path, rows=total_rows, nodes=1)

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
        owner_scope = owner_scope_for(caller, session_id)
        with self._run_lock:
            self._runs[run_id] = run
            self._run_outputs[run_id] = outputs
            self._run_owners[run_id] = owner_scope
            while len(self._runs) > self._run_cache_size:
                old = next(iter(self._runs))
                self._runs.pop(old)
                self._run_outputs.pop(old, None)
                self._run_owners.pop(old, None)
        # run 级取消令牌注册（REST/工具凭 run_id 请求取消；M1）
        if cancel_token is None:
            cancel_token = CancellationToken(job_id=run_id)
        self._run_tokens[run_id] = cancel_token

        tracing.emit("run_started", run_id=run_id, plan_fingerprint=plan_fp,
                     nodes=len(plan.nodes), status="running",
                     budget_scope=gov_path)
        started = time.monotonic()
        try:
            self._run_waves(run, plan, outputs, session_id=session_id,
                            caller=caller, owner_scope=owner_scope,
                            cancel_token=cancel_token, deadline_ts=deadline_ts,
                            governor=governor, gov_path=gov_path)
        finally:
            if governor is not None and gov_path:
                # 摘除 execution 作用域（已发生用量保留在祖先链上）
                governor.teardown_scope(gov_path)
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
        # 载荷保留上限（并发评审 M3）：run 终态后立即丢弃原始节点输出 ——
        # 证据/摘要已在 run.evidence；复用走字节预算化的 NodeResultStore。
        if not self._retain_outputs:
            with self._run_lock:
                self._run_outputs.pop(run_id, None)
        self._run_tokens.pop(run_id, None)
        return run

    def cancel_run(self, run_id: str, reason: str = "cancelled by caller") -> bool:
        """请求取消一个 run（幂等；未知 run → False）。

        级联：run token 已在 execute_plan 内传入各节点 → durable 分支会把
        取消请求写入 job 行（request_cancel_sync），worker 侧 checkpoint
        生效。
        """
        token = self._run_tokens.get(run_id)
        if token is None:
            return False
        return token.cancel(reason)

    def get_run(self, run_id: str, *, owner_scope: Optional[str] = None) -> Optional[ExecutionRun]:
        """读取 run；给定 ``owner_scope`` 时做读隔离 —— 归属不符一律返回
        None（调用方 404，不区分「不存在」与「他人 run」，避免存在性预言机）。

        不传 ``owner_scope``（进程内工具/executor 自身路径）保持原有语义。
        """
        with self._run_lock:
            if owner_scope is not None and self._run_owners.get(run_id) != owner_scope:
                return None
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
        caller: Optional[dict[str, Any]],
        owner_scope: str,
        cancel_token: Optional[CancellationToken],
        deadline_ts: float,
        governor: Optional[Any] = None,
        gov_path: Optional[str] = None,
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
                                  session_id=session_id, caller=caller,
                                  owner_scope=owner_scope,
                                  cancel_token=cancel_token,
                                  deadline_ts=deadline_ts, budget=plan.budget,
                                  governor=governor, gov_path=gov_path)
            else:
                with ThreadPoolExecutor(
                    max_workers=min(self._max_workers, len(runnable)),
                    thread_name_prefix="geocompute-node",
                ) as pool:
                    futures = {
                        nid: pool.submit(
                            self._execute_one, run, node_map[nid], outputs,
                            session_id=session_id, caller=caller,
                            owner_scope=owner_scope,
                            cancel_token=cancel_token,
                            deadline_ts=deadline_ts, budget=plan.budget,
                            governor=governor, gov_path=gov_path,
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
        caller: Optional[dict[str, Any]],
        owner_scope: str,
        cancel_token: Optional[CancellationToken],
        deadline_ts: float,
        budget: Any = None,
        governor: Optional[Any] = None,
        gov_path: Optional[str] = None,
    ) -> None:
        ev = run.evidence[node.node_id]
        node_deadline = deadline_ts
        if node.deadline_s is not None:
            node_deadline = min(node_deadline, time.monotonic() + node.deadline_s)

        if node.policy.value == "durable_job":
            started_dj = time.monotonic()
            try:
                from app.services.geocompute import durable

                if not session_id:
                    raise NodeExecutionError(
                        "durable_job policy requires a session context for "
                        "result handoff (session ref)",
                        retry_safe=False, node_id=node.node_id,
                    )
                ret = durable.dispatch_node(
                    node,
                    session_id=session_id,
                    plan_fingerprint=run.plan_fingerprint,
                    deadline_s=(node_deadline - time.monotonic())
                    if node.deadline_s is not None else None,
                )
                done = durable.await_node_job(
                    ret["job_id"],
                    session_id=session_id,
                    deadline_ts=node_deadline,
                    cancel_token=cancel_token if node.cancellable else None,
                )
                payload = done["payload"]
                if not payload:
                    raise NodeExecutionError(
                        "durable job produced no resolvable payload",
                        retry_safe=False, node_id=node.node_id,
                    )
                outputs[node.node_id] = payload
                ev.status = "completed"
                ev.rows_emitted = self._count_rows(payload)
                ev.duration_s = round(time.monotonic() - started_dj, 6)
                ev.output_ref = payload.get("ref_id")
                ev.attempts = 1
                tracing.emit("node_completed", run_id=run.run_id, node_id=node.node_id,
                             status="completed", rows=ev.rows_emitted,
                             duration_s=ev.duration_s, job_id=done["job_id"],
                             policy="durable_job")
                self._governor_charge(governor, gov_path, node, payload)
            except OperationCancelled:
                ev.status = "cancelled"
                ev.error_code = "CANCELLED"
                ev.duration_s = round(time.monotonic() - started_dj, 6)
                tracing.emit("node_cancelled", run_id=run.run_id, node_id=node.node_id,
                             status="cancelled", policy="durable_job")
            except GeoComputeError as exc:
                ev.status = "failed"
                ev.error_code = exc.code
                ev.error_message = _scrub_error_message(str(exc))
                ev.retry_safe = getattr(exc, "retry_safe", False)
                ev.duration_s = round(time.monotonic() - started_dj, 6)
                tracing.emit("node_failed", run_id=run.run_id, node_id=node.node_id,
                             status="failed", error_code=ev.error_code,
                             policy="durable_job")
            return

        # 复用：语义指纹命中 + owner 域隔离 + 策略允许 → 跳过执行。
        # owner_scope 使不同用户/会话即使指纹相同也绝不共享缓存条目（SEC）。
        if node.reuse == NodeReusePolicy.ALLOW:
            cached = self._store.get(graph.node_reuse_key(run.plan_fingerprint, node, owner_scope))
            if cached is not None and "__size__" in cached:
                payload = {k: v for k, v in cached.items() if k != "__size__"}
                outputs[node.node_id] = payload
                self._governor_charge(governor, gov_path, node, payload)
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
                    caller=caller,
                    budget=budget,
                    deadline_ts=node_deadline,
                    cancel_token=cancel_token,
                )
                token = cancel_token if node.cancellable else None
                with use_token(token):
                    payload = ops.execute_node(ctx, node, outputs)
                outputs[node.node_id] = payload
                self._store.put(
                    graph.node_reuse_key(run.plan_fingerprint, node, owner_scope), payload
                )
                self._governor_charge(governor, gov_path, node, payload)
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
                ev.error_message = _scrub_error_message(str(exc))
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
        ev.error_message = _scrub_error_message(str(last_err) if last_err else "unknown failure")
        if isinstance(last_err, NodeExecutionError):
            ev.retry_safe = last_err.retry_safe
        ev.duration_s = round(time.monotonic() - started, 6)
        tracing.emit("node_failed", run_id=run.run_id, node_id=node.node_id,
                     status="failed", error_code=ev.error_code,
                     duration_s=ev.duration_s)

    @staticmethod
    def _governor_charge(governor: Any, gov_path: Optional[str],
                         node: ExecutionNode, payload: dict[str, Any]) -> None:
        """节点完成 → 沿层级链记账（行数；字节计量的 O(N) 近似只在有
        bytes 限额的作用域上才有意义，这里以行数为准，诚实有界）。"""
        if governor is None or gov_path is None:
            return
        rows = len(payload.get("features") or payload.get("rows") or [])
        governor.charge(gov_path, rows=rows, nodes=1)

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
