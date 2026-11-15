"""G46 冻结导出（WP09）：组合条件 → 三格式出版（SVG/PNG/PDF）。

流程（全部同代）：
1. 视图/计划 → 重新冻结核对锚 → preview（组合求值 + facet/full 统计，
   复用 service.preview_conditions —— 导出与 UI/Pi 同一生产服务）；
2. 读 committed MapSpec → 深拷贝 → 投影（绝不写回 committed spec）：
   - 引用探索源 ref 的图层：``layer.filter`` AND 组合 MapLibre 表达式
     （SVG 编译器 mapspec_filter 已支持该子集；与前端 live 过滤同一表达式）；
   - 注入 chart_panel 组件 ×N（inline chart 数据 —— facet 统计的
     ``[{name,value}]`` 投影；**印出 facet 口径说明**，不伪装全条件口径）；
   - 注入 annotation 组件：匹配计数/总数/源锚/条件 digest 口径卡；
3. ``compile_mapspec_to_svg_detailed`` → SVG；PNG（rsvg-convert，复用
   print_layout_authoring 编码器）；PDF（WeasyPrint，同链）；persist。

不重做布局/编码器；复杂 frame/template 组合未验证 → 本域导出用
MapSpec.view.bounds 合成范围（单页）。
"""
from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any, Dict, List, Optional

from app.services.linked_exploration import contract
from app.services.linked_exploration.buckets import STATS_SCAN_LIMIT
from app.services.linked_exploration.errors import LinkedExplorationError
from app.services.linked_exploration.service import (
    _owned_view,
    _plan_charts,
    _validate_plan_payload,
    preview_conditions,
)

logger = logging.getLogger(__name__)

_VALID_FORMATS = ("svg", "png", "pdf")
#: 导出注入图表/注记的有界性
_MAX_EXPORT_ROWS = 32
_MAX_EXPORT_BINS = 32
_ANNOTATION_MAX_CHARS = 400


def _actor_of(actor: Dict[str, Any]) -> Dict[str, Any]:
    return actor


