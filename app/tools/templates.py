"""
地图制图模板 FC 工具 - 提供 list_templates 与 apply_template 能力
"""
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.base_layers import resolve_provider_id_to_name
from app.schemas.template_schema import SEED_TEMPLATES
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _safe_parse_geojson(geojson: Any) -> dict | None:
    """解析输入 GeoJSON (支持 dict 或 json 字符串)"""
    import json
    if isinstance(geojson, dict):
        return geojson
    if not isinstance(geojson, str):
        return None
    try:
        return json.loads(geojson)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _normalize_symbology_style(style: dict) -> dict:
    """把模板 style 载荷归一为前端 layer_style_update 消费的 flat paint 键集。

    #557 断点 1：前端 run 读取 color/strokeColor/strokeWidth/pointSize/dashArray/
    fill/fillOpacity（camelCase）。模板载荷可能用 snake_case（stroke_color /
    stroke_width）或只给 fillOpacity —— 这里做一次轻量归一，避免前端拿到
    不认识的键继续 invalid_params / 丢透明度（断点 5 的发射端）。
    """
    out: Dict[str, Any] = {}
    color = style.get("color")
    if color is not None:
        out["color"] = color
        out["fill"] = color
    fill_opacity = style.get("fillOpacity")
    if fill_opacity is None:
        fill_opacity = style.get("opacity")
    if fill_opacity is not None:
        out["fillOpacity"] = fill_opacity
    stroke_color = style.get("strokeColor") or style.get("stroke_color")
    if stroke_color is not None:
        out["strokeColor"] = stroke_color
    stroke_width = style.get("strokeWidth")
    if stroke_width is None:
        stroke_width = style.get("stroke_width")
    if stroke_width is not None:
        out["strokeWidth"] = stroke_width
    for key in ("pointSize", "dashArray"):
        if style.get(key) is not None:
            out[key] = style[key]
    return out


class ListTemplatesArgs(BaseModel):
    kind: Optional[str] = Field(
        None,
        description="模板类别过滤: basemap (底图), symbology (符号化), layout (版式), thematic (专题图)"
    )
    q: Optional[str] = Field(
        None,
        description="搜索关键词，模糊匹配模板名称、描述或关键字"
    )
    limit: int = Field(20, ge=1, le=100, description="最大返回数量")


class ApplyTemplateArgs(BaseModel):
    template_id: str = Field(..., description="要套用的模板 ID (如 tmpl_sym_admin_blue)")
    geojson: Optional[Any] = Field(None, description="可选的目标 GeoJSON 数据")
    field: Optional[str] = Field(None, description="可选的字段名称 (专题图或分类符号化时使用)")
    layer_id: Optional[str] = Field(None, description="可选的目标图层 ID")
    session_id: Optional[str] = Field(None, description="会话 ID（composite 样式预设经生命周期引擎提交时必填）")


def _get_all_templates() -> List[Dict[str, Any]]:
    """从数据库查询模板，若数据库未就绪则回退使用 SEED_TEMPLATES"""
    try:
        from app.core.database import SessionLocal
        from app.models.db_model import CartographyTemplate

        db = SessionLocal()
        try:
            records = db.query(CartographyTemplate).all()
            if records:
                return [
                    {
                        "id": r.id,
                        "kind": r.kind,
                        "name": r.name,
                        "category": r.category,
                        "keywords": r.keywords or [],
                        "description": r.description,
                        "payload": r.payload,
                        "is_builtin": r.is_builtin,
                        "version": r.version,
                    }
                    for r in records
                ]
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"DB query for templates failed, using SEED_TEMPLATES fallback: {e}")

    return SEED_TEMPLATES


