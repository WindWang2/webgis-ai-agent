"""地图模型库（Map Model Library）—— 制图模型的权威目录。

把主流地理可视化框架的**能力枚举**收敛成一份机器可读目录，供 GIS
Harness 的 recipe/planner 引用：

- MapLibre Style Spec 图层类型（fill / line / circle / symbol / raster /
  heatmap / fill-extrusion / hillshade / color-relief / background）
  —— https://maplibre.org/maplibre-style-spec/layers/
- deck.gl 图层目录（原始要素层 vs 聚合层二分）
  —— https://deck.gl/docs/api-reference/layers
- kepler.gl 图层类型 + 视觉通道（color/size channel）模型
  —— https://docs.kepler.gl/docs/user-guides/c-types-of-layers
- QGIS 矢量渲染器模式（single/categorized/graduated/rule-based/heatmap/
  point-displacement/2.5D…）与 graduated 分类方法清单
  —— https://docs.qgis.org/latest/en/docs/user_manual/working_with_vector/vector_properties.html
- GeoDa 分类地图体系（quantile/natural breaks/equal interval/percentile/
  box map hinge15/30/std dev/custom）
  —— https://geodacenter.github.io/workbook/3a_mapping/lab3a.html

设计约束：
- **描述性目录，不是第二套执行引擎**。数值分级只由
  ``CartographyService.classify`` 计算（thematic_spec.py 的单一权威约束）；
  本库只登记方法元数据（适用场景/权威出处），并引用实现位置。
- ``runtime_status="planned"`` 诚实标记尚未接渲染链路的模型（如 OD 弧线、
  3D 挤出），plan 阶段可见但不会假装可用。
- ``validate_model_library()`` 在单测里跑完整性断言：maplibre 图层类型
  必须在 Style Spec 枚举内、palette/classifier 引用必须存在。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ─── MapLibre Style Spec 图层类型（当前稳定版全量枚举）───────────────────
MAPLIBRE_LAYER_TYPES = frozenset({
    "background", "fill", "line", "symbol", "raster", "circle",
    "heatmap", "fill-extrusion", "hillshade", "color-relief",
})


ClassificationId = Literal[
    "none", "categorical", "graduated",
    "quantiles", "equal_interval", "natural_breaks", "std_dev", "head_tail",
]


class ClassificationMethod(BaseModel):
    """数值分级方法元数据（算法本体在 CartographyService.classify）。"""
    id: str
    name_zh: str
    authority: str                    # 出处（论文/GIS 软件）
    best_for_zh: str                  # 适用数据形态
    caveat_zh: str = ""               # 误用警告
    implemented_in: str = (
        "app.services.cartography_service.CartographyService.classify"
    )
    default_k: int = 5


CLASSIFICATION_METHODS: Dict[str, ClassificationMethod] = {
    m.id: m for m in [
        ClassificationMethod(
            id="quantiles", name_zh="分位数法",
            authority="QGIS『Equal Count (Quantile)』；GeoDa Quantile map",
            best_for_zh="均匀/中等偏态数据；每类样本数均衡的排序对比图",
            caveat_zh="值域跨度大时同类内差异可能很大；相同值多时类大小会失衡",
        ),
        ClassificationMethod(
            id="equal_interval", name_zh="等间距法",
            authority="QGIS『Equal Interval』；GeoDa Equal Intervals map",
            best_for_zh="近似均匀分布的数据；需要和直方图直觉对应时",
            caveat_zh="右偏数据会把大多数样本压进第一类（城市计数数据的常见坑）",
        ),
        ClassificationMethod(
            id="natural_breaks", name_zh="自然断裂点 (Jenks)",
            authority="Jenks 1967；Fisher-Jenks 最小方差 DP；QGIS/GeoDa Natural Breaks",
            best_for_zh="默认首选：组内方差最小化，能隔离极值簇",
            caveat_zh="O(n·k) 动态规划，超大样本先抽样；类边界不可复现性弱于分位数",
        ),
        ClassificationMethod(
            id="std_dev", name_zh="标准差分级",
            authority="QGIS『Standard Deviation』（0.5 SD 步进）；GeoDa Std Dev map",
            best_for_zh="围绕均值波动、需要突出异常高/低的统计面（z 值、比率）",
            caveat_zh="强偏态下均值被拉偏，先用 natural_breaks 看形态再选它",
            default_k=6,
        ),
        ClassificationMethod(
            id="head_tail", name_zh="头尾断裂法",
            authority="Jiang (2013) Head/Tail Breaks——专为重尾（长尾）分布设计",
            best_for_zh="城市计数/等级数据（POI 数、路网密度）：少数高值 + 大量低值",
            caveat_zh="类别数由数据自身决定可能少于请求的 k（这是特性不是缺陷）；近均匀数据几乎不产生断裂",
        ),
    ]
}


class PaletteKind(BaseModel):
    """调色板语义家族（ColorBrewer 三族 + 感知均匀族）。"""
    palette: str
    kind: Literal["sequential", "diverging", "qualitative", "perceptual_uniform"]
    colorblind_safe: bool = False
    note_zh: str = ""


PALETTE_KINDS: Dict[str, PaletteKind] = {
    p.palette: p for p in [
        PaletteKind(palette="YlOrRd", kind="sequential", colorblind_safe=True,
                    note_zh="计数/密度通用首推（ColorBrewer print-safe）"),
        PaletteKind(palette="Blues", kind="sequential", colorblind_safe=True),
        PaletteKind(palette="Greens", kind="sequential", colorblind_safe=True),
        PaletteKind(palette="Reds", kind="sequential", colorblind_safe=True),
        PaletteKind(palette="Oranges", kind="sequential", colorblind_safe=True),
        PaletteKind(palette="Purples", kind="sequential", colorblind_safe=True),
        PaletteKind(palette="RdYlGn", kind="diverging",
                    note_zh="以有意义的中点为中心（阈值/均值）；红绿色盲不友好，正式出版用 PuOr/RdBu"),
        PaletteKind(palette="RdBu", kind="diverging", colorblind_safe=True,
                    note_zh="正负偏差/相关性的安全发散方案"),
        PaletteKind(palette="Set1", kind="qualitative",
                    note_zh="最多 9 类；类别专题默认"),
        PaletteKind(palette="Set2", kind="qualitative", colorblind_safe=True,
                    note_zh="最多 8 类；柔和底色，适合叠加符号"),
        PaletteKind(palette="Dark2", kind="qualitative", colorblind_safe=True,
                    note_zh="最多 8 类；深色高区分度"),
        PaletteKind(palette="Pastel1", kind="qualitative",
                    note_zh="最多 9 类；大面积填色的柔和方案"),
        PaletteKind(palette="Viridis", kind="perceptual_uniform", colorblind_safe=True,
                    note_zh="感知均匀 + 色盲安全 + 灰度打印保真"),
        PaletteKind(palette="Magma", kind="perceptual_uniform", colorblind_safe=True),
        PaletteKind(palette="Inferno", kind="perceptual_uniform", colorblind_safe=True),
        PaletteKind(palette="Plasma", kind="perceptual_uniform", colorblind_safe=True),
    ]
}


def palettes_for_kind(kind: str) -> List[str]:
    """按语义族取 palette id 列表（确定性排序）。"""
    return sorted(pid for pid, p in PALETTE_KINDS.items() if p.kind == kind)


class MapModel(BaseModel):
    """单个制图模型：用途 × 几何 × 运行时映射 × 配套表达。"""
    id: str
    name_zh: str
    purpose_zh: str = ""
    geometry_kinds: List[str] = Field(default_factory=list)  # point/line/polygon/raster
    maplibre_layer_type: str                                  # Style Spec 枚举内
    classification: ClassificationId = "none"
    color_scheme_kind: Literal["sequential", "diverging", "qualitative",
                               "perceptual_uniform", "none"] = "none"
    default_palette: str = ""
    recommended_classifiers: List[str] = Field(default_factory=list)
    # 框架对照（文档性知识，便于跨运行时迁移/导出器对齐）
    deck_gl_layer: str = ""
    kepler_layer: str = ""
    qgis_renderer: str = ""
    runtime_status: Literal["native", "planned"] = "native"
    aliases: List[str] = Field(default_factory=list)          # 旧词汇别名
    pitfalls_zh: List[str] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)


_MAPLIBRE_SPEC_URL = "https://maplibre.org/maplibre-style-spec/layers/"
_DECKGL_URL = "https://deck.gl/docs/api-reference/layers"
_KEPLER_URL = "https://docs.kepler.gl/docs/user-guides/c-types-of-layers"
_QGIS_URL = ("https://docs.qgis.org/latest/en/docs/user_manual/"
             "working_with_vector/vector_properties.html")
_GEODA_URL = "https://geodacenter.github.io/workbook/3a_mapping/lab3a.html"


SEED_MAP_MODELS: List[MapModel] = [
    MapModel(
        id="visual_heatmap", name_zh="视觉热力图",
        purpose_zh="点密度的一眼可读概览；回答『大概哪儿密』而非精确数值",
        geometry_kinds=["point"],
        maplibre_layer_type="heatmap",
        color_scheme_kind="perceptual_uniform", default_palette="classic",
        deck_gl_layer="HeatmapLayer", kepler_layer="heatmap",
        qgis_renderer="heatmap",
        aliases=["density_overview"],
        pitfalls_zh=[
            "heatmap-radius 单位是屏幕像素（Style Spec 明确），分析带宽是米——"
            "两者只能经 heatmap_contract 归一化边界转换，禁止混用",
            "样本过少时热力核无统计意义（min_points 门槛 + INSUFFICIENT_POINTS 回退）",
            # #724: Web-Mercator 面积随纬度膨胀 —— 相同 radius_px/bandwidth_m
            # 在高纬覆盖的地面米数比赤道少 ~cos(lat) 倍（60°N 约一半），
            # 跨纬度/高纬城市（乌鲁木齐、哈尔滨）的密度对比被系统性放大。
            "纬度失真：Web-Mercator 下相同 radius_px/bandwidth_m 在高纬覆盖的"
            "地面范围比赤道小 ~cos(lat) 倍——跨纬度密度对比需按 cos(lat) 校正"
            "或避免直接比较",
        ],
        sources=[_MAPLIBRE_SPEC_URL],
    ),
    MapModel(
        id="administrative_choropleth", name_zh="行政分级统计图",
        purpose_zh="按行政区聚合数值后分级填色；回答『各区多少/排名』",
        geometry_kinds=["polygon"],
        maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="YlOrRd",
        recommended_classifiers=["natural_breaks", "quantiles", "equal_interval",
                                 "std_dev"],
        default_class_count=5,
        deck_gl_layer="PolygonLayer(+CPU 分级)", kepler_layer="geojson",
        qgis_renderer="graduated",
        aliases=["graduated_choropleth", "choropleth"],
        pitfalls_zh=[
            "大区县面积大≠数值大，面积偏差严重时考虑密度归一或 cartogram",
            "分级数建议 4-7（默认 5）；类数 >7 人眼不可辨",
            "数据有自然中点（如正/负增长率）时应换 diverging 色系并以中点为中心",
        ],
        sources=[_QGIS_URL, _GEODA_URL],
    ),
    MapModel(
        id="administrative_aggregation", name_zh="行政区聚合参考层",
        purpose_zh="聚合结果的面状呈现（常作 choropleth 的同源参考/边界层）",
        geometry_kinds=["polygon"],
        maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="Blues",
        qgis_renderer="graduated",
        sources=[_QGIS_URL],
    ),
    MapModel(
        id="aggregate_grid", name_zh="格网聚合图（H3/渔网）",
        purpose_zh="点计数入 H3 六边形/方格后分级填色；比热力更可量化、比行政面更均质",
        geometry_kinds=["point"],
        maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="YlOrRd",
        recommended_classifiers=["quantiles", "natural_breaks"],
        default_class_count=5,
        deck_gl_layer="HexagonLayer/GridLayer(CPU binning)",
        kepler_layer="hexbin / grid",
        qgis_renderer="graduated（前置 fishnet/h3 聚合）",
        aliases=["hexbin", "grid_binning"],
        pitfalls_zh=[
            "网格统计意义依赖分辨率：分辨率过高每格 0-1 个点，过低失去局部性",
            "空网格应透明而非着最低档色（避免把『没有数据』画成『数值为零』）",
            "样本 <20 时网格噪声大于信号，回退点图/热力",
        ],
        sources=[_DECKGL_URL, _KEPLER_URL],
    ),
    MapModel(
        id="proportional_symbol", name_zh="比例符号图（气泡图）",
        purpose_zh="用圆面积编码数值大小；适合点数少但每点有权重值的场景（热力的合法替代）",
        geometry_kinds=["point"],
        maplibre_layer_type="circle",
        classification="none",
        color_scheme_kind="sequential", default_palette="Blues",
        deck_gl_layer="ScatterplotLayer", kepler_layer="point(size channel)",
        qgis_renderer="single symbol + 数据定义覆盖（辅助大小）",
        aliases=["bubble_map", "graduated_symbol"],
        pitfalls_zh=[
            "面积比例律：radius ∝ sqrt(value)；直接线性映射半径会让大值被平方级夸大",
            "重叠气泡按值降序绘制（小的在上）；超过 ~50 点改用聚合网格",
        ],
        sources=[_MAPLIBRE_SPEC_URL, _KEPLER_URL],
    ),
    MapModel(
        id="categorical_thematic", name_zh="分类专题图",
        purpose_zh="唯一值/类别字段着色（QGIS Categorized / GeoDa Unique Value）",
        geometry_kinds=["point", "polygon", "line"],
        maplibre_layer_type="fill",
        classification="categorical",
        color_scheme_kind="qualitative", default_palette="Set1",
        deck_gl_layer="GeoJsonLayer(match 表达式)", kepler_layer="geojson(color by category)",
        qgis_renderer="categorized",
        aliases=["unique_values"],
        pitfalls_zh=[
            "定性色系上限 ~9-12 类，超出的长尾类别并入『其他』",
            "顺序语义的字段（低/中/高）不该用定性色，改 sequential 渐变",
        ],
        sources=[_QGIS_URL, _GEODA_URL],
    ),
    MapModel(
        id="hotspot_overlay", name_zh="热点显著性图层",
        purpose_zh="Gi*/LISA 显著性聚类标注；结论必须有统计检验背书",
        geometry_kinds=["point", "polygon"],
        maplibre_layer_type="fill",
        classification="categorical",
        color_scheme_kind="diverging", default_palette="RdBu",
        qgis_renderer="rule-based（显著性阈值）",
        aliases=["lisa_cluster"],
        pitfalls_zh=[
            "HH/LL/HL/LH/NS 五类语义固定，颜色必须红蓝发散且 NS 低饱和置灰",
            "样本稀疏时 Gi* 多重比较校正不可省",
        ],
        sources=[_GEODA_URL],
    ),
    MapModel(
        id="proximity_overlay", name_zh="邻近/缓冲叠加",
        purpose_zh="缓冲面 + 落点叠加，回答『N 米范围内有什么』",
        geometry_kinds=["point", "line", "polygon"],
        maplibre_layer_type="fill",
        color_scheme_kind="qualitative", default_palette="Set2",
        deck_gl_layer="PolygonLayer(buffer)", kepler_layer="geojson",
        qgis_renderer="single symbol + 缓冲几何",
        sources=[_DECKGL_URL],
    ),
    MapModel(
        id="raster_surface", name_zh="栅格连续色面",
        purpose_zh="DEM/遥感指数/气象场的连续色带渲染",
        geometry_kinds=["raster"],
        maplibre_layer_type="raster",
        color_scheme_kind="perceptual_uniform", default_palette="Viridis",
        qgis_renderer="paletted/singleband pseudocolor",
        pitfalls_zh=["连续色条（colorbar）而非离散图例"],
        sources=[_MAPLIBRE_SPEC_URL],
    ),
    MapModel(
        id="simple_point_map", name_zh="轻量点图",
        purpose_zh="『给我看看』级浏览：统一符号点图，不过度分析",
        geometry_kinds=["point"],
        maplibre_layer_type="circle",
        color_scheme_kind="none",
        qgis_renderer="single symbol",
        sources=[_QGIS_URL],
    ),
    MapModel(
        id="point_overlay", name_zh="点叠加层",
        purpose_zh="在主专题之上叠参照点（半透明、细边），保持主表达的可读性",
        geometry_kinds=["point"],
        maplibre_layer_type="circle",
        color_scheme_kind="none",
        qgis_renderer="single symbol",
        sources=[_QGIS_URL],
    ),
    # ── 目录完整但运行时未接线（诚实标记 planned）──────────────────────
    MapModel(
        id="flow_od_arc", name_zh="OD 流向弧线图",
        purpose_zh="起讫对之间的流量/迁徙表达",
        geometry_kinds=["point"],
        maplibre_layer_type="line",
        color_scheme_kind="sequential", default_palette="Plasma",
        deck_gl_layer="ArcLayer", kepler_layer="arc / flow(experimental)",
        runtime_status="planned",
        pitfalls_zh=["需要 OD 结构化输入（起终点坐标对 + 权重），当前工具面未提供"],
        sources=[_DECKGL_URL, _KEPLER_URL],
    ),
    MapModel(
        id="extrusion_3d", name_zh="3D 挤出柱状图",
        purpose_zh="以高度（米）编码数值的多边形立体表达",
        geometry_kinds=["polygon"],
        maplibre_layer_type="fill-extrusion",
        color_scheme_kind="sequential", default_palette="Oranges",
        deck_gl_layer="PolygonLayer(extruded)/HexagonLayer(3D)",
        kepler_layer="hexbin(height channel) / 3d building",
        qgis_renderer="2.5D",
        runtime_status="planned",
        pitfalls_zh=["高度单位是米（fill-extrusion-height），倾角大时遮挡严重需配合相机俯仰限制"],
        sources=[_MAPLIBRE_SPEC_URL, _QGIS_URL],
    ),
    MapModel(
        id="isoline_contour", name_zh="等值线/等值面",
        purpose_zh="KDE/插值场的等阈值线带（ContourLayer 思路）",
        geometry_kinds=["point", "raster"],
        maplibre_layer_type="line",
        color_scheme_kind="sequential", default_palette="Inferno",
        deck_gl_layer="ContourLayer", kepler_layer="contour",
        runtime_status="planned",
        pitfalls_zh=["等值距应为整数值以便读图；带宽过窄会产生锯齿"],
        sources=[_DECKGL_URL, _KEPLER_URL],
    ),
]


class MapModelRegistry:
    """Indexed model registry（id + 别名 O(1)）。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, MapModel] = {}
        self._alias: Dict[str, str] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        self._alias.clear()
        for model in SEED_MAP_MODELS:
            self.register(model)

    def register(self, model: MapModel) -> None:
        if model.id in self._by_id:
            return
        self._by_id[model.id] = model
        for alias in model.aliases:
            self._alias.setdefault(alias, model.id)

    def resolve(self, model_or_alias_id: str) -> Optional[MapModel]:
        canon = self._alias.get(model_or_alias_id, model_or_alias_id)
        return self._by_id.get(canon)

    def get(self, model_id: str) -> Optional[MapModel]:
        return self._by_id.get(model_id)

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def native_ids(self) -> List[str]:
        return sorted(m.id for m in self._by_id.values() if m.runtime_status == "native")

    def planned_ids(self) -> List[str]:
        return sorted(m.id for m in self._by_id.values() if m.runtime_status == "planned")


