"""Methodology Descriptor V2 —— 方法级知识增强层（Epic 11 §5.C）。

对 MethodologyRegistry 的每个候选方法（``method_id`` 为键）补充
V4 候选表没有承载的方法学知识：

    problem_class（问题类型学）
    input_constraints / assumptions / parameters
    alternatives（同族或跨族的合法替代，⊆ method 词表）
    computational_class（计算类型学）
    uncertainty_support（不确定性支持度）
    invalid_when / degraded_when（结构化失效条件，引用维度词表）
    visualization_guidance（viz family 词表，桥接 viz_bridge）
    assumes_continuous_measure（量测连续性假设——categorical 数据
      × 连续假设方法 = 资格拒绝的科学依据，R1-F4）
    provenance_id（出处登记引用）

红线：

- **引用字段不复制**：capabilities/algorithm_ids/geometry_kinds 等
  仍以 V4 MethodCandidate 为唯一事实源；本表校验 method_id 存在性
  （悬空 fatal），替代方法 id 同样对账；
- 有界：assumptions/invalid_when 每条 ≤8 项、字符串截断；
- 零 LLM、零 I/O；表是代码内审定工件（provenance 记录出处）。

消费方：qualification 引擎（assumes_continuous_measure / invalid_when）、
ranking（uncertainty_support / computational_class → cost_fit）、
KnowledgeService.explain（assumptions/parameters 披露）、viz_bridge
（visualization_guidance）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

#: descriptor schema 版本（进指纹）。
DESCRIPTOR_SCHEMA_VERSION = 2

#: 问题类型学（封闭词表）。
ProblemClass = (
    "display",             # 描述展示（无推断）
    "aggregate",           # 聚合统计
    "density_estimation",  # 密度估计
    "surface_prediction",  # 连续面预测
    "significance_testing",  # 统计显著性检验
    "pattern_detection",   # 模式/结构探测
    "routing",             # 路径/网络
    "zone_definition",     # 邻近域/服务区界定
    "overlay_composition", # 叠加合成
    "scoring",             # 多准则评分
    "transform_derivation",  # 派生变换（地形/指数）
    "change_quantification",  # 变化量化
    "composition",         # 多产品组合
)

#: 计算类型学（封闭词表；ranking cost_fit 消费）。
ComputationalClass = (
    "closed_form",     # 封闭解（低成本）
    "iterative",       # 迭代求解
    "local_neighborhood",  # 邻域算子（栅格窗口）
    "combinatorial",   # 组合/图搜索
    "simulation",      # 模拟/随机化
)

#: 不确定性支持度（封闭词表）。
UncertaintySupport = (
    "none",        # 不产出不确定性
    "disclosure_only",  # 仅文字披露
    "analytic",    # 解析方差/标准误
    "field",       # 逐点不确定性面
    "ensemble",    # 模拟集合
)

#: 资格维度词表（与 qualification 引擎共享的稳定词表）。
QUALIFICATION_DIMENSIONS = (
    "geometry",
    "sample_size",
    "crs_scale",
    "measure_semantics",
    "temporal",
    "nodata_quality",
    "data_roles",
    "scientific_precondition",
)


class InvalidationCondition(BaseModel):
    """一条结构化失效条件（维度 + 判据描述；机器可读裁决在引擎层）。"""

    dimension: str                  # ⊆ QUALIFICATION_DIMENSIONS
    condition: str = ""             # 人类可读判据（有界）
    severity: str = "reject"        # reject / degrade

    @field_validator("dimension")
    @classmethod
    def _known_dimension(cls, v: str) -> str:
        if v not in QUALIFICATION_DIMENSIONS:
            raise ValueError(f"unknown invalidation dimension: {v}")
        return v

    @field_validator("condition")
    @classmethod
    def _bounded(cls, v: str) -> str:
        return v[:160]


class MethodologyDescriptorV2(BaseModel):
    """一个方法的方法学增强描述（引用 V4 候选，不复制其事实）。"""

    method_id: str
    problem_class: str                    # ⊆ ProblemClass
    computational_class: str = "iterative"   # ⊆ ComputationalClass
    uncertainty_support: str = "none"     # ⊆ UncertaintySupport
    #: 量测连续性假设（R1-F4）：True = 假定 measure 连续
    #: （categorical 字段进入资格引擎时按 measure_semantics 拒绝）。
    assumes_continuous_measure: bool = False
    assumptions: Tuple[str, ...] = ()
    input_constraints: Tuple[str, ...] = ()
    parameters: Tuple[Tuple[str, str], ...] = ()   # (名称, 约束说明) ≤8
    alternatives: Tuple[str, ...] = ()    # ⊆ method 词表（校验）
    invalid_when: Tuple[InvalidationCondition, ...] = ()
    degraded_when: Tuple[InvalidationCondition, ...] = ()
    visualization_guidance: Tuple[str, ...] = ()   # ⊆ viz family 词表（桥接层对账）
    provenance_id: str = "prov.method_enrichment.curated"

    @field_validator("assumptions", "input_constraints")
    @classmethod
    def _bounded_lists(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(str(x)[:160] for x in tuple(v)[:6])

    @field_validator("parameters")
    @classmethod
    def _bounded_params(
        cls, v: Tuple[Tuple[str, str], ...],
    ) -> Tuple[Tuple[str, str], ...]:
        out = [(str(n)[:40], str(d)[:120]) for n, d in tuple(v)[:8]]
        return tuple(out)

    @field_validator("visualization_guidance")
    @classmethod
    def _bounded_viz(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(str(x)[:40] for x in tuple(v)[:6])

    @model_validator(mode="after")
    def _cross_check(self) -> "MethodologyDescriptorV2":
        if self.problem_class not in ProblemClass:
            raise ValueError(
                f"{self.method_id}: unknown problem_class {self.problem_class!r}")
        if self.computational_class not in ComputationalClass:
            raise ValueError(
                f"{self.method_id}: unknown computational_class "
                f"{self.computational_class!r}")
        if self.uncertainty_support not in UncertaintySupport:
            raise ValueError(
                f"{self.method_id}: unknown uncertainty_support "
                f"{self.uncertainty_support!r}")
        return self

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "method_id": self.method_id[:64],
            "problem_class": self.problem_class[:24],
            "uncertainty": self.uncertainty_support[:16],
            "assumes_continuous_measure": self.assumes_continuous_measure,
            "assumptions": list(self.assumptions[:4]),
            "alternatives": list(self.alternatives[:4]),
            "invalid_when": [
                {"dim": c.dimension[:24], "sev": c.severity[:8]}
                for c in self.invalid_when[:4]
            ],
        }


class MethodDescriptorRegistry:
    """方法增强描述登记表：O(1) by method_id + 对账校验 + 指纹。

    ``method_exists`` 注入（缺省延迟对接 V4 单例）；alternatives 与
    visualization_guidance 的存在性在 validate 时对账（viz family 词表
    由调用方传入，避免与 viz_bridge 循环依赖）。
    """

    def __init__(
        self,
        descriptors: Tuple[MethodologyDescriptorV2, ...],
        *,
        method_exists: Optional[Callable[[str], bool]] = None,
    ) -> None:
        if method_exists is None:
            method_exists = _default_method_exists()
        self._method_exists = method_exists
        self._by_id: Dict[str, MethodologyDescriptorV2] = {}
        for d in descriptors:
            if d.method_id in self._by_id:
                raise ValueError(f"duplicate method descriptor: {d.method_id}")
            self._by_id[d.method_id] = d

    def get(self, method_id: str) -> Optional[MethodologyDescriptorV2]:
        return self._by_id.get(method_id)

    def has(self, method_id: str) -> bool:
        return method_id in self._by_id

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def validate(
        self,
        *,
        viz_family_exists: Optional[Callable[[str], bool]] = None,
    ) -> List[str]:
        violations: List[str] = []
        for mid, d in self._by_id.items():
            tag = f"method_descriptor[{mid}]"
            if not self._method_exists(mid):
                violations.append(f"{tag}: method_id 不在 V4 候选表")
            for alt in d.alternatives:
                if not self._method_exists(alt):
                    violations.append(f"{tag}: alternative {alt} 不存在")
                if alt == mid:
                    violations.append(f"{tag}: alternative 指向自身")
            for cond in d.invalid_when + d.degraded_when:
                if cond.dimension not in QUALIFICATION_DIMENSIONS:
                    violations.append(f"{tag}: unknown dimension {cond.dimension}")
            for vf in d.visualization_guidance:
                if viz_family_exists and not viz_family_exists(vf):
                    violations.append(f"{tag}: viz family {vf} 未注册")
        return violations

    def fingerprint(self) -> str:
        payload = {
            "version": DESCRIPTOR_SCHEMA_VERSION,
            "descriptors": [
                self._by_id[k].model_dump(mode="json")
                for k in sorted(self._by_id)
            ],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _default_method_exists() -> Callable[[str], bool]:
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )
    reg = get_methodology_registry()
    return lambda mid: reg.method(mid) is not None


def _d(**kw: Any) -> MethodologyDescriptorV2:
    kw.setdefault("provenance_id", "prov.method_enrichment.curated")
    return MethodologyDescriptorV2(**kw)


def _ic(dim: str, cond: str, severity: str = "reject") -> InvalidationCondition:
    return InvalidationCondition(dimension=dim, condition=cond, severity=severity)


#: 审定增强表（51 方法全覆盖；纯加法演进——新方法必须补条目，validate 报缺）。
_CURATED_DESCRIPTORS: Tuple[MethodologyDescriptorV2, ...] = (
    # ── descriptive_mapping ─────────────────────────────────────────
    _d(method_id="descriptive.category_breakdown", problem_class="aggregate",
       computational_class="closed_form", uncertainty_support="none",
       assumptions=("类别字段语义可信（无系统性错分披露）",),
       parameters=(("category_field", "分类字段名"), ("top_n", "展示上限（默认 10）")),
       alternatives=("descriptive.choropleth_display",),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="descriptive.simple_display", problem_class="display",
       computational_class="closed_form", uncertainty_support="none",
       assumptions=("点位即真实位置（采集完备性不声明）",),
       alternatives=("descriptive.category_breakdown",),
       visualization_guidance=("point_distribution",)),
    _d(method_id="descriptive.choropleth_display", problem_class="display",
       computational_class="closed_form", uncertainty_support="none",
       assumptions=("数值为可直接比较的属性（非率时提示归一化）",),
       alternatives=("zonal.admin_stats",),
       visualization_guidance=("choropleth_raw",)),
    # ── distribution_density ───────────────────────────────────────
    _d(method_id="density.kernel_surface", problem_class="density_estimation",
       computational_class="local_neighborhood", uncertainty_support="none",
       assumes_continuous_measure=False,
       assumptions=("点过程平稳性（带宽内密度缓变）", "带宽选择影响平滑度"),
       parameters=(("bandwidth", "核带宽（米，投影 CRS）"), ("cell_size", "输出格网")),
       alternatives=("density.grid_binning", "density.admin_rate"),
       invalid_when=(_ic("geometry", "主体非点几何（KDE 需点支持）"),),
       degraded_when=(_ic("sample_size", "样本 <10：核估计不稳定", "degrade"),),
       visualization_guidance=("density_surface",)),
    _d(method_id="density.admin_rate", problem_class="aggregate",
       computational_class="closed_form", uncertainty_support="analytic",
       assumptions=("分母（面积/人口）语义正确且非零",),
       parameters=(("rate_field", "率/密度分母字段"),),
       alternatives=("density.grid_binning",),
       invalid_when=(_ic("data_roles", "分母角色缺失时不得输出率结论"),),
       visualization_guidance=("choropleth_normalized",)),
    _d(method_id="density.grid_binning", problem_class="aggregate",
       computational_class="closed_form", uncertainty_support="none",
       parameters=(("grid", "H3 分辨率/渔网尺寸"),),
       alternatives=("density.kernel_surface", "density.admin_rate"),
       visualization_guidance=("choropleth_raw",)),
    _d(method_id="density.visual_heatmap", problem_class="display",
       computational_class="local_neighborhood", uncertainty_support="none",
       assumptions=("仅视觉渲染代理——不构成定量密度估计",),
       alternatives=("density.kernel_surface",),
       degraded_when=(_ic("measure_semantics", "需要定量结论时视觉热力不适用",
                          "degrade"),),
       visualization_guidance=("density_surface",)),
    # ── interpolation ──────────────────────────────────────────────
    _d(method_id="interp.ordinary_kriging", problem_class="surface_prediction",
       computational_class="iterative", uncertainty_support="field",
       assumes_continuous_measure=True,
       assumptions=("二阶平稳（均值常数）", "变异函数模型拟合可靠"),
       parameters=(("variogram_model", "理论变异函数"), ("n_neighbors", "邻点数")),
       alternatives=("interp.idw", "interp.indicator_kriging"),
       invalid_when=(
           _ic("measure_semantics", "类别字段 × 连续量假设：用指示克里金"),
           _ic("geometry", "主体非点观测"),
       ),
       degraded_when=(_ic("sample_size", "样本 <30：方差估计不可靠", "degrade"),),
       visualization_guidance=("interpolation_surface", "uncertainty_overlay")),
    _d(method_id="interp.regression_kriging", problem_class="surface_prediction",
       computational_class="iterative", uncertainty_support="field",
       assumes_continuous_measure=True,
       assumptions=("协变量与目标存在线性关系", "残差二阶平稳"),
       parameters=(("covariates", "环境协变量栅格"),),
       alternatives=("interp.ordinary_kriging",),
       invalid_when=(_ic("measure_semantics", "类别字段 × 连续量假设"),),
       visualization_guidance=("interpolation_surface",)),
    _d(method_id="interp.idw", problem_class="surface_prediction",
       computational_class="closed_form", uncertainty_support="none",
       assumes_continuous_measure=True,
       assumptions=("局部同质性（权重仅随距离衰减）",),
       parameters=(("power", "距离幂（默认 2）"),),
       alternatives=("interp.ordinary_kriging",),
       invalid_when=(_ic("measure_semantics", "类别字段 × 连续量假设"),),
       visualization_guidance=("interpolation_surface",)),
    _d(method_id="interp.trend_surface", problem_class="surface_prediction",
       computational_class="closed_form", uncertainty_support="none",
       assumes_continuous_measure=True,
       assumptions=("全局多项式趋势可近似场结构（忽略局部细节）",),
       parameters=(("degree", "多项式阶数（1-3）"),),
       alternatives=("interp.idw",),
       invalid_when=(_ic("measure_semantics", "类别字段 × 连续量假设"),),
       visualization_guidance=("interpolation_surface",)),
    _d(method_id="interp.indicator_kriging", problem_class="surface_prediction",
       computational_class="iterative", uncertainty_support="field",
       assumes_continuous_measure=False,
       assumptions=("指示变换后二阶平稳", "阈值/类别定义明确"),
       parameters=(("thresholds", "指示阈值或类别表"),),
       alternatives=("interp.ordinary_kriging",),
       invalid_when=(_ic("geometry", "主体非点观测"),),
       visualization_guidance=("interpolation_surface", "uncertainty_overlay")),
    # ── zonal_statistics ───────────────────────────────────────────
    _d(method_id="zonal.admin_stats", problem_class="aggregate",
       computational_class="closed_form", uncertainty_support="none",
       parameters=(("stat", "count/sum/mean"), ("group_by", "行政单元字段")),
       alternatives=("zonal.grid_stats",),
       visualization_guidance=("statistics_chart", "choropleth_raw")),
    _d(method_id="zonal.grid_stats", problem_class="aggregate",
       computational_class="closed_form", uncertainty_support="none",
       parameters=(("grid", "格网尺寸/分辨率"),),
       alternatives=("zonal.admin_stats",),
       visualization_guidance=("choropleth_raw",)),
    _d(method_id="zonal.areal_crosswalk", problem_class="aggregate",
       computational_class="closed_form", uncertainty_support="analytic",
       assumptions=("面积权重假设（均匀分布）——面插值固有近似",),
       alternatives=("zonal.admin_stats",),
       visualization_guidance=("choropleth_normalized",)),
    # ── suitability ────────────────────────────────────────────────
    _d(method_id="suit.weighted_overlay", problem_class="scoring",
       computational_class="local_neighborhood", uncertainty_support="none",
       assumptions=("因子线性可加", "权重由决策方显式给定（不合成）"),
       parameters=(("weights", "因子权重表"), ("standardization", "标准化方法")),
       alternatives=("suit.constraint_filter", "mcda.wsm"),
       invalid_when=(_ic("data_roles", "准则角色缺失：无因子可加权"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="suit.constraint_filter", problem_class="zone_definition",
       computational_class="closed_form", uncertainty_support="none",
       parameters=(("constraints", "硬约束集"),),
       alternatives=("suit.weighted_overlay",),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="suit.overlay_composite", problem_class="overlay_composition",
       computational_class="closed_form", uncertainty_support="none",
       parameters=(("operation", "intersect/union/erase"),),
       alternatives=("suit.constraint_filter",),
       visualization_guidance=("statistics_chart",)),
    # ── network ────────────────────────────────────────────────────
    _d(method_id="network.shortest_path", problem_class="routing",
       computational_class="combinatorial", uncertainty_support="none",
       assumptions=("路网拓扑连通且阻抗字段正确",),
       parameters=(("impedance", "距离/时间阻抗"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="network.service_area", problem_class="zone_definition",
       computational_class="combinatorial", uncertainty_support="none",
       assumptions=("路网数据覆盖完整（断链会低估服务域）",),
       parameters=(("cutoffs", "服务阈值（分钟/米）"),),
       alternatives=("proximity.euclidean_buffer",),
       invalid_when=(_ic("data_roles", "路网角色缺失：不可伪称路网可达"),),
       visualization_guidance=("service_area",)),
    _d(method_id="network.gravity_accessibility", problem_class="scoring",
       computational_class="closed_form", uncertainty_support="none",
       assumptions=("质量-距离衰减函数形式给定",),
       parameters=(("beta", "距离衰减系数"),),
       alternatives=("network.service_area",),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="network.od_flow", problem_class="routing",
       computational_class="combinatorial", uncertainty_support="none",
       parameters=(("od_table", "OD 需求表"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="network.facility_location", problem_class="scoring",
       computational_class="simulation", uncertainty_support="none",
       assumptions=("需求分布已知", "候选点集给定"),
       parameters=(("p", "设施书中数量"),),
       alternatives=("mcda.wsm",),
       visualization_guidance=("statistics_chart",)),
    # ── terrain_hydrology ──────────────────────────────────────────
    _d(method_id="terrain.slope_aspect", problem_class="transform_derivation",
       computational_class="local_neighborhood", uncertainty_support="none",
       assumptions=("DEM 垂直基准一致", "局部度量 CRS（坡度以度/百分比）"),
       parameters=(("method", "Horn/Zevenbergen"),),
       invalid_when=(_ic("crs_scale", "地理坐标系下坡度无意义（需重投影）"),),
       visualization_guidance=("terrain_derivative",)),
    _d(method_id="terrain.watershed", problem_class="zone_definition",
       computational_class="local_neighborhood", uncertainty_support="none",
       assumptions=("DEM 已填洼", "局部度量 CRS"),
       parameters=(("pour_points", "出水口"),),
       invalid_when=(_ic("crs_scale", "地理坐标系下流向计算失效"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="terrain.stream_network", problem_class="transform_derivation",
       computational_class="local_neighborhood", uncertainty_support="none",
       assumptions=("汇流累积阈值合理",),
       parameters=(("threshold", "河网启动阈值"),),
       invalid_when=(_ic("crs_scale", "地理坐标系下流向失效"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="terrain.indices", problem_class="transform_derivation",
       computational_class="local_neighborhood", uncertainty_support="none",
       assumptions=("TWI/LS 的坡长近似适用于研究尺度",),
       parameters=(("index", "twi/ls"),),
       invalid_when=(_ic("crs_scale", "地形指数需米制单位"),),
       visualization_guidance=("terrain_derivative",)),
    # ── remote_sensing ─────────────────────────────────────────────
    _d(method_id="rs.spectral_index", problem_class="transform_derivation",
       computational_class="closed_form", uncertainty_support="none",
       assumptions=("波段定标/大气校正状态已知",),
       parameters=(("index", "ndvi/ndwi/…"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="rs.classification", problem_class="pattern_detection",
       computational_class="iterative", uncertainty_support="disclosure_only",
       assumptions=("训练样本代表性", "类别体系封闭"),
       parameters=(("classifier", "rf/svm/…"), ("training", "样本集")),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="rs.anomaly_detection", problem_class="pattern_detection",
       computational_class="iterative", uncertainty_support="analytic",
       parameters=(("alpha", "显著性水平"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="rs.sar_calibration", problem_class="transform_derivation",
       computational_class="closed_form", uncertainty_support="none",
       assumptions=("定标参数（LUT）来自产品元数据",),
       parameters=(("product", "GRC/SLC"),),
       visualization_guidance=("statistics_chart",)),
    # ── change_detection ───────────────────────────────────────────
    _d(method_id="change.bi_temporal_raster", problem_class="change_quantification",
       computational_class="local_neighborhood", uncertainty_support="none",
       assumptions=("两期辐射基准可比（定标/大气一致）",),
       parameters=(("method", "diff/cva"),),
       invalid_when=(_ic("temporal", "缺少第二时相"),),
       visualization_guidance=("change_map",)),
    _d(method_id="change.post_classification", problem_class="change_quantification",
       computational_class="closed_form", uncertainty_support="disclosure_only",
       assumptions=("两期分类体系一致（误差传递披露）",),
       parameters=(("classes", "共同类别表"),),
       invalid_when=(_ic("temporal", "缺少第二时相"),),
       visualization_guidance=("change_map",)),
    _d(method_id="change.temporal_trend", problem_class="change_quantification",
       computational_class="iterative", uncertainty_support="analytic",
       assumes_continuous_measure=True,
       assumptions=("时序足够长且采样规则",),
       parameters=(("method", "ols/mann-kendall"),),
       invalid_when=(_ic("temporal", "时间字段缺失"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="change.sar_log_ratio", problem_class="change_quantification",
       computational_class="local_neighborhood", uncertainty_support="analytic",
       assumptions=("两期 SAR 已定标且配准",),
       parameters=(("nlooks", "视数（斑噪模型）"),),
       invalid_when=(_ic("temporal", "缺少第二时相"),),
       visualization_guidance=("change_map",)),
    # ── spatial_statistics ─────────────────────────────────────────
    _d(method_id="stats.global_autocorrelation", problem_class="significance_testing",
       computational_class="closed_form", uncertainty_support="analytic",
       assumes_continuous_measure=True,
       assumptions=("空间权重矩阵定义明确", "无整体趋势（平稳）"),
       parameters=(("weights", "contiguity/distance-band"),),
       invalid_when=(
           _ic("measure_semantics", "无数值量测字段"),
           _ic("sample_size", "样本 <8：检验功效不足"),
       ),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="stats.local_cluster", problem_class="significance_testing",
       computational_class="closed_form", uncertainty_support="analytic",
       assumes_continuous_measure=True,
       assumptions=("全局检验先行（局部多重比较校正）",),
       parameters=(("correction", "fdr/bonferroni"),),
       invalid_when=(
           _ic("measure_semantics", "无数值量测字段"),
           _ic("sample_size", "样本 <20：局部检验不稳定"),
       ),
       visualization_guidance=("hotspot_significance",)),
    _d(method_id="stats.spatial_regression", problem_class="significance_testing",
       computational_class="iterative", uncertainty_support="analytic",
       assumes_continuous_measure=True,
       assumptions=("残差空间依赖形式（lag/error）已诊断",),
       parameters=(("model", "ols/lag/error/gwr"),),
       invalid_when=(_ic("sample_size", "样本 <30：回归不稳定"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="stats.weights_diagnostics", problem_class="pattern_detection",
       computational_class="closed_form", uncertainty_support="none",
       parameters=(("weights", "待诊断权重"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="stats.point_cluster_dbscan", problem_class="pattern_detection",
       computational_class="iterative", uncertainty_support="disclosure_only",
       assumes_continuous_measure=False,
       assumptions=("DBSCAN 邻域参数决定簇粒度（敏感披露）",),
       parameters=(("eps", "邻域半径"), ("min_samples", "核心点阈值")),
       invalid_when=(_ic("geometry", "主体非点几何"),),
       degraded_when=(_ic("sample_size", "样本 <10：簇结构不可靠", "degrade"),),
       visualization_guidance=("cluster_map",)),
    _d(method_id="stats.space_time_pattern", problem_class="significance_testing",
       computational_class="simulation", uncertainty_support="analytic",
       assumptions=("时间戳质量可靠", "时空权重定义明确"),
       parameters=(("permutations", "随机化次数"),),
       invalid_when=(
           _ic("temporal", "时间字段缺失"),
           _ic("geometry", "主体非点几何"),
       ),
       visualization_guidance=("hotspot_significance",)),
    # ── multi_criteria ─────────────────────────────────────────────
    _d(method_id="mcda.wsm", problem_class="scoring",
       computational_class="closed_form", uncertainty_support="disclosure_only",
       assumptions=("因子线性可加且权重显式（不合成）",),
       parameters=(("weights", "权重表"),),
       alternatives=("suit.weighted_overlay",),
       invalid_when=(_ic("data_roles", "准则角色缺失"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="mcda.risk_exposure", problem_class="scoring",
       computational_class="closed_form", uncertainty_support="none",
       assumptions=("危险性 × 受体叠合语义经决策方确认",),
       parameters=(("hazard_layers", "危险因子"), ("receptors", "受体层")),
       invalid_when=(_ic("data_roles", "危险或受体角色缺失"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="mcda.vulnerability", problem_class="scoring",
       computational_class="closed_form", uncertainty_support="none",
       assumptions=("脆弱性指标体系给定",),
       parameters=(("indicators", "指标表"),),
       invalid_when=(_ic("data_roles", "人口/准则角色缺失"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="mcda.spatial_equity", problem_class="scoring",
       computational_class="closed_form", uncertainty_support="analytic",
       assumptions=("分母（人口/面积）在场且语义正确",),
       parameters=(("denominator", "分母字段"),),
       invalid_when=(_ic("data_roles", "分母缺失：不得下公平性结论"),),
       visualization_guidance=("choropleth_normalized",)),
    # ── compositional_mapping ──────────────────────────────────────
    _d(method_id="compose.comparison_map", problem_class="composition",
       computational_class="closed_form", uncertainty_support="none",
       parameters=(("layout", "并排/卷帘"),),
       invalid_when=(_ic("temporal", "缺少对比期数据"),),
       visualization_guidance=("comparison_frame",)),
    _d(method_id="compose.statistical_map", problem_class="composition",
       computational_class="closed_form", uncertainty_support="none",
       parameters=(("chart_kinds", "附图类型"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="compose.report_map", problem_class="composition",
       computational_class="closed_form", uncertainty_support="none",
       parameters=(("layout_profile", "academic/report"),),
       visualization_guidance=("point_distribution",)),
    # ── proximity ──────────────────────────────────────────────────
    _d(method_id="proximity.multi_ring_buffer", problem_class="zone_definition",
       computational_class="closed_form", uncertainty_support="none",
       assumptions=("欧氏距离（非路网/地形距离）",),
       parameters=(("radii", "环半径序列（米）"),),
       alternatives=("proximity.euclidean_buffer", "network.service_area"),
       invalid_when=(_ic("geometry", "主体无几何"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="proximity.euclidean_buffer", problem_class="zone_definition",
       computational_class="closed_form", uncertainty_support="none",
       assumptions=("欧氏距离（实现内建投影到局部度量系）",),
       parameters=(("radius", "缓冲半径（米）"),),
       alternatives=("proximity.multi_ring_buffer", "network.service_area"),
       invalid_when=(_ic("geometry", "主体无几何"),),
       visualization_guidance=("statistics_chart",)),
    _d(method_id="proximity.closest_facility", problem_class="routing",
       computational_class="combinatorial", uncertainty_support="none",
       assumptions=("路网拓扑连通",),
       parameters=(("facility_set", "候选设施"),),
       alternatives=("network.shortest_path",),
       invalid_when=(_ic("data_roles", "路网角色缺失"),),
       visualization_guidance=("statistics_chart",)),
)


def get_method_descriptor_registry() -> MethodDescriptorRegistry:
    """进程内单例（首访构建；V4 方法表变更由调用方 reset）。"""
    global _singleton
    if _singleton is None:
        _singleton = MethodDescriptorRegistry(_CURATED_DESCRIPTORS)
    return _singleton


def reset_method_descriptor_registry() -> None:
    global _singleton
    _singleton = None


_singleton: Optional[MethodDescriptorRegistry] = None


__all__ = [
    "DESCRIPTOR_SCHEMA_VERSION",
    "ProblemClass",
    "ComputationalClass",
    "UncertaintySupport",
    "QUALIFICATION_DIMENSIONS",
    "InvalidationCondition",
    "MethodologyDescriptorV2",
    "MethodDescriptorRegistry",
    "get_method_descriptor_registry",
    "reset_method_descriptor_registry",
]
