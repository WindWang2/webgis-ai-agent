"""Artifact 感知投影（Workbench V6 / ADR-0119）—— 会话产物台账的派生视图。

安全边界（SEC-KG-01）：路由层禁止直调 artifact_registry —— 本模块是路由与
注册表之间的**只读** service 缝。投影零新真相：全部字段来自既有台账记录，
stale 优先、有界 ≤200、metadata 不透出。
"""
from __future__ import annotations

from typing import Any, Dict

_MAX_ITEMS = 200
_MAX_INPUTS = 8


async def build_artifact_status(session_id: str) -> Dict[str, Any]:
    """会话产物状态投影：stale 优先、按 updated_at 倒序、有界。

    前端以 layer._refId == artifactId 直接 join —— stale/updated 徽标与
    「为何变化」（inputs 血缘 + producer 节点）入口。跨浏览器刷新由总线
    ``artifact`` 事件（ref_lifecycle 失效路径发布）驱动。
    """
    from app.services.artifact_registry import list_artifacts

    records = await list_artifacts(session_id)
    items = [
        {
            "artifactId": r.artifact_id,
            "type": r.artifact_type,
            "status": r.status,
            "producerCapability": r.producer_capability,
            "producerNode": r.producer_node,
            "producerTool": r.producer_tool,
            "inputs": list(r.inputs)[:_MAX_INPUTS],
            "replaces": r.replaces,
            "revision": r.revision,
            "updatedAt": r.updated_at,
        }
        for r in records
    ]
    stale_first = sorted(
        items,
        key=lambda it: (
            0 if it["status"] != "valid" else 1,
            -(it["updatedAt"] or 0.0),
        ),
    )[:_MAX_ITEMS]
    return {
        "session_id": session_id,
        "artifacts": stale_first,
        "staleCount": sum(1 for it in items if it["status"] != "valid"),
        "total": len(items),
    }
