"""Agent 提交撤销点（PR-D：Ctrl+Z 撤销一整笔 Agent 地图提交）。

模型：
- 每笔 ``origin="agent"`` 且真正改变制图内容的 MapSpec 提交，引擎在同一提交
  事务里记录一个**撤销点**：``{revision, base_revision, pre_checkpoint_id,
  post_fingerprint}``。``pre_checkpoint_id`` 是提交前 spec 的自动 checkpoint
  （presentation-only、内容去重、随自动 checkpoint 保留上限裁剪）。
- 用户经 ``undo_agent_commit`` user mutation（expected_revision CAS）请求把
  ``(base_revision, agent_revision]`` 这一段 Agent 提交整体回滚到
  ``base_revision`` 时的 checkpoint。
- 冲突处理（服务端权威，不信任前端）：
  * 段内每个 revision 都必须是 Agent 撤销点（中间夹了用户/系统提交 →
    ``AGENT_UNDO_INTERLEAVED``，拒绝；否则会把用户改动一并抹掉）；
  * 当前 spec 的制图指纹必须等于 ``agent_revision`` 提交后的指纹 —— 用户在
    Agent 提交之后又改过（且未撤销）→ ``AGENT_UNDO_CONFLICT``，拒绝；
  * 撤销点已被裁剪/checkpoint 已过期 → ``AGENT_UNDO_EXPIRED``。
- 重做（redo）：撤销时先把当前（Agent 提交后）状态做成 checkpoint，记在
  撤销点上；redo 要求当前指纹仍等于撤销后的指纹，再回滚到该 checkpoint。

撤销点列表存放在 map_state ``_agent_undo_points``（有界 FIFO），随 spec 提交
单事务落地（``save_mapspec(extra_fields=…)``）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

AGENT_UNDO_KEY = "_agent_undo_points"
#: 与自动 checkpoint 保留上限同量级（checkpoint.MAPSPEC_CKPT_RETENTION=20）——
#: 更多的撤销点也只会指向已被裁剪的 checkpoint。
AGENT_UNDO_LIMIT = 20

ERR_EXPIRED = "AGENT_UNDO_EXPIRED"
ERR_INTERLEAVED = "AGENT_UNDO_INTERLEAVED"
ERR_CONFLICT = "AGENT_UNDO_CONFLICT"
ERR_NOT_UNDONE = "AGENT_REDO_NOT_UNDONE"


def fingerprint(mapspec: Optional[Dict[str, Any]]) -> Optional[str]:
    """制图语义指纹（与引擎 review 同一实现）；失败返回 None。"""
    if not isinstance(mapspec, dict):
        return None
    try:
        from app.lib.cartography.quality_loop import cartographic_fingerprint

        return cartographic_fingerprint(mapspec)
    except Exception:  # noqa: BLE001 — 指纹失败即不可判定（调用方按冲突处理）
        return None


def undo_points_of(state: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    raw = state.get(AGENT_UNDO_KEY) if isinstance(state, dict) else None
    if not isinstance(raw, list):
        return []
    return [dict(p) for p in raw if isinstance(p, dict) and isinstance(p.get("revision"), int)]


def _bounded(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    points = sorted(points, key=lambda p: p["revision"])
    return points[-AGENT_UNDO_LIMIT:]


def record_point_fields(
    pre_state: Optional[Dict[str, Any]],
    *,
    revision: int,
    base_revision: int,
    pre_checkpoint_id: str,
    post_fingerprint: Optional[str],
) -> Dict[str, Any]:
    """提交侧 extra_fields：追加本笔 Agent 提交的撤销点（FIFO 有界）。

    同 revision 旧条目（回滚后重放同世代号）被覆盖；revision 之后的条目
    （理论上不应存在 —— revision 单调）一并丢弃，保持列表单调。
    """
    points = [p for p in undo_points_of(pre_state) if p["revision"] < revision]
    points.append({
        "revision": int(revision),
        "base_revision": int(base_revision),
        "pre_checkpoint_id": str(pre_checkpoint_id),
        "post_fingerprint": post_fingerprint,
    })
    return {AGENT_UNDO_KEY: _bounded(points)}


class AgentUndoRefusal(Exception):
    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def resolve_undo_segment(
    pre_state: Optional[Dict[str, Any]],
    *,
    base_revision: int,
    agent_revision: int,
) -> List[Dict[str, Any]]:
    """校验 ``(base_revision, agent_revision]`` 是连续的 Agent 撤销点段。"""
    if agent_revision <= base_revision:
        raise AgentUndoRefusal(
            ERR_EXPIRED, "agent_revision must be greater than base_revision",
            "撤销点参数非法；请刷新后重试。",
        )
    by_rev = {p["revision"]: p for p in undo_points_of(pre_state)}
    segment: List[Dict[str, Any]] = []
    for rev in range(base_revision + 1, agent_revision + 1):
        point = by_rev.get(rev)
        if point is None:
            if rev == base_revision + 1 or not by_rev or rev > max(by_rev):
                raise AgentUndoRefusal(
                    ERR_EXPIRED,
                    f"No agent undo point for revision {rev}",
                    "该 Agent 提交的撤销点已过期（超出保留上限）或不存在。",
                )
            raise AgentUndoRefusal(
                ERR_INTERLEAVED,
                f"Revision {rev} is not an agent commit",
                "该段 Agent 提交之间夹有其他修改，整体撤销会丢失这些修改；"
                "请先撤销之后的修改。",
            )
        segment.append(point)
    return segment


def replace_points_fields(
    pre_state: Optional[Dict[str, Any]],
    updated: Dict[str, Any],
) -> Dict[str, Any]:
    points = [p for p in undo_points_of(pre_state) if p["revision"] != updated["revision"]]
    points.append(updated)
    return {AGENT_UNDO_KEY: _bounded(points)}
