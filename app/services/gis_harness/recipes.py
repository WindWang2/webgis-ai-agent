"""CartographyRecipe —— 「怎么制作这种地图」的制图方法库。

与既有 template 体系的关系：template（basemap/symbology/layout/thematic/
composite）描述「地图是什么样式」，recipe 描述「这类意图该怎么制图」——
选什么分析、什么主/辅专题表达、什么组件、什么回退。Recipe 不执行工具、
不含硬编码工具调用序列（那由 Tool Resolver 按能力面解析），因此工具
替换时 recipe 无需重写。

eligibility / fallback 全部是代码侧确定性检查（几何兼容、最小点数、
必需字段），LLM 只能**建议** recipe，不能取代检查。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

GeometryType = Literal["Point", "MultiPoint", "LineString", "MultiLineString",
                       "Polygon", "MultiPolygon", "GeometryCollection"]

PointGeometries = ("Point", "MultiPoint")
PolygonGeometries = ("Polygon", "MultiPolygon")
LineGeometries = ("LineString", "MultiLineString")


class EligibilityRule(BaseModel):
    """单条资格规则（确定性阈值检查）。"""
    element: str                     # 被约束的制图元素，如 visual_heatmap
    # 点数检查：min_points=None + check_points=True → 用注入的默认阈值
    # （HEATMAP_MIN_POINTS 设置，与工具/converter 守卫同源，不漂移）。
    check_points: bool = False
    min_points: Optional[int] = None
    requires_geometry: Optional[List[str]] = None   # 允许几何类别
    requires_fields: Optional[List[str]] = None
    reason_code: str = ""            # 不满足时的回退原因码


class RecipeFallback(BaseModel):
    """确定性回退声明。"""
    when: str                        # 人类可读条件（审计用）
    reason_code: str                 # 机器可读原因码
    use: Optional[str] = None        # 回退到的制图元素
    disable: Optional[List[str]] = None  # 禁用的元素列表


class CartographyRecipe(BaseModel):
    """制图方法契约。"""
    id: str
    name: str
    description: str = ""
    intent_tasks: List[str] = []
    intent_cartography: List[str] = []   # 匹配 cartography_intents（加分项）
    required_geometry: List[str] = []
    allowed_geometry: List[str] = []
    required_fields: List[str] = []
    optional_fields: List[str] = []
    eligibility: List[EligibilityRule] = []
    preferred_analysis: List[str] = []   # 能力 id（capability，非工具名）
    optional_analysis: List[str] = []
    # 任务条件能力（v3）：intent task → 专属补充能力。同一 recipe 服务多个
    # task（raster_distribution 兼任 change_detection）时，只有命中该 task
    # 的计划才并入 —— 与 optional_analysis 的无条件并入语义不同，避免污染
    # 共享 recipe 的其他 task 产品。值域同 preferred/optional（capability
    # id，registry_validation 校验存在性）。
    task_optional_analysis: Dict[str, List[str]] = Field(default_factory=dict)
    primary_cartography: str = ""
    secondary_cartography: List[str] = []
    default_components: List[str] = []   # 组件类型列表
    fallbacks: List[RecipeFallback] = []
    validation_rules: List[str] = []
    export_profile: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 50                  # 同分候选时的稳定排序


class DisabledElement(BaseModel):
    element: str
    reason_code: str
    evidence: Dict[str, Any] = Field(default_factory=dict)


class FallbackDecision(BaseModel):
    """一次实际发生的回退（结构化证据：from/to/reason/evidence）。"""
    from_element: str
    to_element: str = ""
    reason_code: str
    evidence: Dict[str, Any] = Field(default_factory=dict)


class EligibilityReport(BaseModel):
    """对某 recipe + 真实数据 profile 的确定性资格判定。"""
    recipe_id: str
    eligible: bool = True
    disabled: List[DisabledElement] = []
    fallbacks: List[FallbackDecision] = []
    checks: List[Dict[str, Any]] = []       # 全部检查记录（含通过项）


def _geometry_category(geometry_types: Optional[List[str]]) -> str:
    """把 geometryTypes 列表归到主导类别 point/line/polygon/unknown。"""
    if not geometry_types:
        return "unknown"
    counts = {"point": 0, "line": 0, "polygon": 0}
    for gt in geometry_types:
        if gt in PointGeometries:
            counts["point"] += 1
        elif gt in LineGeometries:
            counts["line"] += 1
        elif gt in PolygonGeometries:
            counts["polygon"] += 1
    active = [k for k, v in counts.items() if v > 0]
    if not active:
        return "unknown"
    return max(counts, key=lambda c: counts[c])


def check_eligibility(
    recipe: CartographyRecipe,
    *,
    profile: Optional[Dict[str, Any]] = None,
    min_points_default: int = 10,
) -> EligibilityReport:
    """代码侧确定性资格检查 —— 数据回来后必须重新验证（anti: 一锤定音）。

    profile 形态复用 Spatial Meta Profile（featureCount / geometryTypes /
    fields），来自 ref descriptor 派生，零全量扫描。
    """
    report = EligibilityReport(recipe_id=recipe.id)
    profile = profile if isinstance(profile, dict) else {}

    geom_types = profile.get("geometryTypes") or []
    geom_cat = _geometry_category(list(geom_types) if isinstance(geom_types, (list, tuple)) else [])
    feature_count = profile.get("featureCount")
    if not isinstance(feature_count, int):
        # JSON 往返可能给 float（如 1260.0）——数值强制收敛，避免大样本被
        # 误判为 0 点而触发 INSUFFICIENT_POINTS。
        try:
            feature_count = int(feature_count) if isinstance(feature_count, (int, float)) else 0
        except (TypeError, ValueError):
            feature_count = 0
    fields = profile.get("fields") or {}
    field_names = set(fields.keys()) if isinstance(fields, dict) else set()

    # recipe 级几何要求
    if recipe.required_geometry:
        required_cats = {
            "Point": "point", "MultiPoint": "point",
            "LineString": "line", "MultiLineString": "line",
            "Polygon": "polygon", "MultiPolygon": "polygon",
        }
        need = {required_cats.get(g, g.lower()) for g in recipe.required_geometry}
        if geom_cat != "unknown" and geom_cat not in need:
            report.eligible = False
            report.disabled.append(DisabledElement(
                element="recipe",
                reason_code="GEOMETRY_NOT_SUPPORTED",
                evidence={"dominant_geometry": geom_cat, "required": sorted(need)},
            ))
            report.checks.append({
                "check": "recipe_geometry", "passed": False,
                "dominant": geom_cat, "required": sorted(need),
            })
        else:
            report.checks.append({
                "check": "recipe_geometry", "passed": True,
                "dominant": geom_cat, "required": sorted(need),
            })

    # 元素级资格规则（如 native heatmap 的最小点数）
    for rule in recipe.eligibility:
        check: Dict[str, Any] = {"check": rule.element, "passed": True}
        if rule.requires_geometry:
            required_cats = {
                "Point": "point", "MultiPoint": "point",
                "LineString": "line", "MultiLineString": "line",
                "Polygon": "polygon", "MultiPolygon": "polygon",
            }
            need = {required_cats.get(g, g.lower()) for g in rule.requires_geometry}
            ok = geom_cat in need if geom_cat != "unknown" else False
            check["geometry"] = {"dominant": geom_cat, "required": sorted(need)}
            if not ok:
                report.disabled.append(DisabledElement(
                    element=rule.element, reason_code="GEOMETRY_NOT_SUPPORTED",
                    evidence={"dominant_geometry": geom_cat, "required": sorted(need)},
                ))
                check["passed"] = False
        if rule.check_points or rule.min_points is not None:
            # min_points=None（默认语义）→ 调用方注入的阈值（与工具/converter
            # 同源的 HEATMAP_MIN_POINTS 设置）—— recipe 与执行侧不漂移。
            threshold = (
                rule.min_points if rule.min_points is not None else min_points_default
            )
            ok = feature_count >= threshold
            check["min_points"] = {"count": feature_count, "min": threshold}
            if not ok:
                report.disabled.append(DisabledElement(
                    element=rule.element,
                    reason_code=rule.reason_code or "INSUFFICIENT_POINTS",
                    evidence={"point_count": feature_count, "min_points": threshold},
                ))
                check["passed"] = False
        if rule.requires_fields:
            missing = [f for f in rule.requires_fields if f not in field_names]
            if missing:
                report.disabled.append(DisabledElement(
                    element=rule.element,
                    reason_code=rule.reason_code or "MISSING_FIELDS",
                    evidence={"missing": missing},
                ))
                check["passed"] = False
                check["fields"] = {"missing": missing}
        report.checks.append(check)

    # 声明式 fallback → 结构化决策记录：从被禁元素出发，按 reason_code 匹配
    # 声明的回退（此前按 fb.use 比对被禁元素名——那是回退目标永不相等，
    # 声明式记录从未生效）。
    for disabled in report.disabled:
        for fb in recipe.fallbacks:
            if fb.reason_code and fb.reason_code == disabled.reason_code:
                report.fallbacks.append(FallbackDecision(
                    from_element=disabled.element,
                    to_element=fb.use or "",
                    reason_code=disabled.reason_code,
                    evidence=disabled.evidence,
                ))
                break

    return report


# ─── 第一批 Recipe ──────────────────────────────────────────────────────

SEED_RECIPES: List[CartographyRecipe] = [
    CartographyRecipe(
        id="poi_distribution_overview",
        name="POI 分布概览",
        description=(
            "宽泛『分布情况』请求的产品族：视觉热力为主，点叠加 + 行政聚合为辅；"
            "样本不足或非点几何时确定性降级为点图。"
        ),
        intent_tasks=["distribution_overview", "simple_view"],
        intent_cartography=["density_overview", "point_overlay", "administrative_choropleth", "simple_point_map"],
        required_geometry=["Point", "MultiPoint"],
        allowed_geometry=["Point", "MultiPoint"],
        eligibility=[
            EligibilityRule(
                element="visual_heatmap", check_points=True,  # 阈值由 HEATMAP_MIN_POINTS 注入
                requires_geometry=["Point", "MultiPoint"],
                reason_code="INSUFFICIENT_POINTS",
            ),
        ],
        preferred_analysis=["poi_query", "admin_boundary_query", "point_profile", "admin_aggregation"],
        optional_analysis=["kde_density", "hotspot"],
        primary_cartography="visual_heatmap",
        secondary_cartography=["point_overlay", "administrative_aggregation"],
        default_components=["title", "continuous_colorbar", "north_arrow", "scale_bar", "attribution"],
        fallbacks=[
            RecipeFallback(
                when="point_count < 10", reason_code="INSUFFICIENT_POINTS",
                use="point_distribution",
            ),
            RecipeFallback(
                when="geometry not point", reason_code="GEOMETRY_NOT_SUPPORTED",
                disable=["native_heatmap"],
            ),
        ],
        validation_rules=["point_count>=10 for visual_heatmap", "choropleth requires admin aggregation result"],
        export_profile={"formats": ["png", "pdf"], "chart": True},
        priority=40,
    ),
    CartographyRecipe(
        id="point_density",
        name="点密度/热点分析",
        description="『哪里最集中』类请求：KDE/热点语义，允许视觉热力作辅助，定量结论来自分析工具。",
        intent_tasks=["concentration_analysis"],
        intent_cartography=["density_overview", "hotspot_overlay"],
        required_geometry=["Point", "MultiPoint"],
        eligibility=[
            EligibilityRule(
                element="visual_heatmap", check_points=True,  # 阈值由 HEATMAP_MIN_POINTS 注入
                requires_geometry=["Point", "MultiPoint"],
                reason_code="INSUFFICIENT_POINTS",
            ),
        ],
        preferred_analysis=["poi_query", "kde_density", "hotspot", "point_profile"],
        optional_analysis=["admin_aggregation"],
        primary_cartography="visual_heatmap",
        secondary_cartography=["hotspot_overlay", "point_overlay"],
        default_components=["title", "continuous_colorbar", "north_arrow", "scale_bar", "attribution"],
        fallbacks=[
            RecipeFallback(when="point_count < 10", reason_code="INSUFFICIENT_POINTS", use="point_distribution"),
        ],
        export_profile={"formats": ["png"]},
        priority=40,
    ),
    CartographyRecipe(
        id="administrative_choropleth",
        name="行政分级统计图",
        description="『各区…数量/密度/排名』类请求：行政聚合 + choropleth 为第一表达；热力非首选。",
        intent_tasks=["administrative_statistic", "analytical_density"],
        intent_cartography=["administrative_choropleth"],
        # 主体（被统计对象，如学校 POI）几乎总是点数据；行政面来自
        # admin_boundary_query / admin_aggregation 能力，是另一个数据源——
        # 主数据 profile 的几何不该判 recipe 不合格（那是 Case D/E 的误伤）。
        # choropleth 的真实把关在绑定期：只有面状 ref / 已授权 fill 层才挂。
        required_geometry=[],
        allowed_geometry=["Polygon", "MultiPolygon", "Point", "MultiPoint"],
        required_fields=[],
        eligibility=[],
        preferred_analysis=["poi_query", "admin_boundary_query", "admin_aggregation", "point_profile"],
        optional_analysis=["analytical_density"],
        primary_cartography="administrative_choropleth",
        secondary_cartography=["point_overlay"],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution", "statistics_panel"],
        fallbacks=[
            RecipeFallback(when="no admin units available", reason_code="NEEDS_ADMIN_UNITS", use="point_distribution"),
        ],
        export_profile={"formats": ["png", "pdf", "csv"], "layout": "report"},
        priority=30,
    ),
    CartographyRecipe(
        id="categorical_distribution",
        name="分类分布专题",
        description="『各类别占比/类型分布』：分类 match 专题 + 分类图例 + 统计图表。",
        intent_tasks=["categorical_distribution"],
        intent_cartography=["categorical_thematic"],
        allowed_geometry=["Point", "MultiPoint", "Polygon", "MultiPolygon"],
        required_fields=[],
        preferred_analysis=["poi_query", "category_breakdown", "point_profile"],
        primary_cartography="categorical_thematic",
        secondary_cartography=["point_overlay"],
        default_components=["title", "categorical_legend", "north_arrow", "scale_bar", "attribution", "statistics_panel"],
        fallbacks=[],
        export_profile={"formats": ["png"], "chart": True},
        priority=35,
    ),
    CartographyRecipe(
        id="hotspot_analysis",
        name="热点分析产品",
        description="统计显著性热点（Gi*/LISA）：等值面/标注为主，视觉热力仅作底。",
        intent_tasks=["concentration_analysis"],
        intent_cartography=["hotspot_overlay"],
        required_geometry=["Point", "MultiPoint"],
        preferred_analysis=["poi_query", "hotspot", "kde_density"],
        primary_cartography="hotspot_overlay",
        secondary_cartography=["density_overview", "point_overlay"],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution"],
        fallbacks=[],
        export_profile={"formats": ["png"]},
        priority=45,
    ),
    CartographyRecipe(
        id="proximity_analysis",
        name="邻近/缓冲分析",
        description="『N 米范围内』：缓冲面 + 落点叠加 + 距离统计。",
        intent_tasks=["proximity_analysis"],
        intent_cartography=["proximity_overlay"],
        allowed_geometry=["Point", "MultiPoint", "LineString", "MultiLineString"],
        preferred_analysis=["poi_query", "proximity_buffer", "point_profile"],
        primary_cartography="proximity_overlay",
        secondary_cartography=["point_overlay"],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution", "statistics_panel"],
        fallbacks=[],
        export_profile={"formats": ["png"]},
        priority=35,
    ),
    CartographyRecipe(
        # ADR-0092 D7：mobility_flow / od_flow 产品 recipe —— 自然语言
        # 「分析 A 到 B 的出行流 / 展示各区通勤联系」→ OD 流向图产品。
        # 能力链：od_matrix（成本/坐标对来源）→ od_flow_mapping（有界流向
        # 线要素）→ flow_od_arc 主表达 + table/chart/statistics facets。
        id="od_flow_overview",
        name="OD 出行流向图",
        description="『通勤流/出行流/客流』：OD 对 → 有界主流向线要素 + 流量统计。",
        intent_tasks=["mobility_flow"],
        intent_cartography=["flow_od_arc"],
        preferred_analysis=["od_matrix", "od_flow_mapping", "point_profile"],
        optional_analysis=["admin_boundary_query"],
        primary_cartography="flow_od_arc",
        secondary_cartography=[],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution", "statistics_panel"],
        fallbacks=[],
        validation_rules=["flow_bounded_output"],
        export_profile={"formats": ["png", "pdf"], "chart": True},
        priority=35,
    ),
    CartographyRecipe(
        id="accessibility_analysis",
        name="可达性/服务区分析",
        description="『等时圈/服务区/覆盖范围』：网络服务区 + 覆盖统计。",
        intent_tasks=["accessibility_analysis"],
        intent_cartography=["proximity_overlay"],
        preferred_analysis=["poi_query", "service_area", "point_profile"],
        primary_cartography="proximity_overlay",
        secondary_cartography=["point_overlay"],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution", "statistics_panel"],
        fallbacks=[],
        export_profile={"formats": ["png"]},
        priority=35,
    ),
    CartographyRecipe(
        id="raster_distribution",
        name="栅格面分布",
        description="遥感/DEM/气象等栅格分布：raster surface + 连续色条。",
        intent_tasks=["raster_distribution", "change_detection", "vegetation_index"],
        intent_cartography=["raster_surface"],
        preferred_analysis=["raster_source", "point_profile"],
        # change_detection task 的专属能力：双时相栅格变化检测（capability
        # raster_change_detection / tool detect_raster_change）。此前该
        # task 复用本 recipe 却从不计划变化检测能力 —— 任务语义断线。
        # vegetation_index task 同理：显式 NDVI/植被指数请求此前从不计划
        # ndvi capability（remote.ndvi）—— benchmark golden G5 锁定。
        task_optional_analysis={
            "change_detection": ["raster_change_detection"],
            "vegetation_index": ["ndvi"],
        },
        primary_cartography="raster_surface",
        secondary_cartography=[],
        default_components=["title", "continuous_colorbar", "north_arrow", "scale_bar", "attribution"],
        fallbacks=[],
        export_profile={"formats": ["png", "pdf"]},
        priority=40,
    ),
    CartographyRecipe(
        id="grid_density_aggregate",
        name="格网聚合密度",
        description=(
            "『按格网/蜂窝看分布』：点聚合到 H3/渔网格后分级填色——"
            "比视觉热力可量化、比行政面均质。模型库对照 map_model=aggregate_grid"
            "（deck.gl HexagonLayer/GridLayer、kepler hexbin 同族）。"
            "空网格必须透明：无数据 ≠ 数值为零。"
        ),
        intent_tasks=["analytical_density", "concentration_analysis", "distribution_overview"],
        intent_cartography=["aggregate_grid"],
        # 模型库注明 <20 点时网格噪声大于信号；但这是「主元素降级」
        # 而非「recipe 整体不合格」：把 recipe 标记为 INELIGIBLE 会触发
        # fallback_gate 的 RECIPE_INELIGIBLE 分支，把整个产品推向兜底
        # 点图而丢掉已绑定的上下文——因此仅给网格元素设置阈值。
        required_geometry=["Point", "MultiPoint"],
        eligibility=[
            EligibilityRule(
                element="aggregate_grid",
                min_points=20,   # <20 点时网格噪声大于信号（模型库 pitfalls）
                requires_geometry=["Point", "MultiPoint"],
                reason_code="INSUFFICIENT_POINTS",
            ),
        ],
        preferred_analysis=["poi_query", "grid_binning", "point_profile"],
        optional_analysis=["admin_boundary_query"],
        primary_cartography="aggregate_grid",
        secondary_cartography=["point_overlay"],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution"],
        fallbacks=[
            RecipeFallback(
                when="point_count < 20", reason_code="INSUFFICIENT_POINTS",
                use="point_distribution",
            ),
        ],
        validation_rules=["empty grid cell renders transparent (no-data ≠ zero)"],
        export_profile={"formats": ["png"], "layout": "report"},
        priority=42,
    ),
    CartographyRecipe(
        id="proportional_symbol_map",
        name="比例符号（气泡）地图",
        description=(
            "点少但每点带权重值时的合法热力替代：圆面积 ∝ sqrt(value)"
            "（面积比例律，直接线性映射半径会平方级夸大）。"
            "模型库对照 map_model=proportional_symbol。"
        ),
        intent_tasks=["distribution_overview"],
        intent_cartography=["proportional_symbol"],
        allowed_geometry=["Point", "MultiPoint"],
        preferred_analysis=["poi_query", "point_profile"],
        primary_cartography="proportional_symbol",
        secondary_cartography=["point_overlay"],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution"],
        fallbacks=[
            RecipeFallback(
                when="geometry not point", reason_code="GEOMETRY_NOT_SUPPORTED",
                use="point_distribution",
            ),
        ],
        validation_rules=["radius ∝ sqrt(value); draw descending by value (small on top)"],
        export_profile={"formats": ["png"]},
        priority=55,
    ),
    CartographyRecipe(
        id="extrusion_3d_thematic",
        name="3D 挤出立体专题图",
        description=(
            "多边形 3D 柱状立体表达：以定量数值字段编码柱体高度（米），"
            "辅以专题设色（相同或不同指标）与 3D 相机视角推荐。"
        ),
        intent_tasks=["spatial_distribution", "density_aggregation", "comparative_analysis"],
        intent_cartography=["extrusion_3d"],
        required_geometry=["Polygon", "MultiPolygon"],
        preferred_analysis=["admin_aggregation", "point_profile"],
        primary_cartography="extrusion_3d",
        secondary_cartography=[],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution"],
        fallbacks=[
            RecipeFallback(
                when="geometry not polygon", reason_code="GEOMETRY_NOT_SUPPORTED",
                use="administrative_choropleth",
            ),
        ],
        validation_rules=["height field must be numeric; clamp negative heights to 0"],
        export_profile={"formats": ["png", "pdf", "svg"]},
        priority=60,
    ),
    CartographyRecipe(
        id="isoline_contour_map",
        name="等值线/等值面专题图",
        description=(
            "连续表面、核密度估计或高程场的等值线/面制图：支持折线或面带，"
            "支持用户显式指定分级等级、计曲线样式与标注。"
        ),
        intent_tasks=["density_distribution", "surface_interpolation", "topography"],
        intent_cartography=["isoline_contour"],
        allowed_geometry=["Point", "MultiPoint", "Polygon", "MultiPolygon", "LineString", "MultiLineString"],
        preferred_analysis=["kde_density", "point_profile"],
        primary_cartography="isoline_contour",
        secondary_cartography=["point_overlay"],
        default_components=["title", "legend", "continuous_colorbar", "north_arrow", "scale_bar", "attribution"],
        fallbacks=[
            RecipeFallback(
                when="insufficient points or constant surface", reason_code="CONTOUR_UNAVAILABLE",
                use="point_density",
            ),
        ],
        validation_rules=["contour levels must be monotonic and have at least 2 distinct values"],
        export_profile={"formats": ["png", "pdf", "svg"]},
        priority=58,
    ),
]


class RecipeRegistry:
    """Indexed recipe registry（仿 TemplateRegistry 的 O(1) 思路）。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, CartographyRecipe] = {}
        self._by_task: Dict[str, List[CartographyRecipe]] = {}
        self._lock_ids: set = set()

    def load_builtins(self) -> None:
        self._by_id.clear()
        self._by_task.clear()
        for recipe in SEED_RECIPES:
            self.register(recipe)

    def register(self, recipe: CartographyRecipe) -> None:
        if recipe.id in self._by_id:
            # #1075(D-2): 与 lib/gis 系（register 即 raise）对齐至少留痕 ——
            # 此前静默 return，后续种子编辑可无声遮蔽既有条目。
            logger.warning("recipe %r 重复注册：保留既有条目，忽略新条目", recipe.id)
            return
        self._by_id[recipe.id] = recipe
        for task in recipe.intent_tasks:
            self._by_task.setdefault(task, []).append(recipe)

    def get(self, recipe_id: str) -> Optional[CartographyRecipe]:
        return self._by_id.get(recipe_id)

    def default_recipe(self) -> CartographyRecipe:
        """确定性兜底（通用 POI 分布）；注册表为空属编程错误，fail loud。"""
        return self._by_id["poi_distribution_overview"]

    def __contains__(self, recipe_id: str) -> bool:
        return recipe_id in self._by_id

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def select_candidates(
        self,
        intent,
        limit: int = 3,
        project_verified: Optional[set] = None,
    ) -> List[CartographyRecipe]:
        """按 intent 选择候选 recipe（确定性排序）。

        排序键（稳定六元组）：
            1. geometry 期望失配（#781：geometry_expectation=='raster' 时
               非栅格面族候选全部后置——栅格主体绝不推荐 POI 热力族）
            2. task 精确命中
            3. 显式制图意图（图末尾的 aggregate_grid / proportional_symbol
               等加法信号）是否命中 recipe.intent_cartography
            4. cartography_intents 交集多
            5. 项目验证加成（ADR-0069 / spec 开放问题 3）：本项目
               recipe_outcome 事实 ACTIVE 的 recipe 前置——同语义信号下
               优先复用本项目已验证的制图方法。放在 priority 之前：
               项目证据比静态种子优先级更有资格定序。
            6. priority 小（同分稳定排序）

        显式信号那一层解决「同一 distribution_overview 任务下，宽口径
        recipe 交集计数把用户明确的形态词请求压掉」的优先级错置；其余
        回归锚（Golden Case A/D/E）保持原有行为。

        ``project_verified`` 为 None（无项目上下文）时第 5 层恒 0，
        排序与既有行为完全一致——记忆只在本项目内改变起点（决策 1/2）。
        """
        task = getattr(intent, "task", "")
        cartography = set(getattr(intent, "cartography_intents", []) or [])
        geometry = str(getattr(intent, "geometry_expectation", "") or "")
        verified = project_verified or set()
        explicit: Optional[str] = None
        # cartography_intents 的尾端是 intent.py 加法注入的显式形态信号
        # （_GRID_AGG_RE / _BUBBLE_RE 直命中），前面是 task 默认覆盖——
        # 显式信号前置一层优先级，不被 3-交集的计数优势覆盖。
        for candidate in ("aggregate_grid", "proportional_symbol"):
            if candidate in cartography:
                explicit = candidate
                break
        scored: List[tuple] = []
        for recipe in self._by_id.values():
            task_hit = task in recipe.intent_tasks
            # #781: geometry_expectation=='raster' 时栅格面族（raster_surface）
            # 候选前置并保证入选 —— 此前 select_candidates 只看 task/cartography，
            # 栅格主体被推荐成 POI 热力族。硬过滤语义（几何期望是最强信号），
            # 非 raster 期望时该层恒 0，既有候选排序不变。
            raster_family = (
                recipe.primary_cartography == "raster_surface"
                or "raster_surface" in (recipe.intent_cartography or [])
            )
            geometry_mismatch = 1 if (geometry == "raster" and not raster_family) else 0
            cart_set = set(recipe.intent_cartography or [])
            explicit_hit = bool(explicit and explicit in cart_set)
            cart_hit = len(cartography & cart_set)
            score = (
                geometry_mismatch,
                0 if task_hit else 1,
                0 if (explicit is not None and explicit_hit) else (
                    1 if explicit is not None else 0
                ),
                -cart_hit,
                0 if recipe.id in verified else 1,
                recipe.priority, recipe.id,
            )
            if task_hit or cart_hit or (geometry == "raster" and raster_family):
                scored.append((score, recipe))
        scored.sort(key=lambda pair: pair[0])
        return [recipe for _, recipe in scored[:limit]]


_registry: Optional[RecipeRegistry] = None


def get_recipe_registry() -> RecipeRegistry:
    global _registry
    if _registry is None:
        _registry = RecipeRegistry()
        _registry.load_builtins()
    return _registry


def reset_recipe_registry() -> None:
    global _registry
    _registry = None


__all__ = [
    "CartographyRecipe",
    "EligibilityRule",
    "RecipeFallback",
    "EligibilityReport",
    "DisabledElement",
    "FallbackDecision",
    "SEED_RECIPES",
    "RecipeRegistry",
    "get_recipe_registry",
    "reset_recipe_registry",
    "check_eligibility",
]
