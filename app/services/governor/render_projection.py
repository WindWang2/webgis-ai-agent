"""RenderWorkProjection 会话供给（F13，ADR-0214 D2）。

把 :mod:`app.lib.cartography.render_work_projection` 的纯投影接到生产
会话面上：读会话 MapSpec（desired state 单一真相）+ 当前 CAS 修订
（``_cartographic_mutation_revision``）→ versioned 投影 → governor
render 细化通道（``estimate_for_tool(render_input=...)``）。

#1484 的缺口在此闭合：``RenderWorkInput`` 此前只有估工模型与 seam，
没有任何生产代码构造它 —— 派发面 ``GovernorDispatchAdapter`` 现在按
#1408 df_cost 通道同款模式供给 render 通道。

纪律：

- **fail-open**：本供给的任何失败（session store 异常 / spec 缺失 /
  投影异常）都返回 ``None`` —— 估算细化是增值面，绝不让准入链路
  因投影而失败（与 dispatch_adapter 的双保险 fail-open 同纪律）；
- **有界缓存**：按 (session, fingerprint, revision) 缓存投影结果，
  ≤128 会话（LRU）；无 TTL —— 身份键变化即失效，同键即命中；
- **零阻塞**：map_state 读取走 session_data_manager 的既有线程卸载
  路径；本模块自身只做 O(spec 骨架) 的纯函数投影。

边界：本模块不做准入裁决、不改 MapSpec、不懂工具语义 —— 选择哪个
工具喂 render_input 是 dispatch_adapter 的职责。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Optional, Tuple

from app.lib.cartography.render_work_projection import (
    RenderWorkProjection,
    project_render_work,
)
from app.services.governor.render_budget import RenderWorkInput

logger = logging.getLogger(__name__)

#: 会话投影缓存上限（LRU；单条 = 一个有界投影对象）。
_MAX_CACHED_SESSIONS = 128

#: 缓存条目（投影 + 身份键）。
_Entry = Tuple[str, int, RenderWorkProjection]  # (fingerprint, revision, proj)

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


def invalidate_render_projection(session_id: str) -> None:
    """会话投影缓存失效（mutation 落地后由调用方可选触发；身份键校验
    是主防线，本接口只是主动清退）。"""
    with _cache_lock:
        _cache.pop(session_id, None)


def reset_render_projection_cache_for_tests() -> None:
    with _cache_lock:
        _cache.clear()


async def get_render_work_projection(
    session_id: str, *, force: bool = False
) -> Optional[RenderWorkProjection]:
    """会话当前 MapSpec → versioned RenderWorkProjection（fail-open）。

    单次 ``get_map_state`` 同时取 spec 与 CAS 修订（与 engine 的
    state_hint 同款防双读）；state 无 spec 时回退
    ``mapspec_store_instance.get_mapspec``（磁盘兜底 + revision 复活
    语义保持单一实现）。
    """
    sid = (session_id or "").strip()
    if not sid:
        return None
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(sid)
        if not isinstance(state, dict) or state.get("_cartographic_deleted") is True:
            return None
        mapspec = state.get("mapspec")
        revision_raw = state.get("_cartographic_mutation_revision")
        try:
            revision = max(0, int(revision_raw or 0))
        except (TypeError, ValueError):
            revision = 0
        if not isinstance(mapspec, dict):
            # 磁盘兜底/复活路径（store 单一实现，避免第二套恢复语义）。
            from app.services.mapspec.store import mapspec_store_instance

            mapspec = await mapspec_store_instance.get_mapspec(sid)
            if not isinstance(mapspec, dict):
                return None

        from app.lib.cartography.quality_loop import cartographic_fingerprint

        fingerprint = await asyncio.to_thread(cartographic_fingerprint, mapspec)

        if not force:
            cached = _cache_get(sid)
            if cached is not None and cached[0] == fingerprint and cached[1] == revision:
                return cached[2]

        projection = await asyncio.to_thread(
            project_render_work, mapspec, revision=revision, fingerprint=fingerprint
        )
        _cache_put(sid, (fingerprint, revision, projection))
        return projection
    except Exception:  # noqa: BLE001 — 供给 fail-open：细化缺席 ≠ 派发失败
        logger.debug(
            "[render-projection] projection unavailable for session",
            exc_info=True,
        )
        return None


async def render_input_for_session(session_id: str) -> Optional[RenderWorkInput]:
    """供给面的窄投影：只要 RenderWorkInput（dispatch_adapter 消费形）。"""
    projection = await get_render_work_projection(session_id)
    if projection is None:
        return None
    return projection.work_input


__all__ = [
    "get_render_work_projection",
    "render_input_for_session",
    "invalidate_render_projection",
    "reset_render_projection_cache_for_tests",
]
