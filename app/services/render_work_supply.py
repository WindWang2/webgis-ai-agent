"""RenderWorkEnvelope 会话供给（Goal 03，ADR-0224）。

把 :mod:`app.lib.cartography.render_work_envelope` 的纯投影接到生产
会话面：读会话 MapSpec（desired state 单一真相）+ CAS 修订 + observation
sequence → versioned 执行信封 → SSE step_result / map_state GET /
observation 对账。

纪律（与 :mod:`app.services.governor.render_projection` 同款）：

- **fail-open**：任何失败（state 异常 / spec 缺失 / 投影异常）→ None —
  信封是增值对账面，绝不让 mutation/SSE 主链路因投影失败而失败；
- **有界缓存**：按 (session, fingerprint, revision, seq) LRU ≤128 ——
  身份键变化即失效，同键即命中（幂等重放同 envelope）；
- **last-emitted 记忆**：per-session 上一份信封（invalidation hints 的
  比较基）；缓存逐出/进程重启 → hints 退化为 unknown（诚实缺席，绝不
  虚构「无变化」）；
- **零扫描**：投影只读骨架（lib 纯函数保证）；fingerprint 走既有
  ``cartographic_fingerprint`` 线程卸载路径。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from app.lib.cartography.render_work_envelope import (
    RenderWorkEnvelope,
    project_render_work_envelope,
)

logger = logging.getLogger(__name__)

#: 会话信封缓存上限（LRU；单条 = 一个有界信封对象）。
_MAX_CACHED_SESSIONS = 128

_Entry = Tuple[str, int, int, RenderWorkEnvelope]  # (fp, revision, seq, env)

_cache: "OrderedDict[str, _Entry]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(session_id: str) -> Optional[_Entry]:
    with _cache_lock:
        entry = _cache.get(session_id)
        if entry is not None:
            _cache.move_to_end(session_id)
        return entry


def _cache_put(session_id: str, entry: _Entry) -> None:
    with _cache_lock:
        _cache[session_id] = entry
        _cache.move_to_end(session_id)
        while len(_cache) > _MAX_CACHED_SESSIONS:
            _cache.popitem(last=False)


def invalidate_render_work_envelope(session_id: str) -> None:
    """会话信封缓存失效（身份键校验是主防线，本接口主动清退）。"""
    with _cache_lock:
        _cache.pop(session_id, None)


def reset_render_work_cache_for_tests() -> None:
    with _cache_lock:
        _cache.clear()


def project_envelope_from_state(
    state: Dict[str, Any],
    session_id: str,
    *,
    fingerprint: str = "",
) -> Optional[RenderWorkEnvelope]:
    """已读 state → 信封（同步纯投影；previous 取 last-emitted 记忆）。

    ``fingerprint`` 缺省时由调用方保证已算出（state 路径外复用）；
    本函数不重算 fingerprint（O(spec) 成本归 async 供给面线程卸载）。
    """
    try:
        mapspec = state.get("mapspec")
        if not isinstance(mapspec, dict):
            return None
        try:
            revision = max(0, int(state.get("_cartographic_mutation_revision") or 0))
        except (TypeError, ValueError):
            revision = 0
        observation = state.get("_cartographic_observation")
        try:
            seq = max(
                0,
                int(
                    observation.get("sequence", 0)
                    if isinstance(observation, dict) else 0
                ),
            )
        except (TypeError, ValueError):
            seq = 0
        cached = _cache_get(session_id)
        # 幂等身份命中：直接返回已缓存信封（重投影绝不把自身当 previous ——
        # 否则 hints 退化为 style_only=True 的自比较，破坏确定性契约）。
        if cached is not None and (
            cached[0], cached[1], cached[2]
        ) == (fingerprint, revision, seq):
            return cached[3]
        previous = cached[3] if cached is not None else None
        envelope = project_render_work_envelope(
            mapspec,
            session_id=session_id,
            revision=revision,
            fingerprint=fingerprint,
            observation_seq=seq,
            previous=previous,
        )
        _cache_put(
            session_id, (fingerprint, revision, seq, envelope)
        )
        return envelope
    except Exception:  # noqa: BLE001 — fail-open：信封缺席 ≠ 主链路失败
        logger.debug(
            "[render-work] envelope projection failed", exc_info=True
        )
        return None


async def get_render_work_envelope(
    session_id: str, *, force: bool = False
) -> Optional[RenderWorkEnvelope]:
    """会话当前 MapSpec → versioned 执行信封（fail-open）。"""
    sid = (session_id or "").strip()
    if not sid:
        return None
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(sid)
        if not isinstance(state, dict) or state.get("_cartographic_deleted") is True:
            return None
        mapspec = state.get("mapspec")
        if not isinstance(mapspec, dict):
            return None

        from app.lib.cartography.quality_loop import cartographic_fingerprint

        fingerprint = await asyncio.to_thread(cartographic_fingerprint, mapspec)
        try:
            revision = max(0, int(state.get("_cartographic_mutation_revision") or 0))
        except (TypeError, ValueError):
            revision = 0
        observation = state.get("_cartographic_observation")
        try:
            seq = max(
                0,
                int(
                    observation.get("sequence", 0)
                    if isinstance(observation, dict) else 0
                ),
            )
        except (TypeError, ValueError):
            seq = 0

        if not force:
            cached = _cache_get(sid)
            if cached is not None and (
                cached[0], cached[1], cached[2]
            ) == (fingerprint, revision, seq):
                return cached[3]

        return await asyncio.to_thread(
            project_envelope_from_state, state, sid, fingerprint=fingerprint
        )
    except Exception:  # noqa: BLE001 — fail-open
        logger.debug(
            "[render-work] envelope unavailable for session", exc_info=True
        )
        return None


__all__ = [
    "get_render_work_envelope",
    "project_envelope_from_state",
    "invalidate_render_work_envelope",
    "reset_render_work_cache_for_tests",
]
