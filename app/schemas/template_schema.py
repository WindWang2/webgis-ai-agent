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
    field: str
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
            "field": "landuse",
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
]
