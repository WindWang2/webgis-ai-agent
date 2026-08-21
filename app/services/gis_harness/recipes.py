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
        if rule.min_points is not None:
            threshold = rule.min_points or min_points_default
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

    # 声明式 fallback → 结构化决策记录（与 disabled 对应）
    for fb in recipe.fallbacks:
        for disabled in report.disabled:
            if disabled.element == fb.use or (fb.disable and disabled.element in fb.disable):
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
        intent_tasks=["distribution_overview"],
        intent_cartography=["density_overview", "point_overlay", "administrative_choropleth"],
        required_geometry=["Point", "MultiPoint"],
        allowed_geometry=["Point", "MultiPoint"],
        eligibility=[
            EligibilityRule(
                element="visual_heatmap", min_points=10,
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
        export_profile={"formats": ["png", "pdf"], "layout": "report"},
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
                element="visual_heatmap", min_points=10,
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
        required_geometry=["Polygon", "MultiPolygon"],
        allowed_geometry=["Polygon", "MultiPolygon", "Point", "MultiPoint"],
        required_fields=[],
        eligibility=[
            EligibilityRule(
                element="choropleth", requires_geometry=["Polygon", "MultiPolygon"],
                reason_code="NEEDS_ADMIN_UNITS",
            ),
        ],
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
        intent_tasks=["raster_distribution", "change_detection"],
        intent_cartography=["raster_surface"],
        preferred_analysis=["raster_source", "point_profile"],
        primary_cartography="raster_surface",
        secondary_cartography=[],
        default_components=["title", "continuous_colorbar", "north_arrow", "scale_bar", "attribution"],
        fallbacks=[],
        export_profile={"formats": ["png", "pdf"]},
        priority=40,
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
            return
        self._by_id[recipe.id] = recipe
        for task in recipe.intent_tasks:
            self._by_task.setdefault(task, []).append(recipe)

    def get(self, recipe_id: str) -> Optional[CartographyRecipe]:
        return self._by_id.get(recipe_id)

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
    ) -> List[CartographyRecipe]:
        """按 intent 选择候选 recipe（确定性排序）。

        排序键：task 精确命中 > cartography_intents 交集多 > priority 小 >
        id 字典序。Agent 可建议 recipe，但最终裁决是 eligibility 检查。
        """
        task = getattr(intent, "task", "")
        cartography = set(getattr(intent, "cartography_intents", []) or [])
        scored: List[tuple] = []
        for recipe in self._by_id.values():
            task_hit = task in recipe.intent_tasks
            cart_hit = len(cartography & set(recipe.intent_cartography or []))
            score = (0 if task_hit else 1, -cart_hit, recipe.priority, recipe.id)
            if task_hit or cart_hit:
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
