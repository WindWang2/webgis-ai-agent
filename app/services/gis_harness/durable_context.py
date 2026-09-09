"""Durable Context & Project Memory（ADR-0119 决策 D4）。

V5 基线（baseline G2）：``resume_anchor`` 只是恢复**指针**（user_goal +
chapter 关键块 + trace 游标 + ref 清单）；reasoning/recovery 状态不在
锚点内，turn 边界不落 project 级上下文，无 durable/rebuildable/forbidden
分层 —— 什么允许长期持久化没有显式词表。

V6 三分层模型（封闭词表，`classify_context_key` 可审计）：

- **durable facts**（允许长期持久化 / 跨 session 恢复）：用户目标、
  workflow 位置、方法族选择、数据源指纹、verdict 摘要、ref 清单、
  recovery 状态（循环预算余量）、trace 游标；
- **rebuildable projections**（可由权威状态重建，**不**复制进 durable
  层 —— 防第二事实源）：tool surface、observation payload、SessionPlan
  投影、 capability 反查视图、组件目录；
- **forbidden**（永不持久化）：LLM raw context（无边界）、token/密钥、
  完整工具 payload、任意 map_state 键（白名单外一律禁止）。

载体（零新表 / 零 migration）：
- **live**：session-plane map_state 键 ``_recovery_state``（turn 边界
  写，有界 ≤2KB 摘要 + ≤16 条循环历史）；
- **project**：``WorkflowResumeAnchor.anchor`` additive JSON 键
  （``recovery_state`` / ``reasoning_digest``）—— ``build_anchor`` 采集、
  ``resume_from_anchor`` 重注入（owner/project authz 语义不变）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

RECOVERY_STATE_KEY = "_recovery_state"

#: durable facts（锚点/恢复面允许携带的封闭词表）。
DURABLE_FACT_KEYS: Tuple[str, ...] = (
    "user_goal", "workflow_position", "methodology_family",
    "source_fingerprints", "verdict_summary", "ref_ids",
    "recovery_state", "trace_last_seq", "resumed_from", "reasoning_digest",
)
#: rebuildable projections（权威状态可重建 —— 不复制）。
REBUILDABLE_KEYS: Tuple[str, ...] = (
    "tool_surface", "observation", "session_plan_projection",
    "component_catalog", "capability_view", "cartography_status",
)
#: forbidden（永不持久化）。
FORBIDDEN_KEYS: Tuple[str, ...] = (
    "llm_raw_context", "messages", "token", "api_key", "secret",
    "password", "credential", "tool_payload",
)

#: recovery_state 循环历史上限（bounded everything）。
MAX_LOOP_HISTORY = 16
#: 循环预算（每种 long-horizon 回路的有界重入数）。
LOOP_BUDGETS: Dict[str, int] = {
    "deepen": 2,      # 数据资格不足 → deepen_profile
    "requalify": 2,   # 重资格裁决
    "repair": 2,      # 渲染/运行时修复回路（runtime_repair 另有自己的
                      # per-fingerprint 预算 —— 本账本是跨回路的总量）
    "replan": 1,
}
_MAX_STATE_BYTES = 2048


def classify_context_key(key: str) -> str:
    """上下文键 → 分层词表（durable/rebuildable/forbidden/unknown）。

    unknown → 按保守纪律视为 forbidden（白名单外不持久化）。"""
    k = (key or "").strip().lower()
    if any(b in k for b in FORBIDDEN_KEYS):
        return "forbidden"
    if k in DURABLE_FACT_KEYS:
        return "durable"
    if k in REBUILDABLE_KEYS:
        return "rebuildable"
    return "forbidden"


def new_recovery_state(*, position: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """空 recovery_state（结构化、有界、schema 版本化）。"""
    return {
        "v": 1,
        "position": dict(position or {}),
        "loops": {k: 0 for k in LOOP_BUDGETS},
        "history": [],       # [{loop, detail, ts}] ≤ MAX_LOOP_HISTORY
        "updated_at": time.time(),
    }


async def load_recovery_state(session_id: str) -> Dict[str, Any]:
    """读 session-plane recovery_state（缺席 → 空态；任何失败不抛）。"""
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id)
        if isinstance(state, dict):
            raw = state.get(RECOVERY_STATE_KEY)
            if isinstance(raw, dict) and isinstance(raw.get("loops"), dict):
                return raw
    except Exception:  # noqa: BLE001 — 读失败按空态
        logger.debug("[DurableContext] load_recovery_state failed sid=%s",
                     session_id, exc_info=True)
    return new_recovery_state()


async def update_recovery_state(
    session_id: str,
    *,
    position: Optional[Dict[str, Any]] = None,
    loop: Optional[str] = None,
    detail: str = "",
    reset_loop: Optional[str] = None,
) -> Dict[str, Any]:
    """turn 边界 / 回路事件时更新 recovery_state（读-改-写，有界）。

    - ``loop``：该回路计数 +1 并记历史；
    - ``reset_loop``：计数清零（如 spec 内容变化 → 修复预算重置语义）；
    - ``position``：merge 更新 workflow 位置摘要（浅合并，值截断）。"""
    state = await load_recovery_state(session_id)
    if position:
        pos = state.setdefault("position", {})
        for k, v in list(position.items())[:8]:
            pos[str(k)[:48]] = str(v)[:120]
    if reset_loop in LOOP_BUDGETS:
        state.setdefault("loops", {})[reset_loop] = 0
    if loop in LOOP_BUDGETS:
        state.setdefault("loops", {})
        state["loops"][loop] = int(state["loops"].get(loop) or 0) + 1
        hist = state.setdefault("history", [])
        hist.append({"loop": loop, "detail": str(detail)[:160],
                     "ts": time.time()})
        del hist[:-MAX_LOOP_HISTORY]
    state["updated_at"] = time.time()
    try:
        from app.services.session_data import session_data_manager

        await session_data_manager.set_map_state(
            session_id, RECOVERY_STATE_KEY, state)
    except Exception:  # noqa: BLE001 — 写失败不阻断（live 态缺席可重建）
        logger.debug("[DurableContext] persist recovery_state failed sid=%s",
                     session_id, exc_info=True)
    return state


def reasoning_digest(chapter: Dict[str, Any], recovery: Dict[str, Any],
                     *, max_len: int = 512) -> str:
    """有界 reasoning 摘要（进锚点的 durable fact；非 LLM raw context）。"""
    try:
        wf = chapter.get("workflow_instance") if isinstance(chapter, dict) else None
        pos = ""
        if isinstance(wf, dict):
            pos = str(wf.get("status") or "")[:40]
        loops = recovery.get("loops") if isinstance(recovery, dict) else {}
        loop_bits = ",".join(
            f"{k}={v}" for k, v in sorted((loops or {}).items()) if v)
        digest = f"wf={pos};loops={loop_bits or 'clean'}"
        return digest[:max_len]
    except Exception:  # noqa: BLE001 — 摘要失败按空
        return ""


def recovery_state_for_anchor(state: Dict[str, Any]) -> Dict[str, Any]:
    """recovery_state → 锚点内嵌快照（有界；schema 校验失败按空）。"""
    if not isinstance(state, dict) or not isinstance(state.get("loops"), dict):
        return {}
    return {
        "v": 1,
        "position": dict(list((state.get("position") or {}).items())[:8]),
        "loops": {k: int((state.get("loops") or {}).get(k) or 0)
                  for k in LOOP_BUDGETS},
        "history": list((state.get("history") or []))[-MAX_LOOP_HISTORY:],
        "updated_at": state.get("updated_at") or 0,
    }


__all__ = [
    "RECOVERY_STATE_KEY",
    "DURABLE_FACT_KEYS",
    "REBUILDABLE_KEYS",
    "FORBIDDEN_KEYS",
    "LOOP_BUDGETS",
    "MAX_LOOP_HISTORY",
    "classify_context_key",
    "new_recovery_state",
    "load_recovery_state",
    "update_recovery_state",
    "reasoning_digest",
    "recovery_state_for_anchor",
]
