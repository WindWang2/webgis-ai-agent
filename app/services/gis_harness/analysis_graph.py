"""Explicit Analysis Graph —— 可检视的会话分析图投影（ADR-0097）。

用户与 Agent 需要在同一份世界状态上看到「这个会话正在做什么分析」：
不是不可读的工具调用历史，而是显式的图 —— 目标、数据/分析步骤（依赖、
状态、证据、警告）、产品 facets（欠账/修复）、下一步动作。

**不变式（ADR-0076/0085/0096）**：本模块是 SessionPlan 章节 + MapSpec +
artifact/observation 证据的**纯派生投影**，零持久化、零第二事实源。节点
状态由 `_mark_progress` 的行状态与绑定事实推进；这里只读评估。任何字段
都可以从既有真相重新计算 —— 删掉缓存立即重建，值不变。

图的三个层次：

    goal        会话目标（SessionPlan.user_goal + 方法论警告）
    execution   PlanGraph 节点（capability DAG：requirement/analysis）
    product     ProductGraph facets（图层/图表/统计/图例/导出 + 完成度）

边界（§20 性能）：节点有界（execution ≤ 96，product ≤ 64，warnings ≤ 12，
provenance ≤ 16）；大载荷永远以 ref 表达。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── 有界常量（性能约束 §20）────────────────────────────────────────────
_MAX_EXECUTION_NODES = 96
_MAX_PRODUCT_NODES = 64
_MAX_WARNINGS = 12
_MAX_PURPOSE_LEN = 160

# 产品 facet → 五维 diff 中会触发重算的维度（MapProductService.diff_versions
# 的语义投影：data/algorithm/parameter 改变 → 分析重算；style 只重渲染）。
_FACET_RECOMPUTE_DIMS = {
    "map_layer": ["data", "algorithm", "parameter", "style", "output"],
    "analysis": ["data", "algorithm", "parameter"],
    "chart": ["data", "algorithm", "parameter", "style"],
    "statistics": ["data", "algorithm", "parameter"],
    "legend": ["style"],
    "annotation": ["style"],
    "export": ["style", "output"],
}


def _bounded(text: str, limit: int = _MAX_PURPOSE_LEN) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _execution_nodes(chapter: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """PlanGraph → 有界执行节点（capability DAG）。"""
    from app.services.gis_harness.plan_graph import build_plan_graph

    graph = build_plan_graph(chapter)
    nodes: List[Dict[str, Any]] = []
    for n in graph.nodes[:_MAX_EXECUTION_NODES]:
        nodes.append({
            "id": n.node_id,
            "kind": n.kind,                      # requirement | analysis
            "capability": n.capability,
            "purpose": _bounded(n.purpose),
            "status": n.status.value,
            "algorithm": n.resolved_algorithm,
            "tool": n.resolved_tool,
            "depends_on": list(n.depends_on),
            "bound_ref": n.bound_ref,            # 输出 artifact ref（cursor）
            "input_refs": list(n.input_refs)[:8],
            "optional": n.optional,
            "cost_class": n.cost_class,
            "fallback_to": n.fallback_to,
            "blocked_by": list(n.blocked_by),
            "notes": [ _bounded(x, 120) for x in n.notes[:4] ],
            # 重算影响：该节点重算 ⇒ 依赖它的下游全部需要重算（图拓扑事实）。
            "recompute_impact": "downstream",
        })
    return nodes


def _product_nodes(
    chapter: Optional[Dict[str, Any]],
    mapspec: Optional[Dict[str, Any]],
    *,
    descriptors: Optional[Dict[str, Any]] = None,
    observation: Optional[Dict[str, Any]] = None,
    current_revision: int = 0,
) -> List[Dict[str, Any]]:
    """ProductGraph facets → 有界产品节点（完成度 + 渲染证据）。"""
    from app.services.gis_harness.product_graph import build_facet_completion

    facets = build_facet_completion(
        chapter, mapspec,
        descriptors=descriptors,
        observation=observation,
        current_revision=current_revision,
    )
    nodes: List[Dict[str, Any]] = []
    for f in facets[:_MAX_PRODUCT_NODES]:
        nodes.append({
            "id": f.facet_id,
            "kind": "product",
            "facet_kind": f.kind,                # map_layer/chart/statistics/…
            "label": f.label,
            "status": f.status,                  # complete/pending/needs_repair/…
            "required": f.required,
            "capabilities": list(f.capability_ids)[:6],
            "artifact_ref": f.artifact_ref,
            "layer_ids": list(f.layer_ids)[:8],
            "component_ids": list(f.component_ids)[:8],
            "dependencies": list(f.dependencies)[:8],
            "render_status": f.render_status or "",
            # 五维 diff 语义：哪些维度变化会让这个 facet 失效（需重算/重渲染）。
            "recompute_dims": _FACET_RECOMPUTE_DIMS.get(f.kind, ["data"]),
        })
    return nodes


def _goal_node(
    plan: Any,
    chapter: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    goal = getattr(plan, "user_goal", "") or (chapter or {}).get("query", "")
    warnings: List[Dict[str, Any]] = []
    for w in (chapter or {}).get("methodology_warnings") or []:
        if isinstance(w, dict):
            warnings.append({
                "pattern": str(w.get("pattern") or ""),
                "code": str(w.get("code") or ""),
                "missing_roles": list(w.get("missing_roles") or [])[:4],
                "disclosures": [
                    _bounded(d, 200) for d in (w.get("disclosures") or [])[:2]
                ],
            })
        if len(warnings) >= _MAX_WARNINGS:
            break
    return {
        "id": "goal",
        "kind": "goal",
        "label": _bounded(goal, 200),
        "query": _bounded(getattr(plan, "user_goal", ""), 200),
        "recipe_id": str((chapter or {}).get("recipe_id") or ""),
        "plan_id": str((chapter or {}).get("plan_id") or ""),
        "status": str((chapter or {}).get("status") or ""),
        "superseded": bool(getattr(plan, "superseded", False)),
        "replaced": bool(getattr(plan, "replaced", False)),
        "methodology_warnings": warnings,
    }


def build_analysis_graph(
    plan: Any,
    mapspec: Optional[Dict[str, Any]] = None,
    *,
    descriptors: Optional[Dict[str, Any]] = None,
    observation: Optional[Dict[str, Any]] = None,
    current_revision: int = 0,
) -> Dict[str, Any]:
    """SessionPlan + MapSpec + 证据 → 显式分析图（纯投影，有界，可序列化）。

    ``plan`` 为 None（无会话计划）时返回空图 —— 空图是诚实状态，不是错误。
    """
    chapter: Optional[Dict[str, Any]] = getattr(plan, "gis_chapter", None)
    if chapter is None and isinstance(plan, dict):
        chapter = plan.get("gis_chapter")

    graph: Dict[str, Any] = {
        "session_id": str(getattr(plan, "session_id", "") or ""),
        "envelope_id": str(getattr(plan, "envelope_id", "") or ""),
        "goal": None,
        "nodes": [],
        "counts": {"goal": 0, "execution": 0, "product": 0},
        "next_action": None,
        "notes": [],
    }
    if not isinstance(chapter, dict):
        graph["notes"].append("no session plan chapter — call webgis_map_intent")
        return graph

    execution = _execution_nodes(chapter)
    product = _product_nodes(
        chapter, mapspec,
        descriptors=descriptors,
        observation=observation,
        current_revision=current_revision,
    )
    goal = _goal_node(plan, chapter)

    # 统一确定性下一动作（执行债 → 产品债 → 观察债 → 收尾债）。
    next_action: Optional[Dict[str, Any]] = None
    try:
        from app.services.gis_harness.action_intent import resolve_next_gis_action
        from app.services.gis_harness.product_graph import build_facet_completion

        facets = build_facet_completion(
            chapter, mapspec,
            descriptors=descriptors,
            observation=observation,
            current_revision=current_revision,
        )
        action = resolve_next_gis_action(chapter, facets)
        if action is not None:
            next_action = {
                "facet_id": action.facet_id,
                "kind": action.kind,
                "action": action.action,
                "reason": _bounded(action.reason, 200),
                "capability": action.capability,
                "mode": action.execution_mode,
                "class": action.action_class,
            }
    except Exception:  # noqa: BLE001 — 下一动作是增值投影，缺席不阻断
        next_action = None

    graph["goal"] = goal
    graph["nodes"] = [goal] + execution + product
    graph["counts"] = {
        "goal": 1,
        "execution": len(execution),
        "product": len(product),
    }
    graph["next_action"] = next_action
    if getattr(plan, "superseded", False):
        graph["notes"].append("plan superseded by a newer goal")
    return graph


async def build_analysis_graph_for_session(session_id: str) -> Dict[str, Any]:
    """会话入口：加载 SessionPlan + MapSpec + 渲染观察 → 显式分析图。

    加载失败按缺席投影（空图/降级证据），绝不抛给调用方 —— 图是检视图，
    不是门槛。渲染观察只在 revision 匹配时参与（与 facet 契约一致）。
    """
    plan = None
    mapspec: Optional[Dict[str, Any]] = None
    observation: Optional[Dict[str, Any]] = None
    current_revision = 0
    try:
        from app.services.session_plan import load_session_plan

        plan = await load_session_plan(session_id)
    except Exception:  # noqa: BLE001 — 无计划 = 空图
        plan = None
    try:
        from app.services.mapspec_store import mapspec_store

        mapspec = await mapspec_store.get_mapspec(session_id)
        current_revision = int((mapspec or {}).get("revision") or 0)
    except Exception:  # noqa: BLE001
        mapspec = None
    try:
        from app.services.gis_harness.render_observation import (
            load_render_observation,
        )

        observation = await load_render_observation(session_id)
    except Exception:  # noqa: BLE001
        observation = None

    return build_analysis_graph(
        plan, mapspec,
        observation=observation,
        current_revision=current_revision,
    )


__all__ = [
    "build_analysis_graph",
    "build_analysis_graph_for_session",
]
