"""Workflow Runtime V5 —— GeoCompute 适配器（workflow node → ExecutionPlan）。

确定性映射 + 诚实边界（架构 §7）：

- transform 节点的 operation ∈ 显式接线表 → 对应 NodeCategory；
- analysis 节点：无已接线 GeoCompute 算子 → ``NODE_NOT_EXECUTABLE``
  （节点 BLOCKED + 披露）—— **绝不假装执行**；
- 每个可执行工作流节点编译为两节点计划：``[op, MATERIALIZE]`` ——
  产物经既有 session ref 正门落存（MATERIALIZE 是大载荷离开执行图的
  唯一正门），``ev.output_ref`` 自动携带 ref；
- 输入数据经 ``parameters["features"]`` 内联（``_features_from_input``
  既有契约），适配器侧显式行数上界；
- dataset_fingerprints 携带输入内容指纹 → geocompute 自身语义指纹天然
  跨实例去重 [R1-M8]；``parameters.idempotent=True``（durable 通道要求）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.geocompute.plan import (
    ExecutionNode,
    ExecutionPlan,
    ExecutionPolicyKind,
    NodeCategory,
    NodeReusePolicy,
    PayloadKind,
    ResourceBudget,
)

logger = logging.getLogger(__name__)

#: transform operation → (NodeCategory, geocompute op 名)。
#: geocompute 未接线的 operation 不入表（诚实 NODE_NOT_EXECUTABLE）。
_TRANSFORM_OPS: Dict[str, Tuple[NodeCategory, str]] = {
    "buffer": (NodeCategory.VECTOR_OPERATION, "buffer"),
    "clip": (NodeCategory.VECTOR_OPERATION, "clip"),
    "dissolve": (NodeCategory.VECTOR_OPERATION, "dissolve"),
    "overlay": (NodeCategory.VECTOR_OPERATION, "overlay"),
    "filter": (NodeCategory.FILTER, ""),
    "aggregate": (NodeCategory.AGGREGATE, ""),
    "reproject": (NodeCategory.REPROJECT, ""),
}

#: 适配器内联输入的行数硬上界（无 budget 时的兜底；正常走 plan budget）。
ADAPTER_INLINE_ROW_CAP = 50_000


class NodeNotExecutable(Exception):
    """工作流节点无已接线执行路径（诚实阻断，调用方落 BLOCKED 证据）。"""

    def __init__(self, node_id: str, reason: str):
        super().__init__(f"node {node_id} not executable: {reason}")
        self.node_id = node_id
        self.reason = reason


def _node_field(node: Any, field: str) -> str:
    """dict / 对象双形态字段读取（driver 传 dict，测试可传 SimpleNamespace）。"""
    if isinstance(node, dict):
        return str(node.get(field, "") or "")
    return str(getattr(node, field, "") or "")


def node_executable_op(node: Any) -> Optional[Tuple[NodeCategory, str]]:
    """工作流节点 → (category, op)；不可执行 → None。

    - transform：node_id 的 operation 段查 ``_TRANSFORM_OPS``；
    - analysis：geocompute 数据面无对应科学算子 → None（诚实）；
    - data_input / output：绑定态节点，不由 GeoCompute 执行 → None。
    """
    kind = _node_field(node, "kind")
    if kind == "transform":
        node_id = _node_field(node, "node_id")
        op = node_id.split(":")[1] if node_id.count(":") >= 1 else ""
        return _TRANSFORM_OPS.get(op)
    return None


def _input_params_for(
    category: NodeCategory,
    op: str,
    params: Dict[str, Any],
    input_features: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """op 节点 parameters 组装（输入经既有 ``parameters["features"]`` 内联契约）。"""
    out = dict(params or {})
    if category in (NodeCategory.VECTOR_OPERATION, NodeCategory.FILTER):
        if op:
            out["op"] = op
        out["features"] = input_features
    elif category == NodeCategory.AGGREGATE:
        # aggregate 走 _rows_from_input（payloads 优先）——单节点计划无
        # 上游 payload，聚合类当前不内联（诚实：不可执行路径）。
        return {}
    return out


def build_node_plan(
    node: Any,
    *,
    node_params: Dict[str, Any],
    input_features: List[Dict[str, Any]],
    input_fingerprints: Dict[str, str],
    session_id: str,
    owner_scope: str,
) -> ExecutionPlan:
    """可执行工作流节点 → [op, MATERIALIZE] 两节点 ExecutionPlan。"""
    mapped = node_executable_op(node)
    if mapped is None:
        raise NodeNotExecutable(
            str(getattr(node, "node_id", "") or ""),
            "no wired geocompute operator for this node kind/operation")
    category, op = mapped
    wf_node_id = str(getattr(node, "node_id", "")[:120])
    op_params = _input_params_for(category, op, node_params, input_features)
    if not op_params:
        raise NodeNotExecutable(wf_node_id,
                                f"category {category.value} unsupported inline")
    if len(input_features) > ADAPTER_INLINE_ROW_CAP:
        raise NodeNotExecutable(
            wf_node_id,
            f"inline input rows {len(input_features)} exceeds adapter cap "
            f"{ADAPTER_INLINE_ROW_CAP}（先物化/裁剪上游）")
    op_node = ExecutionNode(
        node_id=f"wf:{wf_node_id}",
        category=category,
        operation=op,
        parameters=op_params,
        produces=PayloadKind.FEATURES,
        accepts=[PayloadKind.ANY],
        dataset_fingerprints=dict(input_fingerprints),
        reuse=NodeReusePolicy.ALLOW,
        deterministic=True,
        cancellable=True,
        policy=ExecutionPolicyKind.IN_PROCESS,
    )
    mat_node = ExecutionNode(
        node_id=f"wf:{wf_node_id}:out",
        category=NodeCategory.MATERIALIZE,
        parameters={"prefix": f"wfv5-{wf_node_id[:48]}".replace(":", "-")},
        produces=PayloadKind.REF,
        accepts=[PayloadKind.FEATURES],
        inputs=[op_node.node_id],
        reuse=NodeReusePolicy.DISALLOW,
        deterministic=True,
    )
    return ExecutionPlan(
        plan_id=f"wfv5-{wf_node_id[:48]}",
        nodes=[op_node, mat_node],
        budget=ResourceBudget(max_rows=max(
            1, min(len(input_features) * 4 + 1000, ADAPTER_INLINE_ROW_CAP))),
        description=f"workflow-v5 node {wf_node_id}",
    )


class GeoComputeNodeOutcome:
    """一次节点执行的结果（有界；载荷留在 session ref）。"""

    __slots__ = ("ok", "output_ref", "error_code", "error_message",
                 "duration_ms", "rows_emitted")

    def __init__(self, *, ok: bool, output_ref: str = "",
                 error_code: str = "", error_message: str = "",
                 duration_ms: int = 0, rows_emitted: int = 0):
        self.ok = ok
        self.output_ref = output_ref
        self.error_code = error_code
        self.error_message = error_message[:200]
        self.duration_ms = duration_ms
        self.rows_emitted = rows_emitted


def execute_node_plan(
    plan: ExecutionPlan,
    *,
    session_id: str,
    caller: Optional[Dict[str, Any]],
    cancel_token: Optional[Any] = None,
    engine: Optional[Any] = None,
) -> GeoComputeNodeOutcome:
    """同步执行节点计划（调用方负责 to_thread 卸载；MATERIALIZE 内部自管）。"""
    import time as _time

    if engine is None:
        from app.services.workflow_runtime.driver import get_engine

        engine = get_engine()
    start = _time.monotonic()
    try:
        run = engine.execute_plan(
            plan, session_id=session_id, caller=caller,
            cancel_token=cancel_token)
    except Exception as exc:  # noqa: BLE001 — 失败分类落证据
        from app.services.geocompute.errors import classify_failure

        failure = classify_failure(exc)
        return GeoComputeNodeOutcome(
            ok=False,
            error_code=getattr(failure, "code", None) or "NODE_EXECUTION_ERROR",
            error_message=str(exc),
            duration_ms=int((_time.monotonic() - start) * 1000))
    duration_ms = int((_time.monotonic() - start) * 1000)
    ev = run.evidence.get(plan.nodes[0].node_id)
    out_ev = run.evidence.get(f"{plan.nodes[0].node_id}:out")
    if run.status.value in ("cancelled",):
        return GeoComputeNodeOutcome(ok=False, error_code="CANCELLED",
                                     duration_ms=duration_ms)
    if run.status.value != "completed" or ev is None \
            or ev.status not in ("completed", "reused"):
        code = (ev.error_code if ev is not None else None) \
            or run.error_code or "NODE_FAILED"
        msg = (ev.error_message if ev is not None else None) \
            or run.error_message or ""
        return GeoComputeNodeOutcome(ok=False, error_code=code,
                                     error_message=msg,
                                     duration_ms=duration_ms)
    ref = (out_ev.output_ref if out_ev is not None else "") or ""
    return GeoComputeNodeOutcome(
        ok=True, output_ref=ref,
        duration_ms=duration_ms,
        rows_emitted=ev.rows_emitted or 0)


async def load_ref_features(
    session_id: str, refs: List[str], *, max_rows: int,
) -> List[Dict[str, Any]]:
    """session refs → 内联 features（有界；超界诚实截断由调用方拦截）。"""
    from app.services.session_data import session_data_manager

    feats: List[Dict[str, Any]] = []
    for ref in refs[:8]:
        if len(feats) >= max_rows:
            break
        payload = await session_data_manager.get(session_id, ref)
        if isinstance(payload, dict) and isinstance(
                payload.get("features"), list):
            feats.extend(payload["features"][:max(0, max_rows - len(feats))])
        elif isinstance(payload, list):
            feats.extend(payload[:max(0, max_rows - len(feats))])
    return feats
