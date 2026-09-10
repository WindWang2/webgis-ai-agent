"""Workflow Runtime V5 —— 子工作流运行时（typed boundary + 义务继承）。

架构 §10 [R1-M7]：

- subworkflow 节点引用 package_id；实例化时惰性展开一层子实例
  （parent_instance_id / parent_node_id 反向指针）；
- 深度 ≤3（visited_packages 持久于实例行；环 → SUBWORKFLOW_CYCLE）；
- 每 owner 活跃子实例 ≤32（超界 → SUBWORKFLOW_CAP）；
- 义务：父子 recipe profile 经 V4 ``inherit_obligations`` 合并
  （via_subworkflow provenance + depth），合并链指纹落父节点 binding
  —— 不新造义务语义；
- 取消：父实例 cancel → 子实例 cancel；子终态映射父节点状态
  （succeeded→SUCCEEDED；failed/blocked→FAILED（error 附子实例 id）；
  cancelled→CANCELLED）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.workflow_runtime import contracts as C

logger = logging.getLogger(__name__)

#: 子工作流最大嵌套深度（含父；1 = 无嵌套）。
MAX_SUBWORKFLOW_DEPTH = 3
#: 每 owner 活跃子实例上限。
MAX_ACTIVE_SUBINSTANCES = 32


class SubworkflowError(Exception):
    """typed 子工作流错误（code → 父节点 FAILED 证据）。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def merged_obligation_fingerprint(
    parent_package_id: str, child_package_id: str, *, depth: int,
) -> Dict[str, Any]:
    """父子义务继承链（V4 inherit_obligations 单一语义；有界摘要）。"""
    from app.services.gis_harness.recipes import get_recipe_registry
    from app.services.gis_harness.workflow_v4.obligations import (
        inherit_obligations,
        profile_to_source,
    )

    reg = get_recipe_registry()
    parent = reg.get(parent_package_id)
    child = reg.get(child_package_id)
    chain = inherit_obligations([
        profile_to_source(parent_package_id,
                          getattr(parent, "workflow", None) if parent else None,
                          depth=0),
        profile_to_source(child_package_id,
                          getattr(child, "workflow", None) if child else None,
                          via_subworkflow=child_package_id, depth=depth),
    ])
    bounded = chain.to_bounded_dict()
    return {
        "obligation_chain_fp": bounded.get("fingerprint", ""),
        "sources": bounded.get("sources_count", 0),
        "obligations": len(bounded.get("obligations") or []),
        "conflicts": sum(1 for o in bounded.get("obligations") or []
                         if o.get("conflict")),
    }


