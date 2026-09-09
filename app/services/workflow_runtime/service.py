"""Workflow Runtime V5 —— 服务门面（API 与 chat 集成的唯一入口）。

职责（架构 §8/§9/§11）：编译→注册→实例化→驱动→变更→取消→投影。
事实源纪律：

- 注册时 re-emit 比对指纹（包内容事实源 = V4 编译器）；
- ``record_tool_result`` 是 chat 路径唯一写入口：会话锁外、fail-open、
  单次 CAS（锁内长事务回归向量已消除 [R1-C3/M5]）；
- data 角色绑定复用 ``workflow_instance._derive_bound_refs`` 同一规则
  （不新造 role→ref 语义 [R1-M5]）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import machine as M
from app.services.workflow_runtime.recompute import ChangeApplier
from app.services.workflow_runtime.registry import PackageRegistry
from app.services.workflow_runtime.store import InstanceStore
from app.services.workflow_runtime.store import new_run_token

logger = logging.getLogger(__name__)

#: 总开关（chat 集成面；默认开 —— 注册/实例化/记录是 fail-open 附加事实）。
#: driver 的自动启动由 AUTORUN 独立控制（默认关：执行只走显式 API）。
def runtime_enabled() -> bool:
    return os.getenv("GIS_WORKFLOW_RUNTIME", "1") not in ("0", "false", "False")


def autorun_enabled() -> bool:
    return os.getenv("GIS_WORKFLOW_RUNTIME_AUTORUN", "0") in (
        "1", "true", "True")


class WorkflowRuntimeError(Exception):
    """typed 错误（code 机器可读）。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class InstanceBusy(WorkflowRuntimeError):
    def __init__(self, instance_id: str, detail: str = ""):
        super().__init__("INSTANCE_BUSY", detail or instance_id)


def owner_scope_for(caller: Optional[Dict[str, Any]],
                    session_id: Optional[str] = None) -> str:
    """owner 域（复用 geocompute 同一实现 —— 单一哈希域纪律）。"""
    from app.services.geocompute.executor import owner_scope_for as _osf

    return _osf(caller, session_id)


