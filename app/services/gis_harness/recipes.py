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

from app.services.gis_harness.workflow_schema import (
    RECIPE_SCHEMA_VERSION,
    WorkflowProfile,  # noqa: F401 - re-export 供 recipe packs / 测试使用
)

GeometryType = Literal["Point", "MultiPoint", "LineString", "MultiLineString",
                       "Polygon", "MultiPolygon", "GeometryCollection"]

PointGeometries = ("Point", "MultiPoint")
PolygonGeometries = ("Polygon", "MultiPolygon")
LineGeometries = ("LineString", "MultiLineString")


class FieldExpectation(BaseModel):
    """字段级期望（基数 / 缺失率；ADR-0151）。"""
    field: str
    # 期望字段形态：categorical（低唯一值比）/ continuous（高唯一值比）。
    # 空 = 只查缺失率。
    kind: str = ""
    min_unique_ratio: Optional[float] = None   # 唯一值比下限（continuous）
    max_unique_ratio: Optional[float] = None   # 唯一值比上限（categorical）
    max_missing_ratio: Optional[float] = None  # 缺失率上限（0-1）


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
    # ── V4（ADR-0151）：多维检查（additive；None/空 = 不检查，旧三维
    #    语义逐位保留并继续作为 fast-fail 前置）。新维度更严格，与旧维度
    #    冲突时按 AND 语义取严格者（§0.5 默认决策）。──────────────────
    # 样本量分档下限（<8 恒拒；声明值触发 SAMPLE_INSUFFICIENT）。
    min_samples: Optional[int] = None
    # 字段基数/缺失率期望（唯一值比：分类 vs 连续；缺失率上限）。
    field_expectations: List[FieldExpectation] = []
    # 分布形态白名单（uniform/skewed/heavy_tailed/zero_inflated/unknown；
    # 空 = 不检查；数据形态不在白名单 → DISTRIBUTION_UNFIT）。
    allowed_distribution_shapes: List[str] = []
    # CRS 与空间尺度：要求投影坐标（聚合/长度/面积类元素在地理 CRS 下
    # 不可信）。
    require_projected_crs: bool = False
    # 聚合粒度下限：点密度（点/km²）低于该值时聚合网格噪声大于信号。
    min_point_density: Optional[float] = None
    # 时间覆盖：要求时间字段且覆盖达标（temporal 事实缺席 = unknown 放行）。
    requires_temporal: bool = False
    min_temporal_coverage: Optional[float] = None


class RecipeFallback(BaseModel):
    """确定性回退声明（元素级：被禁元素 → 同计划内的替代元素）。"""
    when: str                        # 人类可读条件（审计用）
    reason_code: str                 # 机器可读原因码
    use: Optional[str] = None        # 回退到的制图元素
    disable: Optional[List[str]] = None  # 禁用的元素列表


class FallbackLink(BaseModel):
    """recipe 级声明式降级链环节（ADR-0151）。

    与元素级 ``RecipeFallback`` 的分工：RecipeFallback 描述「同计划内
    换表达元素」，FallbackLink 描述「整个 recipe 不可行 → 换 recipe」。
    ``to`` 必须指向已注册 recipe id（registry_validation 启动期悬空校验）。
    """
    to: str                          # 目标 recipe_id
    when: str = ""                   # 人类可读条件（审计用）
    # 匹配的失格原因码（INSUFFICIENT_POINTS / GEOMETRY_NOT_SUPPORTED /
    # SAMPLE_INSUFFICIENT / DISTRIBUTION_UNFIT …）；空 = 任意失格触发。
    reason_code: str = ""
    evidence_hint: str = ""          # 建议随决策携带的证据说明（有界）
    auto_generated: bool = False     # P7 通用兜底补齐标记（台账列明占比）


#: 通用兜底链（§0.5：缺声明时自动补齐）——点图 → 分级图。目标必须真实
#: 存在（registry_validation 对全 registry 校验该常量）。
DEFAULT_FALLBACK_CHAIN = ("poi_distribution_overview", "administrative_choropleth")


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
    # ── V4（ADR-0151）：recipe 级声明式降级链（additive；空 = 无显式链，
    #    全链失败时按 §0.5 自动落通用兜底 DEFAULT_FALLBACK_CHAIN）。────
    fallback_links: List[FallbackLink] = []
    # ── V4（ADR-0151 / P6）：统计/图表产出由 recipe 声明驱动（此前
    #    planner 内联字面量 admin_bar/category_bar 的外迁出口；空列表 =
    #    按 task 的确定性派生规则，单一事实源在本模块）。────────────────
    default_statistics: List[str] = []
    default_charts: List[str] = []
    validation_rules: List[str] = []
    export_profile: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 50                  # 同分候选时的稳定排序
    # ── V2（Goal C / ADR-0101）：Workflow Recipe DSL 扩展（additive）────
    # schema_version=1 为 V1 seed 语义；带 workflow 画像的 recipe 声明 V2。
    schema_version: int = 1
    # 工作流画像：数据角色 / 科学义务 / 完成契约 / 语义回退 / 专业关键词。
    # None = 纯 V1 制图 recipe，选择与规划行为与历史完全一致。
    workflow: Optional[WorkflowProfile] = None
    # ── V3（GIS Task Ontology）：本 recipe 服务的本体任务 id（opt-in）──
    # 空 = 不参与本体路由（行为与历史完全一致）；非空时，intent 的本体
    # 匹配命中其中任一 task → 该候选在本体层前置，全不命中则后置。
    # id 必须命中 gis_ontology 登记表（registry_validation 编译期校验）。
    ontology_tasks: List[str] = Field(default_factory=list)


class DisabledElement(BaseModel):
    element: str
    reason_code: str
    evidence: Dict[str, Any] = Field(default_factory=dict)


# ═══ V4（ADR-0151）：多维度资格裁决的数据事实契约 ═══════════════════════
#
# EligibilityContext 由 04 线（数据剖析）逐步供给；本线定义接口并提供
# `from_profile` 兜底派生（现有 Spatial Meta Profile / resolver camelCase
# 形态，零全量扫描）。所有事实缺席 = unknown 放行（与义务评估同红线：
# 未知 ≠ 不满足），绝不虚构证据。


class FieldFacts(BaseModel):
    """单字段剖析事实（缺省键 = 未知放行）。"""
    kind: str = ""                            # categorical/continuous/text/time
    unique_ratio: Optional[float] = None      # 唯一值比 unique/n（0-1）
    missing_ratio: Optional[float] = None     # 缺失率（0-1）
    numeric: Optional[bool] = None


