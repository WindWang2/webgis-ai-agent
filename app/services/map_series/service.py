"""Map Series 服务层 —— 成员会话解析与地图状态读取助手（WP3）。

权威纪律：成员的真实地图状态 = 各自 session 的权威 MapSpec
（``app/services/mapspec/store`` + lifecycle engine）。本模块只提供
**读取投影**：加载 spec、解析指纹、提取表达证据（组件类型/图例方法/
色带/资产绑定）。任何写操作都必须走 ``apply_gis_mutation`` 既有通道
（见 ``propagation.py``），本模块绝不直写。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple


async def load_member_mapspec(session_id: str) -> Optional[Dict[str, Any]]:
    """读取成员 session 的权威 MapSpec（store 权威；含磁盘兜底）。"""
    from app.services.mapspec.store import mapspec_store_instance

    try:
        return await mapspec_store_instance.get_mapspec(session_id)
    except Exception:  # noqa: BLE001 —— 会话不可达如实返回 None（一致性/传播如实披露）
        return None


async def member_fingerprint(
    session_id: str, mapspec: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """成员身份指纹：``session_id + mapspec_fingerprint`` 的 fingerprint 半边。

    优先读 store 落盘的权威指纹（``.fp`` sidecar / Redis 定向字段的内存
    投影）；后端缺接口或无值时回退为对已加载 spec 计算 canonical-JSON
    SHA256（与 ``app/services/mapspec/store._fingerprint_sync`` 同一算法，
    单独实现避免 import 私有符号）。
    """
    from app.services.session_data import session_data_manager

    getter = getattr(session_data_manager, "get_map_spec_fingerprint", None)
    if getter is not None:
        try:
            fp = await getter(session_id)
            if fp:
                return str(fp)
        except Exception:  # noqa: BLE001
            pass
    spec = mapspec if mapspec is not None else await load_member_mapspec(session_id)
    if spec is None:
        return None
    payload = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def member_revision(session_id: str) -> Optional[int]:
    """成员 session 当前 mutation revision（CAS 期望值；读不到 = None）。"""
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(state, dict):
        return None
    try:
        return int(state.get("_cartographic_mutation_revision") or 0)
    except (TypeError, ValueError):
        return 0


# ── 表达证据投影（纯函数；一致性/传播 preview/freeze 共用） ────────────


def components_of(mapspec: Dict[str, Any]) -> List[Dict[str, Any]]:
    layout = mapspec.get("layout") if isinstance(mapspec.get("layout"), dict) else {}
    comps = layout.get("components")
    if not isinstance(comps, list):
        return []
    return [c for c in comps if isinstance(c, dict)]


def component_types(mapspec: Dict[str, Any]) -> List[str]:
    return [str(c.get("type") or "") for c in components_of(mapspec) if c.get("type")]


def layers_of(mapspec: Dict[str, Any]) -> List[Dict[str, Any]]:
    layers = mapspec.get("layers")
    if not isinstance(layers, list):
        return []
    return [l for l in layers if isinstance(l, dict)]


def _paint_has_stops(paint: Dict[str, Any]) -> bool:
    for value in paint.values():
        if isinstance(value, dict) and isinstance(value.get("stops"), list):
            return True
    return False


def legend_method(layer: Dict[str, Any]) -> Optional[str]:
    """层的分类方法声明（legend_spec.method；无 = None）。"""
    ls = layer.get("legend_spec")
    if isinstance(ls, dict) and ls.get("method"):
        return str(ls["method"])
    return None


def palette_evidence(layer: Dict[str, Any]) -> Optional[str]:
    """层的色带证据（legend_spec.colors / palette id / paint 主色）。"""
    ls = layer.get("legend_spec")
    if isinstance(ls, dict):
        colors = ls.get("colors")
        if isinstance(colors, list) and colors:
            return json.dumps([str(c) for c in colors], ensure_ascii=False)
        if ls.get("palette"):
            return str(ls["palette"])
    paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
    for key in ("fill-color", "circle-color", "line-color", "color"):
        if isinstance(paint.get(key), str) and paint.get(key):
            return str(paint[key])
    return None


def thematic_layers(
    mapspec: Dict[str, Any], bound_fields: Tuple[str, ...] = ()
) -> List[Dict[str, Any]]:
    """专题层投影：带 legend_spec 的层，或 paint 携带分级 stops 的层，
    或图例字段命中成员 data_bindings 字段名的层。"""
    out: List[Dict[str, Any]] = []
    for layer in layers_of(mapspec):
        ls = layer.get("legend_spec") if isinstance(layer.get("legend_spec"), dict) else None
        paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
        field = str(ls.get("field") or "") if ls else ""
        if ls is not None or field in bound_fields or _paint_has_stops(paint):
            out.append(layer)
    return out


def primary_thematic_layer(
    mapspec: Dict[str, Any], bound_fields: Tuple[str, ...] = ()
) -> Optional[Dict[str, Any]]:
    """成员的主专题层：优先图例字段命中绑定字段的层，否则第一个专题层。"""
    thematics = thematic_layers(mapspec, bound_fields)
    for layer in thematics:
        ls = layer.get("legend_spec") if isinstance(layer.get("legend_spec"), dict) else None
        if ls and str(ls.get("field") or "") in bound_fields:
            return layer
    return thematics[0] if thematics else None


def declared_unit(layer: Dict[str, Any]) -> Optional[str]:
    """层声明的单位（legend_spec.unit / metadata.unit；无声明 = None）。"""
    ls = layer.get("legend_spec") if isinstance(layer.get("legend_spec"), dict) else None
    if ls and ls.get("unit"):
        return str(ls["unit"])
    metadata = layer.get("metadata") if isinstance(layer.get("metadata"), dict) else {}
    if metadata.get("unit"):
        return str(metadata["unit"])
    return None


def source_bounds(mapspec: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    """来源 bounds 投影（第一个 4 数 bounds；[w, s, e, n]）。"""
    sources = mapspec.get("sources") if isinstance(mapspec.get("sources"), dict) else {}
    for src in sources.values():
        if not isinstance(src, dict):
            continue
        bounds = src.get("bounds")
        if (
            isinstance(bounds, (list, tuple))
            and len(bounds) == 4
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in bounds)
        ):
            w, s, e, n = (float(x) for x in bounds)
            if w < e and s < n:
                return (w, s, e, n)
    return None


def symbol_asset_bindings(mapspec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """G40 符号资产绑定投影（asset_id + revision；层族定位）。"""
    out: List[Dict[str, Any]] = []
    for layer in layers_of(mapspec):
        binding = layer.get("symbol_asset")
        if isinstance(binding, dict) and binding.get("asset_id"):
            out.append(
                {
                    "layer_id": str(layer.get("id") or ""),
                    "asset_id": str(binding["asset_id"]),
                    "revision": binding.get("revision"),
                }
            )
    return out


__all__ = [
    "load_member_mapspec",
    "member_fingerprint",
    "member_revision",
    "components_of",
    "component_types",
    "layers_of",
    "legend_method",
    "palette_evidence",
    "thematic_layers",
    "primary_thematic_layer",
    "declared_unit",
    "source_bounds",
    "symbol_asset_bindings",
]