class SubworkflowExecutor:
    """父驱动调用的子工作流展开+执行（生产实现；测试可注入假件）。

    V6 Phase F：父取消**全异步传播**（后台轮询父旗标 → 子实例持久取消）
    + deadline 继承（子 deadline ≤ 父剩余时间 —— 嵌套链不再突破父时限）。
    """

    #: 父取消轮询周期（秒）。
    PARENT_POLL_S = 0.5

    def __init__(self, service: Any, *, owner_scope: str,
                 caller: Optional[Dict[str, Any]] = None,
                 deadline_s: float = 60.0):
        self.service = service
        self.owner_scope = owner_scope
        self.caller = caller
        self.deadline_s = deadline_s

    async def __call__(
        self, node: Dict[str, Any], *, parent: Dict[str, Any],
        parent_visited: List[str], session_id: str,
        input_refs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """展开子实例并驱动至终态。返回 {ok, child_instance_id,
        error_code, obligation_chain, status}。"""
        from app.services.workflow_runtime.driver import Driver

        child_pkg = str(node.get("subworkflow_package_id", "") or "")
        if not child_pkg:
            return {"ok": False, "error_code": "SUBWORKFLOW_NO_PACKAGE_REF"}
        # 父取消传播（入口检查；展开后由后台轮询接管全异步传播）
        import asyncio as _aio

        parent_row = await _aio.to_thread(
            self.service.store.get_instance, parent["instance_id"],
            self.owner_scope)
        if parent_row is not None and parent_row.get("cancel_requested"):
            return {"ok": False, "error_code": "SUBWORKFLOW_CANCELLED"}
        # deadline 继承（V6）：子 ≤ 父剩余时间（嵌套链不再重置时钟）
        remaining_s = float(parent.get("remaining_s") or 0.0)
        child_deadline = min(self.deadline_s, remaining_s) \
            if remaining_s > 0 else self.deadline_s
        # 深度 + 环守卫（visited = 祖先链上的包指纹/包 id 集）
        visited = list(parent_visited or [])
        if parent.get("package_id") not in visited:
            visited.append(parent["package_id"])
        depth = len(visited)
        if depth >= MAX_SUBWORKFLOW_DEPTH:
            return {"ok": False, "error_code": "SUBWORKFLOW_DEPTH",
                    "detail": f"depth {depth} > {MAX_SUBWORKFLOW_DEPTH}"}
        if child_pkg in visited:
            return {"ok": False, "error_code": "SUBWORKFLOW_CYCLE",
                    "detail": child_pkg}
        # 每 owner 活跃子实例硬界
        active = await _aio.to_thread(
            self.service.store.count_active_subworkflows, self.owner_scope)
        if active >= MAX_ACTIVE_SUBINSTANCES:
            return {"ok": False, "error_code": "SUBWORKFLOW_CAP",
                    "detail": f"{active} active subinstances"}
        try:
            child = await _aio.to_thread(
                self.service.instantiate,
                child_pkg, owner_scope=self.owner_scope,
                session_id=session_id,
                parent_instance_id=parent["instance_id"],
                parent_node_id=str(node.get("node_id", "")),
                visited_packages=visited + [child_pkg])
        except Exception as exc:  # noqa: BLE001 — typed 错误映射
            return {"ok": False, "error_code": "SUBWORKFLOW_EXPAND_FAIL",
                    "detail": str(exc)[:120]}
        # typed boundary：父节点输入 refs 按声明序传入子实例 data 角色
        # （角色绑定语义与 attach 同源：PENDING→READY + bound_ref）
        import asyncio as _asyncio

        child_nodes = await _asyncio.to_thread(
            self.service.store.get_nodes, child["instance_id"])
        data_nodes = sorted(
            (n["node_id"] for n in child_nodes
             if n["state"] == C.NodeState.PENDING and not n["bound_ref"]
             and n["node_id"].startswith("data:")))
        for nid, ref in zip(data_nodes, list(input_refs or [])[:4]):
            if not ref or ref.startswith("wi:"):
                continue
            await _asyncio.to_thread(
                self.service.store.transition_node,
                child["instance_id"], nid, C.NodeState.READY,
                expected_from=C.NodeState.PENDING, reason="PARENT_BOUND",
                event="subworkflow", patch={"bound_ref": ref[:96]})
        chain = merged_obligation_fingerprint(
            parent["package_id"], child_pkg, depth=depth)
        driver = Driver(
            self.service.store, reuse_index=self.service.reuse_index,
            deadline_s=child_deadline, owner_scope=self.owner_scope,
            caller=self.caller,
            subworkflow_executor=self,
            parent_visited=visited + [child_pkg])
        child_dag = await self.service._instance_dag(child)

        async def _watch_parent() -> None:
            """父取消旗标 → 子实例持久取消（全异步传播；V6 Phase F）。"""
            import asyncio as _a

            while True:
                await _a.sleep(self.PARENT_POLL_S)
                row = await _a.to_thread(
                    self.service.store.get_instance, parent["instance_id"],
                    self.owner_scope)
                if row is None or row.get("cancel_requested"):
                    await self.service.cancel_instance(
                        child["instance_id"], owner_scope=self.owner_scope)
                    return

        watcher = _asyncio.create_task(_watch_parent())
        try:
            summary = await driver.run(
                child["instance_id"], child_dag,
                node_params=await self.service._node_params(
                    child, child_dag),
                session_id=session_id,
                run_token=f"sw-{child['instance_id'][-12:]}",
                package_fingerprint=child["package_fingerprint"])
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error_code": "SUBWORKFLOW_RUN_FAIL",
                    "detail": str(exc)[:120],
                    "child_instance_id": child["instance_id"],
                    "obligation_chain": chain}
        finally:
            watcher.cancel()
        status = summary.get("status")
        if status == C.InstanceStatus.SUCCEEDED:
            return {"ok": True, "status": status,
                    "child_instance_id": child["instance_id"],
                    "obligation_chain": chain}
        code = "SUBWORKFLOW_" + str(status or "failed").upper()
        return {"ok": False, "status": status, "error_code": code,
                "child_instance_id": child["instance_id"],
                "obligation_chain": chain}