class DistributionFacts(BaseModel):
    """度量字段分布形态事实（由上游统计派生；本层只比对不计算分布）。"""
    skew: Optional[float] = None
    kurtosis: Optional[float] = None
    zero_ratio: Optional[float] = None        # 零值占比（0-1）
    unique_value_ratio: Optional[float] = None  # 度量唯一值比（近均匀判据）


class SpatialFacts(BaseModel):
    """CRS 与空间尺度事实。"""
    crs: str = ""
    crs_class: str = ""               # geographic/projected/projected_local_metric/unknown
    extent_span_km: Optional[float] = None      # 主跨度（km）
    point_density_per_km2: Optional[float] = None


class TemporalFacts(BaseModel):
    """时间覆盖事实。"""
    field: str = ""
    span_days: Optional[float] = None
    coverage_ratio: Optional[float] = None      # 非空时间戳占比（0-1）


#: 样本量分档（ADR-0151）：<8 恒拒；8–30 small；30–500 medium；>500 large。
SAMPLE_TIER_SMALL_MAX = 30
SAMPLE_TIER_MEDIUM_MAX = 500
SAMPLE_HARD_FLOOR = 8


class EligibilityContext(BaseModel):
    """多维度资格裁决的数据事实（04 线供给；from_profile 兜底派生）。"""
    geometry: str = "unknown"                 # point/line/polygon/raster/unknown
    n: Optional[int] = None                   # 要素数
    fields: Dict[str, FieldFacts] = Field(default_factory=dict)
    distribution: Optional[DistributionFacts] = None
    spatial: Optional[SpatialFacts] = None
    temporal: Optional[TemporalFacts] = None

    @classmethod
    def from_profile(cls, profile: Optional[Dict[str, Any]]) -> "EligibilityContext":
        """现有 profile dict → EligibilityContext（诚实派生，不虚构）。

        识别两种形态：Spatial Meta Profile（featureCount/geometryTypes/
        fields）与 resolver camelCase 补充键（crs/crsClass）。新维度事实
        （分布/字段基数/时间）尚未由 profile 携带时保持缺席 —— unknown 放行。
        """
        p = profile if isinstance(profile, dict) else {}
        geom_types = p.get("geometryTypes") or []
        fields_facts: Dict[str, FieldFacts] = {}
        raw_fields = p.get("fields")
        if isinstance(raw_fields, dict):
            for name, meta in raw_fields.items():
                if isinstance(meta, dict):
                    fields_facts[str(name)] = FieldFacts(
                        kind=str(meta.get("kind") or meta.get("type") or ""),
                        unique_ratio=_ratio_or_none(meta.get("uniqueRatio")),
                        missing_ratio=_ratio_or_none(meta.get("missingRatio")),
                        numeric=meta.get("numeric") if isinstance(
                            meta.get("numeric"), bool) else None,
                    )
                else:
                    fields_facts[str(name)] = FieldFacts()
        spatial = SpatialFacts(
            crs=str(p.get("crs") or ""),
            crs_class=str(p.get("crsClass") or ""),
            extent_span_km=_finite_or_none(p.get("extentSpanKm")),
            point_density_per_km2=_finite_or_none(p.get("pointDensityPerKm2")),
        )
        return cls(
            geometry=_geometry_category(
                list(geom_types) if isinstance(geom_types, (list, tuple)) else []),
            n=_profile_count(p.get("featureCount")),
            fields=fields_facts,
            distribution=DistributionFacts(
                skew=_finite_or_none((p.get("distribution") or {}).get("skew"))
                if isinstance(p.get("distribution"), dict) else None,
                kurtosis=_finite_or_none((p.get("distribution") or {}).get("kurtosis"))
                if isinstance(p.get("distribution"), dict) else None,
                zero_ratio=_ratio_or_none((p.get("distribution") or {}).get("zeroRatio"))
                if isinstance(p.get("distribution"), dict) else None,
                unique_value_ratio=_ratio_or_none(
                    (p.get("distribution") or {}).get("uniqueValueRatio"))
                if isinstance(p.get("distribution"), dict) else None,
            ),
            spatial=spatial,
            temporal=TemporalFacts(
                field=str(p.get("timeField") or ""),
                span_days=_finite_or_none(p.get("temporalSpanDays")),
                coverage_ratio=_ratio_or_none(p.get("temporalCoverageRatio")),
            ) if (p.get("timeField") or p.get("temporalSpanDays")
                  or p.get("temporalCoverageRatio")) else None,
        )

    def sample_tier(self) -> str:
        """样本量分档（unknown 时不分档，返回 "unknown"）。"""
        if self.n is None:
            return "unknown"
        if self.n < SAMPLE_HARD_FLOOR:
            return "below_floor"
        if self.n < SAMPLE_TIER_SMALL_MAX:
            return "small"
        if self.n < SAMPLE_TIER_MEDIUM_MAX:
            return "medium"
        return "large"

    def distribution_shape(self) -> str:
        """分布形态分类（近均匀/偏态/重尾/零膨胀/unknown；确定性阈值）。"""
        d = self.distribution
        if d is None:
            return "unknown"
        if d.zero_ratio is not None and d.zero_ratio >= 0.3:
            return "zero_inflated"
        if d.kurtosis is not None and d.kurtosis >= 10:
            return "heavy_tailed"
        if d.skew is not None and abs(d.skew) >= 1.0:
            return "skewed"
        if (d.unique_value_ratio is not None and d.unique_value_ratio <= 0.05) or (
                d.skew is not None and abs(d.skew) < 0.2
                and (d.kurtosis is None or abs(d.kurtosis - 3) < 1.0)
                and (d.zero_ratio is None or d.zero_ratio < 0.05)):
            return "uniform"
        return "unknown"


class CheckResult(BaseModel):
    """单维检查结果（禁止裸 bool：一律携带 reason_code 与 evidence）。"""
    check: str
    ok: bool
    reason_code: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)


