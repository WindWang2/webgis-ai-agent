"""Project-level Resume Anchor（Harness V5 — ADR-0118 决策 D8）。

V4 基线缺口：plan / workflow_instance / map_product / observation 全部
session-TTL 绑定；RELOAD_REF 的 durable 半边（RefSpill）只有 24h TTL。
session 过期 = 工作流断头，无法在项目权限下继续。

V5 契约：

- **锚点 = 恢复指针，不是第二真相**：只持久化「从哪里继续」的最小
  事实（user_goal + gis_chapter 关键块 + progress + trace 游标 + ref
  清单）；恢复后的新 session 由既有 session store 承载（SessionPlan /
  save_session_plan 既有通道），DB 里不复制 session 状态机。
- **有界**：chapter 只拷 workflow_instance / map_product / intent /
  query 等关键键（各自本就有界）；ref 清单有界 ≤128。
- **诚实恢复**：旧 ref 载荷能从旧 session（未过期）或 RefSpill（24h
  内被驱逐过的）重水合 → 拷进新 session；否则进 ``missing_refs``
  披露清单 —— 绝不伪造数据在场。
- **授权**：写锚点走 require_owned_session；恢复要求当前用户 ==
  锚点 user_id（匿名锚点不可恢复，与 chat_resume 匿名拒绝同门）。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 锚点内 ref 清单上限（bounded everything）。
MAX_ANCHOR_REFS = 128
#: gis_chapter 中可跨 session 恢复的关键块键。
RESTORABLE_CHAPTER_KEYS = (
    "workflow_instance",
    "map_product",
    "intent",
    "query",
    "goal",
)
_ANCHOR_SCHEMA_VERSION = 1


async def build_anchor(session_id: str) -> Optional[Dict[str, Any]]:
    """从（可能即将过期的）session 提取有界恢复锚点。session 空 → None。"""
    from app.services.session_plan import load_session_plan
    from app.services.gis_harness.trace_store import last_seq
    from app.services.session_data import session_data_manager

    plan = await load_session_plan(session_id)
    if plan is None:
        return None
    chapter = plan.gis_chapter if isinstance(plan.gis_chapter, dict) else {}
    restored_chapter: Dict[str, Any] = {
        k: chapter[k] for k in RESTORABLE_CHAPTER_KEYS if k in chapter
    }
    try:
        ref_ids = list((await session_data_manager.list_refs(session_id)).keys())
    except Exception:  # noqa: BLE001 — ref 清单缺席照常建锚（恢复时披露）
        ref_ids = []
    return {
        "schema_version": _ANCHOR_SCHEMA_VERSION,
        "created_at": time.time(),
        "source_session_id": session_id,
        "user_goal": str(plan.user_goal or ""),
        "envelope_id": str(plan.envelope_id or ""),
        "progress": [p.model_dump() for p in (plan.progress or [])][:32],
        "gis_chapter": restored_chapter,
        "trace_last_seq": last_seq(session_id),
        "ref_ids": ref_ids[:MAX_ANCHOR_REFS],
        "refs_truncated": len(ref_ids) > MAX_ANCHOR_REFS,
    }


async def save_anchor(
    db: Any,
    *,
    session_id: str,
    user_id: Optional[str],
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构建 + 落库（路由侧已做 require_owned_session）。"""
    from app.models.project import WorkflowResumeAnchor

    anchor = await build_anchor(session_id)
    if anchor is None:
        raise ValueError("session has no resumable plan")
    row = WorkflowResumeAnchor(
        session_id=session_id,
        user_id=user_id,
        project_id=project_id,
        anchor=anchor,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"anchor_id": row.id, "anchor": anchor}


async def resume_from_anchor(
    db: Any,
    *,
    anchor_id: str,
    user_id: Optional[str],
) -> Dict[str, Any]:
    """在项目/用户权限下从锚点恢复新 session（旧 session 不复活）。

    授权：锚点必须存在且 ``user_id`` 与当前用户一致（含双方皆 None 的
    匿名拒绝 —— 匿名不可恢复）。
    """
    from app.models.project import WorkflowResumeAnchor
    from app.services.session_plan import (
        SessionPlan,
        save_session_plan,
    )
    from app.services.session_data import session_data_manager

    row = await db.get(WorkflowResumeAnchor, anchor_id)
    if row is None:
        raise LookupError("resume anchor not found")
    if not user_id or str(row.user_id or "") != str(user_id):
        raise PermissionError("resume anchor is not owned by current user")

    anchor = row.anchor if isinstance(row.anchor, dict) else {}
    old_sid = str(anchor.get("source_session_id") or row.session_id)
    new_sid = f"resume-{anchor_id[:8]}-{uuid.uuid4().hex[:8]}"

    # ref 载荷重水合：旧 session 仍在 → 直取；否则 RefSpill（24h）兜底；
    # 双双缺席 → missing_refs 诚实披露（不伪造）。
    # review R1 #1（CRITICAL 修复）：ref id 是 session 域能力令牌 —— store
    # 生成**新** id，必须返回 old→new 映射（ref_map），restored_refs 以
    # 新 id 计，调用方用新 id 寻址；绝不把旧 id 谎报为可用。
    ref_map: Dict[str, str] = {}
    restored_refs: List[str] = []
    missing_refs: List[str] = []
    for ref_id in anchor.get("ref_ids") or []:
        payload = None
        try:
            payload = await session_data_manager.get(old_sid, ref_id)
        except Exception:  # noqa: BLE001 — 旧 session 读失败按缺席
            payload = None
        if payload is None:
            try:
                from app.services.session_data import RefSpillStore

                payload = RefSpillStore().load(old_sid, ref_id)
            except Exception:  # noqa: BLE001 — spill 缺席按缺席
                payload = None
        if payload is None:
            missing_refs.append(ref_id)
            continue
        try:
            new_ref = await session_data_manager.store(new_sid, payload)
            if new_ref:
                ref_map[ref_id] = new_ref
                restored_refs.append(new_ref)
            else:
                missing_refs.append(ref_id)
        except Exception:  # noqa: BLE001 — 新 session 写失败也如实披露
            missing_refs.append(ref_id)

    plan = SessionPlan(
        envelope_id=f"sp-{uuid.uuid4().hex[:12]}",
        session_id=new_sid,
        user_goal=str(anchor.get("user_goal") or ""),
        gis_chapter={
            k: v
            for k, v in (anchor.get("gis_chapter") or {}).items()
            if k in RESTORABLE_CHAPTER_KEYS
        },
        progress=[],
        previous_goal="",
    )
    plan.gis_chapter = dict(plan.gis_chapter or {}) if plan.gis_chapter else {}
    plan.gis_chapter["resumed_from"] = {
        "anchor_id": anchor_id,
        "source_session_id": old_sid,
        "resumed_at": time.time(),
    }
    await save_session_plan(plan)
    await session_data_manager.set_map_state(
        new_sid, "_resumed_from", {"anchor_id": anchor_id, "source_session_id": old_sid}
    )

    return {
        "session_id": new_sid,
        "source_session_id": old_sid,
        "user_goal": plan.user_goal,
        "restored_chapter_keys": [
            k for k in RESTORABLE_CHAPTER_KEYS
            if k in (plan.gis_chapter or {})
        ],
        "restored_refs": restored_refs,
        "ref_map": ref_map,
        "missing_refs": missing_refs,
        "trace_last_seq": int(anchor.get("trace_last_seq") or 0),
    }


__all__ = [
    "build_anchor",
    "save_anchor",
    "resume_from_anchor",
    "RESTORABLE_CHAPTER_KEYS",
    "MAX_ANCHOR_REFS",
]
