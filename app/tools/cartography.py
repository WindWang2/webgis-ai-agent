"""
地图制图 FC 工具 - 提供样式设置和专题图制作能力
"""
import json
import logging
from typing import Any, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

def _safe_parse_geojson(geojson: Any) -> dict | None:
    """从输入解析 GeoJSON (支持 dict 或 str)"""
    if isinstance(geojson, dict):
        return geojson
    if not isinstance(geojson, str):
        return None
    try:
        return json.loads(geojson)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None

class ApplyStyleArgs(BaseModel):
    geojson: Any = Field(..., description="输入 GeoJSON 或数据引用(ref:xxx)")
    color: str = Field("#3b82f6", description="颜色 (Hex, 例如 #ff0000)")
    opacity: float = Field(0.7, ge=0, le=1, description="不透明度 (0~1)")
    stroke_width: float = Field(2.0, description="边框宽度")
    group: str = Field("analysis", description="图层组: analysis(分析), base(底图), reference(参考)")

class ThematicMapArgs(BaseModel):
    geojson: Any = Field(..., description="输入 GeoJSON 或数据引用(ref:xxx)")
    field: str = Field(..., description="用于分类的数值字段名")
    method: Optional[str] = Field(
        None,
        description=("分类方法: quantiles(分位数), equal_interval(等间距), natural_breaks(自然断裂点), "
                     "head_tail(头尾断裂,重尾计数数据), lisa(局部空间自相关)。"
                     "留空 = 由制图规划器按字段分布自动裁决（推荐）"),
    )
    k: int = Field(5, ge=2, le=10, description="分类数量 (2-10)；留默认时由规划器按模型缺省校正 (3-7)")
    palette: str = Field("YlOrRd", description="调色板: YlOrRd, Blues, Greens, Reds, Viridis, Magma")
    group: str = Field("analysis", description="图层组: analysis(分析), base(底图), reference(参考)")

class ExportMapArgs(BaseModel):
    title: str = Field(..., description="制图主标题 (如: '2026年朝阳区绿地分布监测图')")
    subtitle: str = Field(default="", description="制图副标题")
    include_legend: bool = Field(default=True, description="是否在导出图中附带图例")
    include_compass: bool = Field(default=True, description="是否在导出图中绘制指北针")
    include_scale: bool = Field(default=True, description="是否在导出图中绘制比例尺")
    dark_mode: bool = Field(default=True, description="强制使用暗色现代高斯模糊底纹")
    format: str = Field(default="png", description="导出格式: png (位图) / pdf (A4/A3 排版) / svg (位图嵌入 SVG 容器，可在 Illustrator/Inkscape 打开)")
    paper_size: str = Field(default="screen", description="纸张尺寸: screen (按当前屏幕宽高比) / A4 / A3")
    orientation: str = Field(default="landscape", description="方向: landscape (横向) / portrait (纵向)，仅 paper_size=A4/A3 时生效")
    dpi: int = Field(default=96, ge=72, le=600, description="导出 DPI，96 为屏幕级，300 为印刷级；>300 文件会很大")


class ExportBatchMapsArgs(BaseModel):
    titles: list[str] = Field(..., description="要批量导出的多个地图标题；每个标题对应一次导出任务，按顺序排队执行")
    views: list[dict] | None = Field(
        default=None,
        description=(
            "#725 每张导出的独立视图（与 titles 一一对应，可省略）。"
            "每项 {center: [lng,lat], zoom: number, bearing?: number, pitch?: number}；"
            "省略该项则沿用当前相机。例如『总览/北部/南部』三张图给三个不同 view。"
        ),
    )
    subtitle: str = Field(default="", description="共用的副标题，所有图都用它；不需要就留空")
    include_legend: bool = Field(default=True, description="是否附带图例")
    include_compass: bool = Field(default=True, description="是否绘制指北针")
    include_scale: bool = Field(default=True, description="是否绘制比例尺")
    format: str = Field(default="png", description="导出格式: png / pdf / svg")
    paper_size: str = Field(default="screen", description="纸张尺寸: screen / A4 / A3")
    orientation: str = Field(default="landscape", description="方向: landscape / portrait")
    dpi: int = Field(default=96, ge=72, le=600, description="导出 DPI")

