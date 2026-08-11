"""
Pydantic schemas and seed template definitions for Cartography Template System.
"""
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class BasemapPayload(BaseModel):
    providerId: str
    rasterFilters: Optional[Dict[str, Any]] = None
    vectorStyleUrl: Optional[str] = None
    overlays: Optional[List[Dict[str, Any]]] = None


class SymbologySinglePayload(BaseModel):
    mode: Literal["single"] = "single"
    geometry: str
    style: Dict[str, Any]


class SymbologyCategoricalPayload(BaseModel):
    mode: Literal["categorical"] = "categorical"
    geometry: str
    # field is injected at apply-time from user data; not stored in the preset
    # (matches the thematic-preset invariant — US22-style).
    field: Optional[str] = None
    colorMap: Dict[str, str]
    baseStyle: Optional[Dict[str, Any]] = None


SymbologyPayload = Annotated[
    Union[SymbologySinglePayload, SymbologyCategoricalPayload],
    Field(discriminator="mode"),
]


class LayoutStyle(BaseModel):
    titleColor: Optional[str] = None
    titleFont: Optional[str] = None
    subtitleFont: Optional[str] = None
    accentColor: Optional[str] = None
    marginPx: Optional[int] = None
    fontFamily: Optional[str] = None
    graticuleColor: Optional[str] = None
    watermarkText: Optional[str] = None


class LayoutTemplatePayload(BaseModel):
    paperSize: str = "A4"
    orientation: str = "landscape"
    title: Optional[str] = None
    subtitle: Optional[str] = None
    showLegend: bool = True
    showNorthArrow: bool = True
    showScaleBar: bool = True
    showGrid: bool = False
    style: Optional[LayoutStyle] = None


class ThematicChoroplethPayload(BaseModel):
    variant: Literal["choropleth"] = "choropleth"
    method: str = "quantiles"
    k: int = 5
    palette: str = "YlOrRd"


class ThematicHeatmapPayload(BaseModel):
    variant: Literal["heatmap"] = "heatmap"
    intensity: Optional[float] = 0.8
    radius: Optional[int] = 25
    heatPalette: Optional[List[str]] = Field(
        default_factory=lambda: ["#0000ff", "#00ff00", "#ffff00", "#ff0000"]
    )


ThematicPresetPayload = Annotated[
    Union[ThematicChoroplethPayload, ThematicHeatmapPayload],
    Field(discriminator="variant"),
]


class CartographyTemplateBase(BaseModel):
    name: str
    kind: Literal["basemap", "symbology", "layout", "thematic"]
    category: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    payload: Dict[str, Any]
    is_builtin: bool = False
    version: int = 1


class CartographyTemplateCreate(CartographyTemplateBase):
    org_id: Optional[int] = None
    creator_id: Optional[str] = None