_registry: Optional[MapModelRegistry] = None


def get_map_model_registry() -> MapModelRegistry:
    global _registry
    if _registry is None:
        _registry = MapModelRegistry()
        _registry.load_builtins()
    return _registry


def reset_map_model_registry() -> None:
    global _registry
    _registry = None


def get_map_model(model_or_alias_id: str) -> Optional[MapModel]:
    return get_map_model_registry().resolve(model_or_alias_id)


def validate_model_library() -> List[str]:
    """完整性断言：返回违规列表（空列表 = 通过）。

    - maplibre_layer_type 必须在 Style Spec 枚举内；
    - default_palette 必须存在于 COLOR_PALETTES 或原生热力色带；
    - recommended_classifiers / classification 必须在 CLASSIFICATION_METHODS；
    - PALETTE_KINDS 必须覆盖 COLOR_PALETTES 全部键（前后端镜像之外的新
      色板漏登记者会被抓出来）。
    """
    from app.lib.cartography.palettes import COLOR_PALETTES, NATIVE_HEATMAP_COLORS

    issues: List[str] = []
    known_palettes = set(COLOR_PALETTES) | set(NATIVE_HEATMAP_COLORS)
    registry = get_map_model_registry()

    for model in registry._by_id.values():
        mid = model.maplibre_layer_type
        if mid not in MAPLIBRE_LAYER_TYPES:
            issues.append(f"{model.id}: maplibre_layer_type '{mid}' 不在 Style Spec 枚举内")
        if model.default_palette and model.default_palette not in known_palettes:
            issues.append(f"{model.id}: default_palette '{model.default_palette}' 未注册")
        for cid in model.recommended_classifiers:
            if cid not in CLASSIFICATION_METHODS:
                issues.append(f"{model.id}: classifier '{cid}' 未在 CLASSIFICATION_METHODS 注册")
        if model.classification not in ("none", "categorical", "graduated") and (
            model.classification not in CLASSIFICATION_METHODS
        ):
            issues.append(f"{model.id}: classification '{model.classification}' 非法")

    for pid in COLOR_PALETTES:
        if pid not in PALETTE_KINDS:
            issues.append(f"palette '{pid}' 未登记 PALETTE_KINDS 语义族")
    return issues


__all__ = [
    "MAPLIBRE_LAYER_TYPES",
    "ClassificationMethod",
    "CLASSIFICATION_METHODS",
    "PaletteKind",
    "PALETTE_KINDS",
    "palettes_for_kind",
    "MapModel",
    "MapModelRegistry",
    "SEED_MAP_MODELS",
    "get_map_model_registry",
    "reset_map_model_registry",
    "get_map_model",
    "validate_model_library",
]
