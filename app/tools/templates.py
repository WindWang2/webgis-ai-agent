"""
地图制图模板 FC 工具 - 提供 list_templates 与 apply_template 能力
"""
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

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
        name="apply_template",
        description=(
            "按 template_id 统一套用制图模板 (底图/符号化/版式/专题图)。"
            "\n何时用：用户指定套用摸板或选中画廊模板时。"
            "\n内部自动按 kind 分发：symbology→样式注入；basemap→底图切换；layout→版式导出；thematic→专题图映射。"
        ),
        args_model=ApplyTemplateArgs,
    )
    def apply_template(
        template_id: str,
        geojson: Optional[Any] = None,
        field: Optional[str] = None,
        layer_id: Optional[str] = None,
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

                return {
                    "status": "template_applied",
                    "kind": "symbology",
                    "template_id": template_id,
                    "template_name": target_tmpl["name"],
                    "command": "LAYER_STYLE_UPDATE",
                    "layer_id": layer_id,
                    "style_applied": style,
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
                    "layer_id": layer_id,
                    "field": target_field,
                    "colorMap": payload.get("colorMap", {}),
                    "baseStyle": payload.get("baseStyle", {}),
                    "geojson": parsed_geojson,
                }

        elif kind == "basemap":
            return {
                "status": "template_applied",
                "kind": "basemap",
                "template_id": template_id,
                "template_name": target_tmpl["name"],
                "command": "BASE_LAYER_CHANGE",
                "params": payload,
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
                return {
                    "status": "template_applied",
                    "kind": "thematic",
                    "variant": "heatmap",
                    "template_id": template_id,
                    "template_name": target_tmpl["name"],
                    "command": "add_native_heatmap",
                    "field": target_field,
                    "params": {
                        "field": target_field,
                        "intensity": payload.get("intensity", 0.8),
                        "radius": payload.get("radius", 25),
                        "heatPalette": payload.get("heatPalette", ["#0000ff", "#00ff00", "#ffff00", "#ff0000"]),
                    },
                    "geojson": parsed_geojson,
                }
            else:
                # choropleth variant
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

                return {
                    "status": "template_applied",
                    "kind": "thematic",
                    "variant": "choropleth",
                    "template_id": template_id,
                    "template_name": target_tmpl["name"],
                    "command": "create_thematic_map",
                    "field": target_field,
                    "method": payload.get("method", "quantiles"),
                    "k": payload.get("k", 5),
                    "palette": payload.get("palette", "YlOrRd"),
                    "style": style_def,
                    "legend_spec": legend_spec,
                    "geojson": parsed_geojson,
                }

    @tool(
        registry,
        name="combine_map_theme",
        description=(
            "模块化组合地图主题工具。支持通过自由组合 5 大正交组件槽位（basemap 底图件, symbology 符号件, thematic 配色件, layout 版式件, viewport 视口件）或快捷组合预设名称一键合成为目标地图。"
        ),
        args_model=CombineMapThemeArgs,
    )
    def _combine_map_theme_tool(
        preset: str = "",
        basemap: str = "",
        symbology: str = "",
        thematic: str = "",
        layout: str = "",
        viewport: Optional[dict] = None,
        layer_id: str = "default_layer"
    ) -> dict:
        return combine_map_theme(
            preset=preset,
            basemap=basemap,
            symbology=symbology,
            thematic=thematic,
            layout=layout,
            viewport=viewport,
            layer_id=layer_id
        )

    @tool(
        registry,
        name="webgis_map_combine",
        description=(
            "规范化地图组件组合工具 (Canonical alias for combine_map_theme)。合成 5 大地图正交组件槽位为 MapSpec。"
        ),
        args_model=CombineMapThemeArgs,
    )
    def _webgis_map_combine_tool(
        preset: str = "",
        basemap: str = "",
        symbology: str = "",
        thematic: str = "",
        layout: str = "",
        viewport: Optional[dict] = None,
        layer_id: str = "default_layer"
    ) -> dict:
        return combine_map_theme(
            preset=preset,
            basemap=basemap,
            symbology=symbology,
            thematic=thematic,
            layout=layout,
            viewport=viewport,
            layer_id=layer_id
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


def combine_map_theme(
    preset: str = "",
    basemap: str = "",
    symbology: str = "",
    thematic: str = "",
    layout: str = "",
    viewport: Optional[dict] = None,
    layer_id: str = "default_layer"
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

    builder = CompositeMapSpecBuilder()
    mapspec = builder.assemble(combination_ids, layer_id=layer_id)

    return {
        "status": "composite_map_assembled",
        "preset": preset,
        "combination": combination_ids,
        "layer_id": layer_id,
        "mapspec": mapspec
    }


