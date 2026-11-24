"""实验指标契约注册表（method-experiment WP4）——指标名/方向/单位/适用族的
单一事实源。

红线（对齐 methodology 包「不建第二 registry」纪律）：

- 本表**只声明评价语义**（方向/单位类/适用族/可比性注意），不实现任何
  指标计算——数值全部来自真实工具执行（trial adapter 从工具结果提取）；
- 不同指标之间**绝不默默归一化**：可比性由 protocol/evaluate 层按本表的
  ``comparable_group`` 与方向裁决，跨组只并列呈现不排名；
- 有界：指标 id 是封闭词表；新增指标 = additive 注册 + 卡片引用（悬空
  fatal 由 cards/plan 编译期对账）。
"""
from __future__ import annotations

from typing import Dict, Tuple

from pydantic import BaseModel

#: 指标方向词表（evaluate 层排序依据；不满足方向的值 = not_evaluated）。
METRIC_DIRECTIONS = ("lower_better", "higher_better")

#: 单位类词表（跨单位类的指标不可比——单位不同不换算，诚实并列）。
METRIC_UNIT_KINDS = (
    "",              # 未声明（诚实缺省；不可参与跨指标排名）
    "value_unit",    # 输入值字段的单位（RMSE/MAE——随数据量纲变化）
    "unitless",
    "ratio",         # [0,1] 比例
    "minutes",
    "p_value",
)

#: 可比组：同组内指标共享同一数据口径（通常同一次 CV/同一试验集），
#: evaluate 层可在组内构造联合排序/Pareto；跨组绝不联合。
COMPARABLE_GROUPS = (
    "cv_error",          # cv_rmse / cv_mae / cv_abs_bias —— 同一折分配
    "cluster_quality",   # silhouette / stability / noise_share —— 同一聚类输入
    "density_diagnostic",  # mass_error / edge_share —— 同一网格口径
    "network_scenario",  # coverage / mean_cost —— 同一路网+设施
    "autocorr_scenario",  # statistic / p_value —— 同一权重口径
)


class MetricContract(BaseModel):
    """一个实验评价指标的契约（纯声明；计算真相在工具层）。"""

    metric_id: str
    name_zh: str
    name_en: str
    direction: str                       # ⊆ METRIC_DIRECTIONS
    unit_kind: str                       # ⊆ METRIC_UNIT_KINDS
    comparable_group: str                # ⊆ COMPARABLE_GROUPS
    applicable_families: Tuple[str, ...] = ()
    description: str = ""

    def is_applicable(self, family_id: str) -> bool:
        return family_id in self.applicable_families


