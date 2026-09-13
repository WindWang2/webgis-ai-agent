"""GIS Skill 语义词汇层 —— 统计 / 地理 / 时间语义（ADR-0182 §2.3）。

Skill 不仅要声明"做什么步骤"，还必须声明这些步骤在统计、地理、时间三个
轴上的**语义约束**。三个轴的词表是稳定契约：进入 selection evidence 与
replay 报告，文案可演进，值不复用为其它含义。

红线（与 workflow_schema / gis_ontology 一致）：

- 词表是声明不是实现：分母核查的字段事实面复用
  ``workflow_schema.DENOMINATOR_FIELD_HINTS``，时间字段事实复用
  ``workflow_schema.TIME_FIELD_HINTS``，本模块不重复实现科学语义；
- 全部确定性：同输入同输出，零 LLM、零 I/O。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import Field

from app.services.gis_harness.skills._base import SkillAssetModel

# ── 统计语义（S10）───────────────────────────────────────────────────────

#: 度量语义词表：一个数值结论"是什么 kind 的量"，不可随意互换。
MEASURE_SEMANTICS = (
    "count",        # 原始计数（如 各区学校数）
    "rate",         # 率（分子/分母，如 每千人发病率）
    "density",      # 密度（单位面积/单位人口数量）
    "percentage",   # 占比（0-100）
    "index",        # 指数（无量纲复合量，如 NDVI）
    "mean",         # 均值类统计量
    "area_share",   # 面积占比 / 面积账目
)

#: 需要分母（归一化基准）证据的度量语义。对这类度量下结论时，replay 必须
#: 能在 evidence 中找到分母覆盖，否则 obligation 判 missing。
DENOMINATOR_REQUIRED_MEASURES = ("rate", "density", "percentage", "area_share")

#: 统计语义义务的动作词表（对齐 workflow_schema.OBLIGATION_ACTIONS 语义）。
STATISTICAL_OBLIGATION_ACTIONS = ("block_conclusion", "disclose", "warn")


class StatisticalSemantics(SkillAssetModel):
    """Skill 的统计语义声明。

    ``measure_semantics``：本技能产出的数值结论的度量语义集合；
    ``forbidden_transformations``：禁止的静默变换（如 count → rate）——
    只有 goal 显式要求时才允许（disclose 变换）；
    ``normalization``：归一化义务描述（分母是什么、缺失时的行为）。
    """
    measure_semantics: List[str] = Field(default_factory=list)   # ⊆ MEASURE_SEMANTICS
    forbidden_transformations: List[str] = Field(default_factory=list)
    normalization: str = ""
    denominator_required: bool = False

    def validate_vocabulary(self) -> List[str]:
        violations: List[str] = []
        for m in self.measure_semantics:
            if m not in MEASURE_SEMANTICS:
                violations.append(f"statistical.measure_semantics: unknown {m}")
        if self.denominator_required and not (
                set(self.measure_semantics) & set(DENOMINATOR_REQUIRED_MEASURES)):
            violations.append(
                "statistical.denominator_required=true 但 measure_semantics "
                "不含需分母的度量（rate/density/percentage/area_share）")
        # 反向一致性：度量集**全部**是需分母度量却声明无需分母 = 明确矛盾
        # （混合度量集如 count+density 允许 false：分母义务由分支级步骤证据
        # 承担，见 ADR-0182 §2.3）。
        if (not self.denominator_required and self.measure_semantics
                and set(self.measure_semantics) <= set(DENOMINATOR_REQUIRED_MEASURES)):
            violations.append(
                "statistical.denominator_required=false 但 measure_semantics "
                "全部为需分母度量（rate/density/percentage/area_share）")
        return violations


# ── 地理语义（S11）───────────────────────────────────────────────────────

#: 地理单元层级词表（与 intent.ScopeIntent.level 同向，扩展分析面）。
GEOGRAPHIC_UNITS = (
    "city", "district", "province", "country", "grid", "watershed",
    "custom_polygon", "point", "line", "polygon", "network", "raster_cell",
    "unknown",
)


class GeographicSemantics(SkillAssetModel):
    """Skill 的地理语义声明：scope / analysis / aggregation / display 四分离。

    例：成都学校分布 —— scope=city、analysis_unit=point、
    aggregation_unit=district、display_unit=point+district。
    四个层级可能不同，不得混为一个 geographic level。
    """
    scope_unit: str = "unknown"            # 分析范围层级 ⊆ GEOGRAPHIC_UNITS
    analysis_unit: str = "unknown"         # 分析单元（最小被统计对象）
    aggregation_unit: str = ""             # 聚合单元（空 = 不聚合）
    display_unit: List[str] = Field(default_factory=list)  # 展示单元（可多图层）

    def validate_vocabulary(self) -> List[str]:
        violations: List[str] = []
        if self.scope_unit not in GEOGRAPHIC_UNITS:
            violations.append(f"geographic.scope_unit: unknown {self.scope_unit}")
        if self.analysis_unit not in GEOGRAPHIC_UNITS:
            violations.append(f"geographic.analysis_unit: unknown {self.analysis_unit}")
        if self.aggregation_unit and self.aggregation_unit not in GEOGRAPHIC_UNITS:
            violations.append(
                f"geographic.aggregation_unit: unknown {self.aggregation_unit}")
        for u in self.display_unit:
            if u not in GEOGRAPHIC_UNITS:
                violations.append(f"geographic.display_unit: unknown {u}")
        return violations


# ── 时间语义（S12）───────────────────────────────────────────────────────

#: 时间作业模式词表。
TEMPORAL_MODES = (
    "snapshot",      # 单期快照
    "comparison",    # 两期对比
    "trend",         # 多期趋势（≥3 期，斜率/变点）
    "seasonal",      # 季节性
    "before_after",  # 事件前后
)

#: 可比性义务词表：comparison/before_after 模式必须逐条声明并可在 replay
#: 中对照 evidence 验证。
COMPARABILITY_OBLIGATIONS = (
    "same_extent",            # 两期空间范围一致
    "compatible_classes",     # 分类体系/口径兼容
    "comparable_source",      # 数据源/传感器/分辨率可比
    "time_labeling",          # 产物必须带明确期次标注
    "change_semantics",       # 变化语义（增益/损失/净变化）显式声明
)


class TemporalSemantics(SkillAssetModel):
    """Skill 的时间语义声明。"""
    temporal_mode: str = "snapshot"        # ⊆ TEMPORAL_MODES
    min_periods: int = 1                   # 最少期数（trend ≥3 等）
    comparability: List[str] = Field(default_factory=list)  # ⊆ COMPARABILITY_OBLIGATIONS

    def validate_vocabulary(self) -> List[str]:
        violations: List[str] = []
        if self.temporal_mode not in TEMPORAL_MODES:
            violations.append(f"temporal.temporal_mode: unknown {self.temporal_mode}")
        if self.min_periods < 1:
            violations.append("temporal.min_periods: 必须 ≥1")
        for c in self.comparability:
            if c not in COMPARABILITY_OBLIGATIONS:
                violations.append(f"temporal.comparability: unknown {c}")
        if self.temporal_mode in ("comparison", "before_after") and self.min_periods < 2:
            violations.append(
                f"temporal: {self.temporal_mode} 模式 min_periods 必须 ≥2")
        if self.temporal_mode == "trend" and self.min_periods < 3:
            violations.append("temporal: trend 模式 min_periods 必须 ≥3")
        return violations


def validate_semantics(
    statistical: Optional[StatisticalSemantics],
    geographic: Optional[GeographicSemantics],
    temporal: Optional[TemporalSemantics],
) -> List[str]:
    """三轴词汇校验的统一入口（validation.py 复用）。"""
    violations: List[str] = []
    if statistical is not None:
        violations.extend(statistical.validate_vocabulary())
    if geographic is not None:
        violations.extend(geographic.validate_vocabulary())
    if temporal is not None:
        violations.extend(temporal.validate_vocabulary())
    return violations


__all__ = [
    "MEASURE_SEMANTICS",
    "DENOMINATOR_REQUIRED_MEASURES",
    "STATISTICAL_OBLIGATION_ACTIONS",
    "GEOGRAPHIC_UNITS",
    "TEMPORAL_MODES",
    "COMPARABILITY_OBLIGATIONS",
    "StatisticalSemantics",
    "GeographicSemantics",
    "TemporalSemantics",
    "validate_semantics",
]
