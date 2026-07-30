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
        all_tmpls = _get_all_templates()

        filtered = []
        q_lower = q.lower().strip() if q else None

        for t in all_tmpls:
            if kind and t["kind"] != kind:
                continue

            if q_lower:
                name_match = q_lower in t["name"].lower()
                desc_match = bool(t.get("description") and q_lower in t["description"].lower())
                cat_match = bool(t.get("category") and q_lower in t["category"].lower())
                kw_match = any(q_lower in k.lower() for k in t.get("keywords", []))

                if not (name_match or desc_match or cat_match or kw_match):
                    continue

            filtered.append(
                {
                    "id": t["id"],
                    "kind": t["kind"],
                    "name": t["name"],
                    "category": t.get("category"),
                    "keywords": t.get("keywords", []),
                    "description": t.get("description"),
                    "is_builtin": t.get("is_builtin", False),
                }
            )

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
                "command": "EXPORT_LAYOUT_UPDATE",
                "params": payload,
            }

        elif kind == "thematic":
            target_field = field or payload.get("field", "")
            return {
                "status": "template_applied",
                "kind": "thematic",
                "template_id": template_id,
                "template_name": target_tmpl["name"],
                "command": "THEMATIC_MAP_PRESET",
                "field": target_field,
                "params": payload,
                "geojson": _safe_parse_geojson(geojson),
            }

        return {"error": f"Unknown template kind: {kind}"}


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
