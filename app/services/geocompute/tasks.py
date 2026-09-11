"""GeoCompute 节点的 durable job 任务体（ADR-0096 D5 / ADR-0052 修正案）。

穿透既有 durable-job 运行时：``job_id`` 存在时走状态机（进度落库、取消
从 DB 推进到 checkpoint、重复投递被入口守卫拒绝）。生产派发只经
``submit_durable_job``；``job_id=None`` 仅作为 eager/测试直调路径存在
（任务体显式告警）—— 它没有持久语义，生产调用方不得使用。

结果交接：载荷存 session ref（有界），``finish_job(result_ref=...)`` 把
引用写回 job 行 —— 执行器轮询终态后按 ref 解析载荷。DB 行里只有有界
摘要（redaction 照常生效）。

V7（distributed dataflow，01-architecture.md §2.2-§2.5）：
- **input handoff**：``input_refs``（task_kwargs，不进 params —— 幂等键
  不变）把上游 session ref 交给 worker 解析 —— 全 durable 节点链首次可执行
  （此前 worker 以空 payloads 执行，只有源根节点可走 broker）；
- **worker 本地载荷缓存**（object_cache.PayloadCache，owner 域隔离键）：
  fanout/同进程 redelivery 免重复取数 + 位置声明注册（placement 局部性）；
- **worker 侧准入守卫**（placement 第 2 层，强制 + 有界收敛）：``resource_
  envelope`` 与本地 capability 不符 → celery 有界重投（≤3 次）→ 类型化
  ``PLACEMENT_MISMATCH`` 诚实失败（多 worker 异构部署下 GPU 节点收敛到
  GPU worker；全部消费者不合格 ≤3 次内失败，无 livelock）；
- **分布式事件**（run_events，fail-open）：run_id 经 task_kwargs 显式穿透
  （Celery 边界丢 contextvars）→ node_started/output_ready/completed/failed
  全部有界落库；
- 事件/缓存/注册全部尽力而为：任何观测失败绝不倒灌执行结果。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from app.services.task_queue import celery_app

logger = logging.getLogger(__name__)

# V6（wave 7）：worker 生命周期接入集群注册表（幂等；eager 下信号不触发，
# 零副作用；真实 worker 上线即注册 + 心跳，容量对 coordinator 可见）。
from app.services.geocompute.cluster.workers import connect_celery_signals  # noqa: E402

connect_celery_signals()

#: placement 准入守卫的有界重投（celery 级；之后诚实失败 —— 无 livelock）。
_PLACEMENT_RETRY_MAX = 3
_PLACEMENT_RETRY_COUNTDOWN_S = 2

#: worker 进程内能力剖面缓存（静态事实；探针含子进程，每进程一次）。
_capability_cache: Optional[Any] = None
_capability_lock = None  # 惰性建（避免 import 期锁）


def _local_capability():
    global _capability_cache, _capability_lock
    if _capability_cache is None:
        import threading

        if _capability_lock is None:
            _capability_lock = threading.Lock()
        with _capability_lock:
            if _capability_cache is None:
                try:
                    from app.services.geocompute.cluster.capabilities import (
                        probe_capability,
                    )

                    _capability_cache = probe_capability()
                except Exception:  # noqa: BLE001 - 探针失败 = 无能力信息（守卫放行）
                    _capability_cache = None
    return _capability_cache


def _emit_event(
    run_id: Optional[str], event: str, *, node_id: Optional[str] = None,
    worker_id: Optional[str] = None, attempt: Optional[int] = None,
    status: Optional[str] = None, rows: Optional[int] = None,
    bytes_: Optional[int] = None, error_code: Optional[str] = None,
) -> None:
    """worker 侧分布式事件（尽力而为；观测失败绝不倒灌执行路径）。"""
    if not run_id:
        return
    try:
        from app.services.geocompute.cluster.events import RunEventStore

        RunEventStore().append(
            run_id, event, node_id=node_id, worker_id=worker_id,
            attempt=attempt, status=status, rows=rows, bytes_=bytes_,
            error_code=error_code,
        )
    except Exception:  # noqa: BLE001 - fail-open（append 自身已兜底，双保险）
        pass


@celery_app.task(
    name="app.services.geocompute.tasks.run_geocompute_node", bind=True,
    autoretry_for=(), max_retries=_PLACEMENT_RETRY_MAX,
)
def run_geocompute_node(
    self,
    node: dict,
    session_id: Optional[str] = None,
    job_id: Optional[int] = None,
    deadline_s: Optional[float] = None,
    budget: Optional[dict] = None,
    run_id: Optional[str] = None,
    node_attempt: Optional[int] = None,
    input_refs: Optional[dict[str, str]] = None,
    input_keys: Optional[dict[str, str]] = None,
    resource_envelope: Optional[dict] = None,
    owner_scope: Optional[str] = None,
) -> dict[str, Any]:
    """执行单个 ExecutionNode（经 ops 注册表），结果落 session ref。

    V6（P0-3 修复）：``budget`` 是 plan 级预算的持久投影（dispatch 侧经
    task_kwargs 穿透）—— worker 侧 ``OperatorContext`` 据此恢复行数红线。
    V7 新增 kwargs 全部是治理/观测元数据（不进 params，幂等键不变）：
    run_id/node_attempt（事件关联）、input_refs/input_keys（输入交接 +
    局部性键）、resource_envelope（worker 侧准入守卫）、owner_scope
    （缓存身份域）。
    """
    from app.services.geocompute import ops
    from app.services.geocompute.plan import ExecutionNode, ResourceBudget

    exec_node = ExecutionNode(**node)
    ctx_budget = ResourceBudget(**budget) if budget else None
    worker_id = _worker_identity()

    # ── worker 侧准入守卫（placement 第 2 层；eager/无 envelope 跳过）──
    # 终局失败经 _finalize_placement_failure 落 job 行 failed（round1 M2：
    # 在 durable_job 认领前抛错会让 job 行永久滞留 queued、类型化证据
    # 全丢 —— stale sweep 只扫 running 行，收不敛它）。
    if job_id is not None and resource_envelope:
        guard = _placement_guard(self, exec_node, resource_envelope, worker_id)
        if guard == "retry":
            return  # celery self.retry 已抛 Retry（有界：max_retries=3）
        # V8 修复（V7 潜伏缺陷）：``None`` = 合格或守卫缺席 → **继续执行**；
        # 只有显式 ``"failed"``（重投耗尽）才落 job 行终态。此前 None 也会
        # 走 finalize —— 任何带 envelope 的 durable 节点从未通过过守卫
        #（V7 e2e 未携带 envelope，故未暴露；V8 acceptance 首次踩中）。
        if guard == "failed":
            _finalize_placement_failure(job_id, exec_node.node_id, run_id,
                                        node_attempt, worker_id)
            return {"rows": 0, "ref_id": None, "metadata": {
                "error_code": "PLACEMENT_MISMATCH",
                "node_id": exec_node.node_id}}

    if job_id is None:
        # 直调（无 durable 语义）只允许 eager 测试路径存在；生产派发必经
        # submit_durable_job。这里仍诚实执行，但无状态机保护。
        logger.warning(
            "[geocompute] run_geocompute_node called without job_id (eager/test path)"
        )
        ctx = ops.OperatorContext(
            run_id="direct", node_id=exec_node.node_id, session_id=session_id,
            budget=ctx_budget,
        )
        payload = ops.execute_node(ctx, exec_node, {})
        return _bounded_summary(payload)

    from app.services.jobs.worker import durable_job, finish_job

    with durable_job(job_id, celery_task=self) as job:
        job.progress(5, "GeoCompute 节点开始执行", phase="execute")
        job.ensure_not_cancelled()
        deadline_ts = time.monotonic() + deadline_s if deadline_s else None
        _emit_event(run_id, "node_started", node_id=exec_node.node_id,
                    worker_id=worker_id, attempt=node_attempt)
        payloads = _resolve_inputs(
            session_id=session_id,
            input_refs=input_refs or {},
            input_keys=input_keys or {},
            owner_scope=owner_scope,
            run_id=run_id, node_id=exec_node.node_id,
            worker_id=worker_id, attempt=node_attempt,
        )
        ctx = ops.OperatorContext(
            run_id=f"job-{job_id}",
            node_id=exec_node.node_id,
            session_id=session_id,
            deadline_ts=deadline_ts,
            cancel_token=job.token,  # durable_job 已 use_token；此处显式传入
            budget=ctx_budget,
        )
        try:
            payload = ops.execute_node(ctx, exec_node, payloads)
            job.ensure_not_cancelled()  # 不可逆副作用（落存/登记）前的强制检查

            ref_id = payload.get("ref_id")
            if ref_id is None:
                ref_id = _store_payload(session_id, payload, exec_node)
            payload = dict(payload)
            payload["ref_id"] = ref_id
        except Exception:
            # 节点终局事件由 coordinator 统一发射（单一来源契约，round1
            # m6）—— worker 侧只发 started/output_ready/cache_hit。
            raise

        # 本地载荷缓存 + 位置声明（成功之后；fail-open）
        _cache_output(
            session_id=session_id, ref_id=ref_id, owner_scope=owner_scope,
            payload=payload, locality_key=exec_node.semantic_fingerprint(),
        )
        rows = len(payload.get("features") or payload.get("rows") or [])
        # node_output_ready = worker 侧唯一的输出信号；节点终局事件
        # （node_completed 等）由 coordinator 统一发射（契约：终局事实
        # 单一来源，双写会让无重复输出断言失真）。
        # V8：bytes_ = 载荷近似字节（transfer 可观测性；events.bytes 列
        # 首个规模化写入方 —— 此前该列几乎无写入方）。
        _emit_event(run_id, "node_output_ready", node_id=exec_node.node_id,
                    worker_id=worker_id, attempt=node_attempt, rows=rows,
                    bytes_=_estimate_payload_bytes(payload))

        result = _bounded_summary(payload)
        finish_job(job_id, result=result, result_ref=ref_id)
        return result


def _worker_identity() -> str:
    try:
        from app.services.geocompute.cluster.workers import celery_worker_id

        return celery_worker_id()
    except Exception:  # noqa: BLE001
        return "worker-unknown"


def _is_cancel(exc: BaseException) -> bool:
    try:
        from app.lib.cancellation import OperationCancelled

        return isinstance(exc, OperationCancelled)
    except Exception:  # noqa: BLE001
        return False


def _placement_guard(
    task: Any, node: Any, envelope: dict[str, Any], worker_id: str,
) -> Optional[str]:
    """节点级准入校验（placement 第 2 层）。

    返回 "retry"（celery 有界重投已发起）/ None（合格或守卫缺席）。
    不合格且重投耗尽 → 类型化 PLACEMENT_MISMATCH（诚实失败，证据可见）。
    """
    from celery.exceptions import Retry

    if not isinstance(envelope, dict):
        return None
    cap = _local_capability()
    if cap is None:
        return None  # 探针缺席 → 守卫放行（placement 第 1 层已 gating）
    try:
        ok = cap.satisfies(
            min_cpu=int(envelope.get("min_cpu") or 0),
            min_mem_mb=int(envelope.get("min_mem_mb") or 0),
            gpu=int(envelope.get("gpu") or 0),
        )
    except (TypeError, ValueError):
        return None
    if ok:
        return None
    if task.request.retries < _PLACEMENT_RETRY_MAX:
        _emit_event(
            getattr(task.request, "kwargs", {}).get("run_id"),
            "waiting_resource", node_id=node.node_id, worker_id=worker_id,
            status="placement_mismatch",
        )
        try:
            raise task.retry(
                countdown=_PLACEMENT_RETRY_COUNTDOWN_S,
                exc=NodePlacementMismatch(node.node_id),
            )
        except Retry:
            return "retry"
    return "failed"  # 重投耗尽 → 调用方落 job 行 failed（round1 M2）


def _finalize_placement_failure(
    job_id: int,
    node_id: str,
    run_id: Optional[str],
    attempt: Optional[int],
    worker_id: str,
) -> None:
    """placement 守卫终局失败：job 行经生产 mark_failed 路径落 failed。

    必须在 durable_job 认领**之前**把行推进终态 —— 否则行永久滞留
    queued（stale sweep 只扫 running），类型化证据全丢（round1 M2）。
    节点终局事件仍由 coordinator 统一发射（单一来源契约）—— coordinator
    的 await 读到 failed 行后以 error_message 携带 PLACEMENT_MISMATCH。
    """
    from app.services.jobs.store import DurableJobStore

    exc = NodePlacementMismatch(node_id)
    # 会话经 jobs 层工厂（durable.py 同款模式）—— 数据面不得依赖
    # app.tools（ADR-0096 D1 边界，boundary 测试守卫）。
    from app.services.jobs.worker import _default_session_factory

    with _default_session_factory() as db:
        ok = DurableJobStore.mark_failed_sync(
            db, int(job_id), error=exc,
            message="PLACEMENT_MISMATCH: worker does not satisfy the "
                    "node resource envelope",
        )
        db.commit()
    if not ok:
        # round2 Rm3：False = 行已被并发转移/缺席 —— 不能伪装成功；
        # raise 让 celery 任务可见失败（行若滞留 queued，属 DB 故障残留，
        # admin stuck/reset 面可诊断）。
        raise NodePlacementMismatch(
            f"finalize failed for job {job_id} (row not transitionable)")


class NodePlacementMismatch(Exception):
    """worker 侧准入守卫失败（有界重投耗尽后的诚实失败）。"""

    def __init__(self, node_id: str):
        super().__init__(
            f"worker does not satisfy the node's resource envelope "
            f"(node_id={node_id}); bounded requeue exhausted"
        )
        self.code = "PLACEMENT_MISMATCH"


def _resolve_inputs(
    *,
    session_id: Optional[str],
    input_refs: dict[str, str],
    input_keys: dict[str, str],
    owner_scope: Optional[str],
    run_id: Optional[str],
    node_id: str,
    worker_id: str,
    attempt: Optional[int],
) -> dict[str, dict[str, Any]]:
    """上游 session ref → 载荷（本地缓存优先；未命中经会话存储）。

    fail-open 纪律：ref 解析失败 → typed NodeExecutionError（输入丢失是
    执行失败，不是可忽略的观测事件）；缓存/注册失败 → 静默直取。
    """
    payloads: dict[str, dict[str, Any]] = {}
    if not input_refs:
        return payloads
    if not session_id:
        # 有上游 ref 却无会话 → 输入不可达；静默跳过会让节点以缺输入
        # 执行（部分/错误结果），必须 typed 失败（round1 m2）。
        from app.services.geocompute.errors import NodeExecutionError

        raise NodeExecutionError(
            "input handoff requires a session context",
            retry_safe=False, details={"missing": "session_id"},
        )
    from app.services.geocompute._async_bridge import run_coro_sync
    from app.services.session_data import session_data_manager

    cache = None
    try:
        from app.services.geocompute.cluster.object_cache import get_worker_cache

        cache = get_worker_cache(worker_id)
    except Exception:  # noqa: BLE001 - 缓存缺席 = 直取
        cache = None

    resolved: dict[str, dict[str, Any]] = {}
    for src in sorted(input_refs):
        ref = str(input_refs[src] or "")
        if not ref or not session_id:
            continue
        scope = owner_scope or ""
        cached = cache.get(session_id, ref, scope) if cache else None
        if cached is not None:
            _emit_event(run_id, "worker_cache_hit", node_id=node_id,
                        worker_id=worker_id)
        else:
            stored = run_coro_sync(session_data_manager.get(session_id, ref))
            if stored is None:
                raise _input_lost(src, ref)
            cached = {"ref_id": ref, "features": stored,
                      "metadata": {"via": "input_handoff"}}
            if cache:
                cache.put(session_id, ref, scope, cached,
                          locality_key=input_keys.get(src))
        resolved[src] = cached
    payloads.update(resolved)
    return payloads


def _input_lost(src: str, ref: str) -> Exception:
    from app.services.geocompute.errors import NodeExecutionError

    return NodeExecutionError(
        f"upstream input '{src}' (ref {ref[:16]}…) is no longer resolvable",
        retry_safe=False,
        details={"input_node": src, "reason": "input_ref_unresolvable"},
    )


def _cache_output(
    *, session_id: Optional[str], ref_id: Optional[str],
    owner_scope: Optional[str], payload: dict[str, Any],
    locality_key: str,
) -> None:
    if not (session_id and ref_id and owner_scope):
        return
    try:
        from app.services.geocompute.cluster.object_cache import get_worker_cache

        cache = get_worker_cache(_worker_identity())
        cache.put(
            session_id, str(ref_id), owner_scope, payload,
            locality_key=locality_key,
        )
    except Exception:  # noqa: BLE001 - 缓存/注册失败不影响输出
        pass


def _store_payload(session_id: Optional[str], payload: dict, node) -> Optional[str]:
    """把节点载荷显式落存为 session ref（大载荷离开执行面的正门）。"""
    data = payload.get("features") or payload.get("rows")
    if data is None or not session_id:
        return None
    from app.services.geocompute._async_bridge import run_coro_sync
    from app.services.session_data import session_data_manager

    return run_coro_sync(
        session_data_manager.store(
            session_id, data, prefix=f"geocompute-node-{node.semantic_fingerprint()}"
        )
    )


def _estimate_payload_bytes(payload: dict[str, Any]) -> int:
    """载荷近似字节（有界估计：64 条采样外推 + raster 文件大小）。

    与 PayloadCache/NodeResultStore 同一采样口径 —— 绝不全量 str()（大
    节点上的瞬时垃圾源）；估计值只服务 transfer 可观测性。
    """
    total = 0
    for key in ("features", "rows"):
        items = payload.get(key) or []
        if items:
            sample = items[:64]
            avg = sum(len(str(f)) for f in sample) / len(sample)
            total += int(avg * len(items))
    rp = payload.get("raster_path")
    if rp:
        try:
            total += max(0, int(os.path.getsize(str(rp))))
        except OSError:
            pass
    return total


def _bounded_summary(payload: dict) -> dict[str, Any]:
    """有界摘要（进 job 行，经 redaction；绝不含载荷本体）。"""
    meta = payload.get("metadata") or {}
    return {
        "rows": len(payload.get("features") or payload.get("rows") or []),
        "ref_id": payload.get("ref_id"),
        # V8：raster tile 的输出路径经 result_summary 回传 coordinator
        # （raster 载荷通货是路径字符串；partition 合并侧消费）。
        **({"raster_path": payload["raster_path"]}
           if payload.get("raster_path") else {}),
        "metadata": {
            k: v for k, v in meta.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        },
    }
