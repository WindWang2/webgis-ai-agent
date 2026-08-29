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
    """读取 provenance 列表（旧→新）。不可用返回 []。

    v2(audit P1)：定向读单字段 —— 旧实现全量 get_map_state（HGETALL +
    ~1MiB mapspec 完整解析）只为读 64 条 ring；守卫环每 mutation 读两次，
    是热路径 4 次全量解析中的 2 次。
    """
    try:
        get_field = getattr(session_data_manager, "get_state_field", None)
        if callable(get_field):
            entries = await get_field(session_id, _PROVENANCE_KEY)
        else:
            state = await session_data_manager.get_map_state(session_id) or {}
            entries = state.get(_PROVENANCE_KEY)
    except Exception:  # noqa: BLE001
        return []
    return list(entries) if isinstance(entries, list) else []


async def append_provenance(session_id: str, entry: ProvenanceEntry) -> None:
    """追加一条记录并维持环形上限。best-effort。"""
    try:
        get_field = getattr(session_data_manager, "get_state_field", None)
        if callable(get_field):
            entries = await get_field(session_id, _PROVENANCE_KEY)
        else:
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
    """某层 presentation 的最后一条决策记录（按 seq/revision 最大值取赢家）。

    2026-08-27 之前按 list 逆序"最后出现的"取赢家：在并发 append 交错/顺序
    倒置时可能返回旧 revision 的决策（误判 user 已无决策 → 守卫放行）。
    现在按 revision（回退 seq）取最大值——并发写入的完成顺序不影响决策是谁
    的"最新版本"，tail 被 newer revision 覆盖的旧决策不再被误当最后一条。
    """
    best: Optional[Dict[str, Any]] = None
    best_seq = -1
    for entry in entries:
        if (
            entry.get("kind") == "PatchLayerPresentationIntent"
            and entry.get("target") == layer_id
        ):
            seq = entry.get("seq", 0)
            if isinstance(seq, str):
                try:
                    seq = int(seq)
                except (TypeError, ValueError):
                    seq = 0
            if not isinstance(seq, int):
                seq = entry.get("revision", 0) if isinstance(entry.get("revision"), int) else 0
            if seq > best_seq or best is None:
                best = entry
                best_seq = seq
    return best


def durable_presentation_owner(
    entries: List[Dict[str, Any]],
    layer_id: str,
    *,
    revision_threshold: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """durable owner：只在环形日志里找——若最近的可见决策超过 revision 阈值
    前（由 finalize 等大批量 agent 写入把 user 决策挤出尾部），回退从全
    环找（64 条全量）——state 的尾部 16 条不能作为守卫依据。"""

    owner = last_presentation_owner(entries, layer_id)
    return owner
