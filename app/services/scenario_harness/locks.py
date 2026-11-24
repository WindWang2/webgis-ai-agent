"""用户显式锁定（WP1）：任务级"这个不许动"的权威记录面。

与 user-wins 守卫的分工：守卫在 mutation 当刻从 provenance 推导
presentation ownership（显隐/透明度族）；本模块是**显式任务级 pin**：
- 用户说"这个配色定死"/"分母必须用常住人口" → ``set_lock`` 落一条
  ScenarioLock（actor=user，带 reason 与观察值 digest）；
- 候选干预与采纳在应用前过 ``find_blocking_lock``（确定性覆盖判定）；
- 释放只允许 user origin（Agent 不得解用户锁），释放保留审计
  （released_at/reason，环内不物理删除）。
"""

from __future__ import annotations

from typing import Any, List, Optional

from app.services.scenario_harness.contracts import (
    MAX_REASON,
    REGISTRY_LOCKS,
    ScenarioLock,
)
from app.services.scenario_harness.store import (
    RegistryError,
    load_registry,
    locked_registry_update,
    mint_id,
    upsert_registry_entry,
    utc_now_iso,
)


def _short_digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


async def set_lock(
    session_id: str,
    *,
    kind: str,
    target_id: str = "",
    field_paths: List[str],
    observed_value: str = "",
    reason: str = "",
    turn_id: str = "",
    store: Any = None,
) -> ScenarioLock:
    """落一条用户锁定（重复同 (kind,target,fields) 活跃锁 → 幂等返回原锁）。"""
    reason = (reason or "")[:MAX_REASON]
    field_paths = [str(f)[:64] for f in (field_paths or [])][:16]
    existing = await list_active_locks(session_id, store=store)
    for lock in existing:
        if (
            lock.kind == kind
            and (lock.target_id or "") == (target_id or "")
            and sorted(lock.field_paths) == sorted(field_paths)
        ):
            return lock
    lock = ScenarioLock(
        lock_id=mint_id("lock", session_id),
        kind=kind,  # type: ignore[arg-type]
        target_id=str(target_id or "")[:120],
        field_paths=field_paths,
        value_digest=_short_digest(observed_value) if observed_value else "",
        reason=reason,
        created_at=utc_now_iso(),
        turn_id=str(turn_id or "")[:64],
        session_id=session_id,
    )
    await upsert_registry_entry(session_id, REGISTRY_LOCKS, lock, store=store)
    return lock


async def release_lock(
    session_id: str,
    lock_id: str,
    *,
    reason: str = "",
    origin: str = "user",
    store: Any = None,
) -> ScenarioLock:
    """释放锁（origin != user 拒绝 —— Agent 不得解用户锁）。"""
    if origin != "user":
        raise RegistryError(
            "lock_release_forbidden",
            "仅用户可释放锁定（Agent 不得解除用户显式决策）",
        )

    def _mutate(lock: ScenarioLock) -> ScenarioLock:
        if not lock.active:
            return lock
        lock.released_at = utc_now_iso()
        lock.released_reason = (reason or "")[:MAX_REASON]
        return lock

    try:
        released = await locked_registry_update(
            session_id, REGISTRY_LOCKS, lock_id, _mutate, store=store
        )
    except RegistryError as exc:
        # 公共 API 干净机器码（底层码带注册表前缀 `_scenario_locks_not_found`）。
        if exc.code.endswith("_not_found"):
            raise RegistryError(
                "lock_not_found",
                f"锁 {lock_id!r} 不存在（会话 {session_id}）",
            ) from exc
        raise
    assert released is not None  # must_exist=True 保证
    return released


async def list_active_locks(session_id: str, store: Any = None) -> List[ScenarioLock]:
    """当前活跃锁（released 排除；(created_at, lock_id) 稳定序）。"""
    entries = await load_registry(session_id, REGISTRY_LOCKS, store=store)
    return sorted(
        (e for e in entries.values() if e.active),
        key=lambda e: (e.created_at, e.lock_id),
    )


async def find_blocking_lock(
    session_id: str, target_id: str, field_paths: List[str], store: Any = None
) -> Optional[ScenarioLock]:
    """返回第一条命中 (target, field) 的活跃锁（无 → None）。"""
    for lock in await list_active_locks(session_id, store=store):
        for field_path in field_paths or [""]:
            if lock.covers(target_id, field_path):
                return lock
    return None


__all__ = [
    "find_blocking_lock",
    "list_active_locks",
    "release_lock",
    "set_lock",
]
