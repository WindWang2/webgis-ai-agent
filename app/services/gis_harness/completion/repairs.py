"""Repair（确定性 desired-state 修复；有界轮数内执行）— ADR-0081 / ADR-0091。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .contracts import (
    F_COMPONENT_DISABLED,
    F_COMPONENT_MISSING,
    F_LAYER_HIDDEN,
    R_ADD_COMPONENT,
    R_ENABLE_COMPONENT,
    R_SHOW_LAYER,
    MapCompletionFinding,
    _COMPONENT_DEFAULT_IDS,
)

logger = logging.getLogger(__name__)


async def apply_repairs(
    session_id: str,
    findings: List[MapCompletionFinding],
    mapspec: Dict[str, Any],
    prior_repairs: Optional[List[str]] = None,
) -> List[str]:
    """执行可自动修复的发现；返回实际应用的 repair action codes。

    只做三类低风险修复（都经既有突变通道，复用 owner/CAS 守卫）：
    - add_component：required 组件缺失 → 工厂默认值 upsert；
    - enable_component：required 组件被禁用 → 重新启用；
    - show_layer：结果层 desired-visibility=none → GISMutationBatch（用户
      显式隐藏会被既有 user-wins 守卫拒绝并如实保留）。

    ``prior_repairs``（上一持久化块里已应用过的修复）提供组件修复的
    one-shot 语义：用户在 finalizer 启用后再次禁用时不形成修复对抗，
    转为 needs_repair 披露（组件通道没有 layer 那样的 owner 守卫）。
    """
    prior = list(prior_repairs or [])
    applied: List[str] = []
    from app.services.mapspec_store import mapspec_store

    components = [
        c
        for c in ((mapspec.get("layout") or {}).get("components") or [])
        if isinstance(c, dict)
    ]
    for f in findings:
        family = f.family or [f.target]
        if f.repair == R_ADD_COMPONENT and f.code == F_COMPONENT_MISSING:
            # one-shot（review P1）：上一轮已尝试过同族修复而 finding 仍在
            # （典型：用户在 finalizer 启用后再次禁用）→ 不再对抗，披露
            # needs_repair —— 用户显式决策优先。
            if any(p.startswith(f"{R_ADD_COMPONENT}:{t}") for t in family for p in prior):
                continue
            repair_type = family[0]
            default_id = _COMPONENT_DEFAULT_IDS.get(repair_type, f"{repair_type}-main")
            try:
                res = await mapspec_store.patch_component(
                    session_id,
                    component_id=default_id,
                    component_type=repair_type,
                    enabled=True,
                    upsert=True,
                )
                if res.get("success"):
                    applied.append(f"{R_ADD_COMPONENT}:{repair_type}")
            except Exception:  # noqa: BLE001 — 单项修复失败留给下一轮披露
                logger.warning(
                    "[MapFinalizer] add_component repair failed type=%s", repair_type
                )
        elif f.repair == R_ENABLE_COMPONENT and f.code == F_COMPONENT_DISABLED:
            if any(p.startswith(f"{R_ENABLE_COMPONENT}:{t}") for t in family for p in prior):
                continue
            # family-aware（review P2）：禁用的成员可能是族内非 primary 类型
            # （如 categorical_legend），只按 primary 找会命中不存在的 id。
            member = next(
                (
                    c
                    for c in components
                    if c.get("type") in family and c.get("enabled") is False
                ),
                None,
            )
            if member is not None:
                target_id = str(member.get("id") or "")
                target_type = str(member.get("type") or f.target)
            else:
                target_type = family[0]
                target_id = _COMPONENT_DEFAULT_IDS.get(target_type, target_type)
            try:
                res = await mapspec_store.patch_component(
                    session_id,
                    component_id=target_id,
                    component_type=target_type,
                    enabled=True,
                )
                if res.get("success"):
                    applied.append(f"{R_ENABLE_COMPONENT}:{target_type}")
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[MapFinalizer] enable_component repair failed type=%s", target_type
                )
        elif f.repair == R_SHOW_LAYER and f.code == F_LAYER_HIDDEN:
            try:
                from app.services.gis_world_state.mutation import (
                    apply_gis_mutation_batch,
                )
                from app.services.mapspec.lifecycle_engine import (
                    PatchLayerPresentationIntent,
                )

                batch = await apply_gis_mutation_batch(
                    session_id,
                    [PatchLayerPresentationIntent(layer_id=f.target, visible=True)],
                    origin="agent",
                    actor="map_finalizer",
                )
                if batch.committed and any(
                    o.status == "applied" for o in batch.outcomes
                ):
                    applied.append(f"{R_SHOW_LAYER}:{f.target}")
            except Exception:  # noqa: BLE001 — user-wins 拒绝/事务失败留给下一轮披露
                logger.warning(
                    "[MapFinalizer] show_layer repair failed layer=%s", f.target
                )
    return applied


# 旧名（原 map_completion 单体模块的私有入口 ``_apply_repairs``）——
# pipeline 与历史调用方仍按此名引用；与 ``apply_repairs`` 是同一函数对象。
_apply_repairs = apply_repairs