async def export_exploration(
    session_id: str,
    actor: Dict[str, Any],
    *,
    fmt: str = "svg",
    dpi: int = 150,
    view_id: Optional[str] = None,
    plan_raw: Optional[Dict[str, Any]] = None,
    title: str = "",
) -> Dict[str, Any]:
    """冻结导出：组合条件进图面 + 图表统计同范围印出（三格式）。"""
    fmt = str(fmt or "svg").lower()
    if fmt not in _VALID_FORMATS:
        raise LinkedExplorationError(
            "UNSUPPORTED_FORMAT", f"格式必须是 {'/'.join(_VALID_FORMATS)}",
            status_code=422)
    if view_id:
        row = await _owned_view(view_id, session_id)
        plan_raw = row.plan
        title = title or row.title
    if not isinstance(plan_raw, dict):
        raise LinkedExplorationError(
            "INVALID_PLAN", "需要 view_id 或 plan", status_code=422)
    plan = _validate_plan_payload(plan_raw)
    conditions = contract.parse_conditions(plan.conditions)
    charts = _plan_charts(plan)

    # 1. 同代求值（冻结在 preview_conditions 内完成；锚漂移 → typed 拒绝）
    preview = await preview_conditions(
        session_id, plan.anchor.ref_id, charts, conditions, actor,
        create_handle=False)
    # 导出图面需要同代要素数据（SVG 编译器消费 inlineData，不水化 ref）
    from app.services.linked_exploration.snapshot import freeze_source_typed

    frozen = await freeze_source_typed(
        session_id, plan.anchor.ref_id,
        owner_token=actor.get("owner_token"),
        trusted_dispatch=bool(actor.get("trusted_session_id")))
    if (frozen.content_revision != plan.anchor.content_revision
            or frozen.content_fingerprint != plan.anchor.content_fingerprint):
        raise LinkedExplorationError(
            "STALE_SOURCE",
            "导出期间源已推进 —— 已冻结任务保持原结果需重新打开视图",
            status_code=409)

    # 2. MapSpec 副本投影
    from app.services.mapspec.store import mapspec_store_instance as mapspec_store

    mapspec = await mapspec_store.get_mapspec(session_id)
    if not isinstance(mapspec, dict):
        raise LinkedExplorationError(
            "MAPSPEC_UNAVAILABLE", "会话没有可导出的 MapSpec", status_code=409)
    doc = copy.deepcopy(mapspec)

    combined_expr = preview.get("map_filter")
    frozen_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": f.get("geometry"),
                "properties": (f.get("properties") or {}),
            }
            for f in frozen.features if isinstance(f, dict)
        ],
    }
    filtered_layer_ids = _apply_layer_filters(
        doc, plan.anchor.ref_id, combined_expr, frozen_fc)
    if combined_expr is not None and not filtered_layer_ids:
        # review P1-3：目标层失配必须披露（不允许静默未过滤导出）
        raise LinkedExplorationError(
            "EXPORT_NO_TARGET_LAYER",
            "committed MapSpec 中没有引用该探索源的图层 —— 拒绝导出"
            "未过滤图面（防口径声明与图面内容矛盾）",
            status_code=409,
            correction_hint="先在地图上以该 ref 建立图层（或核对视图绑定的"
                            " layer_id），再导出。")
    _inject_chart_components(doc, preview, charts, title)
    _inject_annotation(doc, preview, conditions, plan.anchor.ref_id, title)

    # 3. 编译（bounds = MapSpec.view.bounds —— 单页合成范围）
    view = doc.get("view") or {}
    bounds = view.get("bounds") if isinstance(view, dict) else None
    if not (isinstance(bounds, list) and len(bounds) == 4):
        bounds = None

    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    dpi_i = max(72, min(int(dpi), 400))
    comp = await asyncio.to_thread(
        compile_mapspec_to_svg_detailed,
        doc, target_dpi=dpi_i, width=1600, height=1100, padding=24,
        include_chrome=True, bounds=bounds)
    svg = comp.svg

    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "domain": "linked_exploration",
        "format": fmt,
        "target_dpi": dpi_i,
        "source_anchor": preview.get("anchor"),
        "conditions_digest": preview.get("conditions_digest"),
        "match_count": preview.get("match_count"),
        "total_source_features": preview.get("total"),
        "filtered_layer_ids": filtered_layer_ids,
        "map_filter": combined_expr,
        "chart_scope_note": "facet（排除自身条件）口径统计印出；匹配总数为全部条件口径",
        "feature_count": getattr(comp, "feature_count", None),
        "diagnostics": [d for d in (comp.diagnostics or [])][:40],
    }

    if fmt == "svg":
        payload = svg.encode("utf-8")
        media, ext = "image/svg+xml", ".svg"
    else:
        converter = _png_bytes if fmt == "png" else _pdf_bytes
        payload = await asyncio.to_thread(converter, svg, dpi_i)
        media, ext = (
            ("image/png", ".png") if fmt == "png"
            else ("application/pdf", ".pdf"))

    from app.services.mixed_map_export import persist_mixed_export

    artifact = {"bytes": payload, "media_type": media, "extension": ext,
                "manifest": manifest}
    delivered = await persist_mixed_export(
        session_id, artifact, principal=f"session:{session_id}")
    if not delivered.get("success"):
        raise LinkedExplorationError(
            "EXPORT_PERSIST_FAILED",
            f"导出落盘失败：{delivered.get('error', '')}", status_code=502)
    return {
        "success": True, "status": "exported", "format": fmt, "dpi": dpi_i,
        "bytes": delivered.get("bytes"),
        "download_url": delivered.get("download_url"),
        "manifest_url": delivered.get("manifest_url"),
        "filename": delivered.get("filename"),
        "match_count": preview.get("match_count"),
        "total": preview.get("total"),
        "conditions_digest": preview.get("conditions_digest"),
        "filtered_layer_ids": filtered_layer_ids,
    }


