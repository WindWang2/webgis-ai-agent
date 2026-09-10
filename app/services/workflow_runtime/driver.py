"""Workflow Runtime V5 —— DAG 执行驱动（波次调度 + 节点认领 + 复用 + 取消/恢复）。

调度模型（架构 §4/§7/§8）：

- 拓扑波次：每波取 ``ready_set``（≤并发上界），逐节点：绑定门 → READY→
  RUNNING（CAS 认领）→ 复用裁决 / GeoCompute 执行 → RUNNING→终态 CAS；
- **双执行者协调** [R1-MINOR-7]：READY→RUNNING 的 CAS 是唯一派发权仲裁
  （claim token）；chat 侧 ``record_tool_result`` 对已被 driver 认领的
  节点写完成 → CLAIM_MISMATCH 拒绝；
- **复用闭环**：输入内容指纹 → ``node_reuse_fingerprint`` → 索引寻址 →
  eligibility → 命中 STALE/RUNNING→SUCCEEDED（reuse 证据）；拒绝 →
  索引自愈删除 + 真执行 → 成功后记录复用条目（shape 级只记录不复用）；
- 取消：``cancel_requested`` 在波次边界检查；传播到在飞 token；非终态
  节点沿表转 CANCELLED；
- 恢复：入口发现 RUNNING 且租约过期 → 孤儿复位 READY（attempts 保留）；
- quiescence：RUNNING 在飞时 pending changes 不应用（service 在波次
  边界 drain —— Wave 9）。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import fingerprints as F
from app.services.workflow_runtime import machine as M
from app.services.workflow_runtime import retry as RT
from app.services.workflow_runtime.adapters_geocompute import (
    ADAPTER_INLINE_ROW_CAP,
    GeoComputeNodeOutcome,
    build_node_plan,
    execute_node_plan,
    load_ref_features,
    node_executable_op,
)
from app.services.workflow_runtime.store import (
    DEFAULT_LEASE_TTL_S,
    DEFAULT_NODE_LEASE_TTL_S,
    InstanceStore,
)
from app.services.workflow_runtime.store import StoreUnavailable

logger = logging.getLogger(__name__)

_engine = None


def get_engine():
    """单进程共享 GeoExecutionEngine（进程内 checkpoint 跨 run 复用）。"""
    global _engine
    if _engine is None:
        from app.services.geocompute.executor import GeoExecutionEngine

        _engine = GeoExecutionEngine()
    return _engine


def reset_engine() -> None:
    """测试隔离。"""
    global _engine
    _engine = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Driver:
    """波次执行驱动（async；阻塞面经 to_thread 卸载）。

    V6：节点认领携带**节点租约**（``node_lease_ttl_s``）；波界为在飞节点
    续租 —— worker 死亡后节点租约过期即孤儿（与 run 租约独立判定）。
    """

    def __init__(
        self, store: InstanceStore, *,
        reuse_index: Any = None,
        max_concurrency: int = 4, deadline_s: float = 60.0,
        owner_scope: str = "", caller: Optional[Dict[str, Any]] = None,
        engine: Any = None,
        plan_executor: Optional[Any] = None,
        descriptor_probe: Optional[Any] = None,
        subworkflow_executor: Optional[Any] = None,
        parent_visited: Optional[List[str]] = None,
        node_lease_ttl_s: float = DEFAULT_NODE_LEASE_TTL_S,
        retry_policy: Optional[RT.RetryPolicy] = None,
        node_timeout_s: Optional[float] = None,
        dispatcher: Optional[Any] = None,
    ):
        self.store = store
        self.reuse_index = reuse_index
        self.max_concurrency = max(1, min(int(max_concurrency), 4))
        self.deadline_s = float(deadline_s)
        self.owner_scope = owner_scope
        self.caller = caller
        self._engine = engine
        #: 可注入 hooks（测试假件；None = 生产真实路径）。
        self.plan_executor = plan_executor
        self.descriptor_probe = descriptor_probe
        self.subworkflow_executor = subworkflow_executor
        self.parent_visited = list(parent_visited or [])
        self.node_lease_ttl_s = max(1.0, float(node_lease_ttl_s))
        #: V6 重试策略（None = 环境默认）；per-node 超时（None = 不限时，
        #: 由 run deadline 兜底）。
        self.retry_policy = retry_policy or RT.default_policy()
        self.node_timeout_s = (
            float(node_timeout_s) if node_timeout_s else None)
        #: V6 派发面（None = 进程内路径不变；service 按 env 装配
        #: Local/DurableDispatcher）。
        self.dispatcher = dispatcher

    # ── 主循环 ────────────────────────────────────────────────────────

    async def run(
        self, instance_id: str, dag: Dict[str, Any], *,
        node_params: Dict[str, Dict[str, Any]],
        session_id: str, run_token: str,
        package_fingerprint: str = "", package_id: str = "",
    ) -> Dict[str, Any]:
        """驱动实例至终态或 deadline。返回 {status, states}。"""
        optional_map = {
            str(n.get("node_id", "")): bool(n.get("optional", False))
            for n in dag.get("nodes") or []
        }
        self._package_fp = package_fingerprint
        self._package_id = package_id
        if not await asyncio.to_thread(
                self.store.acquire_run_lease, instance_id,
                owner_scope=self.owner_scope, token=run_token):
            return {"status": "busy", "states": {}}
        deadline = time.monotonic() + self.deadline_s
        self._parent_deadline = deadline
        cancel_token = self._make_cancel_token()
        try:
            return await self._run_loop(
                instance_id, dag, node_params, session_id, run_token,
                optional_map, deadline, cancel_token)
        finally:
            await asyncio.to_thread(
                self.store.release_run_lease, instance_id,
                owner_scope=self.owner_scope, token=run_token)

    async def _run_loop(
        self, instance_id: str, dag: Dict[str, Any],
        node_params: Dict[str, Dict[str, Any]], session_id: str,
        run_token: str, optional_map: Dict[str, bool], deadline: float,
        cancel_token: Any,
    ) -> Dict[str, Any]:
        while time.monotonic() < deadline:
            try:
                inst = await asyncio.to_thread(
                    self.store.get_instance, instance_id, self.owner_scope)
            except StoreUnavailable:
                # DB busy：退避后重试（deadline 兜底；不与 not_found 混同）
                await asyncio.sleep(0.1)
                continue
            if inst is None:
                return {"status": "not_found", "states": {}}
            states = await asyncio.to_thread(
                self.store.get_node_states, instance_id)
            if inst.get("cancel_requested"):
                await self._cancel_all(instance_id, states, cancel_token)
                break
            # 孤儿复位（崩溃恢复语义；attempts 保留）
            orphans = await asyncio.to_thread(
                self.store.find_orphan_running_nodes, instance_id,
                current_token=run_token)
            for orphan in orphans:
                r = self.store.transition_node(
                    instance_id, orphan, C.NodeState.READY,
                    reason="ORPHAN_LEASE_EXPIRED", event="recovery")
                if r.ok:
                    states[orphan] = C.NodeState.READY
            # 节点级取消（V6）：旗标置位即事实 ——
            # 1) 在飞 RUNNING 节点被标 → 点燃本地 cancel token（geocompute
            #    协作中止 → 下个 outcome 走 CANCELLED 路径）；
            # 2) 非在飞非终态节点被标 → 直接 CANCELLED（queued cancel 语义）。
            cancel_flags = await asyncio.to_thread(
                self.store.get_node_cancel_flags, instance_id)
            if cancel_flags:
                for nid in cancel_flags:
                    if states.get(nid) == C.NodeState.RUNNING \
                            and cancel_token is not None:
                        try:
                            cancel_token.cancel()
                        except Exception:  # noqa: BLE001
                            pass
                for nid, st in list(states.items()):
                    if st in (C.NodeState.RUNNING,) or st in (
                            C.NodeState.SUCCEEDED, C.NodeState.FAILED,
                            C.NodeState.SKIPPED, C.NodeState.CANCELLED):
                        continue
                    if nid in cancel_flags:
                        r = await asyncio.to_thread(
                            self.store.transition_node,
                            instance_id, nid, C.NodeState.CANCELLED,
                            reason="NODE_CANCELLED", event="cancel")
                        if r.ok:
                            states[nid] = C.NodeState.CANCELLED
            # STALE 重入队（拓扑安全，R1-C1）：上游未**结算**（非
            # SUCCEEDED/SKIPPED —— 含 READY/RUNNING/STALE）的节点本轮
            # 跳过 —— 否则会用「重算前的旧上游产物」做复用解除/派发，
            # 旧结果被洗成 SUCCEEDED。每波重读 states，上游重算完成后
            # 下游在后续波以新输入指纹自然结算。
            stale_nodes = [n for n, st in states.items()
                           if st == C.NodeState.STALE][: self.max_concurrency]
            for nid in stale_nodes:
                node = _dag_node(dag, nid)
                if node is None:
                    continue
                if any(states.get(u) not in (C.NodeState.SUCCEEDED,
                                             C.NodeState.SKIPPED)
                       for u in M.upstream_of(dag).get(nid, ())):
                    continue
                input_refs, port_descs, port_idents = await self._gather_inputs(
                    instance_id, dag, nid, session_id)
                stale_params = {
                    **(node.get("params") or {}),
                    **(node_params.get(nid) or {}),
                }
                if await self._try_reuse_stale(
                        instance_id, dag, node, nid, port_idents, session_id,
                        effective_params=stale_params):
                    states[nid] = C.NodeState.SUCCEEDED
                else:
                    r = await asyncio.to_thread(
                        self.store.transition_node,
                        instance_id, nid, C.NodeState.READY,
                        expected_from=C.NodeState.STALE,
                        reason="STALE_RECOMPUTE", event="recompute")
                    if r.ok:
                        states[nid] = C.NodeState.READY
            ready = M.ready_set(dag, states)
            ready, gated = await self._split_retry_gates(instance_id, ready)
            if not ready:
                if any(s == C.NodeState.RUNNING for s in states.values()) \
                        or gated:
                    # 有在飞或退避等待中的节点 → 不是终态（重试队列语义：
                    # next_ready_at 未到 ≠ 无事可做）
                    await asyncio.sleep(0.05)
                    continue
                break  # 无 ready 无 running 无退避等待 → 终态
            batch = sorted(
                ready,
                key=lambda nid: -_node_priority(_dag_node(dag, nid)),
            )[: self.max_concurrency]
            # V6 硬截止：deadline 到点停止**等待**在飞节点（V5 在 gather
            # 上无界等待 —— 卡死节点会拖穿 deadline）。被放弃的节点保持
            # RUNNING 认领态，由租约/恢复面收尾：本地在后台收尾、远端
            # worker 由孤儿复位接管 —— 绝不永久 zombie。
            tasks = [
                asyncio.create_task(
                    self._run_node(instance_id, dag, nid, states,
                                   node_params, session_id, run_token,
                                   cancel_token))
                for nid in batch
            ]
            done, pending = await asyncio.wait(
                tasks, timeout=max(0.05, deadline - time.monotonic()))
            for t in done:
                exc = t.exception()
                if exc is not None and not isinstance(exc,
                                                      asyncio.CancelledError):
                    nid = batch[tasks.index(t)]
                    logger.warning("[WorkflowRuntime] node %s raised: %s",
                                   nid, exc)
                    # 未预期异常 = NODE_EXCEPTION（可重试类）—— 走同一
                    # 补偿/重试裁决路径，绝不旁路。
                    await self._fail_or_cancel(
                        instance_id, nid, session_id, run_token,
                        GeoComputeNodeOutcome(
                            ok=False, error_code="NODE_EXCEPTION",
                            error_message=str(exc)[:200],
                            failure_class="transient_db"))
            if pending:
                # deadline 已到：点燃 cancel token（协作停止），放弃等待
                if cancel_token is not None:
                    with contextlib.suppress(Exception):
                        cancel_token.cancel()
                for t in pending:
                    t.cancel()
                logger.info(
                    "[WorkflowRuntime] run deadline hit with %d in-flight "
                    "node(s) instance=%s", len(pending), instance_id)
                break
            # 租约续期（波次边界）：run 租约 + 在飞节点租约（V6 两级续期）
            await asyncio.to_thread(
                self.store.acquire_run_lease, instance_id,
                owner_scope=self.owner_scope, token=run_token)
            for nid, st in states.items():
                if st == C.NodeState.RUNNING:
                    await asyncio.to_thread(
                        self.store.heartbeat_node, instance_id, nid,
                        token=run_token, ttl_s=self.node_lease_ttl_s)

        states = await asyncio.to_thread(
            self.store.get_node_states, instance_id)
        status = C.instance_status_from_nodes(states,
                                              optional_nodes=optional_map)
        await self._finalize_instance(instance_id, states, optional_map,
                                      status)
        return {"status": status, "states": states}

    async def _finalize_instance(
        self, instance_id: str, states: Dict[str, str],
        optional_map: Dict[str, bool], status: str,
    ) -> None:
        """实例行终态落库（driver = run 生命周期的单写者）。"""
        inst = await asyncio.to_thread(
            self.store.get_instance, instance_id, self.owner_scope)
        if inst is None or inst["status"] == status:
            return
        fields: Dict[str, Any] = {"status": status}
        if status in C.INSTANCE_TERMINAL_STATUSES:
            fields["terminal_at"] = _utcnow()
        if status == C.InstanceStatus.FAILED:
            failed = [n for n, s in states.items()
                      if s in (C.NodeState.FAILED, C.NodeState.BLOCKED)]
            fields["error_code"] = "NODES_INCOMPLETE"
            fields["error_detail"] = ",".join(failed[:6])[:255]
        await asyncio.to_thread(
            self.store.update_instance, instance_id,
            owner_scope=self.owner_scope, fields=fields)

    # ── 单节点执行 ────────────────────────────────────────────────────

    async def _split_retry_gates(
        self, instance_id: str, ready: List[str],
    ) -> Tuple[List[str], List[str]]:
        """ready 集按重试退避门二分（next_ready_at 未到的回到等待）。

        同查询顺带读 cancel_requested —— 被标节点不派发（pre-claim 门）。
        """
        if not ready:
            return [], []
        dispatchable: List[str] = []
        gated: List[str] = []
        now = _utcnow()
        for nid in ready:
            row = await asyncio.to_thread(
                self.store.get_node, instance_id, nid)
            if row is None:
                continue
            if row.get("cancel_requested"):
                r = await asyncio.to_thread(
                    self.store.transition_node,
                    instance_id, nid, C.NodeState.CANCELLED,
                    reason="NODE_CANCELLED", event="cancel")
                if r.ok:
                    pass  # states 由下一波刷新捕获
                continue
            gate = row.get("next_ready_at") or ""
            if gate:
                try:
                    if datetime.fromisoformat(gate) > now:
                        gated.append(nid)
                        continue
                except (TypeError, ValueError):
                    pass
            dispatchable.append(nid)
        return dispatchable, gated

    async def _run_node(
        self, instance_id: str, dag: Dict[str, Any], node_id: str,
        states: Dict[str, str], node_params: Dict[str, Dict[str, Any]],
        session_id: str, run_token: str, cancel_token: Any,
    ) -> None:
        store = self.store
        node = _dag_node(dag, node_id)
        if node is None:
            return
        input_refs, port_descs, port_idents = await self._gather_inputs(
            instance_id, dag, node_id, session_id)
        verdict = await asyncio.to_thread(
            _verify_node, node, port_descs)
        if not verdict["ok"]:
            # expected_from 不限定（PENDING/READY→BLOCKED 均合法，转移表
            # 裁决）—— READY 态（重算/恢复路径）阻断曾被 CAS 卡死空转到
            # deadline（R1-M1）。
            await asyncio.to_thread(
                store.transition_node,
                instance_id, node_id, C.NodeState.BLOCKED,
                reason="BINDING_BLOCKED", event="driver",
                patch={"binding": verdict})
            states[node_id] = C.NodeState.BLOCKED
            return
        if str(node.get("kind") or "") == "data_input":
            node_row0 = await asyncio.to_thread(
                store.get_node, instance_id, node_id)
            if not (node_row0 or {}).get("bound_ref"):
                # 未绑定 = 无产物在场（诚实阻断；record_tool_result /
                # attach 绑定后经 BLOCKED→READY 解除）
                await asyncio.to_thread(
                    store.transition_node,
                    instance_id, node_id, C.NodeState.BLOCKED,
                    reason="MISSING_BINDING", event="driver",
                    patch={"error_code": "MISSING_BINDING",
                           "binding": {"node_id": node_id, "ok": False,
                                       "action": "blocked",
                                       "violations": [{"port": "data",
                                                       "code": "MISSING_BINDING",
                                                       "detail": "数据角色无绑定 ref"}],
                                       "disclosures": []}})
                states[node_id] = C.NodeState.BLOCKED
                return
        if states.get(node_id, C.NodeState.PENDING) == C.NodeState.PENDING:
            r = await asyncio.to_thread(
                store.transition_node,
                instance_id, node_id, C.NodeState.READY,
                expected_from=C.NodeState.PENDING, reason="DEPS_OK",
                event="driver", patch={"binding": verdict})
            if not r.ok:
                return
            states[node_id] = C.NodeState.READY
        # 派发权仲裁（唯一）；输家直接退出（他认领他完成）。
        # claim 即写节点租约（V6）：本 driver 死亡 → 租约过期 → 孤儿可被
        # 接管，绝不 zombie RUNNING。
        r = await asyncio.to_thread(
            store.transition_node,
            instance_id, node_id, C.NodeState.RUNNING,
            expected_from=C.NodeState.READY, claim=True,
            claimed_by=run_token, reason="DISPATCH", event="driver",
            lease_ttl_s=self.node_lease_ttl_s)
        if not r.ok:
            states[node_id] = (await asyncio.to_thread(
                store.get_node, instance_id, node_id)
                or {}).get("state", C.NodeState.READY)
            return
        states[node_id] = C.NodeState.RUNNING
        # 派发即续租（R1-m8：单波可超 120s TTL，波界续租不够密）
        await asyncio.to_thread(
            store.acquire_run_lease, instance_id,
            owner_scope=self.owner_scope, token=run_token)
        # V6 执行期租约续期（真实竞态修复）：单次执行可能超过节点租约
        # TTL —— 在飞期间独立续约任务（ttl/3 周期），终态即停。否则长
        # 执行会被恢复面误判孤儿 → 双重执行。
        renewal = asyncio.create_task(self._renew_node_lease(
            instance_id, node_id, run_token))
        try:
            await self._run_node_claimed(
                instance_id, dag, node, node_id, states, node_params,
                session_id, run_token, cancel_token, input_refs, port_idents)
        finally:
            renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal

    async def _renew_node_lease(
        self, instance_id: str, node_id: str, run_token: str,
    ) -> None:
        """在飞节点租约续期（ttl/3 周期；持有人失配即停 —— 被接管）。"""
        period = max(0.05, self.node_lease_ttl_s / 3.0)
        while True:
            await asyncio.sleep(period)
            ok = await asyncio.to_thread(
                self.store.heartbeat_node, instance_id, node_id,
                token=run_token, ttl_s=self.node_lease_ttl_s)
            if not ok:
                return

    async def _run_node_claimed(
        self, instance_id: str, dag: Dict[str, Any], node: Dict[str, Any],
        node_id: str, states: Dict[str, str],
        node_params: Dict[str, Dict[str, Any]], session_id: str,
        run_token: str, cancel_token: Any, input_refs: List[str],
        port_idents: Dict[str, Dict[str, str]],
    ) -> None:
        """认领后的执行体（租约续期包裹中运行；所有终态路径经此收口）。"""
        store = self.store

        # 绑定态节点：data_input/output 的「执行」= 绑定传递（产物已存在）。
        kind = str(node.get("kind") or "")
        if kind in ("data_input", "output"):
            ref = (await asyncio.to_thread(
                store.get_node, instance_id, node_id)).get("bound_ref") \
                or (input_refs[0] if input_refs else "")
            out_fp = await self._ref_fingerprint(session_id, ref)
            await asyncio.to_thread(
                store.transition_node,
                instance_id, node_id, C.NodeState.SUCCEEDED,
                require_claim=True, claimed_by=run_token, complete=True,
                reason="BINDING_PASS_THROUGH", event="driver",
                patch={"output_ref": ref[:96],
                       "output_fingerprint": out_fp})
            states[node_id] = C.NodeState.SUCCEEDED
            return

        # 子工作流：展开子实例并驱动（取消/失败映射父节点状态）
        if kind == "subworkflow":
            if self.subworkflow_executor is None:
                await asyncio.to_thread(
                    store.transition_node,
                    instance_id, node_id, C.NodeState.FAILED,
                    require_claim=True, claimed_by=run_token, complete=True,
                    reason="SUBWORKFLOW_UNSUPPORTED", event="driver",
                    patch={"error_code": "SUBWORKFLOW_UNSUPPORTED"})
                states[node_id] = C.NodeState.FAILED
                return
            sw = await self.subworkflow_executor(
                node, parent={"instance_id": instance_id,
                              "package_id": getattr(self, "_package_id", ""),
                              # deadline 继承（V6 Phase F）：父剩余时间
                              "remaining_s": max(
                                  0.0, self._parent_deadline
                                  - time.monotonic())},
                parent_visited=self.parent_visited, session_id=session_id,
                input_refs=input_refs)
            if sw.get("ok"):
                await asyncio.to_thread(
                    store.transition_node,
                    instance_id, node_id, C.NodeState.SUCCEEDED,
                    require_claim=True, claimed_by=run_token, complete=True,
                    reason="SUBWORKFLOW_OK", event="driver",
                    patch={"output_ref":
                           f"wi:{sw.get('child_instance_id', '')}"[:96],
                           "binding": {"obligation_chain":
                                       sw.get("obligation_chain") or {}}})
                states[node_id] = C.NodeState.SUCCEEDED
            else:
                code = str(sw.get("error_code", "SUBWORKFLOW_FAIL"))
                await asyncio.to_thread(
                    store.transition_node,
                    instance_id, node_id, C.NodeState.FAILED,
                    require_claim=True, claimed_by=run_token, complete=True,
                    reason=code[:48], event="driver",
                    patch={"error_code": code[:64],
                           "binding": {"obligation_chain":
                                       sw.get("obligation_chain") or {},
                                       "detail": str(sw.get("detail", ""))[:200]}})
                states[node_id] = C.NodeState.FAILED
            return

        # 复用裁决（先于执行；命中零重算 + reuse 证据）。
        # 生效参数 = 运行时覆盖 ∪ DAG 声明（参数值进指纹 [R1-M8]）。
        effective_params = {
            **(node.get("params") or {}),
            **(node_params.get(node_id) or {}),
        }
        if await self._try_reuse(instance_id, dag, node, node_id,
                                 port_idents, session_id, run_token,
                                 effective_params=effective_params):
            return

        # 生效参数 = DAG 声明 defaults ∪ 运行时覆盖（V5 缺陷修复：声明
        # 参数从未到达执行面 —— buffer 以 distance=0 执行产出退化几何）。
        params = {
            **(node.get("params") or {}),
            **(node_params.get(node_id) or {}),
        }
        # V6 Phase H：真实执行面选择（确定性优先级）——
        # 测试钩子 > cartography 真实渲染 > science 真实聚合 > 派发面 >
        # geocompute in-process。science/cartography 是 workflow 域自有
        # 适配器（data_fabric/matplotlib/PDF 真实栈），不走 geocompute。
        _backend = "geocompute_inprocess"
        if self.plan_executor is not None:

            async def _invoke() -> Any:
                return await self.plan_executor(
                    node, input_refs, params,
                    {"session_id": session_id, "caller": self.caller,
                     "cancel_token": cancel_token})
        elif kind == "cartography":
            _backend = "cartography_render"
            from app.services.workflow_runtime.adapters_cartography import (
                execute_cartography_node,
            )

            async def _invoke() -> Any:
                return await execute_cartography_node(
                    node, input_refs=input_refs, params=params,
                    session_id=session_id)
        elif kind == "analysis" and _science_executable(node):
            _backend = "datafabric_science"
            from app.services.workflow_runtime.adapters_science import (
                execute_science_node,
            )

            async def _invoke() -> Any:
                return await execute_science_node(
                    node, input_refs=input_refs, params=params,
                    session_id=session_id)
        elif self.dispatcher is not None:
            # V6 派发面（local/durable 由 env 决策；durable 复用 geocompute
            # durable 通道的幂等键/心跳/WORKER_LOSS 既有真相）
            async def _invoke() -> GeoComputeNodeOutcome:
                return await self.dispatcher.execute(
                    node=node, dag=dag, input_refs=input_refs,
                    params=params, session_id=session_id,
                    port_idents=port_idents, cancel_token=cancel_token)
        else:
            op = node_executable_op(node)
            if op is None:
                blocked_verdict = {
                    "node_id": node_id, "ok": False, "action": "blocked",
                    "violations": [{"port": "", "code": "NODE_NOT_EXECUTABLE",
                                    "detail": "无已接线执行路径"
                                              "（诚实阻断，不假装执行）"}],
                    "disclosures": [],
                    "disclosure": "NODE_NOT_EXECUTABLE"}
                # 已派发（RUNNING）后的不可执行 = 执行失败语义（RUNNING→
                # BLOCKED 非法 —— 在飞工作不能"变回"阻断态，只能失败留证）。
                await asyncio.to_thread(
                    store.transition_node,
                    instance_id, node_id, C.NodeState.FAILED,
                    require_claim=True, claimed_by=run_token, complete=True,
                    reason="NODE_NOT_EXECUTABLE", event="driver",
                    patch={"error_code": "NODE_NOT_EXECUTABLE",
                           "binding": blocked_verdict})
                states[node_id] = C.NodeState.FAILED
                return

            async def _invoke() -> GeoComputeNodeOutcome:
                return await self._execute_via_geocompute(
                    node, input_refs, params, session_id, cancel_token,
                    port_idents)
        if self.node_timeout_s:
            # per-node 超时（V6）：截断等待者（线程内计算自然结束后被丢弃；
            # durable 通道由 worker 侧硬超时兜底）→ NODE_TIMEOUT 可重试。
            try:
                outcome = await asyncio.wait_for(
                    _invoke(), timeout=self.node_timeout_s)
            except asyncio.TimeoutError:
                outcome = GeoComputeNodeOutcome(
                    ok=False, error_code="NODE_TIMEOUT",
                    error_message=f"node exceeded {self.node_timeout_s}s",
                    failure_class="transient_remote")
        else:
            outcome = await _invoke()
        if outcome.ok:
            # 完成边界的取消裁决（jobs 同纪律：cancelling 中的 late success
            # 收敛为 cancelled）—— 旗标在窗口内置位则成功作废、产物补偿。
            inst_now = await asyncio.to_thread(
                store.get_instance, instance_id, self.owner_scope)
            node_row_c = await asyncio.to_thread(
                store.get_node, instance_id, node_id)
            cancel_hit = (
                (inst_now or {}).get("cancel_requested")
                or (node_row_c or {}).get("cancel_requested"))
            if cancel_hit:
                await self._fail_or_cancel(
                    instance_id, node_id, session_id, run_token,
                    GeoComputeNodeOutcome(ok=False, error_code="CANCELLED",
                                          output_ref=outcome.output_ref,
                                          duration_ms=outcome.duration_ms),
                    cancelled=True)
                states[node_id] = C.NodeState.CANCELLED
                return
            out_fp = await self._ref_fingerprint(session_id, outcome.output_ref)
            node_row = await asyncio.to_thread(
                store.get_node, instance_id, node_id)
            await asyncio.to_thread(
                store.transition_node,
                instance_id, node_id, C.NodeState.SUCCEEDED,
                require_claim=True, claimed_by=run_token, complete=True,
                reason="EXEC_OK", event="driver",
                patch={"output_ref": outcome.output_ref,
                       "attempts_increment": True,
                       "attempt_log": {
                           "attempt": (node_row or {}).get("attempts", 0) + 1,
                           "status": "succeeded",
                           "backend": _backend,
                           "output_ref": outcome.output_ref[:96],
                           "duration_ms": outcome.duration_ms},
                       "output_fingerprint": out_fp})
            states[node_id] = C.NodeState.SUCCEEDED
            await self._record_reuse(
                instance_id, dag, node, node_id, port_idents, session_id,
                outcome.output_ref, out_fp,
                effective_params=effective_params)  # 同指纹源（R1-m1）
        else:
            await self._fail_or_cancel(
                instance_id, node_id, session_id, run_token, outcome)
            fresh_row = await asyncio.to_thread(
                store.get_node, instance_id, node_id)
            states[node_id] = (fresh_row or {}).get(
                "state", C.NodeState.FAILED)

    async def _fail_or_cancel(
        self, instance_id: str, node_id: str, session_id: str,
        run_token: str, outcome: GeoComputeNodeOutcome, *,
        cancelled: Optional[bool] = None, backend: str = "geocompute_inprocess",
    ) -> None:
        """失败/取消收敛（V6）：补偿半提交产物 → 重试裁决 → 终态落库。

        - CANCELLED：RUNNING→CANCELLED（R1-m7）；已 materialize 的产物
          走补偿清理（绝不留孤儿 session ref 不对账）；
        - 失败：RUNNING→FAILED（attempt 证据）→ 可重试且预算未尽 →
          FAILED→READY + ``next_ready_at`` 退避门（journal RETRY_SCHEDULED）；
          预算耗尽 → RETRY_EXHAUSTED 证据。
        """
        store = self.store
        node_row = await asyncio.to_thread(store.get_node, instance_id, node_id)
        attempts = (node_row or {}).get("attempts", 0) + 1
        is_cancel = bool(cancelled) or \
            outcome.error_code == "CANCELLED"
        if outcome.output_ref:
            # 半提交产物补偿（成功路径不会进这里）
            from app.services.workflow_runtime import compensation as CP

            await CP.compensate_ref(
                session_id, outcome.output_ref, node_id=node_id,
                instance_id=instance_id, attempt=attempts,
                reason="CANCELLED_WITH_ARTIFACT" if is_cancel
                else "FAILED_WITH_ARTIFACT",
                store=store)
        if is_cancel:
            await asyncio.to_thread(
                store.transition_node,
                instance_id, node_id, C.NodeState.CANCELLED,
                require_claim=True, claimed_by=run_token,
                reason="EXEC_CANCELLED", event="driver",
                patch={"attempts_increment": True,
                       "attempt_log": {
                           "attempt": attempts, "status": "cancelled",
                           "backend": backend}})
            return
        first = await asyncio.to_thread(
            store.transition_node,
            instance_id, node_id, C.NodeState.FAILED,
            require_claim=True, claimed_by=run_token, complete=True,
            reason=f"EXEC_FAIL:{outcome.error_code[:48]}",
            event="driver",
            patch={"error_code": outcome.error_code,
                   "attempts_increment": True,
                   "attempt_log": {
                       "attempt": attempts,
                       "status": "failed",
                       "error_code": outcome.error_code,
                       "failure_class": getattr(outcome, "failure_class", ""),
                       "backend": backend}})
        if not first.ok:
            return
        policy = self.retry_policy
        retryable = RT.error_retryable(outcome.error_code,
                                       getattr(outcome, "failure_class", ""))
        if not retryable or policy.attempts_exhausted(attempts):
            if retryable:
                await asyncio.to_thread(
                    store.append_event, instance_id,
                    kind=C.EventKind.RETRY_EXHAUSTED, node_id=node_id,
                    reason=f"ATTEMPTS_{attempts}_{outcome.error_code[:48]}",
                    actor="driver", attempt=attempts)
            return
        delay = policy.delay_for(attempts)
        from app.services.workflow_runtime.store import _utcnow

        next_ready = _utcnow() + timedelta(seconds=delay)
        r = await asyncio.to_thread(
            store.transition_node,
            instance_id, node_id, C.NodeState.READY,
            expected_from=C.NodeState.FAILED,
            reason="RETRY_SCHEDULED", event="retry",
            patch={"next_ready_at": next_ready})
        if r.ok:
            await asyncio.to_thread(
                store.append_event, instance_id,
                kind=C.EventKind.RETRY_SCHEDULED, node_id=node_id,
                reason=f"BACKOFF_{delay:.2f}S_ATTEMPT_{attempts}",
                actor="driver", attempt=attempts,
                payload={"error_code": outcome.error_code,
                         "failure_class": getattr(outcome, "failure_class", ""),
                         "next_ready_at": next_ready.isoformat()})

    # ── 输入收集 / 绑定校验 ───────────────────────────────────────────

    async def _gather_inputs(
        self, instance_id: str, dag: Dict[str, Any], node_id: str,
        session_id: str,
    ) -> Tuple[List[str], Dict[str, Any], Dict[str, Dict[str, str]]]:
        """上游 refs + 逐端口 descriptor + 内容身份（绑定门/复用指纹输入）。"""
        # 端口对齐按边 to_port（fan-in ≥2 位置对齐会错配，R1-m6）；
        # 无端口信息的边退化为声明序。
        edge_ports = M.upstream_ports(dag).get(node_id, [])
        node = _dag_node(dag, node_id) or {}
        port_names = [str(p.get("name") or f"input{i + 1}")
                      for i, p in enumerate(node.get("inputs") or [])]
        refs: List[str] = []
        port_descs: Dict[str, Any] = {}
        port_idents: Dict[str, Dict[str, str]] = {}
        for idx, (up, to_port) in enumerate(
                [(u, "") for u in M.upstream_of(dag).get(node_id, ())][:4]
                if not edge_ports else edge_ports[:4]):
            row = await asyncio.to_thread(self.store.get_node, instance_id, up)
            ref = (row or {}).get("output_ref") or (row or {}).get("bound_ref") or ""
            if not ref:
                continue
            refs.append(ref)
            port = to_port if to_port in port_names else (
                port_names[idx] if idx < len(port_names)
                else f"input{idx + 1}")
            if ref.startswith("wi:"):
                # 子工作流实例引用：无会话载荷身份（诚实 unknown，
                # 不虚构 descriptor 事实）
                desc = {"ref_id": ref}
            else:
                desc = await self.h_descriptor(session_id, ref)
            port_descs[port] = desc
            port_idents[port] = F.input_content_identity(
                desc if isinstance(desc, dict) else {})
        return refs, port_descs, port_idents

    async def h_descriptor(self, session_id: str, ref: str) -> Any:
        """live descriptor 探测（复用指纹/绑定门共用；[R1-M3] 非 registry 冻结）。"""
        if self.descriptor_probe is not None:
            return await self.descriptor_probe(session_id, ref)
        from app.services.session_data import session_data_manager

        try:
            return await session_data_manager.get_ref_descriptor(
                session_id, ref)
        except Exception:  # noqa: BLE001 — 探测失败 = 缺证（诚实）
            return None

    # ── 复用闭环 ──────────────────────────────────────────────────────

    def _reuse_fp(
        self, dag: Dict[str, Any], node: Dict[str, Any], node_id: str,
        port_idents: Dict[str, Dict[str, str]], package_fingerprint: str,
        effective_params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """（reuse_fingerprint, env_fp）。

        ``effective_params`` 是**生效参数值**（运行时覆盖 ∪ DAG 默认）——
        参数值变化必须使复用失效（Epic §16 参数 canonicalization）。
        """
        env_fp = F.environment_fingerprint(
            compiler_version=str(dag.get("compiler_version", "") or "4.0.0"),
            execution_plan_version=_plan_version(),
            methodology_fingerprint=str(
                dag.get("methodology_fingerprint", "") or ""))
        algo = str(node.get("algorithm_id") or node.get("capability") or "")
        fp = F.node_reuse_fingerprint(
            package_fingerprint=package_fingerprint,
            node_id=node_id,
            algorithm_id=algo,
            params=effective_params if effective_params is not None
            else node.get("parameters") or {},
            inputs=port_idents,
            env_fp=env_fp,
        )
        return fp, env_fp

    async def _try_reuse(
        self, instance_id: str, dag: Dict[str, Any], node: Dict[str, Any],
        node_id: str, port_idents: Dict[str, Dict[str, str]],
        session_id: str, run_token: str,
        effective_params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """RUNNING 态复用裁决（claim 持有者路径）。"""
        return await self._reuse_resolve(
            instance_id, dag, node, node_id, port_idents, session_id,
            run_token, require_claim=True, expected_from=C.NodeState.RUNNING,
            effective_params=effective_params)

    async def _try_reuse_stale(
        self, instance_id: str, dag: Dict[str, Any], node: Dict[str, Any],
        node_id: str, port_idents: Dict[str, Dict[str, str]],
        session_id: str,
        effective_params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """STALE 态复用裁决（无执行在飞，无需 claim）。"""
        return await self._reuse_resolve(
            instance_id, dag, node, node_id, port_idents, session_id,
            run_token="", require_claim=False,
            expected_from=C.NodeState.STALE,
            effective_params=effective_params)

    async def _reuse_resolve(
        self, instance_id: str, dag: Dict[str, Any], node: Dict[str, Any],
        node_id: str, port_idents: Dict[str, Dict[str, str]],
        session_id: str, run_token: str, *, require_claim: bool,
        expected_from: str,
        effective_params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        from app.services.workflow_runtime.reuse import (
            evaluate_eligibility,
        )

        if self.reuse_index is None or not self._package_fp:
            return False
        fp, _env = self._reuse_fp(dag, node, node_id, port_idents,
                                  self._package_fp,
                                  effective_params=effective_params)
        from app.services.workflow_runtime.reuse import (
            anonymous_session_scope,
        )

        rec = await asyncio.to_thread(
            self.reuse_index.find, self.owner_scope, fp,
            session_scope=anonymous_session_scope(self.owner_scope,
                                                  session_id))
        if rec is None:
            return False
        ok, why = evaluate_eligibility(
            rec, package_fingerprint=self._package_fp,
            current_inputs={p: {"fp": i.get("fp", ""),
                                "content_revision": i.get("content_revision", "")}
                            for p, i in port_idents.items()},
            descriptor_probe=None,  # 存活探测走下方异步 h_descriptor
        )
        if ok:
            # 存活探测在**产物所属会话**内进行（session 存储是隔离域；
            # 用当前会话探测他处产物必失败 → 曾误删他处活缓存 [R1-M2]）
            probe = await self.h_descriptor(
                rec.artifact_session_id or session_id, rec.artifact_ref)
            if not isinstance(probe, dict):
                ok, why = False, "artifact_unresolvable"
        if not ok:
            # 索引自愈：输入/包/存活失配即删除（fail-open，绝不倒灌执行
            # 路径）；level 级拒绝（shape 只记录不复用）是**策略而非失配**
            # —— 条目保留，不空转删除重写（R1 NIT）。
            if not why.startswith("level_"):
                await asyncio.to_thread(
                    self.reuse_index.invalidate, self.owner_scope, fp)
            logger.info("[WorkflowRuntime] reuse denied %s/%s: %s",
                        instance_id, node_id, why)
            return False
        # 命中：→SUCCEEDED（复用解除，零重算；expected_from 由路径决定）
        node_row = await asyncio.to_thread(self.store.get_node,
                                           instance_id, node_id)
        resolved = self.store.transition_node(
            instance_id, node_id, C.NodeState.SUCCEEDED,
            expected_from=expected_from,
            require_claim=require_claim, claimed_by=run_token, complete=True,
            reason="REUSE_HIT", event="reuse",
            patch={"output_ref": rec.artifact_ref,
                   "reuse": C.ReuseEvidence(
                       reused=True, reuse_fingerprint=fp,
                       artifact_ref=rec.artifact_ref,
                       source_instance_id=rec.source_instance_id,
                       verified_inputs=[
                           f"{p}:{i.get('fp', '')[:12]}"
                           for p, i in list(port_idents.items())[:8]],
                       fingerprint_level=rec.fingerprint_level,
                   ).to_bounded_dict(),
                   "output_fingerprint": await self._ref_fingerprint(
                       rec.artifact_session_id or session_id,
                       rec.artifact_ref),
                   "attempt_log": {
                       "attempt": (node_row or {}).get("attempts", 0),
                       "status": "reused",
                       "backend": "reuse_index",
                       "output_ref": rec.artifact_ref[:96]}})
        return bool(resolved.ok)

    async def _record_reuse(
        self, instance_id: str, dag: Dict[str, Any], node: Dict[str, Any],
        node_id: str, port_idents: Dict[str, Dict[str, str]],
        session_id: str, output_ref: str, output_fp: str,
        effective_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        from app.services.workflow_runtime.reuse import (
            ReuseRecord,
            anonymous_session_scope,
        )

        if self.reuse_index is None or not self._package_fp or not output_ref:
            return
        fp, env_fp = self._reuse_fp(dag, node, node_id, port_idents,
                                    self._package_fp,
                                    effective_params=effective_params)
        # 级别聚合（R1-M4）：任一端口身份缺席或不可复用级 → 整条降级
        # shape（只记录不复用）—— 最低水位，绝不掩盖空身份端口。
        levels = [i.get("level", "") for i in port_idents.values()]
        if not port_idents or any(lv not in F.REUSABLE_LEVELS
                                  for lv in levels):
            level = "shape"
        else:
            level = min(
                levels,
                key=lambda lv: (lv != "content", lv != "profile_digest"),
            )
        await asyncio.to_thread(
            self.reuse_index.record,
            ReuseRecord(
                owner_scope=self.owner_scope,
                reuse_fingerprint=fp,
                session_scope=anonymous_session_scope(
                    self.owner_scope, session_id),
                node_id=node_id,
                package_fingerprint=self._package_fp,
                artifact_ref=output_ref,
                artifact_session_id=session_id,
                fingerprint_level=level if level in F.REUSABLE_LEVELS
                else "shape",
                input_fingerprints={
                    p: {"level": i.get("level", ""),
                        "fp": i.get("fp", ""),
                        "content_revision": i.get("content_revision", "")}
                    for p, i in port_idents.items()},
                algorithm_id=str(node.get("algorithm_id")
                                 or node.get("capability") or ""),
                params_fp=F.canonical_fingerprint(
                    F.canonicalize_parameters(
                        effective_params
                        if effective_params is not None
                        else node.get("parameters") or {})),
                env_fp=env_fp,
                source_instance_id=instance_id,
            ))

    # ── 执行 / 取消 ───────────────────────────────────────────────────

    def _make_cancel_token(self) -> Any:
        try:
            from app.lib.cancel_token import CancellationToken

            return CancellationToken()
        except Exception:  # noqa: BLE001 — 取消原语缺席：无取消能力继续
            return None

    async def _execute_via_geocompute(
        self, node: Dict[str, Any], input_refs: List[str],
        params: Dict[str, Any], session_id: str, cancel_token: Any,
        port_idents: Dict[str, Dict[str, str]],
    ) -> GeoComputeNodeOutcome:
        """构建 [op, MATERIALIZE] 计划并同步执行（to_thread 卸载）。"""
        from types import SimpleNamespace

        # 适配器内联上界（与 build_node_plan 的 ADAPTER_INLINE_ROW_CAP
        # 同一常数；超界 = typed 失败 INPUT_TRUNCATED，绝不静默裁剪
        # [R1-C2]——更大数据通道 = 先物化/裁剪上游，属 follow-up）。
        max_inline = ADAPTER_INLINE_ROW_CAP
        input_features, truncated = await load_ref_features(
            session_id, input_refs, max_rows=max_inline)
        if truncated:
            return GeoComputeNodeOutcome(
                ok=False, error_code="INPUT_TRUNCATED",
                error_message=(
                    f"inline input exceeds adapter cap {max_inline} rows; "
                    "materialize/narrow upstream first"))
        ns = SimpleNamespace(node_id=node.get("node_id", ""),
                             kind=node.get("kind", ""),
                             role=node.get("role", ""))
        plan = build_node_plan(
            ns, node_params=params, input_features=input_features,
            input_fingerprints={
                p: i.get("fp", "") for p, i in port_idents.items()},
            session_id=session_id, owner_scope=self.owner_scope)
        engine = self._engine or get_engine()
        return await asyncio.to_thread(
            _execute_plan_sync, engine, plan, session_id, self.caller,
            cancel_token)

    async def _cancel_all(self, instance_id: str,
                          states: Dict[str, str], cancel_token: Any) -> None:
        if cancel_token is not None and hasattr(cancel_token, "cancel"):
            try:
                cancel_token.cancel()
            except Exception:  # noqa: BLE001
                pass
        for nid, st in list(states.items()):
            if st in (C.NodeState.SUCCEEDED, C.NodeState.FAILED,
                      C.NodeState.SKIPPED, C.NodeState.CANCELLED):
                continue
            await asyncio.to_thread(
                self.store.transition_node,
                instance_id, nid, C.NodeState.CANCELLED,
                reason="INSTANCE_CANCELLED", event="cancel")
        await asyncio.to_thread(
            self.store.update_instance, instance_id,
            owner_scope=self.owner_scope,
            fields={"status": C.InstanceStatus.CANCELLED,
                    "terminal_at": _utcnow()})

    async def _ref_fingerprint(self, session_id: str, ref: str) -> str:
        if not ref:
            return ""
        desc = await self.h_descriptor(session_id, ref)
        return F.input_content_identity(
            desc if isinstance(desc, dict) else {}).get("fp", "")


def _execute_plan_sync(engine, plan, session_id, caller, cancel_token):
    """线程内同步执行（异常分类由 execute_node_plan 兜底）。"""
    return execute_node_plan(plan, session_id=session_id, caller=caller,
                             cancel_token=cancel_token, engine=engine)


def _plan_version() -> int:
    from app.services.geocompute.plan import EXECUTION_PLAN_VERSION

    return int(EXECUTION_PLAN_VERSION)


def _dag_node(dag: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    return next((n for n in dag.get("nodes") or []
                 if n.get("node_id") == node_id), None)


def _node_priority(node: Optional[Dict[str, Any]]) -> int:
    """节点派发优先级（有界词表；高先派；同优先级保持声明序 = FIFO 公平）。"""
    try:
        raw = int((node or {}).get("priority", 5))
    except (TypeError, ValueError):
        return 5
    return raw if -10 <= raw <= 10 else 5


def _science_executable(node: Dict[str, Any]) -> bool:
    """analysis 节点是否有已接线的真实 science 执行路径（data_fabric）。"""
    from app.services.workflow_runtime.adapters_science import (
        science_executable,
    )

    cap = str(node.get("capability") or node.get("algorithm_id") or "")
    return science_executable(cap)


def _verify_node(node: Dict[str, Any],
                 input_descs: Dict[str, Any]) -> Dict[str, Any]:
    """节点绑定校验（线程内；dict 形态 TypedPort → SimpleNamespace）。"""
    from types import SimpleNamespace

    from app.services.workflow_runtime.binding import (
        verify_node_bindings,
        violations_to_disclosure,
    )

    ns = SimpleNamespace(
        node_id=node.get("node_id", ""),
        inputs=[SimpleNamespace(**p) for p in node.get("inputs") or []],
        outputs=[],
    )
    verdict = verify_node_bindings(ns, input_descs)
    d = verdict.to_bounded_dict()
    d["disclosure"] = violations_to_disclosure(verdict)
    return d
