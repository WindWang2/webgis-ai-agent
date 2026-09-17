"""锚点活性评估（ADR-0203）：stale 只在**可证失效**时判 stale。

诚实语义（DECISIONS #6）：
- layer/component 锚 → 当前 spec 可证缺失 = stale；
- feature 锚 → 父层缺失 = stale；数据内联且 feature id 不在 = stale；
  数据是 ref（未内联/需拉取）= unverified；
- claim 锚 → ClaimStore 可达且（库非空而该 id 缺失）= stale；store 不可达
  或空库 = unverified；
- artifact 锚 → v1 不接线注册表 = unverified（记录为边界，不做活性断言）。

纯读函数，绝不写状态；claim store 查找经 ``_lookup_claim_store`` seam
（生产 = session_ctx.get_or_create_claim_store；测试可 monkeypatch）。
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from app.schemas.review_schema import Anchor, AnchorKind

logger = logging.getLogger(__name__)


class AnchorState(str, Enum):
    OK = "ok"
    STALE = "stale"
    UNVERIFIED = "unverified"


def _lookup_claim_store(session_id: str):
    """claim store 查找 seam；返回 None = 不可达（不创建、不抛出）。"""
    try:
        from app.services.gis_harness.hotpath_convergence.session_ctx import (
            get_or_create_claim_store,
        )

        return get_or_create_claim_store(session_id)
    except Exception:  # noqa: BLE001 — 锚点活性是附加事实，不倒灌主流程
        logger.debug("[review-anchors] claim store lookup failed", exc_info=True)
        return None


def _layer_ids(spec: Optional[Dict[str, Any]]) -> set:
    if not isinstance(spec, dict):
        return set()
    return {
        str(ly.get("id"))
        for ly in (spec.get("layers") or [])
        if isinstance(ly, dict) and ly.get("id")
    }


def _component_ids(spec: Optional[Dict[str, Any]]) -> set:
    if not isinstance(spec, dict):
        return set()
    layout = spec.get("layout") if isinstance(spec.get("layout"), dict) else {}
    return {
        str(c.get("id"))
        for c in (layout.get("components") or [])
        if isinstance(c, dict) and c.get("id")
    }


def _feature_ids_for_layer(spec: Optional[Dict[str, Any]], layer_id: str):
    """返回 (状态, feature_id 集合或 None)。None = 数据不可内联解析。"""
    if not isinstance(spec, dict):
        return None
    layer = next(
        (
            ly for ly in (spec.get("layers") or [])
            if isinstance(ly, dict) and str(ly.get("id") or "") == layer_id
        ),
        None,
    )
    if layer is None:
        return set()  # 父层缺失 → 调用方判 stale
    source_ref = layer.get("source")
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    source = sources.get(str(source_ref)) if source_ref is not None else None
    if not isinstance(source, dict):
        return None  # ref 型数据源 → unverified
    data = source.get("data") if isinstance(source.get("data"), dict) else None
    if data is None or data.get("type") != "FeatureCollection":
        return None
    ids = set()
    for feat in data.get("features") or []:
        if isinstance(feat, dict) and feat.get("id") is not None:
            ids.add(str(feat["id"]))
    return ids


async def evaluate_anchor(
    session_id: str,
    anchor: Anchor,
    mapspec: Optional[Dict[str, Any]],
) -> AnchorState:
    if anchor.kind in (AnchorKind.LAYER, AnchorKind.FEATURE):
        if anchor.kind is AnchorKind.FEATURE:
            layer_id = anchor.layer_id or ""
            if layer_id and layer_id not in _layer_ids(mapspec):
                return AnchorState.STALE
            result = _feature_ids_for_layer(mapspec, layer_id)
            if result is None:
                return AnchorState.UNVERIFIED
            return AnchorState.OK if anchor.id in result else AnchorState.STALE
        return (
            AnchorState.OK if anchor.id in _layer_ids(mapspec) else AnchorState.STALE
        )
    if anchor.kind is AnchorKind.COMPONENT:
        return (
            AnchorState.OK
            if anchor.id in _component_ids(mapspec)
            else AnchorState.STALE
        )
    if anchor.kind is AnchorKind.CLAIM:
        store = _lookup_claim_store(session_id)
        if store is None:
            return AnchorState.UNVERIFIED
        try:
            found = store.get_claim(anchor.id) is not None
            if found:
                return AnchorState.OK
            # 空库（可能未加载）→ 不妄断 stale。
            has_any = bool(store.all_claims())
        except Exception:  # noqa: BLE001 — 附加事实通道
            return AnchorState.UNVERIFIED
        return AnchorState.STALE if has_any else AnchorState.UNVERIFIED
    # artifact：v1 不接线注册表（边界，见 GAP_ANALYSIS）。
    return AnchorState.UNVERIFIED


async def evaluate_anchors(
    session_id: str,
    anchors: List[Anchor],
    mapspec: Optional[Dict[str, Any]],
) -> List[AnchorState]:
    return [await evaluate_anchor(session_id, a, mapspec) for a in anchors]


async def proposal_anchor_states(
    session_id: str,
    proposal,
    mapspec: Optional[Dict[str, Any]],
) -> Dict[str, AnchorState]:
    """proposal 全部锚定评论的锚态投影（comment_id → state；无锚评论跳过）。"""
    anchored = [c for c in proposal.comments if c.anchor is not None]
    if not anchored:
        return {}
    states = await evaluate_anchors(
        session_id, [c.anchor for c in anchored], mapspec,
    )
    return {c.comment_id: s for c, s in zip(anchored, states)}