def _ratio_or_none(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if 0.0 <= v <= 1.0 else None


def _finite_or_none(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _profile_count(v: Any) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ─── V4 六个新维度检查器（纯函数；unknown 放行；禁止裸 bool）──────────

def check_sample_size(ctx: EligibilityContext, min_samples: Optional[int]) -> CheckResult:
    """样本量分档检查：<8 恒拒（结构性下限）；声明 min_samples 为附加门槛。"""
    tier = ctx.sample_tier()
    evidence = {"tier": tier, "n": ctx.n, "declared_min": min_samples}
    if tier == "unknown":
        return CheckResult(check="sample_size", ok=True, evidence=evidence)
    if tier == "below_floor":
        return CheckResult(check="sample_size", ok=False,
                           reason_code="SAMPLE_BELOW_FLOOR", evidence=evidence)
    if min_samples is not None and ctx.n is not None and ctx.n < min_samples:
        return CheckResult(check="sample_size", ok=False,
                           reason_code="SAMPLE_INSUFFICIENT", evidence=evidence)
    return CheckResult(check="sample_size", ok=True, evidence=evidence)


def check_field_cardinality(ctx: EligibilityContext, exp: FieldExpectation) -> CheckResult:
    """字段基数检查：唯一值比区分分类 vs 连续；缺失率上限（ unknown 放行）。"""
    facts = ctx.fields.get(exp.field)
    evidence: Dict[str, Any] = {
        "field": exp.field, "expected_kind": exp.kind,
        "unique_ratio": facts.unique_ratio if facts else None,
        "missing_ratio": facts.missing_ratio if facts else None,
    }
    if facts is None:
        return CheckResult(check="field_cardinality", ok=True, evidence={
            **evidence, "note": "field_facts_absent"})
    if exp.kind == "categorical" and facts.unique_ratio is not None \
            and exp.max_unique_ratio is not None \
            and facts.unique_ratio > exp.max_unique_ratio:
        return CheckResult(check="field_cardinality", ok=False,
                           reason_code="FIELD_NOT_CATEGORICAL", evidence=evidence)
    if exp.kind == "continuous" and facts.unique_ratio is not None \
            and exp.min_unique_ratio is not None \
            and facts.unique_ratio < exp.min_unique_ratio:
        return CheckResult(check="field_cardinality", ok=False,
                           reason_code="FIELD_NOT_CONTINUOUS", evidence=evidence)
    if exp.max_missing_ratio is not None and facts.missing_ratio is not None \
            and facts.missing_ratio > exp.max_missing_ratio:
        return CheckResult(check="field_cardinality", ok=False,
                           reason_code="FIELD_MISSING_RATIO_HIGH", evidence=evidence)
    return CheckResult(check="field_cardinality", ok=True, evidence=evidence)


def check_missing_ratio(ctx: EligibilityContext, exp: FieldExpectation) -> CheckResult:
    """缺失率检查（独立维度：基数合格但空值过多同样不可编码）。"""
    facts = ctx.fields.get(exp.field)
    evidence = {"field": exp.field,
                "missing_ratio": facts.missing_ratio if facts else None,
                "max": exp.max_missing_ratio}
    if facts is None or facts.missing_ratio is None or exp.max_missing_ratio is None:
        return CheckResult(check="missing_ratio", ok=True, evidence=evidence)
    if facts.missing_ratio > exp.max_missing_ratio:
        return CheckResult(check="missing_ratio", ok=False,
                           reason_code="FIELD_MISSING_RATIO_HIGH", evidence=evidence)
    return CheckResult(check="missing_ratio", ok=True, evidence=evidence)


def check_distribution_shape(ctx: EligibilityContext, allowed: List[str]) -> CheckResult:
    """分布形态检查：白名单外形态拒用（零膨胀直方图/近均匀分级图是制图谎报）。"""
    shape = ctx.distribution_shape()
    evidence = {"shape": shape, "allowed": list(allowed)}
    if shape == "unknown":
        return CheckResult(check="distribution_shape", ok=True, evidence=evidence)
    if shape not in allowed:
        return CheckResult(check="distribution_shape", ok=False,
                           reason_code="DISTRIBUTION_UNFIT", evidence=evidence)
    return CheckResult(check="distribution_shape", ok=True, evidence=evidence)


def check_crs_and_scale(ctx: EligibilityContext, rule: EligibilityRule) -> CheckResult:
    """CRS 与空间尺度检查：地理 CRS 下的聚合/度量与稀疏聚合粒度不可信。"""
    sp = ctx.spatial
    evidence: Dict[str, Any] = {
        "crs_class": sp.crs_class if sp else "",
        "point_density_per_km2": sp.point_density_per_km2 if sp else None,
        "min_density": rule.min_point_density,
    }
    if sp is None or (not sp.crs_class and sp.point_density_per_km2 is None):
        return CheckResult(check="crs_scale", ok=True, evidence=evidence)
    if rule.require_projected_crs and sp.crs_class == "geographic":
        return CheckResult(check="crs_scale", ok=False,
                           reason_code="PROJECTED_CRS_REQUIRED", evidence=evidence)
    if rule.min_point_density is not None and sp.point_density_per_km2 is not None \
            and sp.point_density_per_km2 < rule.min_point_density:
        return CheckResult(check="crs_scale", ok=False,
                           reason_code="SPARSE_FOR_AGGREGATION", evidence=evidence)
    return CheckResult(check="crs_scale", ok=True, evidence=evidence)


def check_temporal_coverage(ctx: EligibilityContext, rule: EligibilityRule) -> CheckResult:
    """时间覆盖检查：requires_temporal 且时间事实缺席/覆盖不足 → 拒用。"""
    tp = ctx.temporal
    evidence = {
        "field": tp.field if tp else "",
        "coverage_ratio": tp.coverage_ratio if tp else None,
        "min": rule.min_temporal_coverage,
    }
    if not rule.requires_temporal:
        return CheckResult(check="temporal_coverage", ok=True, evidence=evidence)
    if tp is None or not tp.field:
        return CheckResult(check="temporal_coverage", ok=False,
                           reason_code="TEMPORAL_FIELD_ABSENT", evidence=evidence)
    if rule.min_temporal_coverage is not None and tp.coverage_ratio is not None \
            and tp.coverage_ratio < rule.min_temporal_coverage:
        return CheckResult(check="temporal_coverage", ok=False,
                           reason_code="TEMPORAL_COVERAGE_INSUFFICIENT",
                           evidence=evidence)
    return CheckResult(check="temporal_coverage", ok=True, evidence=evidence)


def run_eligibility_rules(
    recipe: CartographyRecipe,
    ctx: EligibilityContext,
    *,
    min_points_default: int = 10,
) -> List[CheckResult]:
    """对 recipe 全部规则跑 V4 六维检查（仅声明维度产生检查记录）。"""
    results: List[CheckResult] = []
    for rule in recipe.eligibility:
        prefix = f"{rule.element}:"
        if rule.min_samples is not None or ctx.sample_tier() == "below_floor":
            r = check_sample_size(ctx, rule.min_samples)
            results.append(r.model_copy(update={"check": prefix + r.check}))
        for exp in rule.field_expectations:
            r = check_field_cardinality(ctx, exp)
            results.append(r.model_copy(update={"check": prefix + r.check}))
            if exp.max_missing_ratio is not None:
                r2 = check_missing_ratio(ctx, exp)
                results.append(r2.model_copy(update={"check": prefix + r2.check}))
        if rule.allowed_distribution_shapes:
            r = check_distribution_shape(ctx, rule.allowed_distribution_shapes)
            results.append(r.model_copy(update={"check": prefix + r.check}))
        if rule.require_projected_crs or rule.min_point_density is not None:
            r = check_crs_and_scale(ctx, rule)
            results.append(r.model_copy(update={"check": prefix + r.check}))
        if rule.requires_temporal or rule.min_temporal_coverage is not None:
            r = check_temporal_coverage(ctx, rule)
            results.append(r.model_copy(update={"check": prefix + r.check}))
    return results





class FallbackDecision(BaseModel):
    """一次实际发生的回退（结构化证据：from/to/reason/evidence）。"""
    from_element: str
    to_element: str = ""
    reason_code: str
    evidence: Dict[str, Any] = Field(default_factory=dict)
    # V2（Goal C / C6）：语义降级分类（equivalent/approximation/proxy/
    # degraded/not_allowed，与算法层 fallback_semantics 同词表）与用户可见
    # 披露 —— additive，旧记录两字段缺省为空，消费方按「空 = 未声明」处理。
    downgrade_class: str = ""
    disclosure: str = ""
    # ── V4（ADR-0151）：链式降级证据（additive）。attempts 转录链上每一步
    #    尝试（含落选者与原因），auto_generated 标记通用兜底链。01 线
    #    （adaptive-intent）统一 FallbackDecision 契约前，本线按任务书
    #    §2-P4 定义实现；对齐时以此为准并入。────────────────────────────
    attempts: List[Dict[str, Any]] = Field(default_factory=list)
    auto_generated: bool = False


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

    # ── V4（ADR-0151）六维正式裁决（旧三维之上叠加，AND 语义取严格者）──
    # 事实缺席 = unknown 放行：旧 profile 形态下全部检查通过（evidence
    # 带 absent 注记），既有行为逐位保留。冲突时新维度为准（更严格）。
    ctx = EligibilityContext.from_profile(profile)
    v4_results = run_eligibility_rules(
        recipe, ctx, min_points_default=min_points_default)
    for r in v4_results:
        report.checks.append({
            "check": r.check, "passed": r.ok, "v4": True,
            "reason_code": r.reason_code, "evidence": r.evidence,
        })
        if not r.ok:
            # 归属元素：check 形如 "<element>:<dimension>"；元素级规则失败
            # 禁用该元素；无元素前缀（理论不产生）按 recipe 级处理。
            element = r.check.split(":", 1)[0] if ":" in r.check else "recipe"
            report.eligible = False
            report.disabled.append(DisabledElement(
                element=element,
                reason_code=r.reason_code,
                evidence={**r.evidence, "dimension": r.check.split(":", 1)[-1]},
            ))

    # 声明式 fallback → 结构化决策记录：从被禁元素出发，按 reason_code 匹配
    # 声明的回退（此前按 fb.use 比对被禁元素名——那是回退目标永不相等，
    # 声明式记录从未生效）。V4：同一元素同一原因码只记一条，其余按链式
    # 求解（resolve_fallback_chain）在 planner 层评估，这里不再 break 短路
    # 多余声明 —— 但保持每元素单决策的既有契约。
    seen_pairs: set = set()
    for disabled in report.disabled:
        for fb in recipe.fallbacks:
            if fb.reason_code and fb.reason_code == disabled.reason_code:
                pair = (disabled.element, disabled.reason_code)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                report.fallbacks.append(FallbackDecision(
                    from_element=disabled.element,
                    to_element=fb.use or "",
                    reason_code=disabled.reason_code,
                    evidence=disabled.evidence,
                ))
                break

    return report


# ─── V4（ADR-0151）：声明式降级链求解 ──────────────────────────────────

class FallbackAttempt(BaseModel):
    """链上一次尝试的有界转录（含落选者——降级可解释的最小单元）。"""
    step: int
    from_recipe: str
    to_recipe: str
    reason_code: str = ""            # 触发（或未匹配）的失格原因码
    eligible: Optional[bool] = None  # 目标复检结果；None = 未复检（原因不匹配）
    chosen: bool = False
    note: str = ""
    auto_generated: bool = False
    evidence: Dict[str, Any] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step, "from": self.from_recipe, "to": self.to_recipe,
            "reason_code": self.reason_code, "eligible": self.eligible,
            "chosen": self.chosen, "note": self.note[:120],
            "auto_generated": self.auto_generated,
        }


class ChainResolution(BaseModel):
    """resolve_fallback_chain 的确定性结论。"""
    origin_recipe: str
    final_recipe: str = ""
    resolved: bool = False           # True = 找到 eligible 终点（含原地）
    exhausted: bool = False          # True = 链空/全败（→ 说明卡路径）
    attempts: List[FallbackAttempt] = []
    auto_generated_used: bool = False

    @property
    def primary_reason_code(self) -> str:
        """链的首发原因码（首条非空 attempt；用户可见披露的锚）。"""
        for a in self.attempts:
            if a.reason_code:
                return a.reason_code
        return "RECIPE_INELIGIBLE"


def resolve_fallback_chain(
    recipe: CartographyRecipe,
    *,
    profile: Optional[Dict[str, Any]] = None,
    ctx: Optional[EligibilityContext] = None,
    registry: Optional["RecipeRegistry"] = None,
    min_points_default: int = 10,
    depth_limit: int = 4,
) -> ChainResolution:
    """声明式降级链的链式求解（ADR-0151 P2）。

    - 起点 recipe 复检 eligible → 原地 resolved（attempts 记一条 chosen）。
    - 否则按声明序评估 ``fallback_links``：原因码不匹配 → 落选（eligible
      = None，note 记原因）；匹配 → 对目标做**完整复检**（同一 profile）。
    - 多条目标同时 eligible → 按 registry 排序键（priority, id）取最优，
      其余记落选者（§0.5 默认决策）。
    - 无匹配或目标全败 → 递归进入各失败目标的链（深度优先，环守卫，
      depth_limit 封顶）。
    - 链穷尽 → 通用兜底 DEFAULT_FALLBACK_CHAIN（auto_generated=True）。
    """
    reg = registry
    if reg is None:
        reg = get_recipe_registry()
    if ctx is None:
        ctx = EligibilityContext.from_profile(profile)

    resolution = ChainResolution(origin_recipe=recipe.id)
    visited: set = {recipe.id}
    step = 0

    def _check(candidate: CartographyRecipe) -> EligibilityReport:
        return check_eligibility(
            candidate, profile=profile, min_points_default=min_points_default)

    def _walk(current: CartographyRecipe, current_report: EligibilityReport,
              depth: int) -> Optional[str]:
        """返回 eligible 终点 recipe id；无则 None。

        ``current_report``：调用方已复检的失格报告（原因码门的单一依据，
        链上不复检同一 recipe —— 同输入同输出，省重复判定）。
        """
        nonlocal step
        links = list(current.fallback_links)
        auto = False
        if not links:
            # §0.5：无显式链 → 通用兜底（点图 → 分级图）。
            links = [
                FallbackLink(to=target, when="generic fallback (auto)",
                             reason_code="", auto_generated=True)
                for target in DEFAULT_FALLBACK_CHAIN
            ]
            auto = True
        live_codes = {d.reason_code for d in current_report.disabled}
        matched: List[tuple] = []   # (link, target_recipe, report)
        skipped: List[tuple] = []   # (link, target)
        for link in links:
            target = reg.get(link.to)
            if target is None:
                # 悬空引用在 registry_validation 启动期已拦截；运行期防御性
                # 记录（不得静默，也不得炸掉整条链）。
                step += 1
                resolution.attempts.append(FallbackAttempt(
                    step=step, from_recipe=current.id, to_recipe=link.to,
                    reason_code=link.reason_code, eligible=None, chosen=False,
                    note="dangling fallback target", auto_generated=link.auto_generated,
                ))
                continue
            if link.reason_code and link.reason_code not in live_codes:
                step += 1
                resolution.attempts.append(FallbackAttempt(
                    step=step, from_recipe=current.id, to_recipe=link.to,
                    reason_code=link.reason_code, eligible=None, chosen=False,
                    note="reason_code_not_matched",
                    auto_generated=link.auto_generated or auto,
                ))
                continue
            step += 1
            report_target = _check(target)
            resolution.attempts.append(FallbackAttempt(
                step=step, from_recipe=current.id, to_recipe=link.to,
                reason_code=link.reason_code or next(
                    (d.reason_code for d in current_report.disabled), ""),
                eligible=report_target.eligible,
                chosen=False,
                note="" if report_target.eligible else next(
                    (d.reason_code for d in report_target.disabled), "ineligible"),
                auto_generated=link.auto_generated or auto,
                evidence={"disabled": [d.reason_code for d in report_target.disabled][:4]}
                if not report_target.eligible else {},
            ))
            if report_target.eligible:
                matched.append((link, target, report_target))
            elif target.id not in visited:
                skipped.append((link, target))
            else:
                # 环守卫：已访问目标直接判失败留痕。
                resolution.attempts[-1].note = (
                    resolution.attempts[-1].note or "cycle") + "|cycle_guard"

        if matched:
            # §0.5：多条 eligible → registry 排序键取最优（priority 小者优，
            # 同分 id 字典序），落选者显式记录。
            matched.sort(key=lambda t: (t[1].priority, t[1].id))
            for _link, w_target, _rep in matched[1:]:
                for att in resolution.attempts:
                    if att.to_recipe == w_target.id and att.eligible:
                        att.chosen = False
                        att.note = (att.note + "|" if att.note else "") \
                            + "demoted_by_sort_key"
            best = matched[0]
            for att in resolution.attempts:
                if att.to_recipe == best[1].id and att.eligible:
                    att.chosen = True
            if auto:
                resolution.auto_generated_used = True
            return best[1].id

        if depth >= depth_limit:
            return None
        for _link, target in skipped:
            if target.id in visited:
                continue
            visited.add(target.id)
            found = _walk(target, _check(target), depth + 1)
            if found:
                return found
        return None

    start_report = _check(recipe)
    if start_report.eligible:
        resolution.final_recipe = recipe.id
        resolution.resolved = True
        resolution.attempts.append(FallbackAttempt(
            step=0, from_recipe=recipe.id, to_recipe=recipe.id,
            eligible=True, chosen=True, note="origin_eligible",
        ))
        return resolution

    visited.add(recipe.id)
    found = _walk(recipe, start_report, 0)
    if found:
        resolution.final_recipe = found
        resolution.resolved = True
    else:
        resolution.final_recipe = ""
        resolution.exhausted = True
    return resolution


def render_fallback_for_llm(decisions: List[FallbackDecision], *, limit: int = 4) -> str:
    """降级决策 → 有界 LLM 上下文文本（对齐 render_verdict_for_llm 风格）。

    每条决策一行：from→to + 原因码 + 用户披露；链式尝试折叠为计数。
    空列表 → 空串（调用方按「无降级」处理，不注入噪声）。
    """
    if not decisions:
        return ""
    lines: List[str] = []
    for d in decisions[:limit]:
        head = f"- {d.from_element} → {d.to_element or '(无目标)'} [{d.reason_code}]"
        tail: List[str] = []
        if d.downgrade_class:
            tail.append(f"降级分类={d.downgrade_class}")
        if d.attempts:
            tail.append(f"链尝试={len(d.attempts)}步")
        if d.auto_generated:
            tail.append("通用兜底")
        if d.disclosure:
            tail.append(str(d.disclosure)[:160])
        lines.append(head + ("；" + "；".join(tail) if tail else ""))
    if len(decisions) > limit:
        lines.append(f"（另有 {len(decisions) - limit} 条降级决策未展开）")
    return "\n".join(lines)


def default_charts_for_task(task: str) -> List[str]:
    """task → 默认图表（P6 字面量外迁的单一事实源；recipe 未声明时使用）。"""
    return ["category_bar"] if task == "categorical_distribution" else ["admin_bar"]


def default_statistics_for_task(task: str) -> List[str]:
    """task → 默认统计产出（P6 字面量外迁的单一事实源）。"""
    if task == "administrative_statistic":
        return ["admin_aggregation", "ranking", "total"]
    return ["feature_count", "admin_summary"]


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
    # ── Semantic V2（ADR-0098）：决策族一等产品 recipe ─────────────────
    # 此前 选址/适建/风险/公平 查询落入 distribution 兜底（G27–G29 锁定的
    # 旧行为）—— 本组 recipe 给它们专属的计划路径：诚实的角色需求（分母/
    # 受体/准则）、推荐能力（含 mcda_evaluation）、方法论校验与回退。
    CartographyRecipe(
        id="spatial_equity",
        name="空间公平评价",
        description=(
            "『资源分配是否公平/均衡』：行政区聚合 + 分母归一化比率 + "
            "公平性专题图。无分母时只能产出数量/密度结论并强制披露方法论"
            "边界 —— 绝不把数量差异伪装成公平性判断。"
        ),
        intent_tasks=["spatial_equity"],
        intent_cartography=["administrative_choropleth"],
        allowed_geometry=["Point", "MultiPoint", "Polygon", "MultiPolygon"],
        preferred_analysis=["poi_query", "admin_boundary_query",
                            "admin_aggregation", "point_profile"],
        optional_analysis=["global_morans_i", "local_morans_i"],
        primary_cartography="administrative_choropleth",
        secondary_cartography=["point_overlay"],
        default_components=["title", "legend", "north_arrow", "scale_bar",
                            "attribution", "statistics_panel"],
        fallbacks=[
            RecipeFallback(
                when="无归一化分母（人口/面积）数据",
                reason_code="MISSING_DENOMINATOR",
                use="administrative_choropleth",
            ),
        ],
        validation_rules=[
            "equity conclusion requires a normalization denominator; "
            "counts-only products must carry the missing-denominator disclosure",
        ],
        export_profile={"formats": ["png"], "chart": True},
        priority=50,
    ),
    CartographyRecipe(
        id="site_selection",
        name="选址分析（多准则）",
        description=(
            "『新设施选址/最优位置』：范围底图 + 准则图层（缓冲/服务区）+ "
            "候选方案 MCDA 评价（WSM/TOPSIS）。硬约束（禁建区等）是 否决项；"
            "权重来源（用户指定/假设）必须显式，不合成准则值。"
        ),
        intent_tasks=["site_selection"],
        intent_cartography=["proximity_overlay"],
        allowed_geometry=["Point", "MultiPoint", "Polygon", "MultiPolygon"],
        preferred_analysis=["admin_boundary_query", "proximity_buffer",
                            "service_area", "mcda_evaluation", "point_profile"],
        optional_analysis=["poi_query", "spatial_join", "admin_aggregation"],
        primary_cartography="proximity_overlay",
        secondary_cartography=["point_overlay"],
        default_components=["title", "legend", "north_arrow", "scale_bar",
                            "attribution", "statistics_panel"],
        fallbacks=[
            RecipeFallback(
                when="无候选方案可评价（仅范围）",
                reason_code="NO_CANDIDATES",
                use="proximity_overlay",
            ),
        ],
        validation_rules=[
            "hard-constraint violations must remain vetoes in any recommendation",
            "criterion values must be evidence-backed or disclosed as assumptions",
        ],
        export_profile={"formats": ["png", "pdf"]},
        priority=50,
    ),
    CartographyRecipe(
        id="suitability_assessment",
        name="适宜性评价（加权叠加）",
        description=(
            "『适建区/适宜性评价』：因子标准化（重分类到统一等级）+ 加权"
            "叠加得适宜面。量纲不一致的原始值不得直接相加；权重敏感性与"
            "硬约束（禁建区）必须披露。"
        ),
        intent_tasks=["suitability_assessment"],
        intent_cartography=["raster_surface", "proximity_overlay"],
        allowed_geometry=["Point", "MultiPoint", "Polygon", "MultiPolygon"],
        preferred_analysis=["admin_boundary_query", "proximity_buffer",
                            "raster_reclassify", "mcda_evaluation"],
        optional_analysis=["raster_source", "geometry_clip", "spatial_join"],
        primary_cartography="raster_surface",
        secondary_cartography=["proximity_overlay"],
        default_components=["title", "continuous_colorbar", "north_arrow",
                            "scale_bar", "attribution", "statistics_panel"],
        fallbacks=[
            RecipeFallback(
                when="无栅格因子（仅矢量约束）",
                reason_code="NO_RASTER_FACTORS",
                use="proximity_overlay",
            ),
        ],
        validation_rules=[
            "factors must be normalized to a common scale before weighting",
            "suitability map must disclose weight provenance and hard constraints",
        ],
        export_profile={"formats": ["png", "pdf"]},
        priority=50,
    ),
    CartographyRecipe(
        id="risk_exposure",
        name="风险暴露评价",
        description=(
            "『风险区/暴露/危险源』：影响范围（缓冲半径需有依据）× 受体"
            "叠加 → 暴露量统计 + 风险专题图。只画影响范围不统计受体是"
            "半成品；无受体数据时必须披露。"
        ),
        intent_tasks=["risk_exposure"],
        intent_cartography=["proximity_overlay", "administrative_choropleth"],
        allowed_geometry=["Point", "MultiPoint", "Polygon", "MultiPolygon"],
        preferred_analysis=["poi_query", "proximity_buffer", "spatial_join",
                            "admin_aggregation"],
        optional_analysis=["zonal_statistics", "service_area"],
        primary_cartography="proximity_overlay",
        secondary_cartography=["point_overlay", "administrative_choropleth"],
        default_components=["title", "legend", "north_arrow", "scale_bar",
                            "attribution", "statistics_panel"],
        fallbacks=[
            RecipeFallback(
                when="无受体数据（仅危险源）",
                reason_code="NO_RECEPTORS",
                use="proximity_overlay",
            ),
        ],
        validation_rules=[
            "buffer radius must cite a basis (standard/literature/user-declared)",
            "exposure statistics require receptor data; disclose when absent",
        ],
        export_profile={"formats": ["png"]},
        priority=50,
    ),
]


