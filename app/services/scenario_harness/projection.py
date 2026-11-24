"""情境投影（WP1）：scenario 注册表 → GISSituation additive fact + 指代解析。

红线：
- 只读：投影不写任何权威状态；
- 确定性：同注册表输入同输出（无 wall-clock / 无 LLM）；
- 有界：候选行 ≤8、锁定行 ≤8，全部是摘要（id/标题/状态/分组），
  不携带任何数据 payload；
- 显式 unknown：注册表为空/缺失 → unknown（status），不造空壳默认值。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.scenario_harness.contracts import (
    REGISTRY_BASELINES,
    REGISTRY_CANDIDATES,
    REGISTRY_LOCKS,
    ScenarioBaseline,
    ScenarioCandidate,
    ScenarioLock,
)
from app.services.scenario_harness.store import load_registry

#: 投影行上限（与 gis_situation 编译期裁剪纪律一致）。
MAX_CANDIDATE_ROWS = 8
MAX_LOCK_ROWS = 8


def _candidate_row(c: ScenarioCandidate) -> Dict[str, Any]:
    """一行候选摘要（稳定键序；只读字段）。"""
    return {
        "candidate_id": c.candidate_id,
        "title": c.title,
        "state": c.state,
        "baseline_id": c.baseline_id,
        "groups": sorted({d.group for d in c.delta}),
        "delta_count": len(c.delta),
        "applied_count": len(c.applied_seqs),
        "stale_reasons": list(c.stale_reasons[:3]),
        "created_by": c.created_by,
        "adoption_id": c.adoption_id,
    }


def _lock_row(lock: ScenarioLock) -> Dict[str, Any]:
    return {
        "lock_id": lock.lock_id,
        "kind": lock.kind,
        "target_id": lock.target_id,
        "field_paths": list(lock.field_paths[:8]),
        "reason": lock.reason,
    }


def _baseline_row(b: Optional[ScenarioBaseline]) -> Optional[Dict[str, Any]]:
    if b is None:
        return None
    return {
        "baseline_id": b.baseline_id,
        "mapspec_revision": b.mapspec_revision,
        "mapspec_fingerprint": b.mapspec_fingerprint[:16],
        "dataset_refs": [d.ref_id for d in b.datasets[:12]],
        "goal_digest": b.goal_digest[:16],
        "captured_at": b.captured_at,
    }


async def scenario_situation_value(
    session_id: str,
    store: Any = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """异步编译输入：读注册表 → (fact value | None, corrupt 披露)。"""
    if store is None:
        from app.services.session_data import session_data_manager as store
    corrupt: List[str] = []
    candidates_map = await load_registry(
        session_id, REGISTRY_CANDIDATES, corrupt=corrupt, store=store
    )
    if not candidates_map and not corrupt:
        raw = await store.get_map_state(session_id)
        # 注册表键完全缺席 → unknown（无数候选 ≠ 编译失败）
        if not isinstance(raw, dict) or REGISTRY_CANDIDATES not in raw:
            return None, corrupt
    baselines_map = await load_registry(session_id, REGISTRY_BASELINES, store=store)
    locks_map = await load_registry(session_id, REGISTRY_LOCKS, store=store)

    candidates = sorted(
        candidates_map.values(),
        key=lambda c: (c.created_at, c.candidate_id),
    )
    rows = [_candidate_row(c) for c in candidates[:MAX_CANDIDATE_ROWS]]
    if not rows:
        return None, corrupt

    baseline_digests: Dict[str, int] = {}
    for c in candidates:
        baseline_digests[c.baseline_id] = baseline_digests.get(c.baseline_id, 0) + 1
    primary_baseline = None
    for b in sorted(
        baselines_map.values(), key=lambda b: (b.captured_at, b.baseline_id)
    ):
        if b.baseline_id in baseline_digests:
            primary_baseline = b
            break

    active_locks = sorted(
        (lock for lock in locks_map.values() if lock.active),
        key=lambda lock: (lock.created_at, lock.lock_id),
    )

    value: Dict[str, Any] = {
        "total": len(candidates),
        "active": sum(
            1
            for c in candidates
            if c.state in ("draft", "running", "comparable", "stale")
        ),
        "adopted": sum(1 for c in candidates if c.state == "adopted"),
        "candidates": rows,
        "active_lock_count": len(active_locks),
    }
    pb = _baseline_row(primary_baseline)
    if pb is not None:
        value["baseline"] = pb
    if active_locks:
        value["locks"] = [_lock_row(lock) for lock in active_locks[:MAX_LOCK_ROWS]]
    if corrupt:
        value["corrupt_entries"] = corrupt[:8]
    return value, corrupt


async def scenario_situation_fact(session_id: str, store: Any = None):
    """→ SitFact（known / unknown；编译器消费）。编译失败降级 unknown。"""
    from app.services.gis_situation.facts import known, unknown

    source = "map_state._scenario_candidates"
    try:
        value, _corrupt = await scenario_situation_value(session_id, store=store)
    except Exception:  # noqa: BLE001 — 投影是增值上下文，绝不阻断编译
        return unknown(source=source)
    if value is None:
        return unknown(source=source)
    return known(value, source=source)


async def resolve_scenario_reference(
    session_id: str,
    mention: str,
    store: Any = None,
) -> Optional[str]:
    """语义指代解析（"上一个方案"/"当前候选"→ 稳定 candidate_id）。

    词表（确定性，不依赖 LLM 回忆）：
    - ``current``：最近创建且 state ∈ {draft,running,comparable,stale} 的候选；
    - ``previous``：current 之前一个活跃候选（按创建序）；
    - ``adopted``：最近采纳的候选；
    - 精确 candidate_id / title 全等匹配优先于词表。
    解析不了返回 None（调用方最小澄清，绝不随机挑）。
    """
    if store is None:
        from app.services.session_data import session_data_manager as store
    mention = str(mention or "").strip()
    if not mention:
        return None
    candidates_map = await load_registry(session_id, REGISTRY_CANDIDATES, store=store)
    ordered = sorted(
        candidates_map.values(), key=lambda c: (c.created_at, c.candidate_id)
    )
    if not ordered:
        return None

    for c in ordered:
        if c.candidate_id == mention:
            return c.candidate_id
    for c in ordered:
        if c.title and c.title == mention:
            return c.candidate_id

    active = [
        c for c in ordered if c.state in ("draft", "running", "comparable", "stale")
    ]
    adopted = [c for c in ordered if c.state == "adopted"]

    if mention in ("current", "当前", "当前方案"):
        return active[-1].candidate_id if active else None
    if mention in ("previous", "上一个", "上一个方案", "之前那个"):
        if len(active) >= 2:
            return active[-2].candidate_id
        if active:
            return active[0].candidate_id
        return None
    if mention in ("adopted", "已采纳"):
        return adopted[-1].candidate_id if adopted else None
    return None


__all__ = [
    "MAX_CANDIDATE_ROWS",
    "MAX_LOCK_ROWS",
    "resolve_scenario_reference",
    "scenario_situation_fact",
    "scenario_situation_value",
]
