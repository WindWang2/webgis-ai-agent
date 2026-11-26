"""Scenario Harness 注册表存取（父会话 map_state；持父会话分布式锁写）。

纪律（与 whatif/branch_manager 同款）：
- 注册表是父会话 map_state 的独立键，不与 ``_whatif_branches`` 混存 ——
  whatif 的 ``ScenarioBranchMeta.model_validate`` 会丢弃未知字段，两表
  分键保证各自 schema 演进互不干扰；
- 读不加锁（map_state 读是快照语义），**写一律持父会话锁**（跨进程互斥）；
- 所有条目经 pydantic ``extra="forbid"`` 校验，坏条目按缺失处理并披露，
  绝不静默吞（投影层标 ``corrupt_entries``）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, TypeVar

from pydantic import BaseModel

from app.services.distributed_lock import session_lock_registry
from app.services.scenario_harness.contracts import (
    MAX_ADOPTIONS,
    MAX_BASELINES,
    MAX_CANDIDATES,
    MAX_LOCKS,
    REGISTRY_ADOPTIONS,
    REGISTRY_BASELINES,
    REGISTRY_CANDIDATES,
    REGISTRY_LOCKS,
    AdoptionReceipt,
    ScenarioBaseline,
    ScenarioCandidate,
    ScenarioLock,
)

T = TypeVar("T", bound=BaseModel)

#: 各注册表容量（键 → 上限）；超限拒绝写入（调用方转机器可读错误）。
REGISTRY_LIMITS = {
    REGISTRY_CANDIDATES: MAX_CANDIDATES,
    REGISTRY_BASELINES: MAX_BASELINES,
    REGISTRY_LOCKS: MAX_LOCKS,
    REGISTRY_ADOPTIONS: MAX_ADOPTIONS,
}

_MODEL_OF: Dict[str, type] = {
    REGISTRY_CANDIDATES: ScenarioCandidate,
    REGISTRY_BASELINES: ScenarioBaseline,
    REGISTRY_LOCKS: ScenarioLock,
    REGISTRY_ADOPTIONS: AdoptionReceipt,
}

_KEY_FIELD = {
    REGISTRY_CANDIDATES: "candidate_id",
    REGISTRY_BASELINES: "baseline_id",
    REGISTRY_LOCKS: "lock_id",
    REGISTRY_ADOPTIONS: "adoption_id",
}


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def mint_id(prefix: str, session_id: str) -> str:
    """确定性可读 id：``{prefix}-{short}``（短随机段保证并发不撞）。"""
    import secrets

    return f"{prefix}-{session_id[:8]}-{secrets.token_hex(3)}"


class RegistryError(Exception):
    """机器可读错误（code + 中文消息；绝不静默）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


async def _get_map_state(session_id: str, store: Any = None) -> Dict[str, Any]:
    if store is None:
        from app.services.session_data import session_data_manager as store

    state = await store.get_map_state(session_id)
    return state if isinstance(state, dict) else {}


async def _set_map_state_key(
    session_id: str, key: str, value: Any, store: Any = None
) -> None:
    if store is None:
        from app.services.session_data import session_data_manager as store

    await store.set_map_state(session_id, key, value)


async def load_registry(
    session_id: str,
    registry_key: str,
    *,
    corrupt: Optional[List[str]] = None,
    store: Any = None,
) -> Dict[str, T]:
    """读注册表 → {key_field: model}。坏条目跳过并在 ``corrupt`` 披露。"""
    model = _MODEL_OF[registry_key]
    key_field = _KEY_FIELD[registry_key]
    raw = (await _get_map_state(session_id, store)).get(registry_key)
    out: Dict[str, T] = {}
    if not isinstance(raw, dict):
        return out
    for entry_id, entry in raw.items():
        if not isinstance(entry, dict):
            if corrupt is not None:
                corrupt.append(f"{registry_key}.{entry_id}:not_dict")
            continue
        try:
            parsed = model.model_validate(entry)
        except Exception:
            if corrupt is not None:
                corrupt.append(f"{registry_key}.{entry_id}:schema")
            continue
        out[str(entry.get(key_field) or entry_id)] = parsed
    return out


async def _dump_registry(
    session_id: str, registry_key: str, entries: Dict[str, T], store: Any = None
) -> None:
    raw = {eid: m.model_dump(mode="json") for eid, m in entries.items()}
    await _set_map_state_key(session_id, registry_key, raw, store)


async def upsert_registry_entry(
    session_id: str, registry_key: str, entry: T, *, store: Any = None
) -> None:
    """持锁写单条目（容量检查；超限 RegistryError）。"""
    key_field = _KEY_FIELD[registry_key]
    entry_id = str(getattr(entry, key_field))
    async with session_lock_registry.lock(session_id):
        entries = await load_registry(session_id, registry_key, store=store)
        if entry_id not in entries and len(entries) >= REGISTRY_LIMITS[registry_key]:
            raise RegistryError(
                f"{registry_key}_limit",
                f"{registry_key} 已达上限 {REGISTRY_LIMITS[registry_key]}（先归档/释放闲置条目）",
            )
        entries[entry_id] = entry
        await _dump_registry(session_id, registry_key, entries, store)


async def remove_registry_entry(
    session_id: str, registry_key: str, entry_id: str, *, store: Any = None
) -> bool:
    async with session_lock_registry.lock(session_id):
        entries = await load_registry(session_id, registry_key, store=store)
        if entry_id not in entries:
            return False
        entries.pop(entry_id, None)
        await _dump_registry(session_id, registry_key, entries, store)
        return True


async def get_registry_entry(
    session_id: str, registry_key: str, entry_id: str, *, store: Any = None
) -> Optional[T]:
    entries = await load_registry(session_id, registry_key, store=store)
    return entries.get(entry_id)


async def locked_registry_update(
    session_id: str,
    registry_key: str,
    entry_id: str,
    mutate,
    *,
    must_exist: bool = True,
    store: Any = None,
) -> Optional[T]:
    """持锁读-改-写单条目（``mutate(entry) -> entry`` 原地修改返回）。

    must_exist=False 时条目不存在则返回 None（幂等删除语义用）。
    """
    key_field = _KEY_FIELD[registry_key]
    async with session_lock_registry.lock(session_id):
        entries = await load_registry(session_id, registry_key, store=store)
        entry = entries.get(entry_id)
        if entry is None:
            if must_exist:
                raise RegistryError(
                    f"{registry_key}_not_found",
                    f"{registry_key} {entry_id!r} 不存在（会话 {session_id}）",
                )
            return None
        mutated = mutate(entry)
        entries[mutated.model_dump(mode="json")[key_field]] = mutated
        await _dump_registry(session_id, registry_key, entries, store)
        return mutated


__all__ = [
    "REGISTRY_LIMITS",
    "RegistryError",
    "get_registry_entry",
    "load_registry",
    "locked_registry_update",
    "mint_id",
    "remove_registry_entry",
    "upsert_registry_entry",
    "utc_now_iso",
]
