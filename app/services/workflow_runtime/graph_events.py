"""workflow_graph SSE 事件族 —— 执行图进度的有界投影（方向 5 / ADR-0184 E8）。

事件面纪律：

- **复用既有 SSE 通道**：事件经 ``SessionPlanEvent`` 随
  ``apply_tool_result`` 的事件列表流出（pi bridge ``events_to_sse`` →
  既有的 per-toolCall SSE 缓存/适配），不另造 websocket、不加第二通道；
- **DB/journal 是完整事实**：V5 节点转移全量记录在 workflow_events
  journal 与节点行；本事件族是**有界投影**（≤8 项/事件），供前端进度与
  replanned-diff 展示 —— 绝不逐转移刷流；
- 词表（type）：``replanned``（意图差异 → 携带/失效 + V5 决策摘要）、
  ``node_states``（V5 节点级批量状态投影）。CanonicalPlan 三事件名在
  Pi 路径被禁止（session_plan.events_to_sse 硬门），本词表 additive；
- 全部有界、无时间戳（消费方以事件序为准）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.session_plan import SessionPlanEvent

#: SSE 事件名（additive 词表；不在 CANONICAL_PLAN_EVENT_NAMES 禁用集）。
WORKFLOW_GRAPH_EVENT = "workflow_graph"

#: 有界预算。
_MAX_ITEMS = 8
_MAX_NODES = 32


def _bound_nodes(items: Optional[List[Any]], limit: int = _MAX_NODES) -> List[str]:
    out: List[str] = []
    for item in items or []:
        text = str(item)[:64]
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def intent_graph_event(
    facts: Dict[str, Any],
    v5_summary: Optional[Dict[str, Any]] = None,
) -> SessionPlanEvent:
    """意图差异 → ``replanned`` 事件（携带 / 失效 / V5 决策摘要）。

    payload 键：
    - ``fine_dims``：精细变更维（scope/subject/task/…）；
    - ``carried``：携带完成事实的 capability（≤8）＋总数；
    - ``lost``：失效 capability 及 coarse 维（≤8）＋总数；
    - ``stale_nodes`` / ``carried_nodes``：V5 实例节点级结果（≤8）；
    - ``applied`` / ``deferred``：V5 ChangeApplier 裁决（quiescence 门）。
    """
    carried = {str(k): str(v) for k, v in (facts.get("carried") or {}).items()}
    lost = [item for item in (facts.get("lost") or []) if isinstance(item, dict)]
    data: Dict[str, Any] = {
        "type": "replanned",
        "fine_dims": [str(d)[:16] for d in (facts.get("fine_dims") or [])[:8]],
        "global_reshape": bool(facts.get("global_reshape")),
        "carried": [cap[:64] for cap in list(carried)[:_MAX_ITEMS]],
        "carried_total": len(carried),
        "lost": [
            {
                "capability": str(item.get("capability") or "")[:64],
                "dimension": str(item.get("dimension") or "")[:24],
            }
            for item in lost[:_MAX_ITEMS]
        ],
        "lost_total": len(lost),
    }
    if v5_summary:
        data["stale_nodes"] = _bound_nodes(v5_summary.get("stale"), _MAX_ITEMS)
        data["carried_nodes"] = _bound_nodes(v5_summary.get("carried"), _MAX_ITEMS)
        instances = [item for item in (v5_summary.get("instances") or [])
                     if isinstance(item, dict)]
        data["applied"] = any(item.get("applied") for item in instances)
        data["deferred"] = any(item.get("deferred") for item in instances)
    return SessionPlanEvent(event=WORKFLOW_GRAPH_EVENT, data=data)


def node_states_event(
    instance_id: str,
    changes: List[Dict[str, str]],
    *,
    source: str = "",
) -> SessionPlanEvent:
    """V5 节点级批量状态投影 → ``node_states`` 事件。

    ``changes``：[{node_id, state, reason?}]（调用方从节点行派生；
    语义标签 ⊆ ready/running/succeeded/failed/skipped/reused/invalidated）。
    ``total`` = 过滤后的真实变更数；``changes`` 是其 ≤8 项有界视图。
    """
    filtered: List[Dict[str, str]] = []
    for item in (changes or []):
        if not isinstance(item, dict) or not item.get("node_id"):
            continue
        filtered.append({
            "node_id": str(item["node_id"])[:64],
            "state": str(item.get("state") or "")[:16],
            "reason": str(item.get("reason") or "")[:48],
        })
    return SessionPlanEvent(
        event=WORKFLOW_GRAPH_EVENT,
        data={
            "type": "node_states",
            "instance_id": str(instance_id)[:64],
            "source": str(source)[:24],
            "changes": filtered[:_MAX_ITEMS],
            "total": len(filtered),
        },
    )


__all__ = [
    "WORKFLOW_GRAPH_EVENT",
    "intent_graph_event",
    "node_states_event",
]
