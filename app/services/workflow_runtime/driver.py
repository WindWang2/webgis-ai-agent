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
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import fingerprints as F
from app.services.workflow_runtime import machine as M
from app.services.workflow_runtime.adapters_geocompute import (
    GeoComputeNodeOutcome,
    build_node_plan,
    execute_node_plan,
    load_ref_features,
    node_executable_op,
)
from app.services.workflow_runtime.store import InstanceStore

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
    """波次执行驱动（async；阻塞面经 to_thread 卸载）。"""

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
            inst = await asyncio.to_thread(
                self.store.get_instance, instance_id, self.owner_scope)
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
            # STALE 重入队：先复用裁决（命中零重算），未命中 → READY 重算
            stale_nodes = [n for n, st in states.items()
                           if st == C.NodeState.STALE][: self.max_concurrency]
            for nid in stale_nodes:
                node = _dag_node(dag, nid)
                if node is None:
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
                    r = self.store.transition_node(
                        instance_id, nid, C.NodeState.READY,
                        expected_from=C.NodeState.STALE,
                        reason="STALE_RECOMPUTE", event="recompute")
                    if r.ok:
                        states[nid] = C.NodeState.READY
            ready = M.ready_set(dag, states)
            if not ready:
                if not any(s == C.NodeState.RUNNING for s in states.values()):
                    break  # 无 ready 无 running → 终态
                await asyncio.sleep(0.05)
                continue
            batch = ready[: self.max_concurrency]
            results = await asyncio.gather(*(
                self._run_node(instance_id, dag, nid, states, node_params,
                               session_id, run_token, cancel_token)
                for nid in batch
            ), return_exceptions=True)
            for nid, res in zip(batch, results):
                if isinstance(res, Exception):
                    logger.warning("[WorkflowRuntime] node %s raised: %s",
                                   nid, res)
                    self.store.transition_node(
                        instance_id, nid, C.NodeState.FAILED,
                        require_claim=True, claimed_by=run_token,
                        complete=True, reason="NODE_EXCEPTION",
                        event="driver",
                        patch={"error_code": "NODE_EXCEPTION"})
            # 租约续期（波次边界）
            await asyncio.to_thread(
                self.store.acquire_run_lease, instance_id,
                owner_scope=self.owner_scope, token=run_token)

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
            store.transition_node(
                instance_id, node_id, C.NodeState.BLOCKED,
                expected_from=C.NodeState.PENDING, reason="BINDING_BLOCKED",
                event="driver", patch={"binding": verdict})
            states[node_id] = C.NodeState.BLOCKED
            return
        if str(node.get("kind") or "") == "data_input":
            node_row0 = await asyncio.to_thread(
                store.get_node, instance_id, node_id)
            if not (node_row0 or {}).get("bound_ref"):
                # 未绑定 = 无产物在场（诚实阻断；record_tool_result /
                # attach 绑定后经 BLOCKED→READY 解除）
                store.transition_node(
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
            r = store.transition_node(
                instance_id, node_id, C.NodeState.READY,
                expected_from=C.NodeState.PENDING, reason="DEPS_OK",
                event="driver", patch={"binding": verdict})
            if not r.ok:
                return
            states[node_id] = C.NodeState.READY
        # 派发权仲裁（唯一）；输家直接退出（他认领他完成）
        r = store.transition_node(
            instance_id, node_id, C.NodeState.RUNNING,
            expected_from=C.NodeState.READY, claim=True,
            claimed_by=run_token, reason="DISPATCH", event="driver")
        if not r.ok:
            states[node_id] = (store.get_node(instance_id, node_id)
                               or {}).get("state", C.NodeState.READY)
            return
        states[node_id] = C.NodeState.RUNNING

        # 绑定态节点：data_input/output 的「执行」= 绑定传递（产物已存在）。
        kind = str(node.get("kind") or "")
        if kind in ("data_input", "output"):
            ref = (await asyncio.to_thread(
                store.get_node, instance_id, node_id)).get("bound_ref") \
                or (input_refs[0] if input_refs else "")
            out_fp = await self._ref_fingerprint(session_id, ref)
            store.transition_node(
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
                store.transition_node(
                    instance_id, node_id, C.NodeState.FAILED,
                    require_claim=True, claimed_by=run_token, complete=True,
                    reason="SUBWORKFLOW_UNSUPPORTED", event="driver",
                    patch={"error_code": "SUBWORKFLOW_UNSUPPORTED"})
                states[node_id] = C.NodeState.FAILED
                return
            sw = await self.subworkflow_executor(
                node, parent={"instance_id": instance_id,
                              "package_id": getattr(self, "_package_id", "")},
                parent_visited=self.parent_visited, session_id=session_id,
                input_refs=input_refs)
            if sw.get("ok"):
                store.transition_node(
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
                store.transition_node(
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

        op = node_executable_op(node)
        if op is None:
            blocked_verdict = {
                "node_id": node_id, "ok": False, "action": "blocked",
                "violations": [{"port": "", "code": "NODE_NOT_EXECUTABLE",
                                "detail": "无已接线 GeoCompute 算子"
                                          "（诚实阻断，不假装执行）"}],
                "disclosures": [], "disclosure": "NODE_NOT_EXECUTABLE"}
            # 已派发（RUNNING）后的不可执行 = 执行失败语义（RUNNING→BLOCKED
            # 非法 —— 在飞工作不能"变回"阻断态，只能失败并留证据）。
            store.transition_node(
                instance_id, node_id, C.NodeState.FAILED,
                require_claim=True, claimed_by=run_token, complete=True,
                reason="NODE_NOT_EXECUTABLE", event="driver",
                patch={"error_code": "NODE_NOT_EXECUTABLE",
                       "binding": blocked_verdict})
            states[node_id] = C.NodeState.FAILED
            return

        params = dict(node_params.get(node_id) or {})
        if self.plan_executor is not None:
            outcome = await self.plan_executor(
                node, input_refs, params,
                {"session_id": session_id, "caller": self.caller,
                 "cancel_token": cancel_token})
        else:
            outcome = await self._execute_via_geocompute(
                node, input_refs, params, session_id, cancel_token,
                port_idents)
        if outcome.ok:
            out_fp = await self._ref_fingerprint(session_id, outcome.output_ref)
            node_row = await asyncio.to_thread(
                store.get_node, instance_id, node_id)
            store.transition_node(
                instance_id, node_id, C.NodeState.SUCCEEDED,
                require_claim=True, claimed_by=run_token, complete=True,
                reason="EXEC_OK", event="driver",
                patch={"output_ref": outcome.output_ref,
                       "attempts_increment": True,
                       "attempt_log": {
                           "attempt": (node_row or {}).get("attempts", 0) + 1,
                           "status": "succeeded",
                           "backend": "geocompute_inprocess",
                           "output_ref": outcome.output_ref[:96],
                           "duration_ms": outcome.duration_ms},
                       "output_fingerprint": out_fp})
            states[node_id] = C.NodeState.SUCCEEDED
            await self._record_reuse(
                instance_id, dag, node, node_id, port_idents, session_id,
                outcome.output_ref, out_fp,
                effective_params=effective_params)
        else:
            node_row = await asyncio.to_thread(
                store.get_node, instance_id, node_id)
            attempts = (node_row or {}).get("attempts", 0) + 1
            store.transition_node(
                instance_id, node_id, C.NodeState.FAILED,
                require_claim=True, claimed_by=run_token, complete=True,
                reason=f"EXEC_FAIL:{outcome.error_code[:48]}",
                event="driver",
                patch={"error_code": outcome.error_code[:64],
                       "attempts_increment": True,
                       "attempt_log": {
                           "attempt": attempts,
                           "status": "failed",
                           "error_code": outcome.error_code[:64],
                           "backend": "geocompute_inprocess"}})
            states[node_id] = C.NodeState.FAILED

    # ── 输入收集 / 绑定校验 ───────────────────────────────────────────

    async def _gather_inputs(
        self, instance_id: str, dag: Dict[str, Any], node_id: str,
        session_id: str,
    ) -> Tuple[List[str], Dict[str, Any], Dict[str, Dict[str, str]]]:
        """上游 refs + 逐端口 descriptor + 内容身份（绑定门/复用指纹输入）。"""
        upstream = M.upstream_of(dag).get(node_id, ())[:4]
        node = _dag_node(dag, node_id) or {}
        port_names = [str(p.get("name") or f"input{i + 1}")
                      for i, p in enumerate(node.get("inputs") or [])]
        refs: List[str] = []
        port_descs: Dict[str, Any] = {}
        port_idents: Dict[str, Dict[str, str]] = {}
        for idx, up in enumerate(upstream):
            row = await asyncio.to_thread(self.store.get_node, instance_id, up)
            ref = (row or {}).get("output_ref") or (row or {}).get("bound_ref") or ""
            if not ref:
                continue
            refs.append(ref)
            port = port_names[idx] if idx < len(port_names) else \
                f"input{idx + 1}"
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
        rec = await asyncio.to_thread(
            self.reuse_index.find, self.owner_scope, fp)
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
            probe = await self.h_descriptor(session_id, rec.artifact_ref)
            if not isinstance(probe, dict):
                ok, why = False, "artifact_unresolvable"
        if not ok:
            # 索引自愈：拒绝即删除（fail-open，绝不倒灌执行路径）；
            # 拒绝 → 走真执行路径（调用方继续），RUNNING 态保持。
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
                       session_id, rec.artifact_ref),
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
        level = min(
            (i.get("level", "") for i in port_idents.values()),
            key=lambda lv: (lv != "content", lv != "profile_digest"),
            default="shape",
        ) if port_idents else "shape"
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
                    F.canonicalize_parameters(node.get("parameters") or {})),
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

        input_features = await load_ref_features(
            session_id, input_refs,
            max_rows=2_000)  # 适配器内联上界（诚实小步；更大走物化/裁剪）
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
            self.store.transition_node(
                instance_id, nid, C.NodeState.CANCELLED,
                reason="INSTANCE_CANCELLED", event="cancel")
        self.store.update_instance(
            instance_id, owner_scope=self.owner_scope,
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
