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
    "context_digest",
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
#: 循环预算（**有生产驱动点**的 long-horizon 回路；有界重入数）。
#: 「replan」回路的驱动点 = finalizer 出口 ``plan_runtime.request_replan``
#: （V7 ADR-0134 D2：修复不可达 → 置 replan_pending → 计划事实一变即
#: 消费）—— 预算与驱动点同一 commit 落地（入而无驱动 = 预算耗尽永不可
#: 达，审查 R1 M4 的教训）。
LOOP_BUDGETS: Dict[str, int] = {
    "deepen": 2,      # 数据资格不足 → deepen_profile
    "requalify": 2,   # 重资格裁决
    "repair": 2,      # 渲染/运行时修复回路（runtime_repair 另有自己的
                      # per-fingerprint 预算 —— 本账本是跨回路的总量）
    "replan": 1,      # 重规划回路（修复不可达 → 重规划剩余步骤；
                      # 保守 1 次 —— 重规划是结构级变更，不静默循环）
}
_MAX_STATE_BYTES = 2048


class RecoveryStoreUnavailable(RuntimeError):
    """Session-plane recovery_state store degraded — callers must fail-closed.

    Distinct from an empty/new recovery_state (used=0). Budget decisions
    that cannot read or persist must abort_with_disclosure (#1401).
    """


def unavailable_recovery_state() -> Dict[str, Any]:
    """Fail-closed placeholder: all loop budgets appear exhausted."""
    return {
        "v": 1,
        "position": {},
        # Fully spent → used >= budget at every LOOP_BUDGETS check.
        "loops": {k: int(v) for k, v in LOOP_BUDGETS.items()},
        "history": [],
        "updated_at": time.time(),
        "_unavailable": True,
    }


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
        "source_fingerprints": [],   # [{ref, fingerprint, descriptor_fingerprint?}] ≤16
        "updated_at": time.time(),
    }


#: recovery_state.source_fingerprints 条目上限（与 context_layers data 域同界）。
MAX_SOURCE_FINGERPRINTS = 16


async def record_source_fingerprints(
    session_id: str,
    entries: Any,
) -> None:
    """数据绑定事实 → recovery_state.source_fingerprints（durable facts 生产者）。

    H08（ADR-0215 D7 消费面接线）：``_data_domain`` 投影与恢复验证早已
    预留 ``source_fingerprints`` 读面，此前无生产者（恒空）。生产点 =
    数据落 session 的缝（ingest 成功 / mapspec 绑定成功），经
    ``context_bridge`` 两函数升级为 {ref, fingerprint, descriptor_fingerprint}
    （descriptor 指纹从语义 store 现读 —— O(refs)，不扫描数据）。

    **累积语义（review R1 P1）**：与既有条目按 ref 合并（新值覆盖同 ref，
    旧 ref 保留），再截最近 MAX_SOURCE_FINGERPRINTS 条 —— 多源 session
    的绑定事实逐次累积，绝不是「只剩最后一个 ref」的整表替换。

    additive evidence：store 不可用 / 升级失败只 log 不抛 —— 这不是预算
    语义（update_recovery_state 的 fail-closed 不适用），证据缺席由消费面
    按无指纹诚实披露。并发纪律：与 update_recovery_state 同为无锁读改写
    （调用方 session lock 契约不覆盖 ingest/mapspec 缝）；写前重读最新
    态、只 merge 本键，把覆盖窗口压缩到最小 —— loops/history 由其记账方
    权威，本函数绝不携带旧快照回写它们。
    """
    if not session_id or not isinstance(entries, list) or not entries:
        return
    from app.services.dataset_semantics.context_bridge import (
        augment_source_fingerprints,
        descriptor_fingerprints_for_session,
    )

    try:
        state = await load_recovery_state(session_id)
        if state.get("_unavailable"):
            return
        refs = [str((e or {}).get("ref") or "")
                for e in entries[:MAX_SOURCE_FINGERPRINTS] if isinstance(e, dict)]
        fps_by_ref = await descriptor_fingerprints_for_session(session_id, refs)
        incoming = augment_source_fingerprints(
            entries, fps_by_ref)[:MAX_SOURCE_FINGERPRINTS]
        # 累积合并：既有条目保留，同 ref 被新值覆盖；顺序 = 旧在前新在后。
        merged: Dict[str, Dict[str, Any]] = {}
        for item in list(state.get("source_fingerprints") or []):
            if isinstance(item, dict) and item.get("ref"):
                merged[str(item["ref"])[:200]] = item
        for item in incoming:
            merged[str(item.get("ref") or "")[:200]] = item
        state["source_fingerprints"] = list(merged.values())[
            -MAX_SOURCE_FINGERPRINTS:]
        state["updated_at"] = time.time()
        from app.services.session_data import session_data_manager

        await session_data_manager.set_map_state(
            session_id, RECOVERY_STATE_KEY, state)
    except Exception:  # noqa: BLE001 — additive 证据面不阻断主链
        logger.warning("[DurableContext] record_source_fingerprints skipped sid=%s",
                       session_id, exc_info=True)


async def load_recovery_state(session_id: str) -> Dict[str, Any]:
    """读 session-plane recovery_state。

    - 缺席 → 空态（合法 used=0）；
    - ``get_map_state`` 失败 → ``unavailable_recovery_state``（预算耗尽
      外观 + ``_unavailable`` 标记）—— 与空态区分，fail-closed (#1401)。
    """
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id)
    except Exception:  # noqa: BLE001 — 读失败 ≠ 空态
        logger.warning("[DurableContext] load_recovery_state unavailable sid=%s",
                       session_id, exc_info=True)
        return unavailable_recovery_state()
    if isinstance(state, dict):
        raw = state.get(RECOVERY_STATE_KEY)
        if isinstance(raw, dict) and isinstance(raw.get("loops"), dict):
            return raw
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
    - ``position``：merge 更新 workflow 位置摘要（浅合并，值截断）。

    并发纪律：读改写非原子 —— 当前由调用方 session lock 兜底（
    runtime_repair 契约「调用方持 session lock」）；跨锁并发写入者会
    后写覆盖（计数可能少记一次）。写失败抛 ``RecoveryStoreUnavailable``\n    （fail-closed —— 调用方不得假装已记账，#1401）。"""
    state = await load_recovery_state(session_id)
    if state.get("_unavailable"):
        raise RecoveryStoreUnavailable(
            f"recovery store unavailable for session {session_id}")
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
    except Exception as exc:  # noqa: BLE001 — 写失败 → fail-closed (#1401)
        logger.warning("[DurableContext] persist recovery_state failed sid=%s",
                       session_id, exc_info=True)
        raise RecoveryStoreUnavailable(
            f"recovery store write failed for session {session_id}"
        ) from exc
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
    "RecoveryStoreUnavailable",
    "classify_context_key",
    "new_recovery_state",
    "record_source_fingerprints",
    "MAX_SOURCE_FINGERPRINTS",
    "unavailable_recovery_state",
    "load_recovery_state",
    "update_recovery_state",
    "reasoning_digest",
    "recovery_state_for_anchor",
]
