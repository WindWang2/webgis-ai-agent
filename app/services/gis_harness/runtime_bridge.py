"""Compiler V4 → Harness Runtime bridge（V6 Wave 1–3）。

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
from typing import Any, Dict, List, Optional, Tuple

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
    mapspec: Optional[Dict[str, Any]] = None,
    mapspec_revision: int = 0,
    render_seq: int = 0,
    stored: Optional[Dict[str, Any]] = None,
    records: Optional[Dict[str, Any]] = None,
    compile_fn: Any = None,
) -> Optional[Dict[str, Any]]:
    """从章节纯派生运行态块（确定性、零 I/O 于派生本体；编译经 memo）。

    返回 None = 无 plan / 方法族未映射 / 编译失败（诚实留白，调用方保持
    原块或不写）。``stored`` 是已持久化的旧块：提供时对**已满足**节点做
    证据失配检测（行签名漂移 → 变更分类 → compute_affected_subgraph 闭
    包 → stale），revision 内容变化才 +1。

    ``mapspec``（可选）提供时构建 artifact ↔ node ↔ layer/component
    双向 lineage 索引（W3）；``records``（可选，artifact_id →
    ArtifactRecord/dict 快照）提供时做 reuse validation（W5：证据不足
    → unknown/unsafe，绝不假设复用安全）；两者缺席时相应面诚实降级。
    """
    from app.services.gis_harness.plan_graph import build_plan_graph
    from app.services.gis_harness.workflow_instance import (
        _NODE_TO_STAGE,
        _row_params_hash,
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
    deps_by_cap: Dict[str, List[str]] = {}
    for node in graph.nodes:
        mapped = _NODE_TO_STAGE.get(
            getattr(node.status, "value", node.status))
        status_by_cap[node.capability] = mapped.value if mapped else "pending"
        deps_by_cap[node.capability] = list(node.depends_on or [])[:8]
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

    def _cap_all_refs(cap: str) -> List[str]:
        """该 capability 全部行的绑定 ref（有序去重）——展示取首（合并
        规则），lineage 索引登记全部（过渡态行 ref 可能短暂分叉，索引
        不得对任一 ref 失明）。"""
        refs: List[str] = []
        for row in rows_by_cap.get(cap) or []:
            ref = str(row.get("bound_ref") or "")[:_MAX_BOUND_REF]
            if ref and ref not in refs:
                refs.append(ref)
        return refs

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

    def _cap_algorithm(cap: str) -> str:
        for row in rows_by_cap.get(cap) or []:
            alg = str(row.get("resolved_algorithm") or "")
            if alg:
                return alg[:64]
        return ""

    def _cap_params_hash(cap: str) -> str:
        for row in rows_by_cap.get(cap) or []:
            h = _row_params_hash(row)
            if h:
                return h
        return ""

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
        # W3 输入血缘：该节点消费的 artifact ref（depends_on 上游行的
        # 绑定 ref；与 plan_graph 依赖同一来源，不另行推断）。
        input_refs: List[str] = []
        if mapped_cap:
            for dep in deps_by_cap.get(mapped_cap, []):
                for ref in _cap_all_refs(dep):
                    if ref not in input_refs:
                        input_refs.append(ref)
        nodes_out.append({
            "node_id": node_id[:64],
            "kind": kind,
            "capability": capability[:64] or mapped_cap[:64],
            "role": role[:32],
            "state": state or "pending",
            "bound_ref": _cap_bound_ref(mapped_cap) if mapped_cap else "",
            "inputs": input_refs[:8],
            "evidence": evidence[:32],
            # W4 变更分类原料：算法/参数指纹（行签名分量的字段级投影）。
            "algorithm": _cap_algorithm(mapped_cap) if mapped_cap else "",
            "params_hash": _cap_params_hash(mapped_cap) if mapped_cap else "",
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
            producer_item = next(
                (x for x in nodes_out if x["node_id"] == producer), None,
            )
            item["bound_ref"] = str((producer_item or {}).get("bound_ref") or "")
            item["algorithm"] = str((producer_item or {}).get("algorithm") or "")
            item["params_hash"] = str((producer_item or {}).get("params_hash") or "")
            state_by_node[item["node_id"]] = item["state"]
            evidence_by_node[item["node_id"]] = item["evidence"]
        else:
            unmapped.append(item["node_id"])

    # ── W4：变更分类 → compute_affected_subgraph（唯一受影响子图引擎）────
    # 运行态可观测变更（satisfied 节点的行签名漂移）按字段级对比分类：
    # 算法变 → algorithm；参数指纹变 → parameter；绑定 ref 变 → data
    # （数据修订）；其余 → data（画像/证据漂移）。Prompt §9 的 12 类在此
    # 词表上的映射见 05-recompute-model.md——RECOMPUTE_DIMENSIONS +
    # CHANGE_TARGETS 是单一事实源，不新增平行枚举。
    from app.services.gis_harness.workflow_v4.recompute import (
        WorkflowChange,
        compute_affected_subgraph,
    )

    changes: List[WorkflowChange] = []
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
        if not (old_fp and item["evidence"] and old_fp != item["evidence"]):
            continue
        item["stale_reason"] = "evidence_drift"
        old_alg = str(old.get("algorithm") or "")
        new_alg = str(item.get("algorithm") or "")
        old_ph = str(old.get("params_hash") or "")
        new_ph = str(item.get("params_hash") or "")
        old_ref = str(old.get("bound_ref") or "")
        new_ref = str(item.get("bound_ref") or "")
        # 字段级对比：新值在场且与旧值不同即该类变更（旧值允许为空 ——
        # 空→有 同样是变更；首代块缺字段时退化为后续分支的保守分类）。
        if new_alg and old_alg != new_alg:
            changes.append(WorkflowChange(
                dimension="algorithm", target_kind="algorithm",
                target=node_id, detail=f"{old_alg}→{new_alg}"[:200]))
        elif new_ph and old_ph != new_ph:
            changes.append(WorkflowChange(
                dimension="parameter", target_kind="node",
                target=node_id, detail="params fingerprint changed"))
        elif old_ref != new_ref:
            changes.append(WorkflowChange(
                dimension="data", target_kind="node",
                target=node_id, detail="bound ref revision changed"))
        else:
            # 证据漂移但字段级不可辨（首代块无 algorithm/params_hash 等）
            # —— 保守按数据维处理（宁可多算）。
            changes.append(WorkflowChange(
                dimension="data", target_kind="node",
                target=node_id, detail="evidence drift"))

    recompute_plan = compute_affected_subgraph(dag, changes) if changes else None

    # typed 边下游闭包（由 recompute 引擎给出）：证据漂移只污染受影响子图。
    if recompute_plan is not None:
        dirty = set(recompute_plan.recompute)
        by_id = {item["node_id"]: item for item in nodes_out}
        for node_id in dirty:
            item = by_id.get(node_id)
            if item is None or item["state"] != "satisfied":
                continue
            item["state"] = "stale"
            if not item["stale_reason"]:
                item["stale_reason"] = "upstream_stale"

    # ── W3：artifact ↔ node ↔ layer/component 双向 lineage 索引 ────────
    # 正向：节点 bound_ref（产物）/ inputs（消费）；反向：ref → 生产节点 /
    # 消费节点 / 依赖图层 / 依赖组件。全部投影既有引用（行 bound_ref、
    # spec source ref、组件 chartRef），不发明第二血缘；liveness 不在此处
    # 裁决（ reuse validation 由消费方带快照评估 —— W5）。
    artifact_index: Dict[str, Dict[str, Any]] = {}

    def _idx(ref: str) -> Optional[Dict[str, Any]]:
        key = ref[:64]
        entry = artifact_index.get(key)
        if entry is None:
            if len(artifact_index) >= 96:
                return None
            entry = artifact_index.setdefault(key, {
                "producer_node": "",
                "consumer_nodes": [],
                "layer_ids": [],
                "component_ids": [],
            })
        return entry

    for item in nodes_out:
        mapped_cap = str(item.get("capability") or "")
        if mapped_cap and item["kind"] != "output":
            for ref in _cap_all_refs(mapped_cap):
                entry = _idx(ref)
                if entry is not None and not entry["producer_node"]:
                    entry["producer_node"] = item["node_id"]
        for inp in item.get("inputs") or []:
            entry = _idx(inp)
            if entry is not None and item["node_id"] not in entry["consumer_nodes"]:
                entry["consumer_nodes"].append(item["node_id"])
    if isinstance(mapspec, dict):
        from app.services.gis_harness.product_graph import _spec_source_ref

        for layer in (mapspec.get("layers") or [])[:64]:
            if not isinstance(layer, dict):
                continue
            ref = _spec_source_ref(mapspec, str(layer.get("source") or ""))
            if ref:
                entry = _idx(ref)
                lid = str(layer.get("id") or "")[:64]
                if entry is not None and lid and lid not in entry["layer_ids"]:
                    entry["layer_ids"].append(lid)
        components = ((mapspec.get("layout") or {}).get("components") or [])[:64]
        for comp in components:
            if not isinstance(comp, dict):
                continue
            options = comp.get("options") or {}
            ref = options.get("chartRef")
            if isinstance(ref, str) and ref:
                entry = _idx(ref)
                cid = str(comp.get("id") or comp.get("type") or "")[:64]
                if entry is not None and cid and cid not in entry["component_ids"]:
                    entry["component_ids"].append(cid)
        # 有界截断（防大 spec 膨胀块体）
        for entry in artifact_index.values():
            entry["consumer_nodes"] = entry["consumer_nodes"][:8]
            entry["layer_ids"] = entry["layer_ids"][:8]
            entry["component_ids"] = entry["component_ids"][:8]

    # ── W5：Artifact Reuse Validation（证据不足 → unknown/unsafe，不假设）──
    # 校验项：evidence 指纹（构造保证 current）/ artifact health（records
    # 快照；无快照或记录缺席 → unknown）/ workflow package 稳定性。
    # unsafe 节点翻 stale 并强制进 recompute —— 复用安全由证据说话。
    package_fp = str(getattr(compilation, "package_fingerprint", "") or "")
    stored_package_fp = str(
        (stored or {}).get("package_fingerprint") or "") if isinstance(stored, dict) else ""
    dirty_set = set(recompute_plan.recompute) if recompute_plan is not None else set()
    reuse_validation: Dict[str, Dict[str, Any]] = {}
    forced_recompute: List[str] = []
    for item in nodes_out:
        if item["kind"] == "output" or item["state"] != "satisfied":
            continue
        node_id = item["node_id"]
        if node_id in dirty_set:
            continue
        cap = str(item.get("capability") or "")
        checks: Dict[str, str] = {"evidence_fingerprint": "current"}
        refs = _cap_all_refs(cap) if cap else []
        if records is None:
            checks["artifact_health"] = "unknown"
        elif not refs:
            checks["artifact_health"] = "unknown"
        else:
            statuses: List[str] = []
            for ref in refs:
                rec = records.get(ref)
                if rec is None:
                    statuses.append("missing")
                    continue
                status = str(
                    getattr(rec, "status", "")
                    or (rec.get("status") if isinstance(rec, dict) else "")
                    or "unknown")
                statuses.append(status)
            if all(s == "valid" for s in statuses):
                checks["artifact_health"] = "healthy"
            elif any(s in ("expired", "stale", "superseded", "failed")
                     for s in statuses):
                checks["artifact_health"] = "unhealthy"
            else:
                checks["artifact_health"] = "unknown"
        if not stored_package_fp:
            checks["workflow_package"] = "unknown"
        elif stored_package_fp == package_fp:
            checks["workflow_package"] = "stable"
        else:
            checks["workflow_package"] = "changed"
        if checks["artifact_health"] == "unhealthy" \
                or checks["workflow_package"] == "changed":
            verdict = "unsafe"
        elif "unknown" in checks.values():
            verdict = "unknown"
        else:
            verdict = "safe"
        reuse_validation[node_id] = {"verdict": verdict, "checks": checks}
        if verdict == "unsafe":
            item["state"] = "stale"
            item["stale_reason"] = (
                "reuse_unsafe:artifact_health"
                if checks["artifact_health"] == "unhealthy"
                else "reuse_unsafe:workflow_package")
            forced_recompute.append(node_id)
    if forced_recompute:
        if recompute_plan is None:
            from app.services.gis_harness.workflow_v4.recompute import (
                RecomputePlan,
            )
            recompute_plan = RecomputePlan()
        merged = sorted(set(recompute_plan.recompute) | set(forced_recompute))
        recompute_plan.recompute = merged[:32]
        recompute_plan.reuse = sorted(
            n for n in recompute_plan.reuse if n not in forced_recompute)
        recompute_plan.explanations = list(recompute_plan.explanations) + [
            f"reuse=unsafe → 保守重算: {','.join(forced_recompute[:4])}",
        ]

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
        "artifact_index": artifact_index,
        "changes": [c.to_bounded_dict() for c in changes][:16],
        "recompute_plan": (
            recompute_plan.to_bounded_dict() if recompute_plan is not None else {}
        ),
        "reuse_validation": dict(list(reuse_validation.items())[:64]),
        "unmapped": sorted(set(unmapped))[:_MAX_UNMAPPED],
    }
    block["state_fingerprint"] = canonical_fingerprint({
        "nodes": block["nodes"],
        "artifact_index": block["artifact_index"],
        "changes": block["changes"],
        "recompute_plan": block["recompute_plan"],
        "reuse_validation": block["reuse_validation"],
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

    mapspec: Optional[Dict[str, Any]] = None
    try:
        from app.services.mapspec_store import mapspec_store

        mapspec = await mapspec_store.get_mapspec(session_id) or None
    except Exception:  # noqa: BLE001 — spec 读失败按无 spec 处理（lineage 降级）
        mapspec = None

    # W5 reuse validation 的 artifact 健康快照（有界账本 ≤128；读失败 →
    # None → 全部 unknown —— 证据不足不假设复用安全）。
    records: Optional[Dict[str, Any]] = None
    try:
        from app.services.artifact_registry import list_artifacts

        recs = await list_artifacts(session_id)
        records = {
            str(getattr(r, "artifact_id", "") or ""): r for r in recs
        } if recs else {}
    except Exception:  # noqa: BLE001 — 快照读失败降级 unknown
        records = None

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
        mapspec=mapspec,
        mapspec_revision=revision,
        render_seq=render_seq,
        stored=stored if isinstance(stored, dict) else None,
        records=records,
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


# ── 查询辅助（W3 双向 lineage 的消费 API；纯读块，零派生）─────────────────

def artifact_lineage(
    block: Optional[Dict[str, Any]], ref: str,
) -> Optional[Dict[str, Any]]:
    """artifact ref 的反向血缘：生产节点 / 消费节点 / 依赖图层 / 依赖组件。

    反查链 rendered layer → MapSpec source → artifact → workflow node 的
    块内落地（ref 缺席 → None，调用方按无证据处理）。
    """
    if not isinstance(block, dict) or not ref:
        return None
    entry = (block.get("artifact_index") or {}).get(ref[:64])
    if not isinstance(entry, dict):
        return None
    return {
        "ref": ref[:64],
        "producer_node": str(entry.get("producer_node") or ""),
        "consumer_nodes": list(entry.get("consumer_nodes") or []),
        "layer_ids": list(entry.get("layer_ids") or []),
        "component_ids": list(entry.get("component_ids") or []),
    }


def node_lineage(block: Optional[Dict[str, Any]], node_id: str) -> Optional[Dict[str, Any]]:
    """节点的正向血缘：消费了什么（inputs）、生成了什么（bound_ref）、
    哪些图层/组件依赖它的产物（经 artifact_index 反查）。"""
    if not isinstance(block, dict) or not node_id:
        return None
    node = next(
        (n for n in (block.get("nodes") or [])
         if isinstance(n, dict) and n.get("node_id") == node_id),
        None,
    )
    if node is None:
        return None
    out_ref = str(node.get("bound_ref") or "")
    dependents = artifact_lineage(block, out_ref) if out_ref else None
    return {
        "node_id": node_id,
        "state": str(node.get("state") or ""),
        "stale_reason": str(node.get("stale_reason") or ""),
        "inputs": list(node.get("inputs") or []),
        "output_ref": out_ref,
        "layer_ids": list((dependents or {}).get("layer_ids") or []),
        "component_ids": list((dependents or {}).get("component_ids") or []),
        "consumer_nodes": list((dependents or {}).get("consumer_nodes") or []),
    }


def format_recompute_line(chapter: Optional[Dict[str, Any]]) -> str:
    """[GIS Recompute] 单行投影（SessionPlan projection 的 additive 行）。

    把运行态块里的 recompute 债（stale 节点 + 变更维 + reuse 裁决）暴露
    给 Pi —— 「RecomputePlan 真正调度 affected subgraph」在本架构的落
    地形态：Harness 出状态与建议，执行仍归 Pi（状态红线：行状态只由
    _mark_progress 写，bridge 不翻行）。无债 → 空串（零噪声）。
    """
    if not isinstance(chapter, dict):
        return ""
    block = chapter.get(WORKFLOW_RUNTIME_KEY)
    if not isinstance(block, dict):
        return ""
    stale = [
        str(n.get("node_id") or "")
        for n in (block.get("nodes") or [])
        if isinstance(n, dict) and n.get("state") == "stale"
    ][:4]
    plan = block.get("recompute_plan") or {}
    recompute = [str(n) for n in (plan.get("recompute") or [])][:4]
    if not stale and not recompute:
        return ""
    parts = [f"[GIS Recompute] stale={','.join(stale) or 'none'}"]
    if recompute:
        parts.append(f"recompute={','.join(recompute)}")
    dims = [str(d) for d in (plan.get("changed_dimensions") or [])][:3]
    if dims:
        parts.append(f"dims={','.join(dims)}")
    reuse = block.get("reuse_validation") or {}
    unsafe = sorted(
        k for k, v in reuse.items()
        if isinstance(v, dict) and v.get("verdict") == "unsafe"
    )[:3]
    if unsafe:
        parts.append(f"reuse_unsafe={','.join(unsafe)}")
    return " ".join(parts)[:480]


__all__ = [
    "WORKFLOW_RUNTIME_KEY",
    "capability_node_id",
    "role_node_id",
    "artifact_lineage",
    "node_lineage",
    "derive_runtime_block",
    "format_recompute_line",
    "maybe_update_runtime_projection",
]
