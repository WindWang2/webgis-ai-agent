"""存储态 visual findings 读取与证据新鲜度过滤（C13）。

视觉修复的两个消费方（plan 路由 / auto 通道）共用同一读取与新鲜度门
—— 从路由私有函数下沉到 service 层（C13 review P3-8 层次倒置收口）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


async def stored_visual_findings(session_id: str) -> List[Dict[str, Any]]:
    """map_product.visual_findings（持久化披露面；缺失 = 空）。"""
    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(session_id)
    chapter = plan.gis_chapter if plan is not None else None
    block = (chapter or {}).get("map_product") if isinstance(chapter, dict) else None
    raw = (block or {}).get("visual_findings") if isinstance(block, dict) else None
    if not isinstance(raw, list):
        return []
    return [f for f in raw if isinstance(f, dict)][:12]


def split_fresh_findings(
    findings: List[Dict[str, Any]], current_revision: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """证据新鲜度门（C13）：``observed_revision`` == 当前 revision 的
    finding 才可进入修复面。旧观测不得驱动新地图 —— 终验之后任何
    mutation（用户手改/agent/修复）都会推进 revision，存储态证据随即
    过期；缺省/未知（旧数据 0）保守按过期处理。返回 (fresh, stale_count)。
    """
    fresh: List[Dict[str, Any]] = []
    stale = 0
    for f in findings:
        try:
            observed = int(f.get("observed_revision") or 0)
        except (TypeError, ValueError):
            observed = 0
        if observed == int(current_revision):
            fresh.append(f)
        else:
            stale += 1
    return fresh, stale


async def current_mutation_revision(session_id: str) -> int:
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id)
        return int((state or {}).get("_cartographic_mutation_revision") or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "stored_visual_findings",
    "split_fresh_findings",
    "current_mutation_revision",
]