class RecipeRegistry:
    """Indexed recipe registry（仿 TemplateRegistry 的 O(1) 思路）。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, CartographyRecipe] = {}
        self._by_task: Dict[str, List[CartographyRecipe]] = {}
        self._lock_ids: set = set()
        # V2（Goal C）：专业关键词倒排 + 领域索引 + 内容指纹缓存。
        # 在 register 时一次性构建（immutable registry projection，查询
        # O(命中数)，避免每 turn O(all recipes × all keywords) 重扫）。
        self._by_domain: Dict[str, List[CartographyRecipe]] = {}
        self._keyword_index: Dict[str, List[CartographyRecipe]] = {}
        self._ascii_keywords: set = set()
        self._content_fps: Dict[str, str] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        self._by_task.clear()
        self._by_domain.clear()
        self._keyword_index.clear()
        self._ascii_keywords.clear()
        self._content_fps.clear()
        for recipe in SEED_RECIPES:
            self.register(recipe)
        # V2 领域包（Goal C / C2）：seed 之外的专业工作流知识库。注册顺序
        # 决定性（包内按声明序、包间按模块名序），重复 id 仍按 keep-first
        # ——seed 优先，包之间先注册者胜（确定性问题可由 parity 测试锁定）。
        # R1-B3：逐模块加载并 fail-loud —— 此前整段一个 except，包 #13 崩溃
        # 会静默留下 1-12 的半量知识库继续服役。语义知识库不完整必须启动期
        # 显性失败，不得退化服役。
        import importlib

        from app.services.gis_harness.recipe_packs import PACK_MODULES, _BASE
        failed_modules: List[str] = []
        for module_name in PACK_MODULES:
            try:
                module = importlib.import_module(_BASE + module_name)
                for recipe in getattr(module, "RECIPES", []):
                    self.register(recipe)
            except Exception as e:  # noqa: BLE001 - 收集后统一 fail loud
                failed_modules.append(f"{module_name}: {e}")
        if failed_modules:
            raise RuntimeError(
                "recipe packs 加载失败（知识库不完整，拒绝退化服役）："
                + "; ".join(failed_modules)
            )

    def register(self, recipe: CartographyRecipe) -> None:
        if recipe.id in self._by_id:
            # #1075(D-2): 与 lib/gis 系（register 即 raise）对齐至少留痕 ——
            # 此前静默 return，后续种子编辑可无声遮蔽既有条目。
            logger.warning("recipe %r 重复注册：保留既有条目，忽略新条目", recipe.id)
            return
        self._by_id[recipe.id] = recipe
        for task in recipe.intent_tasks:
            self._by_task.setdefault(task, []).append(recipe)
        wf = recipe.workflow
        if wf is not None:
            self._by_domain.setdefault(wf.domain or "general", []).append(recipe)
            for kw in list(wf.keywords_zh) + list(wf.keywords_en):
                key = kw.strip().lower()
                if key:
                    self._keyword_index.setdefault(key, []).append(recipe)
                    if key.isascii() and key.isalnum():
                        # R1-A5/B1：纯 ASCII 词必须整词命中（"sar" 不得命中
                        # "caesar salad"；与 intent.py 的 lookaround 方案同一
                        # 红线）。非 ASCII（中文）词保持子串语义。
                        self._ascii_keywords.add(key)
        from app.services.gis_harness.workflow_schema import recipe_content_fingerprint
        self._content_fps[recipe.id] = recipe_content_fingerprint(recipe)

    def get(self, recipe_id: str) -> Optional[CartographyRecipe]:
        return self._by_id.get(recipe_id)

    def unregister(self, recipe_id: str) -> bool:
        """ADR-0104：扩展 recipe 卸载回滚用。从 by-id / by-task / by-domain /
        关键词倒排与内容指纹缓存中清干净目标 recipe；目标不存在返回 False
        （幂等）。ASCII 整词集合按剩余倒排重建（关键词可能被多条 recipe
        共享，不能按条目直接摘除）。核心种子 recipe 从不调用。"""
        recipe = self._by_id.get(recipe_id)
        if recipe is None:
            return False
        del self._by_id[recipe_id]
        self._content_fps.pop(recipe_id, None)
        for task in recipe.intent_tasks:
            candidates = self._by_task.get(task)
            if candidates and recipe in candidates:
                candidates.remove(recipe)
                if not candidates:
                    self._by_task.pop(task, None)
        wf = recipe.workflow
        if wf is not None:
            domain_candidates = self._by_domain.get(wf.domain or "general")
            if domain_candidates and recipe in domain_candidates:
                domain_candidates.remove(recipe)
                if not domain_candidates:
                    self._by_domain.pop(wf.domain or "general", None)
            for kw in list(wf.keywords_zh) + list(wf.keywords_en):
                key = kw.strip().lower()
                if not key:
                    continue
                hits = self._keyword_index.get(key)
                if hits and recipe in hits:
                    hits.remove(recipe)
                    if not hits:
                        self._keyword_index.pop(key, None)
        self._ascii_keywords = {
            key for key in self._keyword_index if key.isascii() and key.isalnum()
        }
        return True

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

    def domains(self) -> List[str]:
        """已注册领域包名（排序，catalog/文档生成用）。"""
        return sorted(self._by_domain.keys())

    def recipes_for_domain(self, domain: str) -> List[CartographyRecipe]:
        """领域内 recipe（注册序，确定性）。"""
        return list(self._by_domain.get(domain, []))

    def capability_ids(self) -> List[str]:
        """全部 recipe 引用的 capability id 并集（排序）。"""
        caps: set = set()
        for recipe in self._by_id.values():
            caps.update(getattr(recipe, "preferred_analysis", None) or [])
            caps.update(getattr(recipe, "optional_analysis", None) or [])
            for caps_list in (getattr(recipe, "task_optional_analysis", None) or {}).values():
                caps.update(caps_list or [])
            wf = getattr(recipe, "workflow", None)
            if wf is not None:
                for req in getattr(wf, "data_roles", []) or []:
                    if req.capability_hint:
                        caps.add(req.capability_hint)
        return sorted(caps)

    @property
    def v1_served_tasks(self) -> set:
        """V1 seed（无 workflow 画像）服务的任务族集合。

        「通用产品族保护」的事实来源：这些任务族里 V2 recipe 受资历层
        压制；本体任务升级（gis_ontology.escalation_target）不落在这里
        面的任务族上 —— 专业升级永不越过 seed 保护语义。
        """
        return {
            t for r in self._by_id.values() if r.workflow is None
            for t in r.intent_tasks
        }

    def content_fingerprint(self) -> str:
        """registry 级内容指纹：per-recipe 指纹的 SHA256（C12）。

        workflow 语义变化必然改变本指纹 → 经 runtime manifest 投影传导到
        plan stale 检测（复用既有 manifest 体系，不另造第二套 manifest）。
        """
        import hashlib
        payload = ";".join(
            f"{rid}={self._content_fps.get(rid, '')}" for rid in sorted(self._content_fps)
        )
        return hashlib.sha256(payload.encode("utf-8"), usedforsecurity=False).hexdigest()

    def _keyword_matches(self, keyword: str, lowered_query: str) -> bool:
        """关键词命中判定：中文子串；纯 ASCII 词整词（字母数字边缘判定，
        R1-A5/B1：「sar」不得命中「caesar salad」，与 intent.py 的
        lookaround 方案同一红线）。"""
        if keyword not in self._ascii_keywords:
            return keyword in lowered_query
        # 词缘只看 ASCII 字母（与 intent.py 的 (?<![a-zA-Z]) 方案完全一致，
        # R2-5：数字邻接如 sar2 仍命中 —— intent 规则已路由 task，关键词层
        # 不得再分叉）；汉字邻接不算词内（「看看sar」应命中），ASCII 字母
        # 邻接才算词内（「caesar」不命中 sar）。
        ascii_letters = "abcdefghijklmnopqrstuvwxyz"
        hits = 0
        start = lowered_query.find(keyword)
        while start != -1:
            end = start + len(keyword)
            before = lowered_query[start - 1] if start > 0 else " "
            after = lowered_query[end] if end < len(lowered_query) else " "
            if before not in ascii_letters and after not in ascii_letters:
                hits += 1
            start = lowered_query.find(keyword, start + 1)
        return hits > 0

    def content_fingerprint_of(self, recipe_id: str) -> str:
        """单 recipe 内容指纹（registry 缓存；runtime manifest 投影复用）。

        缓存未命中（只可能来自绕过 register() 的 _by_id 改写）时现场计算，
        绝不静默返回空指纹 —— 空串会无声搅动 manifest 指纹 → 假 stale。
        """
        fp = self._content_fps.get(recipe_id)
        if fp is None:
            recipe = self._by_id.get(recipe_id)
            if recipe is None:
                return ""
            from app.services.gis_harness.workflow_schema import (
                recipe_content_fingerprint,
            )
            fp = recipe_content_fingerprint(recipe)
        return fp

    def keyword_hits(self, query: str) -> List[CartographyRecipe]:
        """query 命中的专业关键词 recipe（去重，命中关键词多者优先）。

        供 select_candidates 的 keyword 路由层与 compiler 阶段 4 使用。
        """
        if not query:
            return []
        low = query.lower()
        hit_count: Dict[int, int] = {}
        hit_recipes: Dict[int, CartographyRecipe] = {}
        for kw, recipes in self._keyword_index.items():
            if self._keyword_matches(kw, low):
                for recipe in recipes:
                    hit_count.setdefault(recipe.id, 0)
                    hit_count[recipe.id] += 1
                    hit_recipes[recipe.id] = recipe
        ordered = sorted(hit_recipes.values(), key=lambda r: (-hit_count[r.id], r.id))
        return ordered

    def select_candidates(
        self,
        intent,
        limit: int = 3,
        project_verified: Optional[set] = None,
    ) -> List[CartographyRecipe]:
        """按 intent 选择候选 recipe（确定性排序）。

        排序键（稳定十一层）：
            1. geometry 期望失配（#781：geometry_expectation=='raster' 时
               非栅格面族候选全部后置——栅格主体绝不推荐 POI 热力族）
            2. seed 资历（任务已有 V1 seed 服务时 V2 recipe 后置）
            3. task 精确命中
            4. V2 通用罚（V2 且无关键词命中则后置）
            5. 专业关键词命中多者优先（V2/Goal C 倒排路由）
            6. 显式制图意图（aggregate_grid / proportional_symbol 直命中）
            7. cartography_intents 交集多
            8. **V3 本体层**（opt-in）：声明 ontology_tasks 的候选与 intent
               本体匹配求交——命中前置、全不命中后置；未声明候选恒 0，
               既有 recipe 排序不受影响
            9. 项目验证加成（ADR-0069）：project recipe_outcome ACTIVE 前置
            10. priority 小（同分稳定排序）
            11. id 字典序兜底

        显式信号那一层解决「同一 distribution_overview 任务下，宽口径
        recipe 交集计数把用户明确的形态词请求压掉」的优先级错置；其余
        回归锚（Golden Case A/D/E）保持原有行为。

        ``project_verified`` 为 None（无项目上下文）时第 9 层恒 0，
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
        # V2 keyword 路由层：一次倒排查询，避免每候选全文扫描。
        keyword_scores: Dict[str, int] = {}
        query = str(getattr(intent, "query", "") or "")
        if query:
            low = query.lower()
            for kw, recipes in self._keyword_index.items():
                if self._keyword_matches(kw, low):
                    for recipe in recipes:
                        keyword_scores[recipe.id] = keyword_scores.get(recipe.id, 0) + 1
        # V1 seed 服务的任务集合：V2 recipe 与 V1 seed 竞争「同一通用任务」
        # 时才有资历压制；新任务族（无 V1 seed）V2 之间正常路由。
        v1_served_tasks = self.v1_served_tasks
        scored: List[tuple] = []
        # V3（GIS Task Ontology）：intent 的本体任务匹配（一次计算，候选
        # 比对为集合交集）。opt-in：无 ontology_tasks 声明的候选不受影响。
        ontology_hits: Optional[set] = None
        if any(r.ontology_tasks for r in self._by_id.values()):
            from app.services.gis_harness.gis_ontology import match_task_ontology
            ontology_hits = {
                m.task_id for m in match_task_ontology(intent, limit=5)
            }
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
            kw_hits = keyword_scores.get(recipe.id, 0)
            v2_generic_penalty = (
                1 if (recipe.workflow is not None and kw_hits == 0) else 0
            )
            # seed_seniority：仅当本任务已有 V1 seed 服务时，V2 后置 ——
            # 通用短语（「各区小学数量」「地表覆盖分布」）保持历史产品族；
            # 新任务族（terrain/watershed/sar/autocorrelation/trend/route）
            # 没有 V1 seed，V2 候选不受压制，由关键词/交集正常路由。
            seed_seniority = (
                1 if (recipe.workflow is not None and task in v1_served_tasks) else 0
            )
            # V3 本体层：声明了 ontology_tasks 的候选，命中 intent 本体匹配
            # → 前置（罚 0），全不命中 → 后置（罚 1）；未声明的候选恒 0
            # —— 既有 recipe（不声明）排序完全不变（纯加法层）。
            if recipe.ontology_tasks and ontology_hits is not None:
                ontology_penalty = (
                    0 if ontology_hits & set(recipe.ontology_tasks) else 1
                )
            else:
                ontology_penalty = 0
            score = (
                geometry_mismatch,
                seed_seniority,
                0 if task_hit else 1,
                v2_generic_penalty,
                -kw_hits,
                0 if (explicit is not None and explicit_hit) else (
                    1 if explicit is not None else 0
                ),
                -cart_hit,
                ontology_penalty,
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
        # R2-2：先构建后赋值 —— 加载失败（知识库不完整）时不留半量单例，
        # 每次访问重新抛错；生产 startup 的 try/except 只能记日志，不能把
        # 半量 registry 当作健康实例缓存下来静默服役。
        candidate = RecipeRegistry()
        candidate.load_builtins()
        _registry = candidate
    return _registry


def reset_recipe_registry() -> None:
    global _registry
    _registry = None


__all__ = [
    "CartographyRecipe",
    "EligibilityRule",
    "FieldExpectation",
    "RecipeFallback",
    "FallbackLink",
    "DEFAULT_FALLBACK_CHAIN",
    "EligibilityReport",
    "DisabledElement",
    "FallbackDecision",
    "FieldFacts",
    "DistributionFacts",
    "SpatialFacts",
    "TemporalFacts",
    "EligibilityContext",
    "CheckResult",
    "FallbackAttempt",
    "ChainResolution",
    "SEED_RECIPES",
    "RecipeRegistry",
    "get_recipe_registry",
    "reset_recipe_registry",
    "check_eligibility",
    "check_sample_size",
    "check_field_cardinality",
    "check_missing_ratio",
    "check_distribution_shape",
    "check_crs_and_scale",
    "check_temporal_coverage",
    "run_eligibility_rules",
    "resolve_fallback_chain",
    "render_fallback_for_llm",
    "default_charts_for_task",
    "default_statistics_for_task",
    "SAMPLE_HARD_FLOOR",
    "SAMPLE_TIER_SMALL_MAX",
    "SAMPLE_TIER_MEDIUM_MAX",
    "WorkflowProfile",       # V2 re-export（recipe packs / 测试 / 文档用）
    "RECIPE_SCHEMA_VERSION",
]
