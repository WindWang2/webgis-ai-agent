"""GISWorldState 统一读模型（C2）——单一有界快照。

把分散在 mapspec（desired）/ map_state（runtime 注册表 + revision + 观察）/
cartographic review / provenance 的状态投影成一个快照，供：

- Agent 感知（webgis_world_state 工具 / 上下文组装）；
- 观察端点与 QA（desired vs observed vs 用户决策三方对账）；
- 前端 restore 调试。

不变量：
- **绝无 payload**：图层/源只携带 ref、类型、计数、presentation 摘要——
  Zero Big Data in Context 对读模型同样成立；
- **有界**：layers/sources/components 摘要各带上限（默认 100/100/64），
  provenance 尾部 16 条；
- 只读：本函数不写任何状态。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.services.session_data import session_data_manager
from app.services.mapspec.store import mapspec_store_instance
from app.services.gis_world_state import provenance as _provenance
from app.services.gis_world_state.provenance import get_provenance  # noqa: F401 - re-export

logger = logging.getLogger(__name__)

MAX_LAYER_SUMMARIES = 100
MAX_SOURCE_SUMMARIES = 100
MAX_COMPONENT_SUMMARIES = 64
MAX_PROVENANCE_SNAPSHOT = 16


def _layer_summary(layer: Dict[str, Any]) -> Dict[str, Any]:
    layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
    paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
    summary: Dict[str, Any] = {
        "id": layer.get("id"),
        "type": layer.get("type"),
        "source": layer.get("source"),
        "visible": layout.get("visibility", "visible") != "none",
        "opacity": paint.get("opacity"),
        "role": layer.get("context_role") or layer.get("role"),
    }
    intent = layer.get("cartographic_intent")
    if isinstance(intent, dict):
        summary["cartographic_intent"] = {
            k: intent.get(k) for k in ("expected_visible", "role") if k in intent
        }
    legend = layer.get("legend_spec")
    if isinstance(legend, dict):
        summary["legend"] = {
            "kind": legend.get("kind"),
            "field": legend.get("field") or legend.get("property"),
        }
    return {k: v for k, v in summary.items() if v is not None}


def _source_summary(source_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    profile = entry.get("profile") if isinstance(entry.get("profile"), dict) else {}
    summary: Dict[str, Any] = {"id": source_id, "type": entry.get("type")}
    for key in ("ref", "ref_id", "url", "imageRef", "dataPath"):
        value = entry.get(key)
        if isinstance(value, str):
            summary[key] = value
            break
    if profile:
        # #1067(E-6): 全部写入方（spatial_meta_profiler / mapspec_store /
        # process_layer_ingestion）都写 camelCase —— 此前读 snake_case 键，
        # sources[*] 的要素计数/几何类型从未出现（工具描述承诺的通道恒死）。
        summary["feature_count"] = (
            profile.get("featureCount")
            if profile.get("featureCount") is not None
            else profile.get("feature_count")
        )
        _geoms = (
            profile.get("geometryTypes")
            if profile.get("geometryTypes") is not None
            else profile.get("geometry_types")
        )
        summary["geometry_types"] = _geoms
    return {k: v for k, v in summary.items() if v is not None}


def _component_summary(component: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "id": component.get("id"),
        "type": component.get("type"),
        "enabled": component.get("enabled", True),
        "variant": component.get("variant"),
    }
    placement = component.get("placement")
    if isinstance(placement, dict):
        summary["placement"] = {
            k: placement.get(k) for k in ("mode", "anchor", "x", "y") if k in placement
        }
    return {k: v for k, v in summary.items() if v is not None}


async def build_world_state(session_id: str) -> Dict[str, Any]:
    """构建 session 的 GISWorldState 快照（只读、有界、无 payload）。

    #1068(E-5): 单次全量 get_map_state 派生 mapspec/provenance/user_hidden/
    observation（此前 3-4 次全量物化）；mapspec 缺失时回退 store 读取（磁盘
    兜底语义不变）。
    """
    map_state: Dict[str, Any] = {}
    try:
        map_state = await session_data_manager.get_map_state(session_id) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[gis_world_state] map_state unavailable: %s", e)
    mapspec = map_state.get("mapspec")
    if not isinstance(mapspec, dict) or not mapspec:
        mapspec = await mapspec_store_instance.get_mapspec(session_id) or {}

    layers = [
        _layer_summary(layer)
        for layer in (mapspec.get("layers") or [])
        if isinstance(layer, dict)
    ][:MAX_LAYER_SUMMARIES]
    sources = [
        _source_summary(source_id, entry)
        for source_id, entry in (mapspec.get("sources") or {}).items()
        if isinstance(entry, dict)
    ][:MAX_SOURCE_SUMMARIES]
    layout = mapspec.get("layout") if isinstance(mapspec.get("layout"), dict) else {}
    components = [
        _component_summary(component)
        for component in (layout.get("components") or [])
        if isinstance(component, dict)
    ][:MAX_COMPONENT_SUMMARIES]

    # #1068(E-5): 全环 provenance 从同一份 map_state 派生 —— 此前
    # get_provenance 被调用两次（尾部切片 + 全环），每次各触发一次全量
    # get_map_state（mapspec 级状态的完整物化），一次快照 3-4 次全量读。
    state_entries = map_state.get(_provenance._PROVENANCE_KEY)
    all_provenance = (
        list(state_entries) if isinstance(state_entries, list) else []
    )
    provenance = all_provenance[-MAX_PROVENANCE_SNAPSHOT:]
    # user_hidden 以全环为依据（ADR-0072 修复 P2-1：finalize 一次写 30+ 条
    # agent provenance 会把用户决策挤出尾部 16 条，快照必须从全环派生，否则
    # 守卫(全环64条)与感知(尾16)互相矛盾——agent 被守卫拦但快照说"没有用户决策"）。
    user_hidden_source = all_provenance
    user_hidden = [
        entry.get("target")
        for entry in reversed(user_hidden_source)
        if entry.get("origin") == "user"
        and entry.get("kind") == "PatchLayerPresentationIntent"
        and entry.get("detail", {}).get("visible") is False
    ]

    observation = map_state.get("_cartographic_observation")
    if isinstance(observation, str):
        try:
            import json

            observation = json.loads(observation)
        except Exception:  # noqa: BLE001
            observation = {"raw": observation[:128]}

    view = mapspec.get("view") if isinstance(mapspec.get("view"), dict) else {}
    return {
        "session_id": session_id,
        "revision": int(map_state.get("_cartographic_mutation_revision", 0) or 0),
        "viewport": {
            "center": view.get("center") or map_state.get("current_view", {}).get("center"),
            "zoom": view.get("zoom"),
            "framed": bool(view.get("framed")),
        },
        "basemap": map_state.get("base_layer"),
        "layers": layers,
        "layer_count_total": len(mapspec.get("layers") or []),
        "sources": sources,
        "components": components,
        "interaction": {
            # 用户 durable 隐藏决策（provenance 裁决）——agent 收口/QA 的豁免依据
            "user_hidden_layers": [lid for lid in user_hidden if lid],
        },
        "cartography": {
            "mapspec_fingerprint": map_state.get("_current_cartographic_fingerprint"),
            "review": _review_summary(map_state.get("_cartographic_review")),
        },
        "observation": observation if isinstance(observation, dict) else None,
        "provenance": provenance,
    }


def _review_summary(review: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(review, dict):
        return None
    return {
        "verdict": review.get("verdict"),
        "fingerprint": review.get("mapspec_fingerprint"),
        "checked": len(review.get("checks") or []),
        "failed": sum(
            1
            for check in (review.get("checks") or [])
            if isinstance(check, dict) and check.get("status") == "failed"
        ),
    }
