"""workflow_graph SSE 事件族 —— 执行图进度的有界投影（方向 5 / ADR-0184 E8）。

事件面纪律：

- **复用既有 SSE 通道**：事件经 ``SessionPlanEvent`` 随
  ``apply_tool_result`` 的事件列表流出（pi bridge ``events_to_sse`` →
  既有的 per-toolCall SSE 缓存/适配），不另造 websocket、不加第二通道；
- **DB/journal 是完整事实**：V5 节点转移全量记录在 workflow_events
  journal 与节点行；本事件族是**有界投影**（≤8 项/事件），供前端进度与
  replanned-diff 展示 —— 绝不逐转移刷流；
- 词表（type）：``replanned``（意图差异 → 携带/失效 + V5 决策摘要，其中
``stale_nodes``/``carried_nodes`` 即节点粒度投影）。逐转移的节点事件流
**有意不做**（journal 是完整事实，SSE 刷流是噪声）；CanonicalPlan 三事件名
在 Pi 路径被禁止（session_plan.events_to_sse 硬门），本词表 additive；
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


__all__ = [
    "WORKFLOW_GRAPH_EVENT",
    "intent_graph_event",
]
