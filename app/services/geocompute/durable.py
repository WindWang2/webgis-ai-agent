"""geocompute 节点 × durable-job 运行时互操作（ADR-0096 D5，ADR-0052 修正案）。

设计不变式：
- **不加新任务表、不加第二状态机** —— 节点派发穿过既有
  ``AnalysisTask`` 行（submit_durable_job），终态由既有状态机守卫；
- 取消/超时传播：run 级 CancellationToken 取消 → 对 job 行请求持久取消
  （``request_cancel_sync``），worker 看门狗把它推进任务体 checkpoint；
- 结果经 **session ref** 交接（``result_ref``），载荷绝不进 DB 行
  （与既有 heavy tools 相同的有界交接契约）。

轮询使用 jobs 子系统自己的 session factory（可注入，测试指向临时库），
保持 geocompute 层不直接依赖工具层会话助手。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from app.services.geocompute.errors import (
    DeadlineExceededError,
    FailureClass,
    NodeExecutionError,
)
from app.services.geocompute.plan import ExecutionNode

logger = logging.getLogger(__name__)

#: job 终态轮询起始间隔（秒）。eager/本地模式下任务体同步完成，首轮即命中。
#: V7（审计 B2）：自适应退避 0.05→0.5s —— 100 并发在飞节点时纯轮询 DB qps
#: 从 2000 降到 ≤数百；取消传播延迟上界仍由 run 心跳（0.5s）主导，不劣化。
_POLL_INTERVAL_S = 0.05
_POLL_INTERVAL_MAX_S = 0.5
_POLL_BACKOFF = 1.5

# ── V5 异构 worker profile（audit 06 §6.1 step 1 / ADR-0101 Deferred 落地）──
#: profile 词表：light_cpu | heavy_cpu | high_memory | raster | network |
#: external_io。队列名 = f"{profile}_queue"，在 task_queue.task_queues 声明；
#: **默认兜底队列仍是 "celery"**。单 worker 部署语义不变：compose 里的
#: worker 以 ``-Q`` 显式消费全部队列（见 docker-compose.yml 注释）——
#: profile 只是「路由真相就位」，等运维真的按 profile 拆 worker 才产生
#: 物理放置差异。Redis 缺席（task_always_eager）时队列完全不参与路由，
#: 行为与 V4 逐字节一致。
EXECUTION_QUEUE_PROFILES: tuple[str, ...] = (
    "light_cpu",
    "heavy_cpu",
    "high_memory",
    "raster",
    "network",
    "external_io",
)

#: 默认兜底队列（Celery 约定的缺省队列名）。
DEFAULT_QUEUE = "celery"


def queue_name_for_profile(profile: str) -> str:
    """profile → 队列名（确定性；非法 profile 一律落回默认队列）。"""
    return f"{profile}_queue" if profile in EXECUTION_QUEUE_PROFILES else DEFAULT_QUEUE


#: 类别 → profile（把既有 ``_CATEGORY_CAPABILITIES`` 提示提升为路由真值；
#: network/external_io 类别按名称语义归属）。
_CATEGORY_PROFILE: dict[str, str] = {
    "raster_operation": "raster",
    "raster_window_operation": "raster",
    "interpolation": "heavy_cpu",
    "spatial_join": "heavy_cpu",
    "network_operation": "network",
    # 物化/导出/登记是外部 I/O 重（写产物/落存）类别。
    "materialize": "external_io",
    "export": "external_io",
    "artifact_register": "external_io",
}

#: 轻量向量类别（无重型信号时 → light_cpu 队列）。
_LIGHT_VECTOR_CATEGORIES = frozenset({
    "source_discovery", "source_scan", "query", "filter", "project",
    "reproject", "attribute_join", "aggregate", "vector_operation",
})

#: ResourceClass 阈值（plan.py:99-104，1..5）：≥4 视为对应 profile 的强信号。
_RESOURCE_PROFILE_THRESHOLD = 4

#: locality_hint 允许的取值（含 ``{profile}_queue`` 拼写容错）。
_VALID_HINTS = set(EXECUTION_QUEUE_PROFILES) | {
    queue_name_for_profile(p) for p in EXECUTION_QUEUE_PROFILES
}


def queue_for_node(node: ExecutionNode) -> str:
    """节点 → 主队列名（**确定性纯函数**；retry affinity 的根基）。

    优先级（高 → 低，全部确定性）：
      1. ``locality_hint``（plan.py:166）：命中 profile 词表 → 直接钉到该
         队列（显式提示覆盖类别/资源类推导）；
      2. 类别能力 profile（raster/gdal/heavy_cpu/network/external_io）；
      3. ``ResourceClass``：memory≥4 → high_memory、io≥4 → external_io、
         cpu≥4 → heavy_cpu；
      4. 已知轻量向量类别 → light_cpu；
      5. 其余 → 默认队列 "celery"。

    这些字段全部**不参与语义指纹**（plan.py:185-205）——改路由提示不改
    节点身份，幂等键稳定。WORKER_LOSS 重派复用同一 dispatch 路径，同一
    节点必然落同一 profile 队列（retry affinity，无需新状态机）。
    """
    hint = (node.locality_hint or "").strip().lower()
    if hint in _VALID_HINTS:
        return hint if hint.endswith("_queue") else queue_name_for_profile(hint)

    profile = _CATEGORY_PROFILE.get(node.category.value)
    if profile is not None:
        return queue_name_for_profile(profile)

    rc = node.resource_class
    threshold = _RESOURCE_PROFILE_THRESHOLD
    if rc.memory >= threshold:
        return queue_name_for_profile("high_memory")
    if rc.io >= threshold:
        return queue_name_for_profile("external_io")
    if rc.cpu >= threshold:
        return queue_name_for_profile("heavy_cpu")

    if node.category.value in _LIGHT_VECTOR_CATEGORIES:
        return queue_name_for_profile("light_cpu")
    return DEFAULT_QUEUE


def _default_session_factory():
    # 必须**调用** jobs 层工厂（返回 Session 实例）—— 返回函数对象本身时
    # ``with session_factory() as db`` 拿到的是函数，TypeError（V5 既有
    # 生产缺陷，eager 测试因注入工厂而未暴露；V6 round2 review C3 修复）。
    from app.services.jobs.worker import _default_session_factory as _jobs_factory

    return _jobs_factory()


#: 可注入的会话工厂（测试替换为临时 SQLite 工厂）。
session_factory: Callable[[], Any] = _default_session_factory


#: 节点类别 → worker 能力提示（ADR-0101 D10，V4 §25）。
#: V5 起（audit 06 §6.1 step 1）这些提示被 ``queue_for_node`` 提升为
#: 路由真值（profile 队列在 task_queue.task_queues 声明）；单 worker 部署
#: 消费全部队列，语义与 V4 不变。
_CATEGORY_CAPABILITIES: dict[str, list[str]] = {
    "raster_operation": ["raster", "gdal", "high_memory"],
    "raster_window_operation": ["raster", "gdal"],
    "interpolation": ["heavy_cpu"],
    "spatial_join": ["heavy_cpu"],
}

_CAPABILITY_ORDER = ["raster", "gdal", "high_memory", "heavy_cpu", "vector", "network_io"]


def required_capabilities(node: ExecutionNode) -> list[str]:
    """节点声明的能力提示（确定性；随 params 进入幂等键 —— 同节点同键）。"""
    caps = set(_CATEGORY_CAPABILITIES.get(node.category.value, ["vector"]))
    return sorted(caps, key=_CAPABILITY_ORDER.index)


def dispatch_node(
    node: ExecutionNode,
    *,
    session_id: str,
    plan_fingerprint: str,
    deadline_s: Optional[float],
    budget: Optional[Any] = None,
    run_id: Optional[str] = None,
    node_attempt: Optional[int] = None,
    input_refs: Optional[dict[str, str]] = None,
    input_keys: Optional[dict[str, str]] = None,
    resource_envelope: Optional[dict[str, Any]] = None,
    owner_scope: Optional[str] = None,
) -> dict[str, Any]:
    """把节点提交为 durable job（幂等键 = 节点语义指纹 + 会话）。

    V5（audit 06 §6.1 step 1）：按 ``queue_for_node`` 路由到 profile 队列
    （``apply_async(queue=...)``，显式选项优先于 task_routes）。Redis 缺席
    （eager）时 Celery 忽略队列，任务同步执行 —— 返回 dict 追加
    ``backend_variant="in_process_eager"``，证据侧据此诚实标注（V5 step 5：
    durable 语义在 eager 下静默降级为进程内执行，必须可见）。

    V7：``run_id``/``node_attempt`` 经 **task_kwargs**（不进 params —— 幂等键
    不含治理/观测元数据，budget 同款先例）穿透到 worker 任务体 —— Celery
    边界丢 contextvars，显式传参是 worker 侧分布式事件（run_events）的唯一
    可靠关联通道；缺席 = eager/旧路径，worker 只跳过事件发射。
    """
    from app.services.geocompute.tasks import run_geocompute_node
    from app.services.jobs.submit import submit_durable_job

    node_dict = node.model_dump(mode="json")
    params = {
        "node": node_dict,
        "plan_fingerprint": plan_fingerprint,
        "capabilities": required_capabilities(node),
    }
    queue = queue_for_node(node)
    ret = submit_durable_job(
        celery_task=run_geocompute_node,
        task_type="geocompute_node",
        display_name=f"GeoCompute 节点 {node.node_id}",
        params=params,
        task_kwargs={
            "node": node_dict,
            "session_id": session_id,
            "deadline_s": deadline_s,
            # V6（P0-3）：plan budget 穿透到 worker 任务体；只进 task_kwargs
            # 不进 params —— 幂等键（params 摘要）不含治理元数据。
            "budget": (
                budget.model_dump(mode="json")
                if budget is not None and hasattr(budget, "model_dump")
                else budget
            ),
            # V7：分布式事件关联（观测元数据，不进幂等键）。
            "run_id": run_id,
            "node_attempt": node_attempt,
            # V7 input handoff / 准入守卫 / 缓存身份（同上：全不进幂等键）
            "input_refs": input_refs,
            "input_keys": input_keys,
            "resource_envelope": resource_envelope,
            "owner_scope": owner_scope,
        },
        session_id=session_id,
        queue=queue,
    )
    ret["queue"] = queue
    try:
        from app.services.task_queue import celery_app

        eager = bool(celery_app.conf.task_always_eager)
    except Exception:  # noqa: BLE001 - Celery conf 不可读时按真实 broker 模式
        eager = False
    if eager:
        ret["backend_variant"] = "in_process_eager"
    return ret


def await_node_job(
    job_id: str,
    *,
    session_id: str,
    deadline_ts: Optional[float],
    cancel_token: Any = None,
) -> dict[str, Any]:
    """等待 durable job 终态并把结果解到节点载荷。

    返回 ``{"payload": ..., "job_id": ...}``；失败/取消以类型化异常上抛
    （NodeExecutionError / DeadlineExceededError / OperationCancelled）。
    """
    from app.lib.cancellation import OperationCancelled
    from app.services.jobs import DurableJobStore
    from app.services.jobs.lifecycle import JobStatus

    cancel_requested = False
    terminal: Optional[dict[str, Any]] = None
    poll_s = _POLL_INTERVAL_S
    while True:
        factory = session_factory
        with factory() as db:
            job = DurableJobStore.get_sync(db, int(job_id))
            if job is None:
                raise NodeExecutionError(
                    f"durable job {job_id} row does not exist",
                    retry_safe=False,
                    details={"job_id": str(job_id)},
                )
            status = job.status
            if status == JobStatus.completed:
                terminal = {"result_ref": getattr(job, "result_ref", None)}
            # AnalysisTask 存的是 error_trace（redaction 后的单行文本）——
            # V3 读 error_message 永远为 None，诚实错误文本丢失（V4 修复）。
            error_message = getattr(job, "error_trace", None)
        if terminal is not None:
            break
        if status == JobStatus.stale:
            # worker 死亡（心跳过期 → stale）：任务体幂等时可安全重派
            # （幂等键在终态行上已释放，重派会建新行）。分类为 WORKER_LOSS，
            # 是否真的重试由节点 RetryPolicy 决定（默认 max_attempts=1）。
            raise NodeExecutionError(
                error_message or f"durable job {job_id} worker lost (stale)",
                retry_safe=True,
                failure_class=FailureClass.WORKER_LOSS,
                node_id=None,
                details={"job_id": str(job_id), "job_status": str(status)},
            )
        if status == JobStatus.failed:
            raise NodeExecutionError(
                error_message or f"durable job {job_id} ended {status}",
                retry_safe=False,
                node_id=None,
                details={"job_id": str(job_id), "job_status": str(status)},
            )
        if status in (JobStatus.cancelled,):
            raise OperationCancelled(f"durable job {job_id} cancelled")
        # run 级取消 → 对 job 请求持久取消（一次）；deadline → 取消并超时
        if cancel_token is not None and cancel_token.cancelled and not cancel_requested:
            with factory() as db:
                DurableJobStore.request_cancel_sync(db, int(job_id))
            cancel_requested = True
        if deadline_ts is not None and time.monotonic() > deadline_ts:
            if not cancel_requested:
                with factory() as db:
                    DurableJobStore.request_cancel_sync(db, int(job_id))
                cancel_requested = True
            raise DeadlineExceededError(
                f"durable job {job_id} exceeded node deadline",
                details={"job_id": str(job_id)},
            )
        # V7（审计 B2）：自适应退避 —— 首轮 50ms 保 eager/短任务响应，
        # 长任务退到 0.5s（取消传播的主导延迟是 run 心跳 0.5s，不劣化）。
        time.sleep(poll_s)
        poll_s = min(poll_s * _POLL_BACKOFF, _POLL_INTERVAL_MAX_S)

    ref = terminal.get("result_ref")
    payload: dict[str, Any] = {}
    if ref:
        from app.services.geocompute._async_bridge import run_coro_sync
        from app.services.session_data import session_data_manager

        stored = run_coro_sync(session_data_manager.get(session_id, ref))
        if stored is None:
            raise NodeExecutionError(
                f"node result ref {ref} is no longer resolvable",
                retry_safe=False,
                details={"job_id": str(job_id), "result_ref": ref},
            )
        payload = {"ref_id": ref, "features": stored, "metadata": {"via": "durable_job"}}
    return {"payload": payload, "job_id": str(job_id)}


def await_node_jobs(
    job_ids: list[int],
    *,
    session_id: str,
    deadline_ts: Optional[float],
    cancel_token: Any = None,
) -> dict[int, dict[str, Any]]:
    """等待**多个** durable job 终态（V8 分区 fan-out 的等待原语）。

    与 ``await_node_job`` 同一语义域（轮询/取消级联/deadline），区别：
    - 单个 job 失败**不**立即上抛 —— 返回 per-job 终态投影，由调用方
      （executor）决定逐 tile 重试；全部门类收敛后返回；
    - 取消/deadline → 对全部未完 job 请求持久取消后抛 OperationCancelled /
      DeadlineExceededError（与单 job 版一致）。

    返回 ``{job_id: {"status": "completed"|"failed"|"cancelled"|"stale",
    "payload": dict, "error": str|None}}``（completed 才有 payload；有
    result_ref → session 重取 features；无 ref → result_summary 投影，
    raster tile 的 raster_path 由此回传）。
    """
    from app.lib.cancellation import OperationCancelled
    from app.services.jobs import DurableJobStore
    from app.services.jobs.lifecycle import JobStatus

    states: dict[int, dict[str, Any]] = {}
    cancel_requested: set[int] = set()
    outstanding = set(int(j) for j in job_ids)
    poll_s = _POLL_INTERVAL_S
    while outstanding:
        factory = session_factory
        terminal_now: list[int] = []
        with factory() as db:
            for job_id in sorted(outstanding):
                job = DurableJobStore.get_sync(db, int(job_id))
                if job is None:
                    states[job_id] = {"status": "failed",
                                      "payload": {},
                                      "error": f"job row {job_id} missing"}
                    terminal_now.append(job_id)
                    continue
                status = job.status
                if status == JobStatus.completed:
                    ref = getattr(job, "result_ref", None)
                    summary = getattr(job, "result_summary", None)
                    payload: dict[str, Any] = {}
                    if ref:
                        from app.services.geocompute._async_bridge import (
                            run_coro_sync,
                        )
                        from app.services.session_data import (
                            session_data_manager,
                        )

                        stored = run_coro_sync(
                            session_data_manager.get(session_id, ref))
                        if stored is None:
                            states[job_id] = {
                                "status": "failed", "payload": {},
                                "error": f"result ref {ref} unresolvable",
                            }
                        else:
                            payload = {"ref_id": ref, "features": stored,
                                       "metadata": {"via": "partition_tile"}}
                    elif isinstance(summary, dict):
                        # raster tile：raster_path 经 result_summary 回传
                        payload = dict(summary)
                    if job_id not in states:
                        states[job_id] = {"status": "completed",
                                          "payload": payload, "error": None}
                    terminal_now.append(job_id)
                elif status == JobStatus.failed:
                    states[job_id] = {
                        "status": "failed", "payload": {},
                        "error": getattr(job, "error_trace", None)
                        or f"job {job_id} failed",
                    }
                    terminal_now.append(job_id)
                elif status == JobStatus.stale:
                    states[job_id] = {
                        "status": "stale", "payload": {},
                        "error": getattr(job, "error_trace", None)
                        or f"job {job_id} worker lost",
                    }
                    terminal_now.append(job_id)
                elif status == JobStatus.cancelled:
                    states[job_id] = {"status": "cancelled", "payload": {},
                                      "error": "cancelled"}
                    terminal_now.append(job_id)
        for job_id in terminal_now:
            outstanding.discard(job_id)
        if not outstanding:
            break
        if cancel_token is not None and cancel_token.cancelled:
            with factory() as db:
                for job_id in sorted(outstanding):
                    if job_id not in cancel_requested:
                        DurableJobStore.request_cancel_sync(db, int(job_id))
                        cancel_requested.add(job_id)
            raise OperationCancelled("partition fan-out cancelled")
        if deadline_ts is not None and time.monotonic() > deadline_ts:
            with factory() as db:
                for job_id in sorted(outstanding):
                    if job_id not in cancel_requested:
                        DurableJobStore.request_cancel_sync(db, int(job_id))
                        cancel_requested.add(job_id)
            from app.services.geocompute.errors import DeadlineExceededError

            raise DeadlineExceededError(
                f"partition fan-out exceeded node deadline "
                f"({len(outstanding)} tile jobs outstanding)",
                details={"outstanding": len(outstanding)},
            )
        time.sleep(poll_s)
        poll_s = min(poll_s * _POLL_BACKOFF, _POLL_INTERVAL_MAX_S)
    return states
