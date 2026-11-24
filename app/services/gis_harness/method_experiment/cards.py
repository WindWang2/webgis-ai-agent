"""科学方法卡注册表（method-experiment WP1）——「同一空间问题的合理方法
组合」的机器可读知识层。

定位（单一事实源纪律）：

- 方法卡**只引用**既有权威 id：``method_id`` ⊆ AlgorithmRegistry、
  ``metric_id`` ⊆ metrics.METRIC_IDS、``eval_tool`` ⊆ ToolRegistry——
  ``validate_cards`` 在注册面可用时对账（悬空 fatal，绝不静默降级）；
  参数界/单位语义的权威仍在 ParameterContractRegistry 与工具 schema，
  卡内的 ``min/max/grid`` 是**实验设计声明**（有界抽样建议），不替代
  执行期工具校验；
- 五族（任务书 WP1）：密度 / 空间聚类 / 插值 / 网络可达性 / 空间自相关；
- 参数按角色分类：strong（改变它=改变问题）/ tunable（可网格）/ 
  recommended（有推荐默认）/ model_choice（模型变体开关）；互斥与派生
  在 v1 由 candidates 结构表达（每个 candidate 是自洽参数组——非法组合
  根本不进候选空间，比运行期互斥检查更诚实）；
- 卡是**审定代码内工件**（零 LLM、零 I/O）：与 methodology/descriptors
  同纪律——模型生成的文字永远不是科学规则。

消费方：plan 编译器（候选展开）、inspect 工具（方法卡披露）、前端面板。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.method_experiment.metrics import (
    METRIC_IDS,
    metric_exists,
)

#: 参数角色词表（任务书 WP1 的六分法；v1 卡内结构先吸收互斥/派生）。
PARAM_ROLES = ("strong", "tunable", "recommended", "model_choice", "derived")

#: 族 id 词表（稳定契约；typed 码与测试断言引用）。
EXPERIMENT_FAMILY_IDS = (
    "interpolation",
    "density",
    "clustering",
    "network_accessibility",
    "spatial_autocorrelation",
)

#: 协议 kind 词表（protocol.py 消费；kind 决定指标的计算语义）。
PROTOCOL_KINDS = (
    "cv",            # 预测 vs 真值（折分配防泄漏）
    "stability",     # 多 seed/子采样一致性（无真值时的内部一致性）
    "conservation",  # 守恒/边界诊断（解析量对照）
    "scenario",      # 受控情景对比（同底数据、参数即情景）
)


class ParamSpec(BaseModel):
    """一个候选参数的实验设计声明（权威界仍在工具/参数契约层）。"""

    name: str
    role: str                                  # ⊆ PARAM_ROLES
    unit: str = ""                             # "m"/"min"/"unitless"/...
    default: float | int | str | None = None
    grid: Tuple[float | int | str, ...] = ()   # 有界抽样建议（≤4 值）
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    note: str = ""

    def grid_values(self) -> Tuple[float | int | str, ...]:
        """声明的网格值（空 grid = 单默认值；≤4 有界——组合爆炸防护的上游）。"""
        if self.grid:
            return self.grid[:4]
        if self.default is not None:
            return (self.default,)
        return ()


class CandidateSpec(BaseModel):
    """一个候选方法：真实算法 id + 真实评价工具 + 自洽参数组。

    ``param_grids`` 是候选内的**有界**参数扫描（各参数独立取值再确定性
    笛卡尔展开，展开上限由 plan 编译器按预算强制）；``sample_floor`` /
    ``geometry_kind`` 是资格声明（plan 预检消费，运行期权威仍在工具）。
    """

    candidate_id: str                          # 稳定 id（fingerprint/tie-break 用）
    method_id: str                             # ⊆ AlgorithmRegistry
    eval_tool: str                             # ⊆ ToolRegistry（评价执行真相）
    label_zh: str = ""
    param_specs: Dict[str, ParamSpec] = Field(default_factory=dict)
    fixed_params: Dict[str, Any] = Field(default_factory=dict)   # 非扫描固定参
    sample_floor: int = 0
    geometry_kind: str = "point"
    metric_ids: Tuple[str, ...] = ()
    limitations: Tuple[str, ...] = ()

    def expand_grid(self, max_variants: int = 8) -> List[Dict[str, Any]]:
        """确定性参数展开（声明序笛卡尔积；超上限截断并保持声明序）。"""
        names = sorted(self.param_specs)         # 确定性：参数名字典序
        axes: List[Tuple[Any, ...]] = [self.param_specs[n].grid_values() for n in names]
        combos: List[Dict[str, Any]] = [{}]
        for name, axis in zip(names, axes):
            combos = [{**c, name: v} for c in combos for v in axis]
            if len(combos) >= max_variants:
                combos = combos[:max_variants]
                break
        return [{**self.fixed_params, **combo} for combo in combos]


class MethodCard(BaseModel):
    """一族问题的科学方法卡（候选空间 + 协议建议 + 可比性声明）。"""

    family_id: str                             # ⊆ EXPERIMENT_FAMILY_IDS
    label_zh: str = ""
    label_en: str = ""
    question_hints: Tuple[str, ...] = ()       # zh/en 检索提示（inspect 用）
    candidates: Tuple[CandidateSpec, ...] = ()
    protocol_kind: str                         # ⊆ PROTOCOL_KINDS
    default_scheme: str = ""                   # cv 族：index|spatial_block|temporal_forward
    default_folds: int = 5
    default_metrics: Tuple[str, ...] = ()
    comparability_notes: Tuple[str, ...] = ()
    requires_value_field: bool = True
    extra_inputs: Tuple[str, ...] = ()         # 族特定额外输入角色（如 network/facilities）
    resource_class: str = "medium"             # light|medium|heavy（预算预览披露）

    def find_candidate(self, candidate_id: str) -> CandidateSpec | None:
        for c in self.cards_candidates():
            if c.candidate_id == candidate_id:
                return c
        return None

    def cards_candidates(self) -> Tuple[CandidateSpec, ...]:
        return self.candidates


# ── 五族方法卡（审定工件；引用 id 与 master 注册表对账）─────────────────

_CARDS: Tuple[MethodCard, ...] = (
    MethodCard(
        family_id="interpolation",
        label_zh="空间插值", label_en="Spatial interpolation",
        question_hints=(
            "插值", "表面", "kriging", "克里金", "idw", "反距离权重",
            "interpolation", "surface", "连续面",
        ),
        protocol_kind="cv",
        default_scheme="spatial_block",
        default_folds=5,
        default_metrics=("cv_rmse", "cv_mae", "cv_abs_bias"),
        comparability_notes=(
            "全部候选共享同一折分配（空间分块防泄漏）——RMSE 跨候选可比",
            "误差与输入值字段同量纲；跨数据集比较无意义",
            "样本 < 各方法 floor 的候选诚实跳过（skipped 行，不是 0 分）",
        ),
        candidates=(
            CandidateSpec(
                candidate_id="idw", method_id="interpolation.idw",
                eval_tool="interpolation_block_cv", label_zh="反距离权重 (IDW)",
                param_specs={
                    "power": ParamSpec(
                        name="power", role="tunable", unit="unitless",
                        default=2.0, grid=(1.0, 2.0, 3.0),
                        minimum=0.5, maximum=5.0,
                        note="距离衰减幂；1=平板、3=局部性强"),
                },
                fixed_params={"method": "idw"},
                sample_floor=2,
                metric_ids=("cv_rmse", "cv_mae", "cv_abs_bias"),
                limitations=("精确插值器：过样本点，无理论方差",),
            ),
            CandidateSpec(
                candidate_id="ordinary_kriging",
                method_id="interpolation.kriging",
                eval_tool="interpolation_block_cv", label_zh="普通克里金",
                param_specs={
                    "model": ParamSpec(
                        name="model", role="model_choice", unit="unitless",
                        default="auto", grid=("auto",),
                        note="变异函数模型自动选择（variogram_selection 同源）"),
                },
                fixed_params={"method": "ordinary_kriging"},
                sample_floor=20,
                metric_ids=("cv_rmse", "cv_mae", "cv_abs_bias"),
                limitations=("样本 <20 拒绝 CV（kriging MIN_CV_SAMPLES）",),
            ),
            CandidateSpec(
                candidate_id="nearest_neighbor",
                method_id="interpolation.nearest_neighbor",
                eval_tool="interpolation_block_cv", label_zh="最近邻（基线）",
                param_specs={},
                fixed_params={"method": "nearest_neighbor"},
                sample_floor=2,
                metric_ids=("cv_rmse", "cv_mae", "cv_abs_bias"),
                limitations=("阶梯状表面；仅作下界基线",),
            ),
            CandidateSpec(
                candidate_id="rbf",
                method_id="interpolation.rbf",
                eval_tool="interpolation_block_cv", label_zh="径向基函数 (RBF)",
                param_specs={
                    "kernel": ParamSpec(
                        name="kernel", role="model_choice", unit="unitless",
                        default="thin_plate_spline", grid=("thin_plate_spline",),
                        note="v1 只暴露 TPS；smoothing=0"),
                },
                fixed_params={"method": "rbf"},
                sample_floor=3,
                metric_ids=("cv_rmse", "cv_mae", "cv_abs_bias"),
                limitations=("全局求解：样本大时内存受限（rbf 硬上限沿用）",),
            ),
        ),
    ),
    MethodCard(
        family_id="density",
        label_zh="定量点密度", label_en="Quantitative point density",
        question_hints=(
            "密度", "每平方公里", "kde", "核密度", "热力", "集中",
            "density", "kde",
        ),
        protocol_kind="conservation",
        default_metrics=("density_mass_error", "density_edge_share"),
        requires_value_field=False,
        comparability_notes=(
            "守恒误差与边界占比在同一网格口径下跨候选可比",
            "视觉热力（radius_px）不是定量密度——不能以视觉美观替代守恒证据",
            "带宽单位为米（metric CRS 投影后计算）",
        ),
        candidates=(
            CandidateSpec(
                candidate_id="kde_fixed", method_id="spatial.kde.surface",
                eval_tool="density_edge_diagnostic", label_zh="固定带宽 KDE",
                param_specs={
                    "bandwidth_m": ParamSpec(
                        name="bandwidth_m", role="tunable", unit="m",
                        default=1000.0, grid=(500.0, 1000.0, 2000.0),
                        minimum=50.0, maximum=50000.0,
                        note="分析带宽（非视觉像素半径）"),
                },
                fixed_params={"method": "kde_fixed"},
                sample_floor=3,
                metric_ids=("density_mass_error", "density_edge_share"),
                limitations=("边界截断使边缘密度低估——edge_share 披露规模",),
            ),
            CandidateSpec(
                candidate_id="kde_adaptive", method_id="spatial.kde.surface",
                eval_tool="density_edge_diagnostic", label_zh="自适应带宽 KDE",
                param_specs={
                    "bandwidth_m": ParamSpec(
                        name="bandwidth_m", role="recommended", unit="m",
                        default=1000.0, grid=(1000.0,),
                        note="自适应化 seed 带宽（局部 h_i 按样本密度缩放）"),
                },
                fixed_params={"method": "kde_adaptive"},
                sample_floor=10,
                metric_ids=("density_mass_error", "density_edge_share"),
                limitations=("样本密集区带宽自动收窄；样本 <10 不稳定",),
            ),
        ),
    ),
    MethodCard(
        family_id="clustering",
        label_zh="空间聚类", label_en="Spatial clustering",
        question_hints=(
            "聚类", "分组", "簇", "dbscan", "kmeans", "k均值",
            "cluster", "hotspot group",
        ),
        protocol_kind="stability",
        default_metrics=("cluster_silhouette", "cluster_stability_ari",
                         "cluster_noise_share"),
        requires_value_field=False,
        comparability_notes=(
            "silhouette 用欧氏距离（metric 投影坐标）；非凸簇形状下偏悲观",
            "ARI 跨 seed/子采样一致性：确定性算法（DBSCAN）用 bootstrap 子采样",
            "k 值不同（簇数不同）的 ARI 仍可比（ARI 不依赖簇数相等）",
        ),
        candidates=(
            CandidateSpec(
                candidate_id="kmeans", method_id="spatial.cluster.kmeans",
                eval_tool="cluster_stability", label_zh="K-Means",
                param_specs={
                    "k": ParamSpec(
                        name="k", role="tunable", unit="unitless",
                        default=5, grid=(3, 5, 8), minimum=2, maximum=32,
                        note="簇数"),
                },
                fixed_params={"method": "kmeans"},
                sample_floor=6,
                metric_ids=("cluster_silhouette", "cluster_stability_ari"),
                limitations=("假设等方差球形簇；必须固定 k",),
            ),
            CandidateSpec(
                candidate_id="dbscan", method_id="spatial.cluster.dbscan",
                eval_tool="cluster_stability", label_zh="DBSCAN 密度聚类",
                param_specs={
                    "eps_m": ParamSpec(
                        name="eps_m", role="tunable", unit="m",
                        default=1000.0, grid=(500.0, 1000.0, 2000.0),
                        minimum=10.0,
                        note="邻域半径（米）"),
                },
                fixed_params={"method": "dbscan", "min_samples": 5},
                sample_floor=6,
                metric_ids=("cluster_silhouette", "cluster_stability_ari",
                            "cluster_noise_share"),
                limitations=("eps 过小→高噪声占比（noise_share 披露）",),
            ),
        ),
    ),
    MethodCard(
        family_id="network_accessibility",
        label_zh="网络可达性", label_en="Network accessibility",
        question_hints=(
            "可达", "等时", "服务区", "15分钟", "覆盖", "路网",
            "isochrone", "accessibility", "coverage",
        ),
        protocol_kind="scenario",
        default_metrics=("network_coverage", "network_mean_cost"),
        requires_value_field=False,
        extra_inputs=("network", "facilities"),
        comparability_notes=(
            "情景只在同一路网版本 + 同一成本模型内可比",
            "断连设施不计入覆盖率分母的隐含口径——由工具显式披露计数",
            "mode 改变成本模型：walking/driving 情景互不可比（分组裁决）",
        ),
        candidates=(
            CandidateSpec(
                candidate_id="isochrone_walk",
                method_id="network.isochrone", eval_tool="isochrone_network",
                label_zh="步行等时圈",
                param_specs={
                    "travel_time": ParamSpec(
                        name="travel_time", role="tunable", unit="min",
                        default=15.0, grid=(10.0, 15.0, 30.0),
                        minimum=1.0, maximum=120.0,
                        note="阈值情景"),
                },
                fixed_params={"mode": "walking"},
                sample_floor=1,
                metric_ids=("network_coverage", "network_mean_cost"),
                limitations=("脱离路网的设施由工具按 exclude_unreachable 披露",),
            ),
            CandidateSpec(
                candidate_id="isochrone_drive",
                method_id="network.isochrone", eval_tool="isochrone_network",
                label_zh="驾车等时圈",
                param_specs={
                    "travel_time": ParamSpec(
                        name="travel_time", role="tunable", unit="min",
                        default=15.0, grid=(10.0, 15.0, 30.0),
                        minimum=1.0, maximum=120.0,
                        note="阈值情景"),
                },
                fixed_params={"mode": "driving"},
                sample_floor=1,
                metric_ids=("network_coverage", "network_mean_cost"),
                limitations=("成本模型与 walking 不同——跨 mode 分组不联合排名",),
            ),
        ),
    ),
    MethodCard(
        family_id="spatial_autocorrelation",
        label_zh="空间自相关", label_en="Spatial autocorrelation",
        question_hints=(
            "自相关", "moran", "聚集", "离散", "空间模式", "显著",
            "autocorrelation", "spatial pattern",
        ),
        protocol_kind="scenario",
        default_metrics=("autocorr_statistic", "autocorr_p_value"),
        comparability_notes=(
            "权重方案（knn k / distance_band 阈值）是情景——统计量跨情景"
            "的差异是权重敏感性证据，不是谁对谁错",
            "置换检验固定种子（工具契约）；seed 稳定性不纳入本族 v1",
            "小样本（<30）置换 p 值颗粒粗——披露有效置换档位",
        ),
        candidates=(
            CandidateSpec(
                candidate_id="moran_knn", method_id="stats.morans_i",
                eval_tool="moran_i", label_zh="Moran's I (kNN 权重)",
                param_specs={
                    "k": ParamSpec(
                        name="k", role="tunable", unit="unitless",
                        default=8, grid=(4, 8, 12), minimum=2, maximum=16,
                        note="kNN 邻居数"),
                },
                fixed_params={"weights_scheme": "knn"},
                sample_floor=8,
                metric_ids=("autocorr_statistic", "autocorr_p_value"),
                limitations=("k 过小→权重图稀疏；k 过大→平滑掉局部结构",),
            ),
            CandidateSpec(
                candidate_id="moran_distance_band",
                method_id="stats.morans_i", eval_tool="moran_i",
                label_zh="Moran's I (距离带权重)",
                param_specs={
                    "distance_band": ParamSpec(
                        name="distance_band", role="tunable", unit="m",
                        default=0.0, grid=(0.0, 1000.0),
                        note="0=按 8 近邻平均距离自动；>0=显式阈值"),
                },
                fixed_params={"weights_scheme": "distance_band"},
                sample_floor=8,
                metric_ids=("autocorr_statistic", "autocorr_p_value"),
                limitations=("band < 平均最近邻距 → 孤立样本（工具拒绝或披露）",),
            ),
        ),
    ),
)

CARD_REGISTRY: Dict[str, MethodCard] = {c.family_id: c for c in _CARDS}


def get_card(family_id: str) -> MethodCard | None:
    return CARD_REGISTRY.get(family_id)


def card_exists(family_id: str) -> bool:
    return family_id in CARD_REGISTRY


def all_cards() -> Tuple[MethodCard, ...]:
    return _CARDS


def match_family_by_question(query: str) -> List[str]:
    """问题文本 → 候选族 id（词法提示命中；确定性顺序 = 卡注册序）。

    这是检索提示（inspect 工具的展示顺序），不是裁决——族选择最终由
    调用方显式声明或由 methodology/qualification 既有引擎裁决。
    """
    q = (query or "").lower()
    hits: List[str] = []
    for card in _CARDS:
        if any(h.lower() in q for h in card.question_hints):
            hits.append(card.family_id)
    return hits


def validate_cards(
    *,
    algorithm_exists: Optional[Callable[[str], bool]] = None,
    tool_exists: Optional[Callable[[str], bool]] = None,
) -> List[str]:
    """卡引用对账（悬空 fatal 汇总）；返回错误列表（空 = 通过）。"""
    errors: List[str] = []
    if algorithm_exists is None:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        reg = get_algorithm_registry()
        algorithm_exists = lambda aid: reg.get(aid) is not None  # noqa: E731
    for card in _CARDS:
        if card.protocol_kind not in PROTOCOL_KINDS:
            errors.append(f"unknown_protocol_kind:{card.family_id}:{card.protocol_kind}")
        for m in card.default_metrics:
            if not metric_exists(m):
                errors.append(f"dangling_metric:{card.family_id}:{m}")
        if not METRIC_IDS:
            errors.append("metric_registry_empty")
        for cand in card.candidates:
            if not algorithm_exists(cand.method_id):
                errors.append(f"dangling_algorithm:{cand.candidate_id}:{cand.method_id}")
            if tool_exists is not None and not tool_exists(cand.eval_tool):
                errors.append(f"dangling_tool:{cand.candidate_id}:{cand.eval_tool}")
            for m in cand.metric_ids:
                if not metric_exists(m):
                    errors.append(f"dangling_metric:{cand.candidate_id}:{m}")
            for spec in cand.param_specs.values():
                if spec.role not in PARAM_ROLES:
                    errors.append(f"unknown_param_role:{cand.candidate_id}:{spec.name}:{spec.role}")
    return errors
