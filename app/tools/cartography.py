"""
地图制图 FC 工具 - 提供样式设置和专题图制作能力
"""
import json
import logging
from typing import Optional, List, Dict, Any
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
    except:
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
    method: str = Field("quantiles", description="分类方法: quantiles(分位数), equal_interval(等间距), lisa(局部空间自相关)")
    k: int = Field(5, ge=2, le=10, description="分类数量 (2-10)")
    palette: str = Field("YlOrRd", description="调色板: YlOrRd, Blues, Greens, Reds, Viridis, Magma")
    group: str = Field("analysis", description="图层组: analysis(分析), base(底图), reference(参考)")

class ExportMapArgs(BaseModel):
    title: str = Field(..., description="制图主标题 (如: '2026年朝阳区绿地分布监测图')")
    subtitle: str = Field(default="", description="制图副标题")
    include_legend: bool = Field(default=True, description="是否在导出图中附带图例")
    include_compass: bool = Field(default=True, description="是否在导出图中绘制指北针")
    include_scale: bool = Field(default=True, description="是否在导出图中绘制比例尺")
    dark_mode: bool = Field(default=True, description="强制使用暗色现代高斯模糊底纹")
    format: str = Field(default="png", description="导出格式: png (位图) / pdf (A4 排版) / svg (位图嵌入 SVG 容器，可在 Illustrator/Inkscape 打开)")
    paper_size: str = Field(default="screen", description="纸张尺寸: screen (按当前屏幕宽高比) 或 A4")
    orientation: str = Field(default="landscape", description="方向: landscape (横向) / portrait (纵向)，仅 paper_size=A4 时生效")
    dpi: int = Field(default=96, ge=72, le=600, description="导出 DPI，96 为屏幕级，300 为印刷级；>300 文件会很大")


class ExportBatchMapsArgs(BaseModel):
    titles: list[str] = Field(..., description="要批量导出的多个地图标题；每个标题对应一次导出任务，按顺序排队执行")
    subtitle: str = Field(default="", description="共用的副标题，所有图都用它；不需要就留空")
    include_legend: bool = Field(default=True, description="是否附带图例")
    include_compass: bool = Field(default=True, description="是否绘制指北针")
    include_scale: bool = Field(default=True, description="是否绘制比例尺")
    format: str = Field(default="png", description="导出格式: png / pdf / svg")
    paper_size: str = Field(default="screen", description="纸张尺寸: screen / A4")
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
                if "properties" not in f: f["properties"] = {}
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
    def create_thematic_map(geojson: Any, field: str, method: str = "quantiles", k: int = 5, palette: str = "YlOrRd", group: str = "analysis") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            
            from app.services.cartography_service import CartographyService
            style_def = CartographyService.build_thematic_style(
                geojson=data,
                field=field,
                method=method,
                k=k,
                palette=palette
            )
            
            return_dict = {
                "geojson": data,  # return unmodified geojson
                "group": group,
                "style": style_def,
            }
            legend_spec = CartographyService.build_legend_spec(style_def, palette=palette)
            if legend_spec is not None:
                return_dict["legend_spec"] = legend_spec
                return_dict["layer_meta"] = {
                    "title": f"{field} 专题图",
                }
            return return_dict
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Error creating thematic map: {e}")
            return {"error": str(e)}

    @tool(registry, name="export_thematic_map",
           description=(
               "当用户请求导出精美地图、制图排版、保存当前地图视图为图片或 PDF 时调用。"
               "该工具会指挥前端抽取当前地图画面，叠加指北针、比例尺、图例，并合成带标题的高质量图件。"
               "支持 PNG / PDF / SVG 三种格式，可指定 A4 纸张方向和高 DPI（300 即印刷级）。"
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
        if ps not in ("screen", "a4"):
            ps = "screen"
        # 前端 ExportOptions.paperSize 类型是 'screen' | 'A4'，标准化大小写
        ps_frontend = "A4" if ps == "a4" else "screen"
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

    @tool(registry, name="export_batch_maps",
           description=(
               "批量导出多张地图：按 titles 顺序依次触发导出，每张都用同样的排版/纸张/DPI 设置。"
               "\n何时用：『把当前结果做成 3 张图：总览、北部、南部』『按图层各导一张』。"
               "\n何时不用：只需要一张图 — 用 export_thematic_map。"
               "\n关键约束：批量导出会按队列依次执行，每张约 2-3 秒；前端会自动等前一张完成才开始下一张。"
               "如果用户希望在导出之间切换视图（比如先飞到北部再导出），请改用『fly_to_location + export_thematic_map』串联调用。"
           ),
           args_model=ExportBatchMapsArgs)
    def export_batch_maps(
        titles: list[str],
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
        ps_frontend = "A4" if ps == "a4" else "screen"
        ori = (orientation or "landscape").lower().strip()
        if ori not in ("landscape", "portrait"):
            ori = "landscape"

        commands = []
        for title in titles:
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
            "count": len(commands),
            "system_message": (
                f"已将 {len(commands)} 张地图的批量导出任务发送至前端，将按顺序合成。"
                "每张完成后都会通过 `[系统通知]` 回传一条带下载链接的提示。"
                "请告知用户『批量制图开始，预计耗时约 N 秒』并耐心等待结果。"
            ),
        }
