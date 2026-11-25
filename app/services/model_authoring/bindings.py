"""模型实例输入绑定 adapter（data_input 节点 bound_ref 预绑）。

边界纪律：
- 本 adapter **不重写 driver**：只做运行前置绑定（PENDING→READY +
  ``bound_ref`` patch），执行/绑定门/透传完成仍由既有 Driver 的
  ``BINDING_PASS_THROUGH`` 与 ``verify_port`` 权威裁决；
- ref 必须**本会话可读**（session_data 权威）——跨会话/跨项目 ref 天然
  miss（返回 None，不泄露存在性），与技能作业 prepare 的 REF_NOT_FOUND
  语义同门；
- 幂等：同 ref 重复绑定 = OK_IDEMPOTENT；不同 ref 覆盖已 READY 节点 =
  拒绝（已在执行态的节点绝不改绑定）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from app.services.workflow_runtime.contracts import NodeState

logger = logging.getLogger(__name__)

#: 单次绑定节点数上限（编辑器 data_input ≤ 角色词表 16；防御性上界）。
MAX_BINDINGS_PER_CALL = 16


class BindingError(Exception):
    """实例绑定 typed 错误（code 稳定；HTTP/Pi 面直接映射）。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class ModelInstanceBinder:
    """WorkflowRuntimeService 之上的模型输入绑定面（组合，不继承）。"""

    def __init__(self, runtime_service: Any):
        self._runtime = runtime_service

    async def bind_inputs(
        self, instance_id: str, *, owner_scope: str, session_id: str,
        bindings: Dict[str, str],
    ) -> Dict[str, Dict[str, Any]]:
        """{data_input node_id: session ref} → 逐节点绑定回执。

        回执：{node_id: {ok, code, bound_ref}}；任一失败不回滚其他节点
        （部分绑定诚实披露——driver 对未绑 data_input 会 MISSING_BINDING
        阻断，调用方可补绑后重跑）。
        """
        if not bindings:
            raise BindingError("NO_BINDINGS", "bindings map is empty")
        if len(bindings) > MAX_BINDINGS_PER_CALL:
            raise BindingError(
                "TOO_MANY_BINDINGS",
                f"{len(bindings)} exceeds {MAX_BINDINGS_PER_CALL}")
        store = self._runtime.store
        inst = await asyncio.to_thread(
            store.get_instance, instance_id, owner_scope)
        if inst is None:
            raise BindingError("INSTANCE_NOT_FOUND", instance_id)
        dag = await self._runtime._instance_dag(inst)
        kinds = {
            str(n.get("node_id", "")): str(n.get("kind", ""))
            for n in dag.get("nodes") or []
        }
        states = await asyncio.to_thread(store.get_node_states, instance_id)
        results: Dict[str, Dict[str, Any]] = {}
        for node_id, ref in list(bindings.items())[:MAX_BINDINGS_PER_CALL]:
            results[node_id] = await self._bind_one(
                instance_id, node_id=node_id, ref=ref,
                owner_scope=owner_scope, session_id=session_id,
                kinds=kinds, states=states)
        return results

    async def _bind_one(
        self, instance_id: str, *, node_id: str, ref: str,
        owner_scope: str, session_id: str,
        kinds: Dict[str, str], states: Dict[str, str],
    ) -> Dict[str, Any]:
        store = self._runtime.store
        ref = str(ref or "").strip()
        if not ref:
            return {"node": node_id, "ok": False, "code": "EMPTY_REF",
                    "bound_ref": ""}
        if kinds.get(node_id) != "data_input":
            return {"node": node_id, "ok": False,
                    "code": "NOT_DATA_INPUT",
                    "bound_ref": ""}
        current = states.get(node_id, NodeState.PENDING)
        if current in (NodeState.RUNNING, NodeState.SUCCEEDED,
                       NodeState.CANCELLED, NodeState.SKIPPED):
            return {"node": node_id, "ok": False,
                    "code": f"STATE:{current[:24]}", "bound_ref": ""}
        # ref 必须本会话可读（session_data 权威；miss 不泄露存在性）
        from app.services.session_data import session_data_manager

        payload = await session_data_manager.get(session_id, ref)
        if payload is None:
            return {"node": node_id, "ok": False, "code": "REF_NOT_FOUND",
                    "bound_ref": ""}
        node_row = await asyncio.to_thread(store.get_node, instance_id, node_id)
        prev_ref = str((node_row or {}).get("bound_ref") or "")
        if prev_ref and prev_ref == ref[:96] and current == NodeState.READY:
            return {"node": node_id, "ok": True, "code": "OK_IDEMPOTENT",
                    "bound_ref": ref[:96]}
        if prev_ref and prev_ref != ref[:96] and current == NodeState.READY:
            return {"node": node_id, "ok": False, "code": "REBIND_REFUSED",
                    "bound_ref": prev_ref}
        # PENDING/BLOCKED/STALE → READY + bound_ref（执行交 driver 透传）
        r = await asyncio.to_thread(
            store.transition_node, instance_id, node_id, NodeState.READY,
            reason="MODEL_BOUND", event="model",
            patch={"bound_ref": ref[:96]})
        if not r.ok and r.code not in ("OK_IDEMPOTENT",):
            return {"node": node_id, "ok": False, "code": r.code,
                    "bound_ref": ""}
        return {"node": node_id, "ok": True, "code": "BOUND",
                "bound_ref": ref[:96]}


__all__ = ["ModelInstanceBinder", "BindingError", "MAX_BINDINGS_PER_CALL"]
