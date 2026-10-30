"""Map Operation 目标解析器 —— 情境指代 → 具体 layer/component id（M3）。

四组真实多轮指代的支持面（输入全部来自权威只读投影，无第二状态）：

- ``layer_ref``：复用 ``layer_manager.resolve_layer_ref`` 的唯一解析器
  （alias → id → 名称子串 + /review P1-6 会话归属存在性门），叠加
  MapSpec 承载层的精确匹配兜底（spec 层未回显 active 时仍可命中）。
- ``focused_layer``：IR 投影 selection（最近一次要素选中的 layer_id），
  回退 recent_interactions 里最近的 layer_* 事件。
- ``last_result``：provenance 环尾最近一次触及图层的 agent mutation
  （Upsert/Patch 面）的目标层 —— 「上一个结果」的操作事实锚点。
- ``component_id``：layout.components 精确匹配。

歧义纪律：多候选且无情境锚点时**不写任何状态**，产出有界候选列表
（id/别名/名称/来源），交由上层转成 clarification。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.map_operations.contracts import (
    MAX_CLARIFICATION_CANDIDATES,
    RC_CLARIFICATION_NEEDED,
    RC_COMPONENT_NOT_FOUND,
    RC_LAYER_NOT_FOUND,
    KIND_SET_COMPONENT,
    KIND_SET_VIEWPORT,
    TARGET_KIND_COMPONENT_ID,
    TARGET_KIND_FOCUSED_LAYER,
    TARGET_KIND_LAST_RESULT,
    MapOperationEffect,
)

logger = logging.getLogger(__name__)


class ResolvedTarget:
    """单效果的目标解析结果（服务内部值对象）。"""

    __slots__ = ("targets", "reason_code", "candidates", "note")

    def __init__(
        self,
        targets: Optional[List[str]],
        reason_code: str = "",
        candidates: Optional[List[Dict[str, Any]]] = None,
        note: str = "",
    ):
        self.targets = targets or []
        self.reason_code = reason_code
        self.candidates = candidates or []
        self.note = note

    @property
    def ok(self) -> bool:
        return bool(self.targets)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"targets": self.targets}
        if self.reason_code:
            out["reason_code"] = self.reason_code
        if self.candidates:
            out["candidates"] = self.candidates[:MAX_CLARIFICATION_CANDIDATES]
        if self.note:
            out["note"] = self.note[:160]
        return out


def _spec_layers(spec: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(spec, dict):
        return []
    return [lyr for lyr in (spec.get("layers") or []) if isinstance(lyr, dict)]


def _spec_components(spec: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(spec, dict):
        return []
    layout = spec.get("layout")
    if not isinstance(layout, dict):
        return []
    return [c for c in (layout.get("components") or []) if isinstance(c, dict)]


async def _focused_layer_id(session_id: str) -> Optional[str]:
    """IR 投影推导焦点层：selection 优先，回退最近 layer_* 交互。"""
    try:
        from app.services.session_data import session_data_manager
        from app.services.gis_harness.interaction_ir.projection import (
            interaction_snapshot_via_store,
        )

        snap = await interaction_snapshot_via_store(session_data_manager, session_id)
    except Exception:  # noqa: BLE001 — 只读情境面 fail-open 为 unknown
        return None
    selection = snap.get("selection") or {}
    if isinstance(selection, dict):
        layer_id = selection.get("layer_id")
        if isinstance(layer_id, str) and layer_id:
            return layer_id
    for rec in reversed(snap.get("recent_interactions") or []):
        if not isinstance(rec, dict):
            continue
        layer_id = rec.get("layer_id")
        if isinstance(layer_id, str) and layer_id:
            return layer_id
    return None


async def _last_result_layer_id(session_id: str) -> Optional[str]:
    """「上一个结果」层的操作事实锚点（两个只读来源，前者优先）。

    1. 本服务回执环：最近一次 committed 操作里最后 applied 的图层目标
       （多轮地图操作的天然「上一结果」）。
    2. provenance 环尾：最近一次 agent Patch 面 mutation 的 target
       （UpsertLayer 的 provenance target=None —— 由来源 1 覆盖）。
    """
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id) or {}
        from app.services.gis_harness.map_operations.contracts import RECEIPTS_KEY

        ring = state.get(RECEIPTS_KEY)
        if isinstance(ring, list):
            for receipt in ring:
                if not isinstance(receipt, dict) or receipt.get("status") not in (
                    "committed", "partial",
                ):
                    continue
                for eff in reversed(receipt.get("effects") or []):
                    if not isinstance(eff, dict) or eff.get("state") != "applied":
                        continue
                    targets = eff.get("targets")
                    if isinstance(targets, list) and targets:
                        first = targets[0]
                        if isinstance(first, str) and first:
                            return first
    except Exception:  # noqa: BLE001 — 只读情境面 fail-open
        pass
    try:
        from app.services.gis_world_state.provenance import get_provenance

        entries = await get_provenance(session_id) or []
    except Exception:  # noqa: BLE001 — 只读情境面 fail-open
        return None
    for entry in reversed(entries[-64:]):
        if not isinstance(entry, dict):
            continue
        if entry.get("origin") != "agent":
            continue
        target = entry.get("target")
        if isinstance(target, str) and target:
            return target
    return None


async def _resolve_layer_ref(
    session_id: str, ref: str, spec: Optional[Dict[str, Any]]
) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]], str]:
    """返回 (resolved_id, 歧义候选, 错误说明)。

    歧义纪律（M3）：**多候选时不擅自选第一个** —— MapSpec 承载层的
    名称精确/子串匹配先行，子串多命中 → 有界候选（clarification）；
    单命中或精确 id → 直接解析。spec 无命中再走唯一解析器
    resolve_layer_ref（alias/active-layers/归属门 —— 其名称子串是
    首匹配语义，适合细粒度工具，不满足本操作的歧义披露要求，故仅
    作兜底且只在 spec 无歧义信号时使用）。
    """
    layers = _spec_layers(spec)
    # 1. 精确 id
    for lyr in layers:
        if str(lyr.get("id") or "") == ref:
            return str(lyr.get("id")), None, ""
    # 2. 名称精确
    exact = [
        lyr for lyr in layers
        if str(lyr.get("name") or "") and str(lyr.get("name")) == ref
    ]
    if len(exact) == 1:
        return str(exact[0].get("id")), None, ""
    # 3. 名称子串：多命中 → 歧义候选（不擅自选第一个）
    partial = [
        lyr for lyr in layers
        if ref and ref in (str(lyr.get("name") or "") or "")
    ]
    if len(partial) == 1:
        return str(partial[0].get("id")), None, ""
    if len(partial) > 1:
        cands = [
            {
                "layer_id": str(lyr.get("id")),
                "name": str(lyr.get("name") or ""),
                "source": "mapspec_name_substring",
            }
            for lyr in partial[:MAX_CLARIFICATION_CANDIDATES]
        ]
        return None, cands, f"名称 {ref!r} 命中 {len(partial)} 个图层"

    # 4. 兜底：唯一解析器（alias → active-layers → 归属门）
    try:
        from app.tools.layer_manager import resolve_layer_ref

        resolved, err = await resolve_layer_ref(session_id, ref)
        if resolved:
            return resolved, None, ""
    except Exception:  # noqa: BLE001 — 解析器缺席时如实未找到
        logger.debug("[map_operations] resolve_layer_ref unavailable", exc_info=True)
    return None, None, f"目标 {ref!r} 未在当前会话图层中找到"


async def resolve_effect_target(
    session_id: str,
    effect: MapOperationEffect,
    spec: Optional[Dict[str, Any]],
) -> ResolvedTarget:
    """解析单个效果的目标（不动任何状态；歧义 → 候选）。"""
    # 视口/无目标效果
    if effect.kind == KIND_SET_VIEWPORT:
        return ResolvedTarget(targets=["viewport"])

    if effect.kind == KIND_SET_COMPONENT:
        comp_id = (effect.target or "").strip()
        if not comp_id:
            return ResolvedTarget(None, RC_COMPONENT_NOT_FOUND, note="component_id 缺失")
        comps = _spec_components(spec)
        for comp in comps:
            if str(comp.get("id") or "") == comp_id:
                return ResolvedTarget([comp_id])
        return ResolvedTarget(
            None, RC_COMPONENT_NOT_FOUND,
            note=f"组件 {comp_id!r} 不在 layout.components",
        )

    # 图层类效果
    if effect.target_kind == TARGET_KIND_COMPONENT_ID:
        # 层效果误用 component 指代 —— 语义不匹配如实拒绝
        return ResolvedTarget(
            None, RC_LAYER_NOT_FOUND, note="图层效果不支持 component_id 指代",
        )

    if effect.target_kind == TARGET_KIND_FOCUSED_LAYER:
        layer_id = await _focused_layer_id(session_id)
        if layer_id:
            layers = _spec_layers(spec)
            if any(str(lyr.get("id")) == layer_id for lyr in layers):
                return ResolvedTarget([layer_id])
            return ResolvedTarget(
                None, RC_LAYER_NOT_FOUND,
                note=f"焦点层 {layer_id!r} 已不在当前 MapSpec",
            )
        return ResolvedTarget(
            None, RC_CLARIFICATION_NEEDED,
            note="当前无焦点层（没有选中/最近图层交互）",
        )

    if effect.target_kind == TARGET_KIND_LAST_RESULT:
        layer_id = await _last_result_layer_id(session_id)
        if layer_id:
            layers = _spec_layers(spec)
            if any(str(lyr.get("id")) == layer_id for lyr in layers):
                return ResolvedTarget([layer_id])
            return ResolvedTarget(
                None, RC_LAYER_NOT_FOUND,
                note=f"上一结果层 {layer_id!r} 已不在当前 MapSpec",
            )
        return ResolvedTarget(
            None, RC_CLARIFICATION_NEEDED, note="会话尚无 agent 图层操作记录",
        )

    # layer_ref（默认路径）
    ref = (effect.target or "").strip()
    if not ref:
        return ResolvedTarget(None, RC_LAYER_NOT_FOUND, note="target 缺失")
    resolved, cands, err = await _resolve_layer_ref(session_id, ref, spec)
    if resolved:
        return ResolvedTarget([resolved])
    if cands:
        return ResolvedTarget(None, RC_CLARIFICATION_NEEDED, candidates=cands, note=err)
    return ResolvedTarget(None, RC_LAYER_NOT_FOUND, note=err)


def layer_layer_ids_for_order(spec: Optional[Dict[str, Any]]) -> List[str]:
    """当前 spec 自定义层序（move_layer 计算新序的基线）。"""
    return [str(lyr.get("id")) for lyr in _spec_layers(spec) if lyr.get("id")]


__all__ = [
    "ResolvedTarget",
    "resolve_effect_target",
    "layer_layer_ids_for_order",
]