class CartographyTemplateResponse(CartographyTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: Optional[int] = None
    creator_id: Optional[str] = None


SEED_TEMPLATES: List[Dict[str, Any]] = [
    # --- BASEMAP (4) ---
    {
        "id": "tmpl_bm_positron",
        "kind": "basemap",
        "name": "学术论文浅色底图",
        "category": "basemap",
        "keywords": ["academic", "light", "positron", "vector", "学术", "浅色"],
        "description": "Carto Positron 矢量底图，适合学术出版与报表绘制",
        "payload": {
            "providerId": "carto-positron",
            "vectorStyleUrl": "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_bm_dark",
        "kind": "basemap",
        "name": "夜间暗色底图",
        "category": "basemap",
        "keywords": ["dark", "night", "vector", "暗色", "大屏", "汇报"],
        "description": "Carto Dark Matter 矢量底图，适合夜间大屏与亮色数据展示",
        "payload": {
            "providerId": "carto-dark",
            "vectorStyleUrl": "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_bm_satellite",
        "kind": "basemap",
        "name": "卫星影像与矢量标注",
        "category": "basemap",
        "keywords": ["satellite", "hybrid", "imagery", "卫星", "影像", "混合"],
        "description": "Esri 卫星影像底图叠加清晰矢量路网与地名标注",
        "payload": {
            "providerId": "esri-imagery",
            "overlays": [
                {
                    "providerId": "carto-positron-labels",
                    "vectorStyleUrl": "https://basemaps.cartocdn.com/gl/positron-nolabels-gl-style/style.json",
                }
            ],
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_bm_grayscale",
        "kind": "basemap",
        "name": "灰度街道滤镜",
        "category": "basemap",
        "keywords": ["grayscale", "raster", "filter", "灰度", "街道"],
        "description": "标准街道图经灰度与降对比度滤镜处理，突显上层专题数据",
        "payload": {
            "providerId": "osm-standard",
            "rasterFilters": {
                "grayscale": 1.0,
                "contrast": 0.8,
                "brightness": 1.1,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    # --- SYMBOLOGY (5) ---
    {
        "id": "tmpl_sym_admin_blue",
        "kind": "symbology",
        "name": "行政区划蓝调面",
        "category": "symbology",
        "keywords": ["admin", "blue", "polygon", "行政区", "蓝色", "面"],
        "description": "经典多边形面填充：蓝色半透明填充 + 深蓝色边框",
        "payload": {
            "mode": "single",
            "geometry": "Polygon",
            "style": {
                "color": "#3b82f6",
                "fillOpacity": 0.4,
                "strokeColor": "#1d4ed8",
                "strokeWidth": 1.5,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_road_orange",
        "kind": "symbology",
        "name": "主干路网橙色虚线",
        "category": "symbology",
        "keywords": ["road", "line", "orange", "dash", "道路", "橙色", "虚线"],
        "description": "线状要素符号化：鲜艳橙色与中等线宽",
        "payload": {
            "mode": "single",
            "geometry": "LineString",
            "style": {
                "color": "#f97316",
                "strokeWidth": 2.5,
                "lineDash": [4, 2],
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_poi_red",
        "kind": "symbology",
        "name": "重点 POI 红色实心点",
        "category": "symbology",
        "keywords": ["poi", "point", "red", "dot", "兴趣点", "红色", "圆点"],
        "description": "点状要素符号化：醒红实心圆点 + 白色轮廓包边",
        "payload": {
            "mode": "single",
            "geometry": "Point",
            "style": {
                "color": "#ef4444",
                "radius": 6,
                "strokeColor": "#ffffff",
                "strokeWidth": 1.5,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_boundary_dash",
        "kind": "symbology",
        "name": "界线黑灰虚线",
        "category": "symbology",
        "keywords": ["boundary", "dash", "line", "边界", "虚线"],
        "description": "标准边界线样式：深灰色虚线与高对比度",
        "payload": {
            "mode": "single",
            "geometry": "LineString",
            "style": {
                "color": "#334155",
                "strokeWidth": 1.8,
                "lineDash": [6, 3, 2, 3],
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_landuse_cat",
        "kind": "symbology",
        "name": "土地利用分类配色",
        "category": "symbology",
        "keywords": ["landuse", "categorical", "polygon", "土地利用", "分类"],
        "description": "定型分类配色：居住/商业/工业/绿地专用颜色映射",
        "payload": {
            "mode": "categorical",
            "geometry": "Polygon",
            # field intentionally omitted — injected at apply-time (e.g. user selects "landuse")
            "colorMap": {
                "residential": "#fca5a5",
                "commercial": "#93c5fd",
                "industrial": "#fdba74",
                "green": "#86efac",
                "water": "#7dd3fc",
            },
            "baseStyle": {"fillOpacity": 0.75, "strokeWidth": 0.5},
        },
        "is_builtin": True,
        "version": 1,
    },
    # --- LAYOUT (4) ---
    {
        "id": "tmpl_ly_academic",
        "kind": "layout",
        "name": "学术期刊黑白排版",
        "category": "layout",
        "keywords": ["academic", "paper", "mono", "serif", "学术", "论文", "排版"],
        "description": "符合学术规范的 A4 竖向版式，使用 Serif 字体与优雅边框",
        "payload": {
            "paperSize": "A4",
            "orientation": "portrait",
            "showLegend": True,
            "showNorthArrow": True,
            "showScaleBar": True,
            "showGrid": True,
            "style": {
                "fontFamily": "Georgia, serif",
                "titleColor": "#0f172a",
                "accentColor": "#334155",
                "graticuleColor": "#cbd5e1",
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_ly_presentation",
        "kind": "layout",
        "name": "汇报演示宽屏暗色",
        "category": "layout",
        "keywords": ["presentation", "widescreen", "dark", "汇报", "演示", "宽屏"],
        "description": "16:9 宽屏横向版式，暗色高对比主题，适合 PPT/大屏展示",
        "payload": {
            "paperSize": "16:9",
            "orientation": "landscape",
            "showLegend": True,
            "showNorthArrow": True,
            "showScaleBar": True,
            "showGrid": False,
            "style": {
                "fontFamily": "Inter, sans-serif",
                "titleColor": "#38bdf8",
                "accentColor": "#0ea5e9",
                "marginPx": 16,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_ly_minimal",
        "kind": "layout",
        "name": "极简无干扰成图",
        "category": "layout",
        "keywords": ["minimal", "clean", "export", "极简", "无边框"],
        "description": "隐藏坐标网格与修饰元素，仅保留核心图元与比例尺",
        "payload": {
            "paperSize": "A4",
            "orientation": "landscape",
            "showLegend": True,
            "showNorthArrow": False,
            "showScaleBar": True,
            "showGrid": False,
            "style": {
                "marginPx": 8,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_ly_standard_report",
        "kind": "layout",
        "name": "标准报告通用版式",
        "category": "layout",
        "keywords": ["standard", "report", "A4", "标准", "报告"],
        "description": "经典 A4 横向综合排版，完整要素组件齐全",
        "payload": {
            "paperSize": "A4",
            "orientation": "landscape",
            "showLegend": True,
            "showNorthArrow": True,
            "showScaleBar": True,
            "showGrid": True,
            "style": {
                "fontFamily": "sans-serif",
                "titleColor": "#1e293b",
                "accentColor": "#2563eb",
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    # --- THEMATIC (5) ---
    {
        "id": "tmpl_th_pop_choro",
        "kind": "thematic",
        "name": "人口密度分级图预设",
        "category": "thematic",
        "keywords": ["population", "density", "quantiles", "choropleth", "人口", "分级"],
        "description": "分位数 5 级分类，采用经典黄橙红 (YlOrRd) 渐变配色",
        "payload": {
            "variant": "choropleth",
            "method": "quantiles",
            "k": 5,
            "palette": "YlOrRd",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_econ_lisa",
        "kind": "thematic",
        "name": "经济差异 LISA 聚类",
        "category": "thematic",
        "keywords": ["economic", "lisa", "cluster", "autocorrelation", "经济", "空间聚类"],
        "description": "局部莫兰指数 (LISA) 空间自相关聚类映射 (HH/LL/HL/LH)",
        "payload": {
            "variant": "choropleth",
            "method": "lisa",
            "k": 4,
            "palette": "RdBu",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_heatmap",
        "kind": "thematic",
        "name": "热力密度分布预设",
        "category": "thematic",
        "keywords": ["heatmap", "density", "points", "热力图", "密度"],
        "description": "平滑高斯核热力图渲染预设，适合点分布密度分析",
        "payload": {
            "variant": "heatmap",
            "intensity": 0.85,
            "radius": 30,
            "heatPalette": ["#0000ff", "#00ffff", "#00ff00", "#ffff00", "#ff0000"],
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_equal_interval",
        "kind": "thematic",
        "name": "等间隔阶梯分级",
        "category": "thematic",
        "keywords": ["equal_interval", "blue", "equal", "等间隔", "阶梯"],
        "description": "等间隔 5 级数据分级，搭配 Blue 色调渐变",
        "payload": {
            "variant": "choropleth",
            "method": "equal_interval",
            "k": 5,
            "palette": "Blues",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_natural_breaks",
        "kind": "thematic",
        "name": "自然断裂点 (Jenks) 评估",
        "category": "thematic",
        "keywords": ["natural_breaks", "jenks", "viridis", "自然断裂"],
        "description": "Jenks 自然断裂法优化组内方差，Viridis 色彩感知均匀",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 5,
            "palette": "Viridis",
        },
        "is_builtin": True,
        "version": 1,
    },
    # ----------------------------------------------------------------
    # F-FE-TPL: V2 template library expansion.
    #
    # The original 18 seed templates cover the 4 base kinds. V2 extends
    # the built-in library to ~50 high-value templates across more
    # categories so the gallery has enough surface for users to find a
    # near-match for the typical GIS request.
    #
    # Each entry follows the same shape: id (stable), kind, name, category,
    # keywords (multilingual), description, payload (per-kind contract).
    # ----------------------------------------------------------------

    # --- Basemap expansions ---
    {
        "id": "tmpl_bm_hybrid_satellite",
        "kind": "basemap",
        "name": "高德卫星影像",
        "category": "basemap",
        "keywords": ["satellite", "gaode", "amap", "卫星", "高德", "影像"],
        "description": "高德卫星影像底图，国内外研究常用",
        "payload": {
            "providerId": "gaode-satellite",
            "overlays": [
                {
                    "providerId": "carto-positron-labels",
                    "vectorStyleUrl": "https://basemaps.cartocdn.com/gl/positron-nolabels-gl-style/style.json",
                }
            ],
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_bm_terrain_context",
        "kind": "basemap",
        "name": "地形晕渲底图",
        "category": "basemap",
        "keywords": ["terrain", "hillshade", "topography", "地形", "晕渲", "高程"],
        "description": "OSM 标准底图叠加地形晕渲，适合地形分析",
        "payload": {
            "providerId": "open-topo",
            "vectorStyleUrl": "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_bm_minimal_gray",
        "kind": "basemap",
        "name": "极简灰度底图",
        "category": "basemap",
        "keywords": ["minimal", "gray", "monochrome", "极简", "灰度", "单色"],
        "description": "极简灰度底图，突显上层专题数据，适合打印",
        "payload": {
            "providerId": "osm-standard",
            "rasterFilters": {
                "grayscale": 1.0,
                "contrast": 0.6,
                "brightness": 1.2,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_bm_print_light",
        "kind": "basemap",
        "name": "印刷浅色底图",
        "category": "basemap",
        "keywords": ["print", "light", "paper", "印刷", "浅色", "纸面"],
        "description": "Carto Voyager 矢量底图，色相柔和适合纸面印刷",
        "payload": {
            "providerId": "carto-voyager",
            "vectorStyleUrl": "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_bm_presentation_dark",
        "kind": "basemap",
        "name": "演示深色底图",
        "category": "basemap",
        "keywords": ["dark", "presentation", "screen", "深色", "演示", "屏幕"],
        "description": "Carto Dark Matter 底图 + 强化对比度，适合演示屏幕",
        "payload": {
            "providerId": "carto-dark",
            "rasterFilters": {
                "brightness": 0.85,
                "contrast": 1.15,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_bm_high_contrast",
        "kind": "basemap",
        "name": "高对比度底图",
        "category": "basemap",
        "keywords": ["contrast", "accessibility", "高对比度", "无障碍"],
        "description": "高对比度底图，符合 WCAG AAA 视觉无障碍",
        "payload": {
            "providerId": "carto-positron",
            "rasterFilters": {
                "contrast": 1.4,
                "saturation": 0.7,
            },
        },
        "is_builtin": True,
        "version": 1,
    },

    # --- Symbology expansions ---
    {
        "id": "tmpl_sym_city_blocks",
        "kind": "symbology",
        "name": "城市街区灰色填充",
        "category": "symbology",
        "keywords": ["city", "block", "urban", "城市", "街区", "灰色"],
        "description": "城市街区灰色多边形面，半透明",
        "payload": {
            "mode": "single",
            "geometry": "Polygon",
            "style": {
                "color": "#94a3b8",
                "fillOpacity": 0.45,
                "strokeColor": "#475569",
                "strokeWidth": 0.5,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_railway_dash",
        "kind": "symbology",
        "name": "铁路黑色横纹线",
        "category": "symbology",
        "keywords": ["railway", "rail", "track", "铁路", "轨道", "横纹"],
        "description": "铁路轨道横纹样式",
        "payload": {
            "mode": "single",
            "geometry": "LineString",
            "style": {
                "color": "#1f2937",
                "strokeWidth": 1.5,
                "lineDash": [8, 2, 1, 2],
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_river_blue",
        "kind": "symbology",
        "name": "河流蓝色线",
        "category": "symbology",
        "keywords": ["river", "water", "blue", "河流", "水域", "蓝色"],
        "description": "河流蓝色半透明粗线",
        "payload": {
            "mode": "single",
            "geometry": "LineString",
            "style": {
                "color": "#0ea5e9",
                "strokeWidth": 3.0,
                "opacity": 0.7,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_admin_boundary",
        "kind": "symbology",
        "name": "行政边界黑灰虚线",
        "category": "symbology",
        "keywords": ["admin", "boundary", "行政", "边界", "虚线"],
        "description": "行政边界黑灰虚线标准样式",
        "payload": {
            "mode": "single",
            "geometry": "LineString",
            "style": {
                "color": "#334155",
                "strokeWidth": 1.8,
                "lineDash": [6, 3, 2, 3],
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_parcel_outline",
        "kind": "symbology",
        "name": "地块细边面",
        "category": "symbology",
        "keywords": ["parcel", "cadastral", "land", "地块", "地籍", "细边"],
        "description": "地块细边面样式，浅色填充",
        "payload": {
            "mode": "single",
            "geometry": "Polygon",
            "style": {
                "color": "#fef3c7",
                "fillOpacity": 0.5,
                "strokeColor": "#92400e",
                "strokeWidth": 0.8,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_building_3d",
        "kind": "symbology",
        "name": "建筑灰色面",
        "category": "symbology",
        "keywords": ["building", "structure", "建筑", "结构", "灰色"],
        "description": "建筑物灰色面，深色边框",
        "payload": {
            "mode": "single",
            "geometry": "Polygon",
            "style": {
                "color": "#cbd5e1",
                "fillOpacity": 0.85,
                "strokeColor": "#0f172a",
                "strokeWidth": 0.4,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_route_origin_destination",
        "kind": "symbology",
        "name": "OD 路径绿色实线",
        "category": "symbology",
        "keywords": ["od", "origin", "destination", "route", "路径", "OD"],
        "description": "起点-终点路径绿色实线",
        "payload": {
            "mode": "single",
            "geometry": "LineString",
            "style": {
                "color": "#10b981",
                "strokeWidth": 3.5,
                "opacity": 0.85,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_survey_point",
        "kind": "symbology",
        "name": "调查点紫色三角",
        "category": "symbology",
        "keywords": ["survey", "point", "purple", "调查", "紫色", "三角"],
        "description": "调查点位紫色三角标记",
        "payload": {
            "mode": "single",
            "geometry": "Point",
            "style": {
                "color": "#8b5cf6",
                "radius": 7,
                "strokeColor": "#ffffff",
                "strokeWidth": 1.5,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_monitoring_station",
        "kind": "symbology",
        "name": "监测站黄色实心圆",
        "category": "symbology",
        "keywords": ["monitoring", "station", "yellow", "监测", "黄色", "圆点"],
        "description": "环境监测站黄色实心圆 + 深色轮廓",
        "payload": {
            "mode": "single",
            "geometry": "Point",
            "style": {
                "color": "#fbbf24",
                "radius": 6,
                "strokeColor": "#78350f",
                "strokeWidth": 1.5,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_fault_line",
        "kind": "symbology",
        "name": "断层红色粗线",
        "category": "symbology",
        "keywords": ["fault", "geology", "red", "断层", "地质", "红色"],
        "description": "地质断层红色粗线",
        "payload": {
            "mode": "single",
            "geometry": "LineString",
            "style": {
                "color": "#dc2626",
                "strokeWidth": 3.0,
                "lineDash": [10, 3],
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_sym_contour_line",
        "kind": "symbology",
        "name": "等高线棕色细线",
        "category": "symbology",
        "keywords": ["contour", "elevation", "brown", "等高线", "棕色"],
        "description": "等高线棕色细线样式",
        "payload": {
            "mode": "single",
            "geometry": "LineString",
            "style": {
                "color": "#92400e",
                "strokeWidth": 0.8,
                "opacity": 0.8,
            },
        },
        "is_builtin": True,
        "version": 1,
    },

    # --- Layout expansions ---
    {
        "id": "tmpl_ly_dark_report",
        "kind": "layout",
        "name": "暗色报告版式",
        "category": "layout",
        "keywords": ["dark", "report", "暗色", "报告"],
        "description": "暗色主题 A3 横向版式，适合技术报告",
        "payload": {
            "paperSize": "A3",
            "orientation": "landscape",
            "showLegend": True,
            "showNorthArrow": True,
            "showScaleBar": True,
            "showGrid": False,
            "style": {
                "fontFamily": "Inter, sans-serif",
                "titleColor": "#e2e8f0",
                "accentColor": "#38bdf8",
                "marginPx": 24,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_ly_engineering",
        "kind": "layout",
        "name": "工程勘测 A1 版式",
        "category": "layout",
        "keywords": ["engineering", "survey", "A1", "工程", "勘测"],
        "description": "A1 大幅面工程勘测版式，密集坐标网格",
        "payload": {
            "paperSize": "A1",
            "orientation": "landscape",
            "showLegend": True,
            "showNorthArrow": True,
            "showScaleBar": True,
            "showGrid": True,
            "style": {
                "fontFamily": "Roboto Mono, monospace",
                "titleColor": "#0f172a",
                "accentColor": "#475569",
                "graticuleColor": "#94a3b8",
                "marginPx": 12,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_ly_poster",
        "kind": "layout",
        "name": "海报大幅版式",
        "category": "layout",
        "keywords": ["poster", "large", "海报", "大幅"],
        "description": "A2 海报版式，强标题对比",
        "payload": {
            "paperSize": "A2",
            "orientation": "portrait",
            "showLegend": True,
            "showNorthArrow": True,
            "showScaleBar": True,
            "showGrid": False,
            "style": {
                "fontFamily": "Inter, sans-serif",
                "titleColor": "#1e3a8a",
                "accentColor": "#3b82f6",
                "marginPx": 32,
            },
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_ly_dashboard",
        "kind": "layout",
        "name": "仪表盘宽屏版式",
        "category": "layout",
        "keywords": ["dashboard", "widescreen", "仪表盘", "宽屏"],
        "description": "21:9 宽屏仪表盘版式，密集信息布局",
        "payload": {
            "paperSize": "21:9",
            "orientation": "landscape",
            "showLegend": True,
            "showNorthArrow": False,
            "showScaleBar": True,
            "showGrid": False,
            "style": {
                "fontFamily": "Inter, sans-serif",
                "titleColor": "#0ea5e9",
                "accentColor": "#0ea5e9",
                "marginPx": 8,
            },
        },
        "is_builtin": True,
        "version": 1,
    },

    # --- Thematic expansions ---
    {
        "id": "tmpl_th_gdp_quantiles",
        "kind": "thematic",
        "name": "GDP 分位数分级",
        "category": "thematic",
        "keywords": ["gdp", "economy", "quantiles", "GDP", "经济", "分位数"],
        "description": "GDP 数据分位数 6 级 + YlOrRd 渐变",
        "payload": {
            "variant": "choropleth",
            "method": "quantiles",
            "k": 6,
            "palette": "YlOrRd",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_income_equal",
        "kind": "thematic",
        "name": "收入等间隔分级",
        "category": "thematic",
        "keywords": ["income", "equal_interval", "收入", "等间隔"],
        "description": "收入数据等间隔 5 级 + Greens 渐变",
        "payload": {
            "variant": "choropleth",
            "method": "equal_interval",
            "k": 5,
            "palette": "Greens",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_land_price",
        "kind": "thematic",
        "name": "地价自然断裂分级",
        "category": "thematic",
        "keywords": ["land", "price", "natural_breaks", "地价", "自然断裂"],
        "description": "土地价格 Jenks 自然断裂 7 级 + OrRd 渐变",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 7,
            "palette": "OrRd",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_slope",
        "kind": "thematic",
        "name": "坡度分级图",
        "category": "thematic",
        "keywords": ["slope", "terrain", "dem", "坡度", "地形"],
        "description": "DEM 坡度计算 + 6 级 YlOrBr 渐变",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 6,
            "palette": "YlOrBr",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_temperature",
        "kind": "thematic",
        "name": "温度分布分级",
        "category": "thematic",
        "keywords": ["temperature", "climate", "温度", "气候"],
        "description": "温度数据分位数 6 级 + RdYlBu 发散",
        "payload": {
            "variant": "choropleth",
            "method": "quantiles",
            "k": 6,
            "palette": "RdYlBu",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_rainfall",
        "kind": "thematic",
        "name": "降雨量分布",
        "category": "thematic",
        "keywords": ["rainfall", "precipitation", "降雨", "降水"],
        "description": "降雨量数据 6 级 + Blues 渐变",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 6,
            "palette": "Blues",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_vegetation_index",
        "kind": "thematic",
        "name": "植被指数分级",
        "category": "thematic",
        "keywords": ["ndvi", "vegetation", "植被指数", "NDVI"],
        "description": "NDVI 指数 7 级 + Greens 渐变",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 7,
            "palette": "Greens",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_travel_time",
        "kind": "thematic",
        "name": "出行时间等高线",
        "category": "thematic",
        "keywords": ["travel", "time", "accessibility", "出行", "时间"],
        "description": "出行时间分位数 5 级 + Plasma 渐变",
        "payload": {
            "variant": "choropleth",
            "method": "quantiles",
            "k": 5,
            "palette": "Plasma",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_risk_score",
        "kind": "thematic",
        "name": "综合风险评分",
        "category": "thematic",
        "keywords": ["risk", "score", "composite", "风险", "综合"],
        "description": "综合风险评分 5 级 + YlOrRd 渐变",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 5,
            "palette": "YlOrRd",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_pollution",
        "kind": "thematic",
        "name": "污染浓度分级",
        "category": "thematic",
        "keywords": ["pollution", "air", "water", "污染", "空气", "水质"],
        "description": "污染浓度 5 级 + Purples 渐变",
        "payload": {
            "variant": "choropleth",
            "method": "quantiles",
            "k": 5,
            "palette": "Purples",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_change_intensity",
        "kind": "thematic",
        "name": "变化强度分级",
        "category": "thematic",
        "keywords": ["change", "intensity", "变化", "强度"],
        "description": "变化强度 5 级 + RdBu 发散",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 5,
            "palette": "RdBu",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_growth_decline",
        "kind": "thematic",
        "name": "增长/下降发散",
        "category": "thematic",
        "keywords": ["growth", "decline", "diverging", "增长", "下降"],
        "description": "增长率 RdBu 发散 5 级 (负值-蓝色，正值-红色)",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 5,
            "palette": "RdBu",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_anomaly",
        "kind": "thematic",
        "name": "异常值分级",
        "category": "thematic",
        "keywords": ["anomaly", "outlier", "异常"],
        "description": "异常值分位数 5 级 + RdYlBu 发散",
        "payload": {
            "variant": "choropleth",
            "method": "quantiles",
            "k": 5,
            "palette": "RdYlBu",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_residual",
        "kind": "thematic",
        "name": "残差分级",
        "category": "thematic",
        "keywords": ["residual", "regression", "残差"],
        "description": "回归残差 5 级 + RdBu 发散 (零居中)",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 5,
            "palette": "RdBu",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_ndvi_choro",
        "kind": "thematic",
        "name": "NDVI 植被指数分级",
        "category": "thematic",
        "keywords": ["ndvi", "ndwi", "nbr", "evi", "index", "植被", "指数"],
        "description": "NDVI / NDWI / NBR / EVI 通用指数分级 6 级 + RdYlGn",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 6,
            "palette": "RdYlGn",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_burn_severity",
        "kind": "thematic",
        "name": "燃烧严重度分级",
        "category": "thematic",
        "keywords": ["burn", "severity", "fire", "燃烧", "严重", "火灾"],
        "description": "燃烧严重度 dNBR 5 级 + YlOrRd",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 5,
            "palette": "YlOrRd",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_water_extraction",
        "kind": "thematic",
        "name": "水体提取分级",
        "category": "thematic",
        "keywords": ["water", "ndwi", "extraction", "水体", "提取"],
        "description": "NDWI 水体提取 5 级 + Blues",
        "payload": {
            "variant": "choropleth",
            "method": "natural_breaks",
            "k": 5,
            "palette": "Blues",
        },
        "is_builtin": True,
        "version": 1,
    },

    # --- Categorical thematic expansions ---
    {
        "id": "tmpl_th_zoning",
        "kind": "thematic",
        "name": "规划用地分类",
        "category": "thematic",
        "keywords": ["zoning", "planning", "landuse", "规划", "用地"],
        "description": "规划用地分类 (R/C/M/G/W) 专用配色",
        "payload": {
            "variant": "choropleth",
            "method": "categorical",
            "k": 5,
            "palette": "Set2",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_soil_type",
        "kind": "thematic",
        "name": "土壤类型分类",
        "category": "thematic",
        "keywords": ["soil", "type", "土壤", "分类"],
        "description": "土壤类型分类 (砂土/壤土/粘土等) 配色",
        "payload": {
            "variant": "choropleth",
            "method": "categorical",
            "k": 6,
            "palette": "Set3",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_geology",
        "kind": "thematic",
        "name": "地质岩性分类",
        "category": "thematic",
        "keywords": ["geology", "lithology", "地质", "岩性"],
        "description": "地质岩性分类配色",
        "payload": {
            "variant": "choropleth",
            "method": "categorical",
            "k": 8,
            "palette": "Tab10",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_facility_type",
        "kind": "thematic",
        "name": "公共设施分类",
        "category": "thematic",
        "keywords": ["facility", "type", "公共", "设施"],
        "description": "公共设施类型分类配色",
        "payload": {
            "variant": "choropleth",
            "method": "categorical",
            "k": 7,
            "palette": "Pastel1",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_risk_category",
        "kind": "thematic",
        "name": "风险等级分类",
        "category": "thematic",
        "keywords": ["risk", "category", "风险", "等级"],
        "description": "风险等级 (低/中/高/极高) 分类配色",
        "payload": {
            "variant": "choropleth",
            "method": "categorical",
            "k": 4,
            "palette": "YlOrRd",
        },
        "is_builtin": True,
        "version": 1,
    },
    {
        "id": "tmpl_th_classification",
        "kind": "thematic",
        "name": "遥感分类结果",
        "category": "thematic",
        "keywords": ["classification", "remote_sensing", "分类", "遥感"],
        "description": "遥感影像分类结果 (10 类以下) 配色",
        "payload": {
            "variant": "choropleth",
            "method": "categorical",
            "k": 10,
            "palette": "Tab20",
        },
        "is_builtin": True,
        "version": 1,
    },
]
