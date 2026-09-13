"""消费方查询缝（narrow interface，R5/R6）。

Situation（方向 2，PR #1275）未合并——本模块只暴露**窄接口**：
``MemoryProjectionInput``（纯数据）+ ``build_memory_projection``（异步门）。
#1275 合并后可把它当作 SituationCompiler 的一个事实源挂入，不需要改本包。

map_intent 的 scope 兜底（R6）也在这里：``consult_scope_fallback``。
org 桥：harvest 把 ``_gis_memory_org`` 烙进 map_state（session 平面），
工具侧读它做租户等值过滤——不猜 org、不旁路 tenancy（fail-closed）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from app.services.gis_memory.projection import render_memory_block
from app.services.gis_memory.retrieval import (
    MemoryQueryContext,
    latest_resolved_place,
    retrieve_memories,
)

logger = logging.getLogger(__name__)

#: session 平面上烙印记忆租户的键（harvest 写 / 工具读）。
MEMORY_ORG_STATE_KEY = "_gis_memory_org"
MEMORY_USER_STATE_KEY = "_gis_memory_user"


@dataclass(frozen=True)
class MemoryProjectionInput:
    """narrow interface：situation/planner → 记忆投影所需的最小情境。"""

    org_id: str
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    query_text: str = ""
    subjects: tuple = ()
    dataset_versions: Dict[str, str] = field(default_factory=dict)
    limit: int = 8


_session_local_factory = None


def set_session_local_factory(factory) -> None:
    """测试 seam：覆盖 queries 侧的 DB 会话工厂（与 harvest 共用）。"""
    global _session_local_factory
    _session_local_factory = factory


def _db_session_factory():
    if _session_local_factory is not None:
        return _session_local_factory
    from app.core.database import SessionLocal

    return SessionLocal


def build_memory_projection_sync(inp: MemoryProjectionInput) -> str:
    """同步实现（DB 读取 + 渲染）。失败返回空串（记忆绝不阻断 turn）。"""
    if not inp.org_id:
        return ""
    try:
        SessionLocal = _db_session_factory()
        with SessionLocal() as db:
            hits = retrieve_memories(
                db,
                MemoryQueryContext(
                    org_id=inp.org_id,
                    session_id=inp.session_id,
                    project_id=inp.project_id,
                    user_id=inp.user_id,
                    query_text=inp.query_text,
                    subjects=inp.subjects,
                    dataset_versions=inp.dataset_versions,
                    limit=inp.limit,
                ),
            )
            return render_memory_block(hits)
    except Exception as exc:  # noqa: BLE001 — fail-open（无记忆退化）
        logger.warning("[GISMemory] projection unavailable: %s", exc)
        return ""


async def build_memory_projection(inp: MemoryProjectionInput) -> str:
    """异步门（事件循环不碰同步 DB）。"""
    import asyncio

    return await asyncio.to_thread(build_memory_projection_sync, inp)


def consult_scope_fallback_sync(
    org_id: str,
    session_id: Optional[str],
    project_id: Optional[str],
) -> Optional[Dict[str, object]]:
    """R6：本 turn scope 未解析时的记忆兜底候选（调用方决定是否采用）。

    返回 ``{"subject", "value", "confidence", "scope"}`` 或 None。
    只读；fresh 解析永远优先（调用侧保证）。
    """
    if not org_id:
        return None
    try:
        SessionLocal = _db_session_factory()
        with SessionLocal() as db:
            hit = latest_resolved_place(
                db,
                org_id=org_id,
                session_id=session_id,
                project_id=project_id,
            )
            if hit is None:
                return None
            return {
                "subject": hit.record.subject,
                "value": dict(hit.record.value),
                "confidence": float(hit.record.confidence),
                "scope": hit.record.scope,
                "evidence_source": str(
                    (hit.record.evidence or {}).get("source", "")
                ),
            }
    except Exception as exc:  # noqa: BLE001 — 兜底失败 = 无兜底
        logger.debug("[GISMemory] scope fallback unavailable: %s", exc)
        return None


async def read_memory_identity(session_id: str) -> tuple[str, str]:
    """工具侧租户桥：读 harvest 烙印的 (org, user)。

    无烙印 → ("", "") = 拒绝检索（fail-closed：没有租户归属就不给记忆）。
    走 session 平面（Redis/内存），零 SQL。
    """
    if not session_id:
        return "", ""
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id)
        if not isinstance(state, dict):
            return "", ""
        return (
            str(state.get(MEMORY_ORG_STATE_KEY) or ""),
            str(state.get(MEMORY_USER_STATE_KEY) or ""),
        )
    except Exception:  # noqa: BLE001 — 桥不可达 = 检索拒绝（fail-closed）
        return "", ""


__all__ = [
    "MEMORY_ORG_STATE_KEY",
    "MEMORY_USER_STATE_KEY",
    "MemoryProjectionInput",
    "build_memory_projection",
    "build_memory_projection_sync",
    "consult_scope_fallback_sync",
    "read_memory_identity",
    "set_session_local_factory",
]
