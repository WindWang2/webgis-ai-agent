"""Situation delta / invalidation / 快照前进（方向 2 S3，ADR-0180）。

``diff_situation(before, after)`` 产出**分组**的稳定变更契约，供
SessionPlan / ExecutionGraph / Goal evaluator / turn 投影消费：
- 变更按 (context, fact) 坐标分组，kind ∈ added/removed/value_changed/
  status_changed；
- **revision 单调性**：after.identity.revision 字典序 < before 时
  ``regressed=True`` —— 迟到的 SSE/前端观察不能把状态倒退（DC-5），
  ``advance_snapshot`` 对 regressed 快照拒收（会话快照只前进）；
- stale 语义由编译期门（verdict 指纹门、观察 revision 盖章）产生，
  diff 只报告 status 变化，不重复实现时效策略。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.gis_situation.contract import GISSituation, SituationRevision
from app.services.gis_situation.facts import SitFact

logger = logging.getLogger(__name__)

_SNAPSHOT_KEY = "_situation_snapshot"

CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"
CHANGE_VALUE = "value_changed"
CHANGE_STATUS = "status_changed"


class FactChange:
    """一条事实级变更（坐标 + 前后摘要；有界小对象）。"""

    __slots__ = ("context", "fact", "kind", "before", "after")

    def __init__(
        self,
        context: str,
        fact: str,
        kind: str,
        before: Optional[SitFact],
        after: Optional[SitFact],
    ) -> None:
        self.context = context
        self.fact = fact
        self.kind = kind
        self.before = before
        self.after = after

    def coordinate(self) -> Tuple[str, str]:
        return (self.context, self.fact)

    def summary(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "context": self.context,
            "fact": self.fact,
            "kind": self.kind,
        }
        for side, fact in (("before", self.before), ("after", self.after)):
            if fact is not None:
                out[side] = {"status": fact.status}
        return out

    def __repr__(self) -> str:  # pragma: no cover - 调试面
        return f"FactChange({self.context}.{self.fact} {self.kind})"


class SituationDelta:
    """两次编译间的分组变更（含单调性裁决）。"""

    def __init__(
        self,
        before_revision: Optional[SituationRevision],
        after_revision: SituationRevision,
        changes: List[FactChange],
    ) -> None:
        self.before_revision = before_revision
        self.after_revision = after_revision
        self.changes = changes
        self.regressed = (
            before_revision is not None
            and not after_revision.ge(before_revision)
        )

    def changed_coordinates(self) -> Set[Tuple[str, str]]:
        """投影层/消费方的变更坐标集（O(changes)，可缓存）。"""
        return {c.coordinate() for c in self.changes if not self.regressed}

    def by_context(self) -> Dict[str, List[FactChange]]:
        """facts 分组变更（context → changes，字典序）。"""
        grouped: Dict[str, List[FactChange]] = {}
        for change in self.changes:
            grouped.setdefault(change.context, []).append(change)
        return {k: grouped[k] for k in sorted(grouped)}

    def summaries(self, limit: int = 16) -> List[Dict[str, Any]]:
        return [c.summary() for c in self.changes[:limit]]

    def __repr__(self) -> str:  # pragma: no cover - 调试面
        return (
            f"SituationDelta(changes={len(self.changes)}, "
            f"regressed={self.regressed})"
        )


def _facts_by_coordinate(situation: GISSituation) -> Dict[Tuple[str, str], SitFact]:
    return {
        (ctx, fact): value
        for ctx, fact, value in situation.iter_facts()
    }


def diff_situation(
    before: Optional[GISSituation], after: GISSituation
) -> SituationDelta:
    """比较两次编译产物。before=None（首轮/快照丢失）→ 全量 added 语义省略，
    只给空变更集（首轮没有"上一轮"可言，不制造 16 条 added 噪声）。"""
    if before is None:
        return SituationDelta(None, after.identity.revision, [])
    before_facts = _facts_by_coordinate(before)
    after_facts = _facts_by_coordinate(after)
    changes: List[FactChange] = []
    for coordinate in sorted(set(before_facts) | set(after_facts)):
        old = before_facts.get(coordinate)
        new = after_facts.get(coordinate)
        if old is None and new is not None:
            changes.append(FactChange(coordinate[0], coordinate[1], CHANGE_ADDED, old, new))
            continue
        if old is not None and new is None:
            changes.append(FactChange(coordinate[0], coordinate[1], CHANGE_REMOVED, old, new))
            continue
        if old is None or new is None:
            continue
        if old.status != new.status:
            changes.append(FactChange(coordinate[0], coordinate[1], CHANGE_STATUS, old, new))
        elif new.status == "known" and old.value != new.value:
            changes.append(FactChange(coordinate[0], coordinate[1], CHANGE_VALUE, old, new))
    return SituationDelta(before.identity.revision, after.identity.revision, changes)


async def load_snapshot(
    session_id: str, *, store: Any = None
) -> Optional[GISSituation]:
    """读取上一轮持久化快照（定向单字段读；损坏/缺失 → None）。"""
    if store is None:
        from app.services.session_data import session_data_manager as store
    try:
        raw = await store.get_state_field(session_id, _SNAPSHOT_KEY)
    except Exception:  # noqa: BLE001 — 快照是增值面，读失败按无快照
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return GISSituation.from_dict(raw)
    except Exception:  # noqa: BLE001 — 契约演进的旧快照按无快照（前向兼容）
        logger.debug("[gis_situation] snapshot unparsable for %s", session_id)
        return None


async def advance_snapshot(
    session_id: str,
    situation: GISSituation,
    *,
    store: Any = None,
) -> bool:
    """会话快照**只前进**（DC-5）：after < stored 时拒收并保留 stored。

    返回 True=快照已推进；False=迟到/倒退编译被拒收。check-then-act 全程
    持 session 锁（situation review P1-4：非原子 RMW 在双轮并发下可倒退
    一格）—— 增值面用默认降级容忍。写失败 best-effort（快照丢失的代价是
    下一轮 diff 为空，语义安全）。
    """
    if store is None:
        from app.services.session_data import session_data_manager as store
    from app.services.distributed_lock import session_lock_registry

    try:
        async with session_lock_registry.lock(session_id) as _lock:
            stored = await load_snapshot(session_id, store=store)
            if stored is not None and not situation.identity.revision.ge(
                stored.identity.revision
            ):
                return False
            if stored is not None and situation.identity.revision.as_tuple() == \
                    stored.identity.revision.as_tuple():
                # 同 revision：保留先到的（避免并发双写互相覆盖）；内容等
                # 价时幂等。
                return True
            await store.set_map_state(
                session_id, _SNAPSHOT_KEY, situation.to_dict()
            )
            return True
    except Exception as e:  # noqa: BLE001 — best-effort 持久化
        logger.warning("[gis_situation] snapshot advance failed: %s", e)
        return False