def register_cartography_tools(registry: ToolRegistry):
    """注册制图工具"""

    @tool(registry, name="apply_layer_style",
           description=(
               "为图层注入统一显示样式 (单色 / 描边 / 透明度) 并返回带样式 hint 的 GeoJSON。"
               "\n何时用：分析输出后给图层定型 (一次性单色覆盖整个图层)；"
               "区分主分析结果 vs 辅助底图 (用 group 字段)。"
               "\n何时不用：(1) 按属性值分级着色 (主题图) — 用 create_thematic_map；"
               "(2) 想做交互过滤 — 用 apply_layer_filter。"
               "\n关键约束：color 必须是 hex (#RRGGBB)；opacity 0-1；输出回写 properties.__style__。"
           ),
           args_model=ApplyStyleArgs)
    def apply_layer_style(geojson: Any, color: str, opacity: float = 0.7, stroke_width: float = 2.0, group: str = "analysis") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            
            # 这里我们不修改原始要素，而是返回一个带有 style hint 的结果
            # 或者直接在 features 的 properties 中注入样式
            features = data.get("features", [])
            for f in features:
                if "properties" not in f:
                    f["properties"] = {}
                f["properties"]["fill_color"] = color
                f["properties"]["opacity"] = opacity
                f["properties"]["stroke_width"] = stroke_width
            
            return {
                "geojson": data,
                "group": group,
                "style_applied": {
                    "color": color,
                    "opacity": opacity,
                    "stroke_width": stroke_width
                }
            }
        except (ValueError, TypeError, KeyError) as e:
            return {"error": str(e)}

    @tool(registry, name="create_thematic_map",
           description="根据指定字段制作分层设色专题图 (Choropleth Map)，自动计算颜色级别。",
           args_model=ThematicMapArgs)
    def create_thematic_map(geojson: Any, field: str, method: Optional[str] = None, k: int = 5, palette: str = "YlOrRd", group: str = "analysis") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}

            from app.services.cartography_service import CartographyService
            from app.lib.cartography.thematic_spec import build_graduated_spec

            # C3（分布驱动分类裁决）：method 缺省时由规划器按字段分布选择
            #（重尾→head_tail；近均匀→equal_interval/quantiles；默认
            # natural_breaks），裁决证据（理由/落选者/authority）随结果下发。
            classification_plan = None
            if method is None or method == "":
                from app.lib.cartography.visualization_plan import (
                    choose_classification,
                    distribution_stats_from_values,
                )

                values = [
                    f.get("properties", {}).get(field)
                    for f in (data.get("features") or [])
                    if isinstance(f, dict)
                ]
                stats = distribution_stats_from_values(values)  # type: ignore[arg-type]
                if stats is not None:
                    choice = choose_classification(stats, requested_k=k)
                    method = choice.method
                    k = choice.k
                    classification_plan = choice.model_dump()

            # ADR-0078: legend_spec is the canonical thematic style — the single
            # source both the live MapSpec paint and the <ThematicLegend> overlay
            # derive from. Built through ONE classification (CartographyService
            # stays the engine; the canonical builder delegates to classify, then
            # resolves palette + filters finite values once). For non-lisa we
            # classify exactly once via build_graduated_spec and synthesize the
            # legacy `style_def` view from it (no double Jenks pass).
            legend_spec = None
            style_def = None
            if method == "lisa":
                style_def = CartographyService.build_thematic_style(
                    geojson=data, field=field, method="lisa", k=k, palette=palette
                )
                legend_spec = CartographyService.build_legend_spec(style_def, palette=palette)
            else:
                if method in (None, ""):
                    # 无分布证据（字段全空/过少）——回退制图学默认
                    method = "natural_breaks"
                legend_spec = build_graduated_spec(
                    data, field=field, method=method, k=k, palette=palette,
                )
                if legend_spec is not None:
                    style_def = {
                        "type": "choropleth",
                        "field": field,
                        "breaks": legend_spec.get("breaks", []),
                        "colors": legend_spec.get("palette_colors", []),
                        "legend_labels": legend_spec.get("labels", []),
                    }

            return_dict = {
                "geojson": data,  # return unmodified geojson
                "group": group,
                "style": style_def,
            }
            if classification_plan is not None:
                return_dict["classification_plan"] = classification_plan
            if legend_spec is not None:
                return_dict["legend_spec"] = legend_spec
                return_dict["layer_meta"] = {
                    "title": f"{field} 专题图",
                }
            return return_dict
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Error creating thematic map: {e}")
            return {"error": str(e)}

    @tool(registry, name="create_3d_extrusion_map",
           description=(
               "制作 3D 挤出立体多边形专题图（extrusion_3d）。"
               "\n✅ 用于：以高度（米）直观表达要素的数值大小（如各区人口总量、GDP总量、建筑高度等），"
               "辅以专题设色与 3D 相机视角推荐。"
               "\n支持高度与颜色独立双通道（例如：高度=GDP总量，颜色=人均GDP）。"
           ),
           tier=2, domains=["cartography"],
           param_descriptions={
               "geojson": "多边形面要素 GeoJSON 或引用 (ref:xxx)",
               "height_field": "用于驱动挤出高度的数值属性字段",
               "color_field": "可选：用于专题填色的属性字段（缺省与 height_field 相同）",
               "height_unit": "高度物理/统计单位（如 '人', '亿元', 'm'），默认 'm'",
               "transform": "高度归一化数学变换：'linear', 'sqrt', 'log1p'，默认 'linear'",
               "min_visual_height_m": "最小可视高度（米），默认 10.0",
               "max_visual_height_m": "最大可视高度（米），默认 5000.0",
               "palette": "色板名称，默认 'Oranges'",
               "k": "颜色分级数，默认 5",
               "method": "颜色分类方法：'natural_breaks', 'equal_interval', 'quantile' 等",
               "group": "图层分组，默认 'analysis'",
           })
    def create_3d_extrusion_map(
        geojson: Any,
        height_field: str,
        color_field: Optional[str] = None,
        height_unit: str = "m",
        transform: str = "linear",
        min_visual_height_m: float = 10.0,
        max_visual_height_m: float = 5000.0,
        palette: str = "Oranges",
        k: int = 5,
        method: Optional[str] = None,
        group: str = "analysis",
    ) -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}

            features = data.get("features") or []
            if not features:
                return {"error": "GeoJSON contains no features"}

            # Validate geometry category
            from app.services.analysis_cartography_converter import _infer_geometry_category
            geom_cat, _ = _infer_geometry_category(data)
            if geom_cat != "polygon":
                return {"error": f"3D 挤出图层需要多边形面要素 (Polygon)，当前几何类型为 {geom_cat or 'unknown'}"}

            c_field = color_field or height_field

            from app.lib.cartography.extrusion_model import (
                ExtrusionHeightSpec,
                analyze_height_field_distribution,
            )
            from app.lib.cartography.thematic_spec import build_graduated_spec

            height_values = [
                f.get("properties", {}).get(height_field)
                for f in features
                if isinstance(f, dict)
            ]
            ext_stats = analyze_height_field_distribution(height_values)
            if not ext_stats.get("valid"):
                return {"error": f"高度字段 '{height_field}' 不存在或无有效数值"}

            # Build color legend / thematic spec
            m = method if method else "natural_breaks"
            legend_spec = build_graduated_spec(
                data, field=c_field, method=m, k=k, palette=palette,
            )

            # ADR-0095 Decision 2.3: When height and color channels differ, emit height scale legend
            height_legend = None
            if c_field != height_field:
                min_v = float(ext_stats.get("min", 0.0))
                max_v = float(ext_stats.get("max", 1.0))
                min_h = float(min_visual_height_m)
                max_h = float(max_visual_height_m)
                span = max(max_v - min_v, 0.0)

                quantiles = [0.0, 0.25, 0.50, 0.75, 1.0]
                stops = []
                for q in quantiles:
                    if transform == "log1p":
                        domain_val = min_v + ((1.0 + span) ** q - 1.0)
                    elif transform == "sqrt":
                        domain_val = min_v + (q ** 2) * span
                    else:
                        domain_val = min_v + q * span
                    vis_h = round(min_h + q * (max_h - min_h), 1)
                    stops.append({
                        "value": round(domain_val, 2),
                        "height_m": vis_h,
                        "label": f"{domain_val:g} {height_unit} → {vis_h:g}m".strip(),
                    })

                height_legend = {
                    "type": "height_scale",
                    "field": height_field,
                    "unit": height_unit,
                    "min_value": min_v,
                    "max_value": max_v,
                    "min_height_m": min_h,
                    "max_height_m": max_h,
                    "transform": transform,
                    "title": f"{height_field} 高度标尺",
                    "stops": stops,
                }

            style_def = {
                "type": "fill-extrusion",
                "field": c_field,
                "height_field": height_field,
                "breaks": legend_spec.get("breaks", []) if legend_spec else [],
                "colors": legend_spec.get("palette_colors", []) if legend_spec else [],
                "legend_labels": legend_spec.get("labels", []) if legend_spec else [],
            }

            title_str = (
                f"{height_field} 3D 挤出立体图"
                if c_field == height_field
                else f"{height_field} (高度) × {c_field} (颜色) 3D 挤出图"
            )

            extrusion_meta = {
                "height_field": height_field,
                "color_field": c_field,
                "height_unit": height_unit,
                "transform": transform,
                "min_visual_height_m": min_visual_height_m,
                "max_visual_height_m": max_visual_height_m,
                "stats": ext_stats,
            }
            if height_legend is not None:
                extrusion_meta["height_legend"] = height_legend

            return_dict = {
                "geojson": data,
                "type_hint": "extrusion_3d",
                "group": group,
                "style": style_def,
                "metadata": {
                    "extrusion": extrusion_meta,
                },
                "recommended_view": {
                    "pitch": 45.0,
                    "bearing": -15.0,
                },
                "layer_meta": {
                    "title": title_str,
                },
            }
            if legend_spec is not None:
                return_dict["legend_spec"] = legend_spec
            if height_legend is not None:
                return_dict["height_legend"] = height_legend

            return return_dict
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Error creating 3D extrusion map: {e}")
            return {"error": str(e)}

    @tool(registry, tier=2, domains=["report"], name="export_thematic_map",
           description=(
               "当用户请求导出精美地图、制图排版、保存当前地图视图为图片或 PDF 时调用。"
               "该工具会指挥前端抽取当前地图画面，叠加指北针、比例尺、图例，并合成带标题的高质量图件。"
               "支持 PNG / PDF / SVG 三种格式，可指定 A4/A3 纸张方向和高 DPI（300 即印刷级）。"
               "\n何时用：用户说『导出』『保存地图』『出一张高清图』『打印用的 A4』。"
               "\n何时不用：要批量导出多张图 — 用 export_batch_maps。"
               "\n关键约束：dpi>300 文件会非常大；svg 是把 PNG 嵌入 SVG 容器（兼容 Illustrator/Inkscape）。"
           ),
           args_model=ExportMapArgs)
    def export_thematic_map(
        title: str,
        subtitle: str = "",
        include_legend: bool = True,
        include_compass: bool = True,
        include_scale: bool = True,
        dark_mode: bool = True,
        format: str = "png",
        paper_size: str = "screen",
        orientation: str = "landscape",
        dpi: int = 96,
    ) -> dict:
        fmt = (format or "png").lower().strip()
        if fmt not in ("png", "pdf", "svg"):
            fmt = "png"
        ps = (paper_size or "screen").lower().strip()
        if ps not in ("screen", "a4", "a3"):
            ps = "screen"
        # 前端 ExportOptions.paperSize 类型是 'screen' | 'A4' | 'A3'，标准化大小写
        ps_frontend = {"a4": "A4", "a3": "A3"}.get(ps, "screen")
        ori = (orientation or "landscape").lower().strip()
        if ori not in ("landscape", "portrait"):
            ori = "landscape"

        return {
            "status": "export_task_created",
            "command": "export_map",
            "params": {
                "title": title,
                "subtitle": subtitle,
                "include_legend": include_legend,
                "include_compass": include_compass,
                "include_scale": include_scale,
                "dark_mode": dark_mode,
                "format": fmt,
                "paperSize": ps_frontend,
                "orientation": ori,
                "dpi": dpi,
            },
            "system_message": (
                f"已将 {fmt.upper()} 导出任务发送至前端 (paper={ps_frontend}, orientation={ori}, dpi={dpi})！"
                "前端合成排版（含指北针、比例尺、图例）需要两到三秒时间，"
                "合成完成后将自动通过 `[系统通知]` 回传带有下载安全链接的高清成果。"
                "请直接告知用户你正在制图排版合成..."
            ),
        }

    @tool(registry, tier=2, domains=["report"], name="export_batch_maps",
           description=(
               "批量导出多张地图：按 titles 顺序依次触发导出，每张都用同样的排版/纸张/DPI 设置。"
               "\n何时用：『把当前结果做成 3 张图：总览、北部、南部』『按图层各导一张』。"
               "\n何时不用：只需要一张图 — 用 export_thematic_map。"
               "\n关键约束：批量导出会按队列依次执行，每张约 2-3 秒；前端会自动等前一张完成才开始下一张。"
               "如需每张不同视角（『总览/北部/南部』），传 views 参数（与 titles 一一对应），"
               "导出之间会自动 fly_to 到对应视图；不需要切视图就不用传。"
           ),
           args_model=ExportBatchMapsArgs)
    def export_batch_maps(
        titles: list[str],
        views: list[dict] | None = None,
        subtitle: str = "",
        include_legend: bool = True,
        include_compass: bool = True,
        include_scale: bool = True,
        format: str = "png",
        paper_size: str = "screen",
        orientation: str = "landscape",
        dpi: int = 96,
    ) -> dict:
        if not titles:
            return {"error": "titles 不能为空"}
        fmt = (format or "png").lower().strip()
        if fmt not in ("png", "pdf", "svg"):
            fmt = "png"
        ps = (paper_size or "screen").lower().strip()
        if ps not in ("screen", "a4", "a3"):
            ps = "screen"
        ps_frontend = {"a4": "A4", "a3": "A3"}.get(ps, "screen")
        ori = (orientation or "landscape").lower().strip()
        if ori not in ("landscape", "portrait"):
            ori = "landscape"

        commands = []
        # #725: per-export views emit an interleaved fly_to before each
        # export — the advertised 『总览/北部/南部』 scenario previously
        # produced N identical maps (title-only variation).
        normalized_views: list[dict | None] = list(views or [])
        if len(normalized_views) == 1 and len(titles) > 1:
            normalized_views = normalized_views * len(titles)
        if views and len(normalized_views) != len(titles):
            return {"error": f"views 数量 ({len(normalized_views)}) 必须与 titles 数量 ({len(titles)}) 一一对应或省略"}
        for idx, title in enumerate(titles):
            view = normalized_views[idx] if idx < len(normalized_views) else None
            if isinstance(view, dict) and isinstance(view.get("center"), (list, tuple)) \
                    and len(view["center"]) >= 2 and isinstance(view.get("zoom"), (int, float)):
                commands.append({
                    "command": "fly_to",
                    "params": {
                        key: view[key]
                        for key in ("center", "zoom", "bearing", "pitch") if key in view
                    },
                })
            commands.append({
                "command": "export_map",
                "params": {
                    "title": title,
                    "subtitle": subtitle,
                    "include_legend": include_legend,
                    "include_compass": include_compass,
                    "include_scale": include_scale,
                    "dark_mode": True,
                    "format": fmt,
                    "paperSize": ps_frontend,
                    "orientation": ori,
                    "dpi": dpi,
                },
            })

        return {
            "status": "export_batch_task_created",
            "commands": commands,
            # count = 导出张数（fly_to 视图切换指令不计入）
            "count": sum(1 for c in commands if c["command"] == "export_map"),
            "system_message": (
                f"已将 {sum(1 for c in commands if c['command'] == 'export_map')} 张地图的批量导出任务发送至前端，将按顺序合成。"
                "每张完成后都会通过 `[系统通知]` 回传一条带下载链接的提示。"
                "请告知用户『批量制图开始，预计耗时约 N 秒』并耐心等待结果。"
            ),
        }