class WorkflowRuntimeService:
    """门面（方法同步/异步与 I/O 对齐；store 经 to_thread 卸载）。"""

    def __init__(self, *, store: Optional[InstanceStore] = None,
                 registry: Optional[PackageRegistry] = None,
                 reuse_index: Any = None):
        self.store = store or InstanceStore()
        self.registry = registry or PackageRegistry()
        if reuse_index is None:
            from app.services.workflow_runtime.reuse import ReuseIndex

            reuse_index = ReuseIndex()
        self.reuse_index = reuse_index

    # ── 编译 → 注册 → 实例化 ─────────────────────────────────────────

    def compile_and_register(
        self, query: str, *, owner_scope: str, recipe_id: str = "",
        profile: Optional[Dict[str, Any]] = None, project_id: str = "",
    ) -> Dict[str, Any]:
        """确定性编译 + 包注册（draft）。返回 {package, compilation 摘要}。"""
        from app.services.gis_harness.workflow_v4.compiler_v4 import (
            compile_workflow_v4,
        )
        from app.services.gis_harness.workflow_v4.package import (
            emit_workflow_package,
        )
        from app.services.workflow_runtime.fingerprints import (
            environment_fingerprint,
        )

        compilation = compile_workflow_v4(
            query, recipe_id=recipe_id or "", profile=profile)
        if not compilation.methodology_family:
            raise WorkflowRuntimeError(
                "METHODOLOGY_FAMILY_UNMAPPED", query[:120])
        package = emit_workflow_package(compilation)
        env_fp = environment_fingerprint(
            compiler_version=package.compiler_version,
            execution_plan_version=_plan_version(),
            methodology_fingerprint=package.methodology_fingerprint)
        row = self.registry.register(
            package, owner_scope=owner_scope,
            environment_fingerprint=env_fp, project_id=project_id)
        return {"package": row, "package_fingerprint": package.fingerprint,
                "methodology_family": package.methodology_family,
                "compiler_version": package.compiler_version}

    def instantiate(
        self, package_id: str, *, owner_scope: str, session_id: str = "",
        version: str = "", project_id: str = "",
        parent_instance_id: str = "", parent_node_id: str = "",
        visited_packages: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """包 → 运行实例（全节点 PENDING；data 节点绑定由 attach/record 补齐）。"""
        from app.services.gis_harness.workflow_v4.package import (
            check_compatibility,
        )
        from app.services.gis_harness.workflow_v4.package import (
            WorkflowPackage,
        )

        row = self.registry.resolve(
            package_id, owner_scope=owner_scope, version=version)
        if row is None:
            raise WorkflowRuntimeError("PACKAGE_NOT_FOUND", package_id)
        pkg = WorkflowPackage(
            package_id=row["package_id"], version=row["version"],
            schema_version=row["schema_version"],
            compiler_version=row["compiler_version"],
            methodology_family=row["methodology_family"],
            recipe_fingerprint=row["recipe_fingerprint"],
            methodology_fingerprint=row["methodology_fingerprint"],
            compiled_form=row.get("compiled_form") or {},
            fingerprint=row["fingerprint"],
        )
        compat = check_compatibility(pkg)
        if not compat.compatible:
            raise WorkflowRuntimeError(
                compat.reason_code, compat.migration_hint or compat.detail)
        dag = (pkg.compiled_form or {}).get("typed_dag") or {}
        nodes = [
            {"node_id": str(n.get("node_id", "")),
             "optional": bool(n.get("optional", False))}
            for n in dag.get("nodes") or [] if n.get("node_id")
        ]
        if not nodes:
            raise WorkflowRuntimeError("EMPTY_DAG", package_id)
        # 同 plan 活实例 → superseded（防幽灵 RUNNING [R1-M6]）
        if session_id:
            for old in self.store.list_session_instances(
                    session_id, owner_scope=owner_scope, active_only=True):
                if old["package_id"] == package_id:
                    self.store.update_instance(
                        old["instance_id"], owner_scope=owner_scope,
                        fields={"status": C.InstanceStatus.SUPERSEDED,
                                "error_code": "SUPERSEDED_BY_NEW_INSTANCE"})
        inst = self.store.create_instance(
            package_id=row["package_id"],
            package_version=row["version"],
            package_fingerprint=row["fingerprint"],
            owner_scope=owner_scope, session_id=session_id,
            project_id=project_id, node_specs=nodes,
            parent_instance_id=parent_instance_id,
            parent_node_id=parent_node_id)
        if visited_packages:
            self.store.update_instance(
                inst["instance_id"], owner_scope=owner_scope,
                fields={"visited_packages": list(visited_packages)[:8]})
        return inst

    # ── 驱动 / 取消 / 变更 ───────────────────────────────────────────

    async def run_instance(
        self, instance_id: str, *, owner_scope: str,
        caller: Optional[Dict[str, Any]] = None,
        deadline_s: float = 60.0,
        node_params: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """显式执行入口（chat 路径绝不自动调用 —— AUTORUN 默认关）。"""
        inst = await asyncio.to_thread(
            self.store.get_instance, instance_id, owner_scope)
        if inst is None:
            raise WorkflowRuntimeError("INSTANCE_NOT_FOUND", instance_id)
        if inst["status"] in (C.InstanceStatus.CANCELLED,
                              C.InstanceStatus.SUPERSEDED):
            raise InstanceBusy(instance_id, f"status={inst['status']}")
        if inst["status"] in (C.InstanceStatus.SUCCEEDED,
                              C.InstanceStatus.FAILED):
            # 终态实例重驱（增量重算）：复位 running（CAS；竞争失败 = 他
            # driver 先行 → busy）
            reset = await asyncio.to_thread(
                self.store.update_instance, instance_id,
                owner_scope=owner_scope,
                expected_revision=inst["revision"],
                fields={"status": C.InstanceStatus.RUNNING,
                        "error_code": "", "error_detail": ""})
            if reset is None:
                raise InstanceBusy(instance_id, "concurrent rerun")
        dag = await self._instance_dag(inst)
        from app.services.workflow_runtime.driver import Driver
        from app.services.workflow_runtime.subworkflow import (
            SubworkflowExecutor,
        )

        driver = Driver(
            self.store, reuse_index=self.reuse_index,
            deadline_s=deadline_s, owner_scope=owner_scope, caller=caller,
            subworkflow_executor=SubworkflowExecutor(
                self, owner_scope=owner_scope, caller=caller,
                deadline_s=deadline_s))
        run_token = new_run_token()
        effective = node_params if node_params is not None \
            else await self._node_params(inst, dag)
        summary = await driver.run(
            instance_id, dag,
            node_params=effective,
            session_id=inst["session_id"], run_token=run_token,
            package_fingerprint=inst["package_fingerprint"],
            package_id=inst["package_id"])
        # 完成边界：drain pending changes（quiescence 已由 run 保证）；
        # 实例终态由 driver._finalize_instance 落库（run 生命周期单写者）。
        if summary.get("status") == C.InstanceStatus.RUNNING:
            applier = ChangeApplier(self.store, owner_scope=owner_scope)
            await applier.drain_pending(instance_id, dag)
        final = await asyncio.to_thread(
            self.store.get_instance, instance_id, owner_scope)
        return {"run": summary,
                "instance": _safe_instance(final)}

    async def cancel_instance(
        self, instance_id: str, *, owner_scope: str,
    ) -> Dict[str, Any]:
        inst = await asyncio.to_thread(
            self.store.get_instance, instance_id, owner_scope)
        if inst is None:
            raise WorkflowRuntimeError("INSTANCE_NOT_FOUND", instance_id)
        if inst["status"] in C.INSTANCE_TERMINAL_STATUSES:
            return {"cancelled": False, "status": inst["status"]}
        updated = await asyncio.to_thread(
            self.store.update_instance, instance_id, owner_scope=owner_scope,
            fields={"cancel_requested": True})
        return {"cancelled": updated is not None,
                "status": (updated or inst)["status"]}

    async def apply_changes(
        self, instance_id: str, changes: List[C.PendingChange], *,
        owner_scope: str, source: str = "api",
    ) -> Dict[str, Any]:
        inst = await asyncio.to_thread(
            self.store.get_instance, instance_id, owner_scope)
        if inst is None:
            raise WorkflowRuntimeError("INSTANCE_NOT_FOUND", instance_id)
        dag = await self._instance_dag(inst)
        applier = ChangeApplier(self.store, owner_scope=owner_scope)
        seq = len(inst.get("decisions") or []) + 1
        result = await applier.apply(
            instance_id, dag, changes, seq=seq, source=source)
        # 重算调度显式分离：apply 只标 STALE；执行由调用方带**生效参数**
        # 调 run_instance（参数值是执行输入，门面不虚构默认语义）。
        return result

    async def recompute_plan(
        self, instance_id: str, changes: List[C.PendingChange], *,
        owner_scope: str,
    ) -> Dict[str, Any]:
        """dry-run：只算 RecomputePlan + 复用候选预览（不落任何状态）。"""
        inst = await asyncio.to_thread(
            self.store.get_instance, instance_id, owner_scope)
        if inst is None:
            raise WorkflowRuntimeError("INSTANCE_NOT_FOUND", instance_id)
        from app.services.workflow_runtime.recompute import (
            is_style_only,
            plan_for_changes,
        )

        plan = plan_for_changes(await self._instance_dag(inst), changes)
        states = await asyncio.to_thread(self.store.get_node_states,
                                         instance_id)
        succeeded = {n for n, s in states.items()
                     if s == C.NodeState.SUCCEEDED}
        return {
            "style_only": is_style_only(plan),
            "recompute": plan.recompute,
            "reuse": [n for n in plan.reuse if n in succeeded],
            "would_mark_stale": [
                n for n in plan.recompute
                if states.get(n) in (C.NodeState.SUCCEEDED,
                                     C.NodeState.READY,
                                     C.NodeState.STALE)],
            "explanations": plan.explanations[:6],
            "changed_dimensions": list(plan.changed_dimensions),
        }

    # ── chat / session 集成（fail-open；会话锁外）────────────────────

    async def attach_session_plan(
        self, session_id: str, *, owner_scope: str, query: str,
        recipe_id: str, profile: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """计划合成时：注册包 + 实例化 + 预绑数据角色（fail-open）。"""
        try:
            reg = self.compile_and_register(
                query, owner_scope=owner_scope, recipe_id=recipe_id,
                profile=profile)
            inst = self.instantiate(
                reg["package"]["package_id"], owner_scope=owner_scope,
                session_id=session_id)
            await self._prefill_role_bindings(inst, owner_scope)
            return inst
        except Exception:  # noqa: BLE001 — 附加事实失败绝不阻断会话
            logger.info("[WorkflowRuntime] attach failed session=%s",
                        session_id, exc_info=True)
            return None

    async def record_tool_result(
        self, session_id: str, *, capability: str, bound_ref: str,
        owner_scope: str, status: str = "complete",
    ) -> Optional[Dict[str, Any]]:
        """工具结果 → 节点绑定/完成证据（chat 执行路径的事实回写）。

        - capability 行 → ``cap:<capability>`` 节点；
        - role 节点：recipe wf_profile 的 capability_hint→role 映射同规则
          补绑（``_derive_bound_refs`` 同源 [R1-M5]）；
        - 对 driver 认领中的节点（claimed_by 非空且非本通道）让位。
        """
        try:
            actives = await asyncio.to_thread(
                self.store.list_session_instances, session_id,
                owner_scope=owner_scope, active_only=True)
            if not actives:
                return None
            results = []
            for inst in actives[:2]:
                dag = await self._instance_dag(inst)
                targets = _capability_nodes(dag, capability)
                roles = await self._role_nodes_for_capability(
                    inst, capability)
                for nid in targets + roles:
                    if status != "complete" or not bound_ref:
                        continue
                    results.append(await self._chat_complete_node(
                        inst["instance_id"], nid, bound_ref, dag))
            return {"bound": results} if results else None
        except Exception:  # noqa: BLE001 — 证据回写绝不倒灌工具路径
            logger.info("[WorkflowRuntime] record_tool_result failed "
                        "session=%s", session_id, exc_info=True)
            return None

    async def _chat_complete_node(
        self, instance_id: str, node_id: str, ref: str,
        dag: Dict[str, Any],
    ) -> Dict[str, Any]:
        """chat 通道完成：PENDING→READY→RUNNING(claim=chat)→SUCCEEDED。"""
        store = self.store
        # 上游未完成 → 不虚构完成（诚实跳过，等 driver/后续工具结果）
        states = await asyncio.to_thread(store.get_node_states, instance_id)
        upstream = M.upstream_of(dag).get(node_id, ())
        if not all(states.get(u) in (C.NodeState.SUCCEEDED,
                                     C.NodeState.SKIPPED)
                   for u in upstream):
            return {"node": node_id, "ok": False, "code": "UPSTREAM_PENDING"}
        claim = f"chat:{node_id[:48]}"
        r = store.transition_node(
            instance_id, node_id, C.NodeState.READY,
            expected_from=C.NodeState.PENDING, reason="CHAT_BOUND",
            event="chat", patch={"bound_ref": ref[:96]})
        if not r.ok and r.code not in ("OK_IDEMPOTENT",):
            # 可能已在 READY/BLOCKED：BLOCKED→READY（绑定补齐解除）
            r = store.transition_node(
                instance_id, node_id, C.NodeState.READY,
                expected_from=C.NodeState.BLOCKED, reason="CHAT_UNBLOCK",
                event="chat", patch={"bound_ref": ref[:96]})
            if not r.ok:
                return {"node": node_id, "ok": False, "code": r.code}
        r2 = store.transition_node(
            instance_id, node_id, C.NodeState.RUNNING,
            expected_from=C.NodeState.READY, claim=True, claimed_by=claim,
            reason="CHAT_DISPATCH", event="chat")
        if not r2.ok:
            return {"node": node_id, "ok": False, "code": r2.code}
        r3 = store.transition_node(
            instance_id, node_id, C.NodeState.SUCCEEDED,
            require_claim=True, claimed_by=claim, complete=True,
            reason="CHAT_DONE", event="chat",
            patch={"output_ref": ref[:96], "bound_ref": ref[:96]})
        return {"node": node_id, "ok": r3.ok, "code": r3.code}

    async def record_style_change(
        self, session_id: str, *, owner_scope: str, target: str = "",
    ) -> Optional[Dict[str, Any]]:
        """样式突变 → style 维变更（fail-open；科学零触碰）。

        目标实例 = 该会话最近的非终态实例；无则在**最近 succeeded** 实例
        上记录呈现态决策（完成后改样式是主场景；cancelled/superseded
        不记录 —— 已终止的执行序不再接受任何变更）。
        """
        try:
            rows = await asyncio.to_thread(
                self.store.list_session_instances, session_id,
                owner_scope=owner_scope, active_only=False)
            actives = [r for r in rows
                       if r["status"] == C.InstanceStatus.RUNNING] or [
                r for r in rows if r["status"] == C.InstanceStatus.SUCCEEDED]
            if not actives:
                return None
            change = C.PendingChange(
                dimension="style", target_kind="style", target=target[:64],
                source="style_hook")
            return await self.apply_changes(
                actives[0]["instance_id"], [change], owner_scope=owner_scope,
                source="style_hook")
        except Exception:  # noqa: BLE001
            logger.info("[WorkflowRuntime] record_style_change failed",
                        exc_info=True)
            return None

    # ── 内部 ─────────────────────────────────────────────────────────

    async def _instance_dag(self, inst: Dict[str, Any]) -> Dict[str, Any]:
        row = await asyncio.to_thread(
            self.registry.resolve, inst["package_id"],
            owner_scope=inst["owner_scope"],
            version=inst["package_version"])
        if row is None:
            raise WorkflowRuntimeError(
                "PACKAGE_MISSING",
                f"{inst['package_id']}@{inst['package_version']}")
        return ((row.get("compiled_form") or {}).get("typed_dag") or {})

    async def _prefill_role_bindings(
        self, inst: Dict[str, Any], owner_scope: str,
    ) -> None:
        """实例化时从 SessionPlan 预绑数据角色（``_derive_bound_refs`` 同规则）。"""
        from app.services.gis_harness.workflow_instance import (
            _derive_bound_refs,
        )
        from app.services.gis_harness.recipes import get_recipe_registry
        from app.services.session_plan import load_session_plan

        if not inst.get("session_id"):
            return
        plan = await load_session_plan(inst["session_id"])
        if plan is None or not isinstance(plan.gis_chapter, dict):
            return
        recipe = get_recipe_registry().get(inst["package_id"])
        wf_profile = getattr(recipe, "workflow", None) if recipe else None
        if wf_profile is None:
            return
        bound = _derive_bound_refs(plan.gis_chapter, wf_profile)
        dag = await self._instance_dag(inst)
        for nid in dag.get("nodes") or []:
            if str(nid.get("kind", "")) != "data_input":
                continue
            role = str(nid.get("role", "") or "")
            ref = bound.get(role)
            if not ref:
                continue
            self.store.transition_node(
                inst["instance_id"], str(nid["node_id"]),
                C.NodeState.READY, expected_from=C.NodeState.PENDING,
                reason="ROLE_BOUND", event="attach",
                patch={"bound_ref": ref[:96]})

    async def _role_nodes_for_capability(
        self, inst: Dict[str, Any], capability: str,
    ) -> List[str]:
        """capability → 其供给的 data:role 节点（capability_hint 映射）。"""
        from app.services.gis_harness.recipes import get_recipe_registry

        recipe = get_recipe_registry().get(inst["package_id"])
        wf_profile = getattr(recipe, "workflow", None) if recipe else None
        if wf_profile is None:
            return []
        roles = [
            str(getattr(r, "role", ""))
            for r in (getattr(wf_profile, "data_roles", []) or [])
            if str(getattr(r, "capability_hint", "")) == capability
        ]
        dag = await self._instance_dag(inst)
        return [
            str(n["node_id"]) for n in dag.get("nodes") or []
            if n.get("kind") == "data_input" and n.get("role") in roles
        ]

    async def _node_params(
        self, inst: Dict[str, Any], dag: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        """包解析参数 → 节点参数注入（analysis 节点执行输入）。"""
        params = (await self._package_compiled(inst)).get("parameters") or []
        analysis_nodes = [str(n.get("node_id", "")) for n in dag.get("nodes")
                          or [] if n.get("kind") == "analysis"]
        out: Dict[str, Dict[str, Any]] = {}
        for p in params[:16]:
            if isinstance(p, dict) and p.get("name"):
                for nid in analysis_nodes[:1]:
                    out.setdefault(nid, {})[str(p["name"])] = p.get("value")
        return out

    async def _package_compiled(self, inst: Dict[str, Any]) -> Dict[str, Any]:
        row = await asyncio.to_thread(
            self.registry.resolve, inst["package_id"],
            owner_scope=inst["owner_scope"],
            version=inst["package_version"])
        return dict((row or {}).get("compiled_form") or {})


def _dag_node(dag: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    return next((n for n in dag.get("nodes") or []
                 if n.get("node_id") == node_id), None)


def _capability_nodes(dag: Dict[str, Any], capability: str) -> List[str]:
    return [str(n.get("node_id", "")) for n in dag.get("nodes") or []
            if n.get("kind") == "analysis"
            and n.get("capability") == capability]


def _safe_instance(inst: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return inst or {}


def _plan_version() -> int:
    from app.services.geocompute.plan import EXECUTION_PLAN_VERSION

    return int(EXECUTION_PLAN_VERSION)


#: 进程级单例（测试 reset）。
_service: Optional[WorkflowRuntimeService] = None


def get_service() -> WorkflowRuntimeService:
    global _service
    if _service is None:
        _service = WorkflowRuntimeService()
    return _service


def reset_service() -> None:
    global _service
    _service = None