def register_template_tools(registry: ToolRegistry):
    """注册制图模板工具: list_templates & apply_template"""

    @tool(
        registry,
        tier=2, domains=["report"],
        name="list_templates",
        description=(
            "查询制图模板列表 (支持按 kind 类别过滤与关键词搜索)。"
            "\n何时用：用户要求套用某种制图样式、排版或专题风格，且需要先查找可用 template_id 时。"
            "\nkind 可选值：basemap (底图), symbology (符号化), layout (版式), thematic (专题图)。"
        ),
        args_model=ListTemplatesArgs,
    )
    def list_templates(
        kind: Optional[str] = None, q: Optional[str] = None, limit: int = 20
    ) -> dict:
        # F-FE-TPL: the registry's ``search`` does the same kind/q filter
        # but on an indexed in-memory structure (no per-call DB query, no
        # Python linear scan of the merged list). The DB path is kept as
        # a fallback for user-saved templates, then merged in front of
        # the registry result.
        from app.services.templates.intent_resolver import list_templates_v2

        page, total = list_templates_v2(kind=kind, q=q, limit=limit, offset=0)
        registry_results = page

        # Also pull user-saved templates from the DB (not in registry).
        try:
            from app.core.database import SessionLocal
            from app.models.db_model import CartographyTemplate
            from sqlalchemy import select as _sel

            db = SessionLocal()
            try:
                stmt = _sel(CartographyTemplate)
                if kind:
                    stmt = stmt.where(CartographyTemplate.kind == kind)
                user_results = list(db.execute(stmt).scalars().all())
            finally:
                db.close()
        except Exception:
            user_results = []

        user_dicts = [
            {
                "id": r.id,
                "kind": r.kind,
                "name": r.name,
                "category": r.category,
                "keywords": r.keywords or [],
                "description": r.description,
                "is_builtin": r.is_builtin,
                "version": r.version,
            }
            for r in user_results
        ]
        seen_ids = {t["id"] for t in registry_results}
        merged = list(registry_results) + [u for u in user_dicts if u["id"] not in seen_ids]

        if q:
            q_lower = q.lower().strip()
            filtered = [
                t for t in merged
                if q_lower in t["name"].lower()
                or (t.get("description") and q_lower in t["description"].lower())
                or (t.get("category") and q_lower in t["category"].lower())
                or any(q_lower in k.lower() for k in t.get("keywords", []))
            ]
        else:
            filtered = merged

        return {"templates": filtered[:limit], "count": len(filtered[:limit])}

    @tool(
        registry,
        tier=2, domains=["report"], name="apply_template",
        description=(
            "按 template_id 统一套用制图模板 (底图/符号化/版式/专题图)。"
            "\n何时用：用户指定套用摸板或选中画廊模板时。"
            "\n内部自动按 kind 分发：symbology→样式注入；basemap→底图切换；layout→版式导出；thematic→专题图映射。"
        ),
        args_model=ApplyTemplateArgs,
    )
    async def apply_template(
        template_id: str,
        geojson: Optional[Any] = None,
        field: Optional[str] = None,
        layer_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        # F-FE-TPL: O(1) registry lookup (was: linear scan over the merged
        # SEED + DB list per call). DB templates still need a DB query,
        # but the registry short-circuits the common case (built-in or
        # composite id) without touching the database at all.
        from app.services.templates.intent_resolver import get_template_or_composite

        target_tmpl = get_template_or_composite(template_id)
        if target_tmpl is None:
            # Fall through to DB lookup (user-saved templates).
            all_tmpls = _get_all_templates()
            target_tmpl = next((t for t in all_tmpls if t["id"] == template_id), None)

        if not target_tmpl:
            return {"error": f"Template not found: {template_id}"}

        kind = target_tmpl["kind"]
        # Composite templates carry `pipeline` (slot→template ids), not `payload`.
        # Handle composite explicitly BEFORE touching payload to avoid KeyError.
        if kind == "composite":
            from app.services.templates.intent_resolver import expand_composite
            # Composite is a style-preset composition: expand pipeline to real templates.
            # Honest contract: field + geojson are required to produce a data-backed
            # legend_spec and a mapspec with real inlineData; session_id is required
            # to commit the mapspec through the lifecycle engine (warehouse rule:
            # tools submit mutations via mapspec_store, not fake success).
            pipeline = target_tmpl.get("pipeline") or {}
            if not pipeline:
                return {"error": f"Composite 模板 {template_id!r} 的 pipeline 为空，无法展开。"}
            thematic_ref = pipeline.get("thematic")
            requires_field = False
            if thematic_ref:
                try:
                    from app.schemas.template_registry import get_template_registry as _get_reg
                    _thematic_tmpl = _get_reg().get(thematic_ref)
                    _variant = (_thematic_tmpl or {}).get("payload", {}).get("variant", "choropleth")
                    requires_field = _variant == "choropleth"
                except Exception:
                    requires_field = True
            if requires_field and not (field or ""):
                return {
                    "error": (
                        f"Composite 模板 {template_id!r} 需要 'field' 参数以驱动专题分级（pipeline thematic={thematic_ref!r}）。"
                        f"请传入 field（要素属性字段名）与 geojson/图层数据，或使用 combine_map_theme(thematic=..., field=..., geojson=..., session_id=...) 组合预设；"
                        f"该预设为样式组合（不含分析计算）。"
                    )
                }
            # Real data required: source must be true inlineData, not a decorative dataPath.
            parsed_geojson = _safe_parse_geojson(geojson)
            if parsed_geojson is None or not parsed_geojson.get("features"):
                return {
                    "error": (
                        f"Composite 模板 {template_id!r} 需要 geojson 数据以生成可验证的 MapSpec 图层（当前未提供或 features 为空）。"
                        f"请传入有效的 GeoJSON FeatureCollection（或 ref:xxx 引用）；样式预设为样式组合，不含分析计算。"
                    )
                }
            if not session_id:
                return {
                    "error": (
                        f"Composite 模板 {template_id!r} 需要 session_id 以经生命周期引擎提交 MapSpec（当前未提供）。"
                        f"请传入当前会话的 session_id；该预设为样式组合，成功后将通过 mapspec_store 写入并返回已验证的 mapspec。"
                    )
                }
            # Expand and assemble via builder — reuse the existing slot machinery.
            try:
                expanded = expand_composite(template_id)
                combination_ids: dict = {}
                for slot, ref in pipeline.items():
                    if ref:
                        combination_ids[slot] = ref
                if field:
                    combination_ids["field"] = field
                combination_ids["geojson"] = parsed_geojson
                from app.services.mapspec.composite_builder import CompositeMapSpecBuilder
                builder = CompositeMapSpecBuilder()
                mapspec = builder.assemble(
                    combination_ids, layer_id=layer_id or "default_layer", field=field or "", geojson=parsed_geojson
                )
                # Commit through lifecycle engine (warehouse rule: tools submit via mapspec_store).
                from app.services.mapspec_store import mapspec_store
                # Build the MapSpec layer dict that pipeline expects: id/type/source/paint/legend_spec
                layer_def = mapspec["layers"][0] if mapspec.get("layers") else {}
                layer = dict(layer_def)
                # Preserve legend_spec for pipeline validation and前端同步
                # source_data is the canonical geojson payload that mapspec_store persists as a ref
                res = await mapspec_store.layer_upsert(session_id, layer, parsed_geojson)
                if not res.get("success"):
                    return {
                        "error": f"Composite 模板 {template_id!r} 的 MapSpec 提交失败: {res.get('message') or res.get('error') or 'unknown'}",
                        "mapspec": mapspec,
                        "expanded": {k: (v.get("id") if isinstance(v, dict) else None) for k, v in expanded.items()},
                    }
                # Evidence-forwarding: expose lifecycle evidence (is_compiled etc.) alongside tool result.
                # 不发前端 command：add_layer 的 handler 需要 params 携带图层数据，而本路径
                # 数据已经 mapspec_store 落库、经 mapspec 同步通道下发（同 webgis_state_set
                # 纯后端提交形态）；params 不全的 add_layer 只会在前端落 invalid_params。
                return {
                    "status": "composite_applied",
                    "kind": "composite",
                    "committed": True,
                    "template_id": template_id,
                    "template_name": target_tmpl["name"],
                    "pipeline": pipeline,
                    "expanded": {k: (v.get("id") if isinstance(v, dict) else None) for k, v in expanded.items()},
                    "mapspec": res.get("mapspec") or mapspec,
                    "layer": res.get("layer") or layer,
                    "is_compiled": res.get("is_compiled"),
                    "mapspec_fingerprint": res.get("mapspec_fingerprint"),
                    "session_id": session_id,
                }
            except Exception as e:
                logger.warning("Composite template %r apply failed: %s", template_id, e, exc_info=True)
                return {"error": f"Composite 模板 {template_id!r} 展开失败: {e}"}

        payload = target_tmpl["payload"]

        if kind == "symbology":
            parsed_geojson = _safe_parse_geojson(geojson)
            mode = payload.get("mode", "single")

            if mode == "single":
                style = payload.get("style", {})
                color = style.get("color", "#3b82f6")
                opacity = style.get("fillOpacity", style.get("opacity", 0.7))
                stroke_width = style.get("strokeWidth", style.get("stroke_width", 2.0))

                output_geojson = None
                if parsed_geojson:
                    output_geojson = json_geojson_style_apply(
                        parsed_geojson, color, opacity, stroke_width
                    )

                # #557 断点 1：前端 layer_style_update 期望 params.style（flat paint
                # 键），不是顶层 style_applied —— 旧形状经 bridge rest 落入 params 后
                # `if (!style) invalid_params` 直接失败。style 键名归一为前端消费的
                # camelCase 集合（fillOpacity/strokeColor/strokeWidth）。
                normalized_style = _normalize_symbology_style(style)
                return {
                    "status": "template_applied",
                    "kind": "symbology",
                    "template_id": template_id,
                    "template_name": target_tmpl["name"],
                    "command": "LAYER_STYLE_UPDATE",
                    "params": {
                        "layer_id": layer_id,
                        "style": normalized_style,
                    },
                    "geojson": output_geojson or parsed_geojson,
                }

            elif mode == "categorical":
                target_field = field or payload.get("field", "")
                return {
                    "status": "template_applied",
                    "kind": "symbology",
                    "template_id": template_id,
                    "template_name": target_tmpl["name"],
                    "command": "LAYER_STYLE_UPDATE",
                    # #557 断点 1（categorical 侧）：前端 layer_style_update 的
                    # categorical 分支消费 params.field + params.colorMap +
                    # params.baseStyle（与 frontend applySymbology 同形）。
                    "params": {
                        "layer_id": layer_id,
                        "field": target_field,
                        "colorMap": payload.get("colorMap", {}),
                        "baseStyle": payload.get("baseStyle", {}),
                    },
                    "geojson": parsed_geojson,
                }
            else:
                return {"error": f"不支持的符号化 mode: {mode!r}（模板 {template_id}）"}

        elif kind == "basemap":
            # #557 断点 2：模板载荷携带 providerId，前端 base_layer_change 期望
            # params.name（TILE_PROVIDERS[].name）。providerId → 规范名解析失败时
            # 显式报错，绝不把 providerId 当 name 发出去（旧形状 invalid_params）。
            canonical_name = resolve_provider_id_to_name(payload.get("providerId", ""))
            if canonical_name is None:
                return {"error": f"底图提供者无法解析为已知底图: {payload.get('providerId', '')!r}（模板 {template_id}）"}
            return {
                "status": "template_applied",
                "kind": "basemap",
                "template_id": template_id,
                "template_name": target_tmpl["name"],
                "command": "BASE_LAYER_CHANGE",
                "params": {"name": canonical_name},
            }

        elif kind == "layout":
            return {
                "status": "template_applied",
                "kind": "layout",
                "template_id": template_id,
                "template_name": target_tmpl["name"],
                "command": "export_map",
                "params": payload,
            }

        elif kind == "thematic":
            target_field = field or payload.get("field", "")
            variant = payload.get("variant", "choropleth")
            parsed_geojson = _safe_parse_geojson(geojson)

            if variant == "heatmap":
                if parsed_geojson is None:
                    # #557 断点 3/4：add_native_heatmap 前端 run 需要 params.geojson，
                    # 缺数据时显式报错（旧实现假成功，params 无 geojson → invalid_params）。
                    return {"error": f"热力专题图需要 geojson 数据（模板 {template_id}）"}
                return {
                    "status": "template_applied",
                    "kind": "thematic",
                    "variant": "heatmap",
                    "template_id": template_id,
                    "template_name": target_tmpl["name"],
                    "command": "add_native_heatmap",
                    "field": target_field,
                    # #557 断点 1 同族：geojson 必须进 params —— useMapBridge 优先用
                    # 显式 params，顶层 geojson 会被 rest 丢弃 → invalid_params。
                    "params": {
                        "geojson": parsed_geojson,
                        "field": target_field,
                        "intensity": payload.get("intensity", 0.8),
                        "radius": payload.get("radius", 25),
                        "heatPalette": payload.get("heatPalette", ["#0000ff", "#00ff00", "#ffff00", "#ff0000"]),
                    },
                    "geojson": parsed_geojson,
                }
            if variant != "choropleth":
                return {"error": f"不支持的专题图 variant: {variant!r}（模板 {template_id}）"}

            # choropleth variant（含 method="categorical"，#557 断点 3）
            style_def = None
            legend_spec = None
            if parsed_geojson and target_field:
                from app.services.cartography_service import CartographyService
                style_def = CartographyService.build_thematic_style(
                    geojson=parsed_geojson,
                    field=target_field,
                    method=payload.get("method", "quantiles"),
                    k=payload.get("k", 5),
                    palette=payload.get("palette", "YlOrRd"),
                )
                if style_def:
                    legend_spec = CartographyService.build_legend_spec(
                        style_def, palette=payload.get("palette", "YlOrRd")
                    )

            if style_def is None:
                # #557 断点 3/4：无数据/字段不支持时显式报错 —— 旧实现返回
                # status:"template_applied" + style:null 的假成功。
                if not parsed_geojson:
                    return {"error": f"专题图需要 geojson 数据（模板 {template_id}）"}
                return {"error": f"字段 {target_field!r} 无法构建专题样式（无有效数值/分类值）（模板 {template_id}）"}

            return {
                "status": "template_applied",
                "kind": "thematic",
                "variant": "choropleth",
                "template_id": template_id,
                "template_name": target_tmpl["name"],
                "command": "create_thematic_map",
                # #557 断点 1 同族：create_thematic_map 前端 run 需要 params.geojson
                # + params.style —— 显式 params 携带完整契约键。
                "params": {
                    "geojson": parsed_geojson,
                    "style": style_def,
                    "legend_spec": legend_spec,
                    "field": target_field,
                    "method": payload.get("method", "quantiles"),
                    "k": payload.get("k", 5),
                    "palette": payload.get("palette", "YlOrRd"),
                },
                "field": target_field,
                "method": payload.get("method", "quantiles"),
                "k": payload.get("k", 5),
                "palette": payload.get("palette", "YlOrRd"),
                "style": style_def,
                "legend_spec": legend_spec,
                "geojson": parsed_geojson,
            }

        # #557 断点 4：任何未覆盖的 kind 都显式报错 —— 旧实现静默落到函数末尾
        # 返回 None，dispatch 视作成功 + 空 payload 喂给 LLM。
        return {"error": f"不支持的模板 kind: {kind!r}（模板 {template_id}）"}

    @tool(
        registry,
        tier=2, domains=["report"], name="combine_map_theme",
        description=(
            "模块化组合地图主题工具。支持通过自由组合 5 大正交组件槽位（basemap 底图件, symbology 符号件, thematic 配色件, layout 版式件, viewport 视口件）或快捷组合预设名称一键合成为目标地图。"
        ),
        args_model=CombineMapThemeArgs,
    )
    async def _combine_map_theme_tool(
        preset: str = "",
        basemap: str = "",
        symbology: str = "",
        thematic: str = "",
        layout: str = "",
        viewport: Optional[dict] = None,
        layer_id: str = "default_layer",
        field: str = "",
        geojson: Optional[Any] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        return await combine_map_theme(
            preset=preset,
            basemap=basemap,
            symbology=symbology,
            thematic=thematic,
            layout=layout,
            viewport=viewport,
            layer_id=layer_id,
            field=field,
            geojson=geojson,
            session_id=session_id,
        )

    @tool(
        registry,
        tier=2, domains=["report"], name="webgis_map_combine",
        description=(
            "规范化地图组件组合工具 (Canonical alias for combine_map_theme)。合成 5 大地图正交组件槽位为 MapSpec。"
        ),
        args_model=CombineMapThemeArgs,
    )
    async def _webgis_map_combine_tool(
        preset: str = "",
        basemap: str = "",
        symbology: str = "",
        thematic: str = "",
        layout: str = "",
        viewport: Optional[dict] = None,
        layer_id: str = "default_layer",
        field: str = "",
        geojson: Optional[Any] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        return await combine_map_theme(
            preset=preset,
            basemap=basemap,
            symbology=symbology,
            thematic=thematic,
            layout=layout,
            viewport=viewport,
            layer_id=layer_id,
            field=field,
            geojson=geojson,
            session_id=session_id,
        )




def json_geojson_style_apply(geojson: dict, color: str, opacity: float, stroke_width: float) -> dict:
    """为 GeoJSON 要素注入 style 属性"""
    import copy
    data = copy.deepcopy(geojson)
    features = data.get("features", [])
    for f in features:
        if "properties" not in f:
            f["properties"] = {}
        f["properties"]["fill_color"] = color
        f["properties"]["opacity"] = opacity
        f["properties"]["stroke_width"] = stroke_width
    return data


class CombineMapThemeArgs(BaseModel):
    preset: Optional[str] = Field("", description="快捷组合预设: academic_research, cyber_dark, natural_terra, heat_density, engineering_survey")
    basemap: Optional[str] = Field("", description="底图件 ID 或提供者名称 (如 carto-positron, carto-dark, esri-imagery, osm-standard)")
    symbology: Optional[str] = Field("", description="符号件 ID 或风格名称 (如 tmpl_sym_admin_blue, single, categorical)")
    thematic: Optional[str] = Field("", description="配色件 ID 或专题图模式 (如 tmpl_th_pop_choro, choropleth, heatmap)")
    layout: Optional[str] = Field("", description="版式件 ID (如 tmpl_ly_academic, tmpl_ly_dark_report, tmpl_ly_minimal, tmpl_ly_engineering)")
    viewport: Optional[Dict[str, Any]] = Field(None, description="视口件配置: {center: [lng, lat], zoom: 10}")
    layer_id: Optional[str] = Field("default_layer", description="目标图层 ID")
    field: Optional[str] = Field("", description="专题字段（thematic 槽为分级/热力时必填，驱动 legend_spec 生成）")
    session_id: Optional[str] = Field(None, description="会话 ID（经生命周期引擎提交 MapSpec 时必填）")


async def combine_map_theme(
    preset: str = "",
    basemap: str = "",
    symbology: str = "",
    thematic: str = "",
    layout: str = "",
    viewport: Optional[dict] = None,
    layer_id: str = "default_layer",
    field: str = "",
    geojson: Optional[Any] = None,
    session_id: Optional[str] = None,
) -> dict:
    """组合 5 大正交地图组件槽位并生成完整 MapSpec。"""
    from app.services.mapspec.composite_builder import CompositeMapSpecBuilder

    combination_ids: Dict[str, Any] = {}
    if preset:
        combination_ids["preset"] = preset
    if basemap:
        combination_ids["basemap"] = basemap
    if symbology:
        combination_ids["symbology"] = symbology
    if thematic:
        combination_ids["thematic"] = thematic
    if layout:
        combination_ids["layout"] = layout
    if viewport:
        combination_ids["viewport"] = viewport
    if field:
        combination_ids["field"] = field

    parsed_geojson = _safe_parse_geojson(geojson) if geojson is not None else None
    if geojson is not None and parsed_geojson is None:
        return {"error": "combine_map_theme: 无效的 geojson 数据（template_id 预设样式组合，不含分析计算）"}
    if geojson is not None:
        combination_ids["geojson"] = parsed_geojson

    builder = CompositeMapSpecBuilder()
    mapspec = builder.assemble(combination_ids, layer_id=layer_id, field=field, geojson=parsed_geojson)

    # If session_id provided, commit through lifecycle (warehouse rule)
    if session_id and parsed_geojson is not None and parsed_geojson.get("features"):
        from app.services.mapspec_store import mapspec_store
        layer_def = mapspec["layers"][0] if mapspec.get("layers") else {}
        layer = dict(layer_def)
        res = await mapspec_store.layer_upsert(session_id, layer, parsed_geojson)
        if not res.get("success"):
            return {
                "error": f"combine_map_theme 的 MapSpec 提交失败: {res.get('message') or res.get('error') or 'unknown'}",
                "mapspec": mapspec,
                "layer_id": layer_id,
            }
        return {
            "status": "composite_applied",
            "preset": preset,
            "combination": combination_ids,
            "layer_id": layer_id,
            "mapspec": res.get("mapspec") or mapspec,
            "layer": res.get("layer") or layer,
            "is_compiled": res.get("is_compiled"),
            "mapspec_fingerprint": res.get("mapspec_fingerprint"),
            "session_id": session_id,
            "committed": True,
        }

    return {
        "status": "composite_map_assembled",
        "preset": preset,
        "combination": combination_ids,
        "layer_id": layer_id,
        "mapspec": mapspec,
        "committed": False,
        "summary": "MapSpec 已组装未提交：传入 session_id 与 geojson 将经生命周期引擎提交并验证。",
    }


