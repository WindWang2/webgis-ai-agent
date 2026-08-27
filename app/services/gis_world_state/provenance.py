"""有界 per-session mutation provenance（GISWorldState C2）。

每次经 `apply_gis_mutation` 的成功 mutation 记录一条：
谁（origin/actor）、什么（intent kind + target）、何时、推进到哪个 revision。

用途：
1. **user-wins 策略依据** —— 层 presentation 的"最后主人"决定 agent 能否
   反转用户决策（见 mutation.py 的 UserPresentationGuard）。
2. **审计与 QA** —— reload 后仍可回答"这个层为什么是隐藏的"。
3. **冲突解释** —— superseded/修复循环可携带最近决策上下文。

存储：map_state 的 `_gis_provenance` 键（list，尾部追加，超限裁剪头部）。
best-effort：读写失败只记日志，绝不阻断 mutation 主语义。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

PROVENANCE_LIMIT = 64
_PROVENANCE_KEY = "_gis_provenance"


@dataclass
class ProvenanceEntry:
    """一条 mutation 决策记录（有界小对象，可安全进 LLM 上下文摘要）。"""

    seq: int
    ts: str
    origin: str  # "user" | "agent" | "system"
    actor: str  # 具体来源：route / tool name / repair loop …
    kind: str  # intent 类别名（PatchLayerPresentationIntent 等）
    target: Optional[str]  # layer_id / component_id / None
    revision: int
    summary: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_provenance(session_id: str) -> List[Dict[str, Any]]:
    """读取 provenance 列表（旧→新）。不可用返回 []。"""
    try:
        state = await session_data_manager.get_map_state(session_id) or {}
    except Exception:  # noqa: BLE001
        return []
    entries = state.get(_PROVENANCE_KEY)
    return list(entries) if isinstance(entries, list) else []


async def append_provenance(session_id: str, entry: ProvenanceEntry) -> None:
    """追加一条记录并维持环形上限。best-effort。"""
    try:
        state = await session_data_manager.get_map_state(session_id) or {}
        entries = state.get(_PROVENANCE_KEY)
        entries = list(entries) if isinstance(entries, list) else []
        entries.append(entry.to_dict())
        if len(entries) > PROVENANCE_LIMIT:
            entries = entries[-PROVENANCE_LIMIT:]
        await session_data_manager.set_map_state(
            session_id, _PROVENANCE_KEY, entries
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[gis_world_state] provenance append failed: %s", e)


def last_presentation_owner(
    entries: List[Dict[str, Any]], layer_id: str
) -> Optional[Dict[str, Any]]:
    """某层 presentation 的最后一条决策记录（新→旧扫描第一条命中）。

    返回 None 表示该层没有已记录的 presentation 决策（初始/未知来源）。
    """
    for entry in reversed(entries):
        if (
            entry.get("kind") == "PatchLayerPresentationIntent"
            and entry.get("target") == layer_id
        ):
            return entry
    return None
