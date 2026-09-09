"""Compiler V4 → Harness Runtime bridge（V6 Wave 1–2）。

现状（V6 Phase-0 审计 00-baseline §A/B）：Workflow Compiler V4 的 typed DAG
只被证据面消费（plan_orchestrator 摘要 / semantic_tools advisory），执行侧
零消费；``build_plan_graph`` 与 ``build_typed_dag`` 双轨并行，node_id 命名
各自为政。本模块把 typed DAG 的 node_id 命名空间变成**运行态共享命名
空间**，在 ``gis_chapter`` 单键（additive）持久化运行态投影：

    SessionPlan.gis_chapter（唯一计划事实，_mark_progress 单写者行状态）
          ↓ derive_runtime_block（纯函数，确定性，零 I/O）
    gis_chapter[WORKFLOW_RUNTIME_KEY]（"workflow_runtime_v6" 运行态块）

红线：

- **不是第二事实源**：行状态仍只由 ``_mark_progress`` 写；节点状态是
  plan_graph（与 WorkflowInstance 同一派生源）在 typed DAG node_id 上的
  **投影**——状态词汇复用 ``StageState``（经 ``_NODE_TO_STAGE`` 同一映射），
  不新增平行枚举；DAG 结构本体是 ``compile_workflow_v4`` 的确定性产物
  （同输入同图），块内只存指纹与运行态，不复制图。
- **确定性**：同输入同输出——块内无时间戳；指纹一律 canonical-JSON sha256
  （复用 ``canonical_fingerprint``/``row_signature`` 单一实现）。
- **诚实降级**：方法族未映射 / 编译失败 / 角色无行映射 → 块缺席或
  ``unmapped`` 披露，绝不虚构运行态；证据不足判 stale，不假设 reuse。
- **有界**：nodes/unmapped 全部截断；旧读者忽略新键。

服务入口 ``maybe_update_runtime_projection`` 复刻
``maybe_update_workflow_instance`` 的触发/门/锁模式（廉价门 + 锁内漂移
守卫 + 单键持久化 + kill switch），由同一批触发点调用（tool 结果 /
turn 收尾 / render observation）。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

#: gis_chapter 单键（additive；旧读者忽略）。
WORKFLOW_RUNTIME_KEY = "workflow_runtime_v6"

#: 有界预算。
_MAX_NODES = 64
_MAX_UNMAPPED = 16
_MAX_BOUND_REF = 64


def _enabled() -> bool:
    """降级开关（默认开；``GIS_WORKFLOW_RUNTIME_V6=0`` 关停）。"""
    return os.getenv("GIS_WORKFLOW_RUNTIME_V6", "1") not in ("0", "false", "False")


# ── node_id 命名空间（typed DAG 约定，唯一权威）──────────────────────────

def capability_node_id(capability: str) -> str:
    """analysis 节点的 typed node_id。"""
    return f"cap:{capability}"


def role_node_id(role: str) -> str:
    """data_input 节点的 typed node_id。"""
    return f"data:{role}"


# ── 角色 → capability 映射（与 workflow_instance._derive_bound_refs 同规则）──

def _role_capability_map(recipe_id: str) -> Dict[str, str]:
    """role → capability_hint（recipe workflow profile；声明序首个胜出）。

    与 ``_derive_bound_refs`` 的 ``role_by_cap`` 同一来源同一规则（数据角色
    绑定事实的单一路径）；profile 缺席 → 空映射（调用方诚实 unmapped）。
    """
    if not recipe_id:
        return {}
    try:
        from app.services.gis_harness.recipes import get_recipe_registry

        recipe = get_recipe_registry().get(recipe_id)
        profile = getattr(recipe, "workflow", None) if recipe is not None else None
        if profile is None:
            return {}
        mapping: Dict[str, str] = {}
        for req in getattr(profile, "data_roles", []) or []:
            hint = str(getattr(req, "capability_hint", "") or "")
            role = str(getattr(req, "role", "") or "")
            if hint and role and role not in mapping:
                mapping[role] = hint
        return mapping
    except Exception:  # noqa: BLE001 — 映射失败按无映射处理（诚实降级）
        return {}


# ── 纯派生 ───────────────────────────────────────────────────────────────

def _node_state_for_capability(
    capability: str,
    state_by_cap: Dict[str, Any],
) -> Optional[str]:
    """capability → StageState 值（经 PlanNodeStatus 同一映射）；无行 → None。"""
    state = state_by_cap.get(capability)
    return str(state) if state else None


def _edge_endpoints(edge: Dict[str, Any]) -> Tuple[str, str]:
    """bounded 边 dict（{"from": "node.port", "to": "node.port"}）→ 节点对。"""
    raw_from = str(edge.get("from") or "")
    raw_to = str(edge.get("to") or "")
    from_node = raw_from.rsplit(".", 1)[0] if "." in raw_from else raw_from
    to_node = raw_to.rsplit(".", 1)[0] if "." in raw_to else raw_to
    return from_node, to_node


def derive_runtime_block(
    chapter: Dict[str, Any],
    *,
    mapspec_revision: int = 0,
    render_seq: int = 0,
    stored: Optional[Dict[str, Any]] = None,
    compile_fn: Any = None,
) -> Optional[Dict[str, Any]]:
    """从章节纯派生运行态块（确定性、零 I/O 于派生本体；编译经 memo）。

    返回 None = 无 plan / 方法族未映射 / 编译失败（诚实留白，调用方保持
    原块或不写）。``stored`` 是已持久化的旧块：提供时对**已满足**节点做
    证据失配检测（行签名漂移 → stale），并沿 typed 边向下游传播
    （只污染受影响子图，无关分支零触碰）；revision 内容变化才 +1。
    """
    from app.services.gis_harness.plan_graph import build_plan_graph
    from app.services.gis_harness.workflow_instance import (
        _NODE_TO_STAGE,
        canonical_fingerprint,
        gate_fingerprint,
        row_signature,
        rows_fingerprint,
    )

    if not isinstance(chapter, dict) or not chapter.get("plan_id"):
        return None
    query = str(chapter.get("query") or "")
    recipe_id = str(chapter.get("recipe_id") or "")
    if not query:
        return None

    if compile_fn is None:
        from app.services.gis_harness.workflow_v4.compiler_v4 import (
            compile_workflow_v4,
        )
        compile_fn = compile_workflow_v4
    try:
        compilation = compile_fn(query[:400], recipe_id=recipe_id)
    except Exception:  # noqa: BLE001 — 编译失败诚实留白（不阻断 runtime）
        logger.info("[RuntimeBridge] V4 compile failed (session plan unchanged)",
                    exc_info=True)
        return None
    if compilation is None or not getattr(compilation, "methodology_family", ""):
        return None
    dag = getattr(compilation, "typed_dag", None) or {}
    dag_nodes = [n for n in (dag.get("nodes") or []) if isinstance(n, dict)]
    if not dag_nodes:
        return None

    # 行状态派生源：与 WorkflowInstance 完全同源（build_plan_graph 同一
    # 求值、_NODE_TO_STAGE 同一映射）——状态语义单一事实源。
    graph = build_plan_graph(chapter, evaluate=True)
    status_by_cap: Dict[str, str] = {}
    for node in graph.nodes:
        mapped = _NODE_TO_STAGE.get(
            getattr(node.status, "value", node.status))
        status_by_cap[node.capability] = mapped.value if mapped else "pending"
    rows_fp = rows_fingerprint(chapter)

    # 行签名（证据指纹）：row_signature 同一实现，但覆盖该 capability 的
    # **全部**行（requirement + step）——只取首行会对另一行的重绑/换参
    # 失明（step-only 参数编辑必须可见；排序拼接保证确定性）。
    rows_by_cap: Dict[str, List[Dict[str, Any]]] = {}
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if isinstance(row, dict) and row.get("capability"):
            rows_by_cap.setdefault(str(row["capability"]), []).append(row)

    role_to_cap = _role_capability_map(recipe_id)

    # stored 旧证据（仅 satisfied 节点参与失配检测——与 instance 同规则）。
    stored_nodes: Dict[str, Dict[str, Any]] = {}
    if isinstance(stored, dict):
        for s in stored.get("nodes") or []:
            if isinstance(s, dict) and s.get("node_id"):
                stored_nodes[str(s["node_id"])] = s

    nodes_out: List[Dict[str, Any]] = []
    unmapped: List[str] = []
    evidence_by_node: Dict[str, str] = {}
    state_by_node: Dict[str, str] = {}

    def _cap_evidence(cap: str) -> str:
        rows = rows_by_cap.get(cap) or []
        if not rows:
            return ""
        return canonical_fingerprint(
            "|".join(sorted(row_signature(r) for r in rows)))

    def _cap_bound_ref(cap: str) -> str:
        # 与 plan_graph 合并规则同源（:338）：requirement 行优先，step 行兜底。
        for row in rows_by_cap.get(cap) or []:
            ref = str(row.get("bound_ref") or "")
            if ref:
                return ref[:_MAX_BOUND_REF]
        return ""

    # 第一遍：结构节点 → 状态/证据（output 节点第二遍按产出者回填）。
    typed_by_id: Dict[str, Dict[str, Any]] = {}
    for n in dag_nodes[:_MAX_NODES]:
        node_id = str(n.get("node_id") or "")
        if node_id:
            typed_by_id[node_id] = n
    producer_of_output: Dict[str, str] = {}
    for e in (dag.get("edges") or [])[:128]:
        src, dst = _edge_endpoints(e)
        if dst.startswith("output:"):
            producer_of_output.setdefault(dst, src)

    for n in dag_nodes[:_MAX_NODES]:
        node_id = str(n.get("node_id") or "")
        kind = str(n.get("kind") or "")
        capability = str(n.get("capability") or "")
        role = str(n.get("role") or "")
        state: Optional[str] = None
        evidence = ""
        mapped_cap = ""
        if kind == "analysis" and capability:
            mapped_cap = capability
        elif kind in ("data_input", "transform") and role:
            mapped_cap = role_to_cap.get(role, "")
        if mapped_cap:
            state = _node_state_for_capability(mapped_cap, status_by_cap)
            evidence = _cap_evidence(mapped_cap)
        if state is None and kind != "output":
            unmapped.append(node_id)
        nodes_out.append({
            "node_id": node_id[:64],
            "kind": kind,
            "capability": capability[:64] or mapped_cap[:64],
            "role": role[:32],
            "state": state or "pending",
            "bound_ref": _cap_bound_ref(mapped_cap) if mapped_cap else "",
            "evidence": evidence[:32],
            "stale_reason": "",
            "failure_class": "",
            "repair_state": "",
        })
        state_by_node[node_id] = state or "pending"
        evidence_by_node[node_id] = evidence

    # 第二遍：output 节点状态 = 产出者状态（artifact 存在性是产出者的
    # bound_ref 事实，不另行虚构）。
    for item in nodes_out:
        if item["kind"] != "output":
            continue
        producer = producer_of_output.get(item["node_id"], "")
        if producer and producer in state_by_node:
            item["state"] = state_by_node[producer]
            item["evidence"] = evidence_by_node.get(producer, "")
            item["bound_ref"] = next(
                (x["bound_ref"] for x in nodes_out if x["node_id"] == producer),
                "",
            )
            state_by_node[item["node_id"]] = item["state"]
            evidence_by_node[item["node_id"]] = item["evidence"]
        else:
            unmapped.append(item["node_id"])

    # stale 检测：仅对已满足节点（satisfied/skipped 视为满足投影——
    # 与 instance「只对 satisfied 旧阶段判 stale」规则对齐，这里对
    # satisfied 判；skipped 无可漂移证据）。
    stale_seeds: Set[str] = set()
    for item in nodes_out:
        node_id = item["node_id"]
        old = stored_nodes.get(node_id)
        if old is None or str(old.get("state") or "") != "satisfied":
            continue
        if item["state"] != "satisfied":
            continue
        # output 节点证据继承自产出者——它的漂移是上游事实的投影，只能经
        # 闭包拿 upstream_stale，不得自立 evidence_drift 种子。
        if item["kind"] == "output":
            continue
        old_fp = str(old.get("evidence") or "")
        if old_fp and item["evidence"] and old_fp != item["evidence"]:
            stale_seeds.add(node_id)
            item["stale_reason"] = "evidence_drift"

    # typed 边下游闭包：证据漂移只污染受影响子图。
    if stale_seeds:
        downstream: Dict[str, List[str]] = {}
        for e in (dag.get("edges") or [])[:128]:
            src, dst = _edge_endpoints(e)
            downstream.setdefault(src, []).append(dst)
        seen: Set[str] = set(stale_seeds)
        stack = list(stale_seeds)
        while stack:
            cur = stack.pop()
            for nxt in downstream.get(cur, []):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        by_id = {item["node_id"]: item for item in nodes_out}
        for node_id in seen:
            item = by_id.get(node_id)
            if item is None or item["state"] != "satisfied":
                continue
            item["state"] = "stale"
            if not item["stale_reason"]:
                item["stale_reason"] = "upstream_stale"

    package_fp = str(getattr(compilation, "package_fingerprint", "") or "")
    block: Dict[str, Any] = {
        "schema": "workflow_runtime.v1",
        "plan_id": str(chapter.get("plan_id") or "")[:64],
        "compiler_version": str(getattr(compilation, "compiler_version", "") or "")[:16],
        "methodology_family": str(getattr(compilation, "methodology_family", "") or "")[:40],
        "selected_method": str(
            (getattr(compilation, "method_qualification", None) or {}).get("selected_id", "")
        )[:64],
        "primary_output": str(dag.get("primary_output") or "")[:64],
        "package_fingerprint": package_fp[:64],
        "rows_fingerprint": rows_fp[:2048],
        "checked_revision": int(mapspec_revision or 0),
        "render_observation_seq": int(render_seq or 0),
        "nodes": nodes_out,
        "unmapped": sorted(set(unmapped))[:_MAX_UNMAPPED],
    }
    block["state_fingerprint"] = canonical_fingerprint({
        "nodes": block["nodes"],
        "unmapped": block["unmapped"],
        "package_fingerprint": block["package_fingerprint"],
        "rows_fingerprint": block["rows_fingerprint"],
        "checked_revision": block["checked_revision"],
        "render_observation_seq": block["render_observation_seq"],
    })
    block["gate_fingerprint"] = canonical_fingerprint({
        "gate": gate_fingerprint(
            rows_fp, mapspec_revision, render_seq,
            chapter.get("workflow_contract") or "",
        ),
        "package": package_fp,
    })
    prev_revision = 0
    prev_fp = ""
    if isinstance(stored, dict):
        try:
            prev_revision = int(stored.get("runtime_revision") or 0)
        except (TypeError, ValueError):
            prev_revision = 0
        prev_fp = str(stored.get("state_fingerprint") or "")
    block["runtime_revision"] = (
        max(prev_revision, 1)
        if prev_fp == block["state_fingerprint"]
        else prev_revision + 1
    )
    return block


# ── 服务入口（触发点：与 maybe_update_workflow_instance 同批）─────────────

async def maybe_update_runtime_projection(
    session_id: str,
    *,
    reason: str = "auto",
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """廉价门 + 纯派生 + 锁内单键持久化（幂等、有界、可关停）。

    返回新块 dict（跳过/禁用/无 V4 语义时 None）。绝不触碰行状态、
    workflow_instance 块与 map_product 块；失败只记日志，下一触发点重试。
    """
    if not session_id or not _enabled():
        return None
    from app.services.gis_harness.render_observation import (
        load_render_observation,
        observation_sequence,
    )
    from app.services.gis_harness.workflow_instance import (
        canonical_fingerprint,
        gate_fingerprint,
        rows_fingerprint,
    )
    from app.services.session_data import session_data_manager
    from app.services.session_plan import (
        goal_key,
        load_session_plan,
        save_session_plan,
    )

    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return None
    chapter = plan.gis_chapter
    if not chapter.get("plan_id"):
        return None
    try:
        map_state = await session_data_manager.get_map_state(session_id)
    except Exception:  # noqa: BLE001 — 门输入读失败按 0 处理
        map_state = None
    try:
        revision = int((map_state or {}).get("_cartographic_mutation_revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    render_seq = observation_sequence(await load_render_observation(session_id, map_state))

    stored = chapter.get(WORKFLOW_RUNTIME_KEY)
    package_fp = str((stored or {}).get("package_fingerprint") or "") \
        if isinstance(stored, dict) else ""
    rows_fp = rows_fingerprint(chapter)
    gate = canonical_fingerprint({
        "gate": gate_fingerprint(
            rows_fp, revision, render_seq, chapter.get("workflow_contract") or ""),
        # 门不含 package 指纹本身（派生前未知）；rows/contract 变化已覆盖
        # 重编译触发面（package 是编译的确定性产物）。
        "package": package_fp,
    })
    if (
        not force
        and isinstance(stored, dict)
        and str(stored.get("gate_fingerprint") or "") == gate
    ):
        return None

    block = derive_runtime_block(
        chapter,
        mapspec_revision=revision,
        render_seq=render_seq,
        stored=stored if isinstance(stored, dict) else None,
    )
    if block is None:
        return None

    validated_goal = goal_key(chapter, plan.user_goal)
    validated_rows = rows_fp
    try:
        from app.services.distributed_lock import session_lock_registry
        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            fresh = await load_session_plan(session_id)
            if fresh is None or not isinstance(fresh.gis_chapter, dict) or lock.lost:
                return None
            if goal_key(fresh.gis_chapter, fresh.user_goal) != validated_goal:
                return None
            if rows_fingerprint(fresh.gis_chapter)[:2048] != validated_rows[:2048]:
                return None
            # 自漂移守卫：等锁窗口内另一触发点已写过 ⇒ 放弃（下一触发点
            # 基于新块重新派生）。
            fresh_stored = fresh.gis_chapter.get(WORKFLOW_RUNTIME_KEY)
            if (
                isinstance(stored, dict)
                and isinstance(fresh_stored, dict)
                and str(fresh_stored.get("state_fingerprint") or "")
                != str(stored.get("state_fingerprint") or "")
            ):
                return None
            fresh.gis_chapter[WORKFLOW_RUNTIME_KEY] = block
            await save_session_plan(fresh)
            return block
    except Exception:  # noqa: BLE001 — 披露失败不阻断 turn（留痕可诊断）
        logger.warning(
            "[RuntimeBridge] persist failed session=%s (retry on next trigger)",
            session_id, exc_info=True,
        )
        return None


__all__ = [
    "WORKFLOW_RUNTIME_KEY",
    "capability_node_id",
    "role_node_id",
    "derive_runtime_block",
    "maybe_update_runtime_projection",
]