def _layer_source_ref(layer: Dict[str, Any], doc: Dict[str, Any]) -> Optional[str]:
    """图层 source → ref id（与 G41 source_ref_of 同协议）。

    生产形态：``layer["source"] = "<source key>"`` + 顶层
    ``sources[key] = {"type":"geojson","ref":"ref:...", ...}``（mutation
    handlers / composite builder 写入）——必须经 sources 表反查。
    兼容直接内联 dict / "ref:" 字符串的旧形态（review P1-3：此前对生产
    形态返回 None → 导出静默未过滤）。
    """
    from app.services.bivariate_reliability_authoring.snapshot import source_ref_of

    src = layer.get("source")
    if isinstance(src, str):
        if src.startswith("ref:"):
            return src
        # source key → 顶层 sources 表反查（生产主路径）
        entry = (doc.get("sources") or {}).get(src) if isinstance(doc.get("sources"), dict) else None
        if isinstance(entry, dict):
            resolved = source_ref_of(entry)
            if resolved:
                return resolved
        return None
    if isinstance(src, dict):
        return source_ref_of(src)
    return None


def _apply_layer_filters(
    doc: Dict[str, Any], ref_id: str, combined_expr: Optional[List[Any]],
    frozen_fc: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """引用探索源 ref 的图层：filter AND 组合表达式（与既有用户过滤相交，
    不覆盖 —— E15）+ 同代水化 source → inlineData（SVG 编译器消费 inline；
    从不水化非目标层）。无组合条件时仍水化（图面忠实反映源代次）。"""
    filtered: List[str] = []
    layers = doc.get("layers")
    if not isinstance(layers, list):
        return filtered
    # MapSpec 结构：layer.source 是顶层 sources 表的 key —— 冻结 FC 作为
    # inlineData 源写入 sources，目标层引用该 key（不碰其它层/源）。
    if frozen_fc is not None:
        sources = doc.get("sources")
        if not isinstance(sources, dict):
            sources = {}
            doc["sources"] = sources
        src_key = f"{ref_id}#g46-frozen"
        sources[src_key] = {"inlineData": frozen_fc}
    else:
        src_key = None
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        if _layer_source_ref(layer, doc) != ref_id:
            continue
        if src_key is not None:
            layer["source"] = src_key
        if combined_expr is None:
            filtered.append(str(layer.get("id") or ""))
            continue
        existing = layer.get("filter")
        if existing is None:
            layer["filter"] = combined_expr
        else:
            layer["filter"] = ["all", existing, combined_expr]
        filtered.append(str(layer.get("id") or ""))
    return filtered


def _chart_payload_for(statistics: Dict[str, Any], chart: Any) -> Optional[Dict[str, Any]]:
    """facet 统计 → chart_panel inline 数据（[{name,value}]，有界）。"""
    if statistics.get("kind") == "category":
        rows = (statistics.get("rows") or [])[:_MAX_EXPORT_ROWS]
        data = []
        for r in rows:
            label = str(r.get("label", ""))
            if r.get("is_other"):
                label = "other（溢出类别合并）"
            data.append({"name": label[:64], "value": int(r.get("count") or 0)})
        if not data:
            return None
        return {
            "type": "bar",
            "title": f"{chart.title or chart.field}（其它条件下）",
            "data": data,
            "x_label": chart.field,
            "y_label": "count",
        }
    bins = (statistics.get("bins") or [])[:_MAX_EXPORT_BINS]
    if not bins:
        return None
    return {
        "type": "histogram",
        "title": f"{chart.title or chart.field}（其它条件下）",
        "data": [
            {"name": str(b.get("label", ""))[:32], "value": int(b.get("count") or 0)}
            for b in bins
        ],
        "x_label": chart.field,
        "y_label": "count",
    }


def _inject_chart_components(
    doc: Dict[str, Any], preview: Dict[str, Any], charts: List[Any], title: str,
) -> None:
    """注入 chart_panel ×N（SVG 编译器渲染 inline chart）。"""
    layout = doc.get("layout")
    if not isinstance(layout, dict):
        layout = {}
        doc["layout"] = layout
    components = layout.get("components")
    if not isinstance(components, list):
        components = []
        layout["components"] = components
    # 移除本域旧导出投影组件（幂等；不动用户其它组件）
    components[:] = [
        c for c in components
        if not (isinstance(c, dict)
                and str(c.get("id") or "").startswith("g46-export-"))
    ]
    for chart_preview in preview.get("charts", []):
        chart = next(
            (c for c in charts if c.chart_id == chart_preview.get("chart_id")), None)
        if chart is None:
            continue
        payload = _chart_payload_for(
            chart_preview.get("facet", {}).get("statistics", {}), chart)
        if payload is None:
            continue
        components.append({
            "id": f"g46-export-chart-{chart.chart_id}",
            "type": "chart_panel",
            "enabled": True,
            "placement": {"mode": "floating", "position": "top-left"},
            "options": {"chart": payload},
        })


def _inject_annotation(
    doc: Dict[str, Any], preview: Dict[str, Any], conditions: List[Any],
    ref_id: str, title: str,
) -> None:
    """口径卡：匹配计数/总数/源锚/条件 digest（facet 说明印出）。"""
    layout = doc.get("layout")
    if not isinstance(layout, dict):
        layout = {}
        doc["layout"] = layout
    components = layout.get("components")
    if not isinstance(components, list):
        components = []
        layout["components"] = components
    lines = [
        f"联合探索导出{(' · ' + title) if title else ''}",
        f"匹配 {preview.get('match_count')} / 共 {preview.get('total')} 要素（全部条件口径）",
        f"源锚：{ref_id} rev{preview.get('anchor', {}).get('content_revision')}",
        f"条件摘要：{preview.get('conditions_digest')} · 条件数 {len(conditions)}",
        "图内统计为 facet 口径（各图排除自身条件）；地图范围与匹配集同代冻结",
    ]
    # annotation 组件的行走 text 字段（_parse_annotation 按换行拆行，≤8 行）
    components.append({
        "id": "g46-export-annotation-scope",
        "type": "annotation",
        "enabled": True,
        "position": "bottom-left",
        "options": {
            "variant": "text",
            # resolve_component 从 options.text 读行（_parse_annotation 按换行拆）
            "text": "\n".join(ln[:_ANNOTATION_MAX_CHARS] for ln in lines[:8]),
        },
    })


def _png_bytes(svg: str, dpi: int) -> bytes:
    """rsvg-convert 栅格化（复用 G34 编码器 —— 不另写第二栅格链）。"""
    from app.services.print_layout_authoring.service import (
        PrintLayoutServiceError,
        _svg_to_png,
    )
    try:
        return _svg_to_png(svg, 297.0, 210.0, dpi)
    except PrintLayoutServiceError as e:
        raise LinkedExplorationError(
            str(getattr(e, "code", "PNG_FAILED")), str(e), status_code=503) from e
    except Exception as e:  # noqa: BLE001
        raise LinkedExplorationError(
            "PNG_FAILED", f"PNG 栅格化失败：{e}", status_code=503) from e


def _pdf_bytes(svg: str, dpi: int) -> bytes:
    """WeasyPrint 出版链（复用 G34 编码器）。"""
    from app.services.print_layout_authoring.service import (
        PrintLayoutServiceError,
        _svg_to_pdf,
    )
    try:
        return _svg_to_pdf(svg, 297.0, 210.0, "G46 联合探索导出")
    except PrintLayoutServiceError as e:
        raise LinkedExplorationError(
            str(getattr(e, "code", "PDF_FAILED")), str(e), status_code=503) from e
    except Exception as e:  # noqa: BLE001
        raise LinkedExplorationError(
            "PDF_FAILED", f"PDF 出版失败：{e}", status_code=503) from e


__all__ = ["export_exploration", "STATS_SCAN_LIMIT"]
