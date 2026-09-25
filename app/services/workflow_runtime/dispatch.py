"""Workflow Runtime V6 —— 节点派发面（local/durable + 全局资源上限）。

调度模型（Phase D）：

- **LocalDispatcher**：进程内执行（现状路径 —— to_thread 卸载的
  geocompute in-process 计划）；API 内 driver 默认。
- **DurableDispatcher**：经 geocompute durable 通道派发
  （``durable.dispatch_node`` —— 幂等键/心跳/WORKER_LOSS 分类/stale 重派
  全部复用既有真相，**不新造任务表**）；节点计划由同一适配器构建
  （``build_node_plan``），仅切换 ``policy=DURABLE_JOB``。
- **全局资源上限**：进程级 ``asyncio.Semaphore``（``GIS_WORKFLOW_DISPATCH_SLOTS``，
  默认 4）—— 多实例并发 run 共享同一爆炸半径上界，测试不会打爆机器。
  节点租约执行期续约（driver 侧）与 worker 侧心跳是两道独立活性证据。
- **优先级/公平**：批次内按节点 priority 降序、同优先级按 DAG 声明序
  （driver ready_set 的确定性序）—— FIFO 公平，无饥饿。
- **大任务隔离**（opt-in）：输入行数超阈值的节点要求 durable worker
  接管；无合格 worker → typed ``NO_CAPABLE_WORKER``（诚实暴露，绝不静默
  降级在进程内跑大任务）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from app.services.workflow_runtime.adapters_geocompute import (
    GeoComputeNodeOutcome,
)
from app.services.workflow_runtime.cluster import WorkerRegistry

logger = logging.getLogger(__name__)

#: 全局派发槽位（进程级；多实例共享上界）。
DEFAULT_DISPATCH_SLOTS = 4
#: 大任务隔离行数阈值（opt-in 生效）。
DEFAULT_ISOLATION_ROW_THRESHOLD = 20_000
#: durable 等待轮询间隔（秒）。
_DURABLE_POLL_S = 0.25


def dispatch_slots() -> int:
    try:
        return max(1, int(os.getenv(
            "GIS_WORKFLOW_DISPATCH_SLOTS", str(DEFAULT_DISPATCH_SLOTS))))
    except (TypeError, ValueError):
        return DEFAULT_DISPATCH_SLOTS


def dispatch_mode() -> str:
    """local（默认，行为不变）| durable | auto。"""
    raw = os.getenv("GIS_WORKFLOW_DISPATCH", "local").strip().lower()
    return raw if raw in ("local", "durable", "auto") else "local"


def isolation_enabled() -> bool:
    return os.getenv("GIS_WORKFLOW_ISOLATE_LARGE_TASKS", "0") in (
        "1", "true", "True")


#: 进程级派发信号量（per-event-loop 字典 —— Semaphore 绑定首个 await 它
#: 的 loop，跨 loop 复用会 RuntimeError；每 loop 各一份，容量同源）。
_slots_sems: Dict[int, asyncio.Semaphore] = {}
_slots_in_use = 0


def get_slots_semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    sem = _slots_sems.get(loop_id)
    if sem is None:
        sem = asyncio.Semaphore(dispatch_slots())
        _slots_sems[loop_id] = sem
    return sem


def slots_in_use() -> int:
    return _slots_in_use


class NoCapableWorker(Exception):
    """隔离要求下无合格 worker（typed；映射不可重试失败证据）。"""


def node_profile(node: Dict[str, Any]) -> str:
    """workflow 节点 → 主 profile（确定性；durable 队列词表子集）。

    优先节点声明的 ``resources.profile``；缺省按 kind 推导（transform →
    light_cpu；raster 类显式声明）。仅作派发参考；真实队列路由仍由
    geocompute ``queue_for_node`` 单一真值决定。
    """
    resources = node.get("resources") or {}
    profile = str(resources.get("profile", "") or "")
    if profile:
        return profile
    return "light_cpu"


class WorkerCapacityExhausted(Exception):
    """有覆盖该 profile 的活跃 worker，但声明槽位已全部在飞（ADR-0214 D4）。

    与 :class:`NoCapableWorker` 的语义分界：后者是「没有任何 worker 能跑
    这个 profile」（确定性，重试只会复现）；本类是「能跑但此刻满载」
    （瞬时，走既有重试退避门）。占用输入 = worker 心跳 load.in_flight
    （registry 未上报 load 时 fail-open 放行 —— 队列本身能吸收瞬时排队）。
    """

    def __init__(self, profile: str, in_flight: int, slots: int):
        self.profile = profile
        self.in_flight = int(in_flight)
        self.slots = int(slots)
        super().__init__(
            f"durable workers cover profile={profile!r} but at capacity "
            f"(in_flight={self.in_flight} >= slots={self.slots})")


def profile_capacity(worker_rows: List[Dict[str, Any]], *,
                     profile: str) -> "tuple[int, int]":
    """worker 行 → (declared_slots, reported_in_flight)（有界求和）。

    行形状异常（非 dict 桩/异构注册表）→ (0, 0) fail-open：容量未知时
    绝不阻塞派发（与 registry 查询失败的降级语义一致）。
    """
    slots = 0
    in_flight = 0
    reported = False
    for w in worker_rows:
        if not isinstance(w, dict):
            return 0, 0
        caps = w.get("capabilities") or {}
        load = w.get("load") or {}
        if not isinstance(caps, dict) or not isinstance(load, dict):
            return 0, 0
        try:
            slots += int((caps.get("profiles") or {}).get(profile, 0) or 0)
        except (TypeError, ValueError):
            return 0, 0
        try:
            val = int(load.get("in_flight", 0) or 0)
        except (TypeError, ValueError):
            val = 0
        if "in_flight" in load:
            reported = True
        in_flight += max(0, val)
    # 无任何 worker 上报过 load → 占用未知，按 0（fail-open：排队吸收）
    return slots, (in_flight if reported else 0)


def choose_dispatch(
    node: Dict[str, Any], *, input_rows: int,
    registry: Optional[WorkerRegistry] = None,
) -> str:
    """派发决策（纯读；local | durable）。

    - 显式 mode（local/durable）优先；
    - auto：重 profile（raster/heavy_cpu/high_memory）且存在活跃 durable
      worker 覆盖该 profile → durable；否则 local；
    - 隔离开启且行数超阈值：必须 durable（无合格 worker → NoCapableWorker；
      worker 在但槽满 → WorkerCapacityExhausted，ADR-0214 D4）。
    """
    profile = node_profile(node)
    mode = dispatch_mode()
    heavy = profile in ("raster", "heavy_cpu", "high_memory")
    isolate = isolation_enabled() and input_rows > \
        DEFAULT_ISOLATION_ROW_THRESHOLD
    if mode == "durable" or isolate:
        registry = registry or WorkerRegistry()
        capable = registry.list_active(profile=profile, backend="durable")
        if not capable:
            if mode == "durable" or isolate:
                raise NoCapableWorker(
                    f"no active durable worker covers profile={profile!r}")
            return "durable"
        slots, in_flight = profile_capacity(capable, profile=profile)
        if slots > 0 and in_flight >= slots:
            raise WorkerCapacityExhausted(profile, in_flight, slots)
        return "durable"
    if mode == "auto" and heavy:
        registry = registry or WorkerRegistry()
        if registry.list_active(profile=profile, backend="durable"):
            return "durable"
    return "local"


class LocalDispatcher:
    """进程内执行（现状路径；全局槽位上限内）。"""

    def __init__(self, engine: Any = None, *, owner_scope: str = "",
                 caller: Optional[Dict[str, Any]] = None):
        self._engine = engine
        self.owner_scope = owner_scope
        self.caller = caller

    async def execute(
        self, *, node: Dict[str, Any], dag: Dict[str, Any],
        input_refs: List[str], params: Dict[str, Any], session_id: str,
        port_idents: Dict[str, Dict[str, str]], cancel_token: Any,
        run_id: str = "", node_attempt: Optional[int] = None,
        node_deadline_s: Optional[float] = None,
        resource_envelope: Optional[Dict[str, Any]] = None,
    ) -> GeoComputeNodeOutcome:
        # run_id/node_attempt/node_deadline_s/resource_envelope 是 durable
        # 通道的派发元数据（run_events 关联 / attempt 证据 / worker 硬超时
        # / rg.v1 资源申报，ADR-0214 D5）。进程内路径不消费（预算记账在
        # driver 侧 governor_link 完成）；签名保持多态对齐。
        del run_id, node_attempt, node_deadline_s, resource_envelope
        from app.services.workflow_runtime.driver import (
            _execute_plan_sync,
            get_engine,
        )

        max_inline = 50_000
        from app.services.workflow_runtime.adapters_geocompute import (
            ADAPTER_INLINE_ROW_CAP,
            build_node_plan,
            load_ref_features,
        )

        max_inline = ADAPTER_INLINE_ROW_CAP
        input_features, truncated = await load_ref_features(
            session_id, input_refs, max_rows=max_inline)
        if truncated:
            return GeoComputeNodeOutcome(
                ok=False, error_code="INPUT_TRUNCATED",
                error_message=(
                    f"inline input exceeds adapter cap {max_inline} rows"))
        from types import SimpleNamespace

        ns = SimpleNamespace(node_id=node.get("node_id", ""),
                             kind=node.get("kind", ""),
                             role=node.get("role", ""))
        plan = build_node_plan(
            ns, node_params=params, input_features=input_features,
            input_fingerprints={
                p: i.get("fp", "") for p, i in port_idents.items()},
            session_id=session_id, owner_scope=self.owner_scope)
        engine = self._engine or get_engine()
        global _slots_in_use
        async with get_slots_semaphore():
            _slots_in_use += 1
            try:
                return await asyncio.to_thread(
                    _execute_plan_sync, engine, plan, session_id,
                    self.caller, cancel_token)
            finally:
                _slots_in_use -= 1


class DurableDispatcher:
    """geocompute durable 通道派发（幂等键/心跳/stale 重派复用既有真相）。"""

    def __init__(self, *, owner_scope: str = "",
                 caller: Optional[Dict[str, Any]] = None,
                 registry: Optional[WorkerRegistry] = None):
        self.owner_scope = owner_scope
        self.caller = caller
        self.registry = registry or WorkerRegistry()

    async def execute(
        self, *, node: Dict[str, Any], dag: Dict[str, Any],
        input_refs: List[str], params: Dict[str, Any], session_id: str,
        port_idents: Dict[str, Dict[str, str]], cancel_token: Any,
        run_id: str = "", node_attempt: Optional[int] = None,
        node_deadline_s: Optional[float] = None,
        resource_envelope: Optional[Dict[str, Any]] = None,
    ) -> GeoComputeNodeOutcome:
        from app.services.geocompute.plan import (
            ExecutionPolicyKind,
        )
        from app.services.workflow_runtime.adapters_geocompute import (
            ADAPTER_INLINE_ROW_CAP,
            build_node_plan,
            load_ref_features,
        )

        input_features, truncated = await load_ref_features(
            session_id, input_refs, max_rows=ADAPTER_INLINE_ROW_CAP)
        if truncated:
            return GeoComputeNodeOutcome(
                ok=False, error_code="INPUT_TRUNCATED",
                error_message="inline input exceeds adapter cap")
        from types import SimpleNamespace

        ns = SimpleNamespace(node_id=node.get("node_id", ""),
                             kind=node.get("kind", ""),
                             role=node.get("role", ""))
        plan = build_node_plan(
            ns, node_params=params, input_features=input_features,
            input_fingerprints={
                p: i.get("fp", "") for p, i in port_idents.items()},
            session_id=session_id, owner_scope=self.owner_scope)
        # 切 durable 策略（同一计划；队列路由 = geocompute 单一真值）
        for en in plan.nodes:
            en.policy = ExecutionPolicyKind.DURABLE_JOB
        op_node = plan.nodes[0]
        global _slots_in_use
        async with get_slots_semaphore():
            _slots_in_use += 1
            try:
                return await self._await_job(
                    node, plan, op_node, session_id, cancel_token,
                    input_refs=input_refs, port_idents=port_idents,
                    run_id=run_id, node_attempt=node_attempt,
                    node_deadline_s=node_deadline_s,
                    resource_envelope=resource_envelope)
            finally:
                _slots_in_use -= 1

    async def _await_job(
        self, node: Dict[str, Any], plan: Any, op_node: Any,
        session_id: str, cancel_token: Any,
        *,
        input_refs: Optional[List[str]] = None,
        port_idents: Optional[Dict[str, Dict[str, str]]] = None,
        run_id: str = "",
        node_attempt: Optional[int] = None,
        node_deadline_s: Optional[float] = None,
        resource_envelope: Optional[Dict[str, Any]] = None,
    ) -> GeoComputeNodeOutcome:
        # #1408: propagate plan budget / node deadline / placement into
        # durable dispatch (previously dropped) and align wait cap.
        # ADR-0214 D5：deadline 优先级 op_node 声明 > plan budget >
        # driver 显式值 —— driver 侧传入值（估算派生，advisory）只作
        # fallback，绝不覆盖 plan 内已声明的执行界（review P2-3）。
        node_deadline = getattr(op_node, "deadline_s", None)
        if node_deadline is None:
            budget = getattr(plan, "budget", None)
            node_deadline = getattr(budget, "deadline_s", None) if budget else None
        if node_deadline is None:
            node_deadline = node_deadline_s
        ret = await asyncio.to_thread(
            _dispatch_sync, op_node, plan, session_id, self.owner_scope,
            input_refs=input_refs, port_idents=port_idents, node=node,
            run_id=run_id or None, node_attempt=node_attempt,
            resource_envelope=resource_envelope)
        job_id = str(ret.get("job_id", "") or "")
        if not job_id:
            return GeoComputeNodeOutcome(
                ok=False, error_code="DISPATCH_FAILED",
                error_message=str(ret)[:200])
        logger.info("[WorkflowRuntime] durable dispatch %s job=%s queue=%s",
                    node.get("node_id"), job_id, ret.get("queue"))
        try:
            res = await asyncio.to_thread(
                _await_job_sync, job_id, session_id, cancel_token,
                durable_wait_timeout_s(node_deadline_s=node_deadline))
        except Exception as exc:  # noqa: BLE001 — 分类由异常携带
            from app.services.geocompute.errors import FailureClass, classify_failure
            from app.lib.cancellation import OperationCancelled

            failure = classify_failure(exc)
            # #1397: OperationCancelled has no .code; without this map the
            # driver only checks error_code=="CANCELLED" and lands FAILED.
            if isinstance(exc, OperationCancelled) or failure is FailureClass.CANCELLED:
                code = "CANCELLED"
            else:
                code = getattr(exc, "code", None) or "DURABLE_JOB_FAILED"
            return GeoComputeNodeOutcome(
                ok=False, error_code=code, error_message=str(exc)[:200],
                failure_class=getattr(failure, "value", ""))
        # #1392: await_node_job returns {"payload": {"ref_id": ...}, "job_id"}
        # — never a top-level result_ref. Accept both shapes so output_ref
        # is recorded for downstream _gather_inputs / compensation.
        payload = res.get("payload") if isinstance(res.get("payload"), dict) else {}
        ref = str(
            (payload or {}).get("ref_id")
            or res.get("result_ref")
            or ""
        )
        return GeoComputeNodeOutcome(ok=True, output_ref=ref,
                                     duration_ms=0)


class AutoDispatcher:
    """按节点逐次决策（choose_dispatch）→ 委派 local/durable。"""

    def __init__(self, *, owner_scope: str = "",
                 caller: Optional[Dict[str, Any]] = None,
                 registry: Optional[WorkerRegistry] = None):
        self.owner_scope = owner_scope
        self.caller = caller
        self.registry = registry or WorkerRegistry()
        self._local = LocalDispatcher(owner_scope=owner_scope,
                                      caller=caller)
        self._durable = DurableDispatcher(owner_scope=owner_scope,
                                          caller=caller,
                                          registry=self.registry)

    async def execute(
        self, *, node: Dict[str, Any], dag: Dict[str, Any],
        input_refs: List[str], params: Dict[str, Any], session_id: str,
        port_idents: Dict[str, Dict[str, str]], cancel_token: Any,
        run_id: str = "", node_attempt: Optional[int] = None,
        node_deadline_s: Optional[float] = None,
        resource_envelope: Optional[Dict[str, Any]] = None,
    ) -> GeoComputeNodeOutcome:
        input_rows = 0
        for i in port_idents.values():
            try:
                input_rows = max(input_rows, int(i.get("rows", 0) or 0))
            except (TypeError, ValueError):
                continue
        try:
            target = choose_dispatch(node, input_rows=input_rows,
                                     registry=self.registry)
        except NoCapableWorker as exc:
            # #1400: typed non-retryable evidence (do not burn retry budget)
            return GeoComputeNodeOutcome(
                ok=False, error_code="NO_CAPABLE_WORKER",
                error_message=str(exc)[:200],
                failure_class="deterministic_unsupported")
        except WorkerCapacityExhausted as exc:
            # ADR-0214 D4：worker 在但槽满 —— 瞬时饱和，退避重试。
            return GeoComputeNodeOutcome(
                ok=False, error_code="RESOURCE_EXHAUSTED",
                error_message=str(exc)[:200],
                failure_class="transient_remote")
        impl = self._durable if target == "durable" else self._local
        return await impl.execute(
            node=node, dag=dag, input_refs=input_refs, params=params,
            session_id=session_id, port_idents=port_idents,
            cancel_token=cancel_token, run_id=run_id,
            node_attempt=node_attempt, node_deadline_s=node_deadline_s,
            resource_envelope=resource_envelope)


def build_dispatcher(
    *, owner_scope: str = "", caller: Optional[Dict[str, Any]] = None,
    registry: Optional[WorkerRegistry] = None,
) -> Optional[Any]:
    """env → dispatcher 实例（local 模式返回 None = driver 内建路径）。"""
    mode = dispatch_mode()
    if mode == "durable":
        return DurableDispatcher(owner_scope=owner_scope, caller=caller,
                                 registry=registry)
    if mode == "auto":
        return AutoDispatcher(owner_scope=owner_scope, caller=caller,
                              registry=registry)
    return None


def _dispatch_sync(
    op_node, plan, session_id: str, owner_scope: str,
    *,
    input_refs: Optional[List[str]] = None,
    port_idents: Optional[Dict[str, Dict[str, str]]] = None,
    node: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    node_attempt: Optional[int] = None,
    resource_envelope: Optional[Dict[str, Any]] = None,
) -> dict:
    """Submit durable job with plan budget / deadline / placement (#1408).

    ADR-0214 D5：``resource_envelope`` 显式传入（driver 侧 rg.v1 节点估算
    快照）优先于节点 ``resources`` 声明投影；``run_id``/``node_attempt``
    显式传入优先于 runtime context 推断。
    """
    from app.services.geocompute.durable import dispatch_node
    from app.lib.runtime.context import current_runtime_context

    deadline_s = getattr(op_node, "deadline_s", None)
    budget = getattr(plan, "budget", None)
    if deadline_s is None and budget is not None:
        deadline_s = getattr(budget, "deadline_s", None)

    # port_idents → input_refs dict (dispatch_node expects dict[str,str])
    refs_map: Optional[dict] = None
    if isinstance(port_idents, dict) and port_idents:
        refs_map = {
            str(p): str((i or {}).get("ref") or (i or {}).get("fp") or "")
            for p, i in port_idents.items()
            if i
        }
        refs_map = {k: v for k, v in refs_map.items() if v} or None
    elif input_refs:
        refs_map = {str(i): str(r) for i, r in enumerate(input_refs)}

    if resource_envelope is None:
        resources = (node or {}).get("resources") if isinstance(node, dict) else None
        resource_envelope = resources if isinstance(resources, dict) else None

    if run_id is None:
        try:
            ctx = current_runtime_context()
            if ctx is not None and ctx.run_id:
                run_id = str(ctx.run_id)
        except Exception:  # noqa: BLE001
            run_id = None

    return dispatch_node(
        op_node, session_id=session_id,
        plan_fingerprint=plan.plan_id,
        deadline_s=deadline_s,
        budget=budget,
        run_id=run_id,
        node_attempt=node_attempt,
        input_refs=refs_map,
        resource_envelope=resource_envelope,
        owner_scope=owner_scope)


#: durable job 等待上界（秒）—— await 线程必须有界，否则 per-node 超时
#: 放弃等待后轮询线程池泄漏（review MAJOR）。
DEFAULT_DURABLE_WAIT_TIMEOUT_S = 300.0


def durable_wait_timeout_s(node_deadline_s: Optional[float] = None) -> float:
    """Wait cap for durable job polling (#1408).

    Env default is a floor; when the node/plan declares a longer deadline,
    honour it so GIS_WORKFLOW_DURABLE_WAIT_TIMEOUT_S does not silently
    clamp node timeouts >300s.
    """
    import os as _os

    try:
        base = max(5.0, float(_os.getenv(
            "GIS_WORKFLOW_DURABLE_WAIT_TIMEOUT_S",
            str(DEFAULT_DURABLE_WAIT_TIMEOUT_S))))
    except (TypeError, ValueError):
        base = DEFAULT_DURABLE_WAIT_TIMEOUT_S
    if node_deadline_s is not None:
        try:
            nd = float(node_deadline_s)
            if nd > 0:
                return max(base, nd)
        except (TypeError, ValueError):
            pass
    return base


def _await_job_sync(job_id: str, session_id: str, cancel_token: Any,
                    durable_wait_timeout_s: float = 300.0) -> dict:
    import time as _time

    from app.services.geocompute.durable import await_node_job

    # await_node_job 的 deadline_ts 是 time.monotonic() 域（uptime）
    return await_node_job(
        job_id, session_id=session_id,
        deadline_ts=_time.monotonic() + float(durable_wait_timeout_s),
        cancel_token=cancel_token)
