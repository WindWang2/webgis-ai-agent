"""地理锚定统计图符 authoring 服务（GOAL-20261126-04，WP4 双入口唯一写面）。

与 G27 scale_portrayal / G47 cartogram 同构：**UI 路由与 Agent 工具共用
本 service / 同一 CAS 链**，不新建状态层 ——
- 读：``mapspec_store_instance.get_mapspec``（committed desired state）+
  图层源要素（inline FC 有界 / ref 经 session_data 解析；vector(MVT) 源
  typed 拒绝 —— 图符需要要素属性，瓦片源没有）；
- 语义：``app.lib.cartography.diagram_symbols``（资格 → 公共尺度 → 几何
  oracle → 布局 → 图例，全链有会计）；
- 写：``UpsertLayerIntent`` + ``apply_gis_mutation``（单笔 CAS：
  expected_revision 不符 superseded 且零写入；mutation_id 幂等）。

纪律：
- **局部写**：只设目标 layer 的 ``diagram_symbol`` 子键，其余字节不动；
- **preview 零突变**：plan/preview 不落任何 mutation；
- **typed 拒绝**：每个拒绝 code 对应可执行下一步（correction_hint），
  绝不静默半应用；
- **有界**：要素数/图符数硬帽复用 diagram_symbols.layout 预算；
  超帽如实拒绝并建议聚合，不偷偷抽稀。
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.lib.cartography.diagram_symbols import (
    DiagramSymbolError,
    build_glyph_geometry,
    normalize_size_policy,
    build_glyph_legend,
    compute_glyph_scale,
    diagram_block_fingerprint,
    glyph_bound_radius,
    qualify_features,
    solve_diagram_symbol_layout,
)
from app.lib.cartography.palettes import resolve_palette_colors
from app.lib.cartography.mapspec_schema import MapSpecLayerDiagramSymbol

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "diagram_symbol.v1"

#: plan/apply 阶段读取要素的硬帽（与 layout.MAX_LAYOUT_FEATURES 对齐）。
MAX_PLAN_FEATURES = 20_000
#: 字段扫描有界面（与 scale_portrayal 同量级）。
MAX_FIELD_SCAN_FEATURES = 50
MAX_FIELDS = 64


def _diag(code: str, field: str, detail: str, *, hint: str = "") -> Dict[str, Any]:
    d: Dict[str, Any] = {"code": code, "field": field, "detail": detail}
    if hint:
        d["hint"] = hint
    return d


def _extract_fc(data: Any) -> Optional[Dict[str, Any]]:
    """session ref / inline 载荷 → GeoJSON FC（与 layer_browse 同判定）。"""
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict) and nested.get("type") == "FeatureCollection":
            return nested
        if data.get("type") == "FeatureCollection":
            return data
    return None


async def _load_spec(session_id: str) -> Dict[str, Any]:
    from app.services.mapspec.store import mapspec_store_instance

    spec = await mapspec_store_instance.get_mapspec(session_id)
    if not isinstance(spec, dict) or not spec:
        raise _ServiceRejection([_diag(
            "DIAGRAM.SOURCE.NO_MAPSPEC", "session_id",
            "会话暂无已提交 MapSpec（先出图再调用本能力）",
            hint="先经 webgis_map_product / 图层面板建立图层")])
    return spec


class _ServiceRejection(Exception):
    """内部控制流：携带 diagnostics 的拒绝（service 出口统一转 status dict）。"""

    def __init__(self, diagnostics: List[Dict[str, Any]]) -> None:
        super().__init__(diagnostics[0]["detail"] if diagnostics else "rejected")
        self.diagnostics = diagnostics


def _match_layer(spec: Dict[str, Any], target: str) -> Dict[str, Any]:
    layers = [lyr for lyr in (spec.get("layers") or []) if isinstance(lyr, dict)]
    exact = [lyr for lyr in layers if str(lyr.get("id") or "") == target]
    if len(exact) == 1:
        return exact[0]
    suffix = [lyr for lyr in layers if str(lyr.get("id") or "").endswith(target)]
    if len(suffix) == 1:
        return suffix[0]
    ids = [str(lyr.get("id")) for lyr in layers]
    raise _ServiceRejection([_diag(
        "DIAGRAM.LAYER.NOT_UNIQUE", "layer",
        f"未唯一命中图层 {target!r}；候选：{', '.join(ids[:12])}")])


def _resolve_source_fc(
    spec: Dict[str, Any], layer: Dict[str, Any], session_id: str,
) -> Tuple[str, Dict[str, Any]]:
    """图层源 → (来源描述, FeatureCollection)。

    geojson inline / geojson ref 均支持；vector(MVT) / raster typed 拒绝
    （图符需要逐要素属性；瓦片通道没有）。
    """
    src_id = layer.get("source")
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    src = sources.get(src_id) if isinstance(src_id, str) else None
    if not isinstance(src, dict):
        raise _ServiceRejection([_diag(
            "DIAGRAM.SOURCE.MISSING", "source",
            f"图层源 {src_id!r} 不存在或不合法",
            hint="检查图层 sources 定义")])
    if src.get("type") == "vector":
        raise _ServiceRejection([_diag(
            "DIAGRAM.SOURCE.UNSUPPORTED_TYPE", "source.type",
            "vector(MVT) 瓦片源不携带逐要素属性，图符语义不可用",
            hint="先为该图层建立 geojson 会话引用，或改用内联 FC 图层")])
    payload: Any = None
    if src.get("type") == "geojson":
        payload = src.get("inlineData")
        if payload is None:
            payload = src.get("data")
    ref_id = None
    for key in ("ref", "ref_id"):
        v = src.get(key)
        if isinstance(v, str) and v:
            ref_id = v
            break
    if payload is None and ref_id:
        from app.services.session_data import session_data_manager

        resolved = session_data_manager.resolve_alias(session_id, ref_id)
        res = session_data_manager.get_ref_data_shared(session_id, resolved)
        if getattr(res, "success", False):
            payload = res.data
        else:
            raise _ServiceRejection([_diag(
                "DIAGRAM.SOURCE.REF_UNAVAILABLE", "source.ref",
                f"ref {ref_id!r} 数据不可用：{getattr(res, 'error', '') or '未知原因'}",
                hint="刷新数据引用或重新上传")])
    fc = _extract_fc(payload)
    if not fc:
        raise _ServiceRejection([_diag(
            "DIAGRAM.SOURCE.NOT_FC", "source",
            "源载荷不是 GeoJSON FeatureCollection",
            hint="图符需要要素集合（Point/Polygon/LineString + 数值属性）")])
    return (ref_id or f"inline:{src_id}"), fc


#: 图例来源标记（unbind 只删自己写的 legend_spec，不动 user-wins 图例）。
_LEGEND_SOURCE_MARKER = "diagram_symbol.v1"


def _diagram_legend_spec(block: Dict[str, Any]) -> Dict[str, Any]:
    """图符块 → 既有 legend_spec categorical 通道（live/出版图例同源）。

    图例与几何同源：条目 = build_glyph_legend 的 category/color_slot，
    hex 由 palettes 权威解析；notes 取语义披露前 3 条；标记 source 以便
    解绑时只清自己写的（不碰既有 legend_spec）。
    """
    kind = block["kind"]
    categories = block["categories"]
    sp = dict(block.get("size_policy") or {})
    scale_for_legend = compute_glyph_scale(kind, [[1.0] * len(categories)], sp)
    legend = build_glyph_legend(
        kind, categories,
        scale_for_legend,
        sp,
        palette_id=block.get("palette_id") or "Set1",
        unit=block.get("unit"),
        layout=block.get("layout"),
    )
    colors = resolve_palette_colors(block.get("palette_id"))
    entries = []
    for item in legend["categories"]:
        entries.append({
            "color": colors[item["color_slot"] % len(colors)],
            "label": item["category"],
        })
    return {
        "type": "categorical",
        "title": "图符图例",
        "entries": entries,
        "unit": block.get("unit") or "",
        "notes": legend["notes_zh"][:3],
        "source": _LEGEND_SOURCE_MARKER,
    }


def _validate_block(block_spec: Mapping[str, Any]) -> Dict[str, Any]:
    """块 pydantic 校验（schema 层承载面）→ dict；非法 typed 拒绝。"""
    try:
        model = MapSpecLayerDiagramSymbol.model_validate(dict(block_spec))
    except Exception as exc:  # pydantic ValidationError → 有界诊断
        msg = str(exc).splitlines()[0][:300]
        raise _ServiceRejection([_diag(
            "DIAGRAM.BLOCK.INVALID", "diagram_symbol",
            f"diagram_symbol 块不合法：{msg}",
            hint="见 INTERFACES：kind/categories/fields/size_policy 词表与上限")]) from exc
    return model.model_dump(exclude_none=True)


async def inspect_diagram_symbol(session_id: str, layer: str) -> Dict[str, Any]:
    """只读：图层要素面事实（数量/几何/字段/现有块），供 Agent 与 UI 决策。"""
    try:
        spec = await _load_spec(session_id)
        hit = _match_layer(spec, layer)
        _src_desc, fc = _resolve_source_fc(spec, hit, session_id)
    except _ServiceRejection as rej:
        return {"status": "rejected", "diagnostics": rej.diagnostics}
    feats = fc.get("features") if isinstance(fc.get("features"), list) else []
    geom_counts: Dict[str, int] = {}
    for feat in feats[:MAX_PLAN_FEATURES]:
        gtype = (feat.get("geometry") or {}).get("type") if isinstance(feat, dict) else None
        geom_counts[str(gtype)] = geom_counts.get(str(gtype), 0) + 1
    field_names: List[str] = []
    for feat in feats[:MAX_FIELD_SCAN_FEATURES]:
        props = feat.get("properties") if isinstance(feat, dict) else None
        if isinstance(props, dict):
            for k in props:
                if isinstance(k, str) and k not in field_names:
                    field_names.append(k)
                if len(field_names) >= MAX_FIELDS:
                    break
        if len(field_names) >= MAX_FIELDS:
            break
    existing = hit.get("diagram_symbol")
    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "layer_id": str(hit.get("id") or layer),
        "source": _src_desc[:64],
        "features_total": len(feats),
        "features_truncated": len(feats) > MAX_PLAN_FEATURES,
        "geometry_counts": geom_counts,
        "available_fields": field_names[:MAX_FIELDS],
        "existing_block": copy.deepcopy(existing) if isinstance(existing, dict) else None,
        "has_existing_block": isinstance(existing, dict),
    }


def _plan_locked(
    fc: Dict[str, Any],
    block: Dict[str, Any],
    *,
    layout_policy: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """资格 → 尺度 → 几何 → 布局 → 图例（纯计算，零 mutation）。"""
    features = fc.get("features") or []
    if len(features) > MAX_PLAN_FEATURES:
        raise _ServiceRejection([_diag(
            "DIAGRAM.SOURCE.TOO_LARGE", "features",
            f"要素数 {len(features)} 超过图符硬帽 {MAX_PLAN_FEATURES}",
            hint="先经 webgis_layer_aggregate 聚合到区域统计，再对聚合结果入图")])
    # P0（review A#1）：size_policy 是可选块 —— 必须经归一化器补默认值
    #（缺省键直接下标会在 compute_glyph_scale 炸裸 KeyError）。
    kind = block["kind"]
    sp = normalize_size_policy(kind, block.get("size_policy"))
    categories = block["categories"]
    fields = block["fields"]
    qualified = qualify_features(
        features,
        kind=kind,
        categories=categories,
        fields=fields,
        missing_policy=block.get("missing_policy", "omit"),
        id_field=block.get("id_field") or "id",
    )
    scale = compute_glyph_scale(kind, qualified["values"], sp)
    glyphs_geo: List[Dict[str, Any]] = []
    radii: List[float] = []
    for row in qualified["values"]:
        geo = build_glyph_geometry(kind, row, scale, sp, layout=block.get("layout"))
        glyphs_geo.append(geo)
        radii.append(glyph_bound_radius(geo))
    features_by_id = _features_qualified_order(features, qualified["glyph_feature_ids"])
    layout_plan = solve_diagram_symbol_layout(
        features_by_id, qualified, radii, layout_policy=layout_policy)
    legend = build_glyph_legend(
        kind, categories, scale, sp,
        palette_id=block.get("palette_id") or "Set1",
        unit=block.get("unit"), layout=block.get("layout"),
    )
    return {
        "qualification": qualified,
        "scale": scale,
        "geometry_sample": glyphs_geo[:16],  # 有界样本（全量几何在渲染端同 oracle 重算）
        "layout": layout_plan,
        "legend": legend,
        "block_fingerprint": diagram_block_fingerprint(block),
    }


def _features_qualified_order(
    features: List[Dict[str, Any]], glyph_ids: List[str],
) -> List[Dict[str, Any]]:
    """把 features 重排成 qualification 输出序（布局同序输入）。"""
    by_id: Dict[str, Dict[str, Any]] = {}
    for feat in features:
        fid = feat.get("id")
        if fid is None:
            fid = (feat.get("properties") or {}).get("id")
        if fid is not None:
            by_id.setdefault(str(fid), feat)
    return [by_id[gid] for gid in glyph_ids if gid in by_id]


async def plan_diagram_symbol(
    session_id: str,
    layer: str,
    block_spec: Mapping[str, Any],
    *,
    layout_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """只读计划：完整资格/布局/图例证据，零 mutation。"""
    try:
        spec = await _load_spec(session_id)
        hit = _match_layer(spec, layer)
        _src_desc, fc = _resolve_source_fc(spec, hit, session_id)
        block = _validate_block(block_spec)
        plan = _plan_locked(fc, block, layout_policy=layout_policy)
    except _ServiceRejection as rej:
        return {"status": "rejected", "diagnostics": rej.diagnostics}
    except DiagramSymbolError as exc:
        return {"status": "rejected",
                "diagnostics": [_diag(f"DIAGRAM.QUALIFICATION.{exc.code}",
                                      exc.field or "diagram_symbol",
                                      exc.message, hint=exc.correction_hint)]}
    return {
        "status": "plan",
        "schema_version": SCHEMA_VERSION,
        "layer_id": str(hit.get("id") or layer),
        "block": block,
        "plan": plan,
    }


async def apply_diagram_symbol(
    session_id: str,
    layer: str,
    block_spec: Optional[Mapping[str, Any]],
    *,
    mode: str = "preview",
    expected_revision: Optional[int] = None,
    operation_id: Optional[str] = None,
    note: str = "",
    origin: str = "agent",
    actor: str = "webgis_apply_diagram_symbol",
) -> Dict[str, Any]:
    """局部受控写：preview 零突变 / commit 单笔 CAS 挂 ``diagram_symbol`` 块。

    block_spec=None = 解绑（移除块，恢复层既有 paint 语义）。
    """
    if mode not in ("preview", "commit"):
        return {"status": "rejected", "diagnostics": [_diag(
            "DIAGRAM.APPLY.UNKNOWN_MODE", "mode", "mode 只能是 preview | commit")]}
    if mode == "commit" and origin == "user" and expected_revision is None:
        return {"status": "rejected", "diagnostics": [_diag(
            "DIAGRAM.APPLY.MISSING_EXPECTED_REVISION", "expected_revision",
            "用户来源的 commit 必须携带 expected_revision（CAS 基线）")],
            "hint": "刷新 MapSpec revision 后重试；本次未产生任何 mutation"}
    try:
        spec = await _load_spec(session_id)
        hit = _match_layer(spec, layer)
        block: Optional[Dict[str, Any]] = None
        plan_payload: Optional[Dict[str, Any]] = None
        if block_spec is not None:
            _src_desc, fc = _resolve_source_fc(spec, hit, session_id)
            block = _validate_block(block_spec)
            plan_payload = _plan_locked(fc, block, layout_policy=None)
    except _ServiceRejection as rej:
        return {"status": "rejected", "diagnostics": rej.diagnostics}
    except DiagramSymbolError as exc:
        return {"status": "rejected",
                "diagnostics": [_diag(f"DIAGRAM.QUALIFICATION.{exc.code}",
                                      exc.field or "diagram_symbol",
                                      exc.message, hint=exc.correction_hint)]}

    new_layer = copy.deepcopy(hit)
    if block is None:
        new_layer.pop("diagram_symbol", None)
        existing_spec = new_layer.get("legend_spec")
        if isinstance(existing_spec, dict) \
                and existing_spec.get("source") == _LEGEND_SOURCE_MARKER:
            new_layer.pop("legend_spec", None)  # 只清自己写的图例
    else:
        new_layer["diagram_symbol"] = block
        existing = new_layer.get("legend_spec")
        # review B#4：user-wins —— 既有 legend_spec 非"自己写的"则不覆写
        #（图例双轨披露：图符几何照常渲染，图例保持用户版本）
        if not (isinstance(existing, dict) and existing.get("source") == _LEGEND_SOURCE_MARKER):
            new_layer["legend_spec"] = _diagram_legend_spec(block)

    compact = {
        "layer_id": str(new_layer.get("id") or layer),
        "block_fingerprint": diagram_block_fingerprint(block) if block else None,
        "evidence": plan_payload["layout"]["evidence"] if plan_payload else None,
        "legend": plan_payload["legend"] if plan_payload else None,
    }
    if mode == "preview":
        return {"status": "preview", "schema_version": SCHEMA_VERSION, **compact}

    UpsertLayerIntent, apply_gis_mutation = _mutation_dependencies()

    op_id = (operation_id or f"diagram:{int(time.time() * 1000)}")[:64]
    try:
        res = await apply_gis_mutation(
            session_id,
            UpsertLayerIntent(layer=new_layer),
            origin=origin,
            actor=actor,
            expected_revision=expected_revision,
            mutation_id=f"op:{op_id}",
            reason=note or "地理锚定统计图符绑定（diagram_symbol.v1）",
        )
    except Exception as exc:  # noqa: BLE001 - 工具错误契约，不泄漏栈
        logger.warning("[diagram_symbol_authoring] mutation failed", exc_info=True)
        return {"status": "error", "error": f"mutation_failed: {exc}", **compact}
    if getattr(res, "superseded", False):
        return {"status": "superseded",
                "error": "MapSpec revision moved (CAS); retry with current revision",
                "operation_id": op_id, **compact}
    if getattr(res, "is_error", False):
        return {"status": "error",
                "error": getattr(res, "error_msg", None) or "mutation rejected",
                "operation_id": op_id, **compact}
    return {
        "status": "committed",
        "schema_version": SCHEMA_VERSION,
        "operation_id": op_id,
        "mapspec_revision": getattr(res, "mutation_revision", None),
        **compact,
    }


def _mutation_dependencies():
    """延迟解析 CAS 链依赖（Web 进程外可注入，不触发 settings/DB 链）。

    这不是第二套 mutation：返回的仍是既有 ``UpsertLayerIntent`` 与
    ``apply_gis_mutation``（scale_portrayal 同款 seam）。
    """
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.services.gis_world_state.mutation import apply_gis_mutation

    return UpsertLayerIntent, apply_gis_mutation


__all__ = [
    "SCHEMA_VERSION",
    "MAX_PLAN_FEATURES",
    "inspect_diagram_symbol",
    "plan_diagram_symbol",
    "apply_diagram_symbol",
    "_mutation_dependencies",
]
