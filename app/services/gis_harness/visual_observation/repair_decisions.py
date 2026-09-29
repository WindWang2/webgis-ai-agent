"""视觉修复决策账本 + 拒绝记忆（C13）。

视觉回路的**因果账本**：每次 plan / apply / reject / auto-apply 都落一条
有界 typed 条目 —— 回答「这个 patch 是谁、基于哪份证据、以何种决定收场」。

- **拒绝记忆**（DoD：用户拒绝后不得反复提同一 patch）：``rejected`` 按
  finding 的 ``recurrence_fingerprint``（原因身份）持久（会话内）——
  plan/auto 面过滤已拒原因，同一缺陷不再索要；证据变化使 finding 消失
  或换新身份后自然放行（不是永久静默）。``defect_fingerprint``（healer
  缺陷集指纹）随条目留痕，供审计与幂等对账。
- **user-wins 证据**：approved / rejected 都记录 origin 与 revision ——
  用户手工修改后 revision 前进，旧证据在修复面按 stale 处理（不与本
  账本重复兜底，双层防御）。

有界：≤32 条 FIFO（``map_state["_visual_repair_decisions"]``，``_`` 前缀
私有键纪律）。全部 fail-open：账本失败只降级披露/记忆面，绝不阻断主链。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

DECISIONS_KEY = "_visual_repair_decisions"
MAX_DECISIONS = 32

#: 决策词表（冻结）。
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_AUTO_APPLIED = "auto_applied"
#: 自动通道尝试未提交（CAS 漂移 / 引擎异常 / 错误回执）—— 因果账本
#: 如实记失败收场，不与 auto_applied 混淆（C13 review P2-3）。
DECISION_AUTO_FAILED = "auto_failed"

_DECISIONS = frozenset({
    DECISION_APPROVED, DECISION_REJECTED,
    DECISION_AUTO_APPLIED, DECISION_AUTO_FAILED,
})


def _clip(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


async def load_decisions(session_id: str) -> List[Dict[str, Any]]:
    """读决策账本（畸形条目诚实跳过；异常 → 空）。"""
    from app.services.session_data import session_data_manager

    try:
        raw = await session_data_manager.get_map_state(session_id)
        entries = (raw or {}).get(DECISIONS_KEY)
        if not isinstance(entries, list):
            return []
        return [e for e in entries if isinstance(e, dict)][:MAX_DECISIONS]
    except Exception:  # noqa: BLE001 — 账本缺席 = 无决策
        return []


async def record_decision(
    session_id: str,
    *,
    decision: str,
    proposal_id: str = "",
    intent_fingerprint: str = "",
    defect_fingerprint: str = "",
    recurrence_fingerprints: Iterable[str] = (),
    origin: str = "",
    revision: int = 0,
    finding_ids: Iterable[str] = (),
    op_labels: Iterable[str] = (),
    reason: str = "",
) -> bool:
    """追加一条决策（≤32 FIFO；同 proposal_id 的旧条目被替换）。"""
    from app.services.session_data import session_data_manager

    if decision not in _DECISIONS:
        return False
    entry = {
        "ts": int(time.time()),
        "decision": decision,
        "proposal_id": _clip(proposal_id, 96),
        "intent_fingerprint": _clip(intent_fingerprint, 80),
        "defect_fingerprint": _clip(defect_fingerprint, 96),
        "recurrence_fingerprints": [
            _clip(x, 64) for x in tuple(recurrence_fingerprints)[:12] if x],
        "origin": _clip(origin, 12),
        "revision": int(revision or 0),
        "finding_ids": [_clip(x, 96) for x in tuple(finding_ids)[:12]],
        "op_labels": [_clip(x, 40) for x in tuple(op_labels)[:8]],
        "reason": _clip(reason, 120),
    }
    try:
        raw = await session_data_manager.get_map_state(session_id)
        entries = (raw or {}).get(DECISIONS_KEY)
        items = [e for e in entries if isinstance(e, dict)] \
            if isinstance(entries, list) else []
        items = [
            e for e in items
            if not (proposal_id and str(e.get("proposal_id") or "") == str(proposal_id))
        ]
        items.append(entry)
        while len(items) > MAX_DECISIONS:
            items.pop(0)
        return bool(await session_data_manager.set_map_state(
            session_id, DECISIONS_KEY, items))
    except Exception:  # noqa: BLE001 — 记账失败不阻断主链
        logger.warning("[VisualDecisions] record failed", exc_info=True)
        return False


def rejected_fingerprints(decisions: List[Dict[str, Any]]) -> frozenset:
    """被用户拒绝的原因身份集（recurrence_fingerprint；plan/auto 过滤输入）。

    approved 条目撤销同键的更早 rejected（用户改主意 = 最新裁决获胜）。
    """
    approved_keys = set()
    for d in decisions or []:
        if str(d.get("decision") or "") == DECISION_APPROVED:
            approved_keys.update(
                str(x) for x in (d.get("recurrence_fingerprints") or []) if x)
    out = set()
    for d in decisions or []:
        if str(d.get("decision") or "") != DECISION_REJECTED:
            continue
        for fp in (d.get("recurrence_fingerprints") or []):
            fp = str(fp or "")
            if fp and fp not in approved_keys:
                out.add(fp)
    return frozenset(out)


def decision_for_proposal(
    decisions: List[Dict[str, Any]], proposal_id: str
) -> Optional[Dict[str, Any]]:
    """提案的最近决策（无 → None）。"""
    wanted = str(proposal_id or "")
    for d in reversed(decisions or []):
        if str(d.get("proposal_id") or "") == wanted:
            return d
    return None


__all__ = [
    "DECISIONS_KEY",
    "MAX_DECISIONS",
    "DECISION_APPROVED",
    "DECISION_REJECTED",
    "DECISION_AUTO_APPLIED",
    "DECISION_AUTO_FAILED",
    "load_decisions",
    "record_decision",
    "rejected_fingerprints",
    "decision_for_proposal",
]