#: 封闭指标词表（method-experiment v1）。方向语义：RMSE/MAE/|bias| 越小
#: 越好；silhouette/ARI/覆盖率/p 稳定性越大越好；noise/edge/mass 误差越小
#: 越好。全部有真实计算依据（工具层产出），未评估 = 值缺失（不是 0）。
_METRICS: Tuple[MetricContract, ...] = (
    MetricContract(
        metric_id="cv_rmse", name_zh="交叉验证 RMSE", name_en="CV RMSE",
        direction="lower_better", unit_kind="value_unit",
        comparable_group="cv_error",
        applicable_families=("interpolation",),
        description="空间分块交叉验证的均方根误差（与输入值字段同量纲）",
    ),
    MetricContract(
        metric_id="cv_mae", name_zh="交叉验证 MAE", name_en="CV MAE",
        direction="lower_better", unit_kind="value_unit",
        comparable_group="cv_error",
        applicable_families=("interpolation",),
        description="交叉验证平均绝对误差（与输入值字段同量纲）",
    ),
    MetricContract(
        metric_id="cv_abs_bias", name_zh="交叉验证 |偏差|", name_en="CV |bias|",
        direction="lower_better", unit_kind="value_unit",
        comparable_group="cv_error",
        applicable_families=("interpolation",),
        description="交叉验证残差均值的绝对值（系统性高/低估幅度）",
    ),
    MetricContract(
        metric_id="cluster_silhouette", name_zh="轮廓系数", name_en="Silhouette",
        direction="higher_better", unit_kind="ratio",
        comparable_group="cluster_quality",
        applicable_families=("clustering",),
        description="非噪声样本的欧氏轮廓系数均值（[-1,1]；欧氏距离适用）",
    ),
    MetricContract(
        metric_id="cluster_stability_ari", name_zh="聚类稳定性 ARI",
        name_en="Stability ARI",
        direction="higher_better", unit_kind="ratio",
        comparable_group="cluster_quality",
        applicable_families=("clustering",),
        description="多 seed / 子采样重复聚类的调整兰德指数均值（[0,1]；1=完全一致）",
    ),
    MetricContract(
        metric_id="cluster_noise_share", name_zh="噪声点占比", name_en="Noise share",
        direction="lower_better", unit_kind="ratio",
        comparable_group="cluster_quality",
        applicable_families=("clustering",),
        description="DBSCAN 标记为噪声的样本比例（高噪声=参数过严的诚实披露）",
    ),
    MetricContract(
        metric_id="density_mass_error", name_zh="密度质量守恒误差",
        name_en="Mass conservation error",
        direction="lower_better", unit_kind="ratio",
        comparable_group="density_diagnostic",
        applicable_families=("density",),
        description="|∫λ̂ − N| / N：密度表面积分与观测点数的相对偏差（定量密度守恒口径）",
    ),
    MetricContract(
        metric_id="density_edge_share", name_zh="边界带质量占比",
        name_en="Edge-band mass share",
        direction="lower_better", unit_kind="ratio",
        comparable_group="density_diagnostic",
        applicable_families=("density",),
        description="落入研究区边界带的密度质量占比（边界截断效应的规模披露）",
    ),
    MetricContract(
        metric_id="network_coverage", name_zh="设施可达覆盖率", name_en="Coverage",
        direction="higher_better", unit_kind="ratio",
        comparable_group="network_scenario",
        applicable_families=("network_accessibility",),
        description="情景参数下可达设施占全部设施的比例（同一网络+设施集内可比）",
    ),
    MetricContract(
        metric_id="network_mean_cost", name_zh="平均网络成本", name_en="Mean cost",
        direction="lower_better", unit_kind="minutes",
        comparable_group="network_scenario",
        applicable_families=("network_accessibility",),
        description="可达设施的网络成本均值（分钟；仅成本口径一致时可比）",
    ),
    MetricContract(
        metric_id="autocorr_statistic", name_zh="自相关统计量", name_en="Statistic",
        direction="higher_better", unit_kind="unitless",
        comparable_group="autocorr_scenario",
        applicable_families=("spatial_autocorrelation",),
        description="Moran's I 等统计量取值（词表方法定义其方向解读）",
    ),
    MetricContract(
        metric_id="autocorr_p_value", name_zh="置换 p 值", name_en="Permutation p",
        direction="lower_better", unit_kind="p_value",
        comparable_group="autocorr_scenario",
        applicable_families=("spatial_autocorrelation",),
        description="固定种子置换检验 p 值（显著性；跨权重方案比较敏感性）",
    ),
)

METRIC_IDS: Tuple[str, ...] = tuple(m.metric_id for m in _METRICS)
METRIC_REGISTRY: Dict[str, MetricContract] = {m.metric_id: m for m in _METRICS}


def get_metric(metric_id: str) -> MetricContract | None:
    """指标契约查询（未知 id → None，由调用方 fail-closed）。"""
    return METRIC_REGISTRY.get(metric_id)


def metric_exists(metric_id: str) -> bool:
    return metric_id in METRIC_REGISTRY


def metrics_for_family(family_id: str) -> Tuple[MetricContract, ...]:
    """某族可用的指标契约（确定性顺序 = 注册序）。"""
    return tuple(m for m in _METRICS if m.is_applicable(family_id))


def direction_prefers(metric_id: str, a: float, b: float) -> bool:
    """方向裁决：a 是否优于 b（未知指标 False——不猜方向）。"""
    contract = METRIC_REGISTRY.get(metric_id)
    if contract is None:
        return False
    if contract.direction == "lower_better":
        return a < b
    return a > b
