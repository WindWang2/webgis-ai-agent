"""Cartographic Planner 基础（C3）：分布驱动的分类裁决 + VisualizationPlan。

设计（ADR-0073）：
- ``choose_classification`` —— 把「数据形态 → 分类方法」的制图学知识接上数据
  证据。知识源是 model_library.CLASSIFICATION_METHODS（best_for/caveat/
  authority）与 MapModel.recommended_classifiers；证据源是待分类字段的分布
  统计（n/min/max/mean/median）。此前这些元数据只是文档，分类方法由调用方
  默认 quantiles 或模板 payload 硬编码。
- ``build_visualization_plan`` —— 把 intent → map model → classification →
  composition 的每步裁决序列化为一等工件（choice + reason + authority），
  供 QA 反查、项目记忆固化与 Agent 解释。

纯函数、无 IO；不改变 classify.py 的算法，只决定「用哪个算法」。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.lib.cartography.model_library import CLASSIFICATION_METHODS


class DistributionStats(BaseModel):
    """待分类数值字段的一阶分布证据（从有限值 O(n) 单遍计算）。"""

    n: int
    min: float
    max: float
    mean: Optional[float] = None
    median: Optional[float] = None

    @property
    def range(self) -> float:
        return self.max - self.min


def distribution_stats_from_values(values: List[float]) -> Optional[DistributionStats]:
    finite = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if len(finite) < 2:
        return None
    ordered = sorted(finite)
    n = len(ordered)
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return DistributionStats(
        n=n,
        min=ordered[0],
        max=ordered[-1],
        mean=sum(ordered) / n,
        median=median,
    )


class ClassificationChoice(BaseModel):
    """一次分类裁决：结论 + 理由 + 落选者（可解释、可审计）。"""

    method: str
    k: int
    authority: str = ""
    reasons: List[str] = Field(default_factory=list)
    rejected: List[Dict[str, str]] = Field(default_factory=list)
    source: str = "distribution"  # explicit（调用方指定）/ model（模型推荐）/ distribution（分布裁决）


def _skew_ratio(stats: DistributionStats) -> Optional[float]:
    """右偏强度（相对形态）：mean 相对 median 的偏离（(mean-median)/median）。

    计数型数据（POI 数、密度）典型重尾：mean ≥ 1.5×median。用相对比例而非
    值域占比——少数极端离群值会把值域拉爆，反而稀释 range 型偏度指标，
    恰恰漏掉 head_tail 要捕捉的形态（Jiang 2013 的经验法则）。
    """
    if stats.mean is None or stats.median is None or stats.median <= 0:
        return None
    return (stats.mean - stats.median) / stats.median


def _range_position(stats: DistributionStats) -> Optional[float]:
    """mean 在值域中的相对位置（近均匀判定用：|mean-median|/range）。"""
    if stats.mean is None or stats.median is None or stats.range <= 0:
        return None
    return abs(stats.mean - stats.median) / stats.range


# 重尾判定阈值：mean ≥ 1.5 × median（相对偏度 ≥ 0.5）
_HEAVY_TAIL_THRESHOLD = 0.5
# 近均匀判定阈值：mean 与 median 的偏离小于值域 2%
_NEAR_UNIFORM_RANGE_POSITION = 0.02


def choose_classification(
    stats: DistributionStats,
    *,
    recommended: Optional[List[str]] = None,
    default_k: Optional[int] = None,
    requested_method: Optional[str] = None,
    requested_k: Optional[int] = None,
) -> ClassificationChoice:
    """分布驱动的分类方法裁决（Cartographic Planner 的核心一步）。

    优先级：
    1. 调用方显式指定 method（known）→ 尊重（source=explicit），只校正 k 边界；
    2. 强右偏（skew_ratio ≥ 0.12，即 mean 偏离 median 超值域 12%）且
       head_tail 在推荐集或无推荐集 → head_tail（重尾计数数据的制图学正解）；
    3. 有模型推荐集 → 推荐集 ∪ {natural_breaks} 中按适度偏态优先
       natural_breaks（组内方差最小化默认首选）、近均匀（|skew| < 0.02）优先
       equal_interval / quantiles；
    4. 无推荐集 → natural_breaks（CLASSIFICATION_METHODS 的默认首选）。

    k：requested_k > default_k（模型缺省 5），夹在 [3, 7]；head_tail 的类别数
    由数据自身决定（特性），k 仅作请求上限。
    """
    method_meta = CLASSIFICATION_METHODS
    rejected: List[Dict[str, str]] = []

    def _reject(method_id: str, why: str) -> None:
        if method_id in method_meta:
            rejected.append({"method": method_id, "reason": why})

    # k 裁决
    k = int(requested_k or default_k or 5)
    k = max(3, min(7, k))

    # 1. 显式指定
    if requested_method and requested_method in method_meta:
        return ClassificationChoice(
            method=requested_method,
            k=k,
            authority=method_meta[requested_method].authority,
            reasons=[f"调用方显式指定 {requested_method}（尊重显式制图决策）"],
            source="explicit",
        )

    skew = _skew_ratio(stats)
    pool = [m for m in (recommended or []) if m in method_meta]

    # 2. 重尾 → head_tail
    if skew is not None and skew >= _HEAVY_TAIL_THRESHOLD and ("head_tail" in pool or not pool):
        reasons = [
            f"分布证据：mean={stats.mean:.4g} ≥ {1 + skew:.1f}×median={stats.median:.4g}"
            f"（重尾计数形态，n={stats.n}）",
            method_meta["head_tail"].best_for_zh,
        ]
        for m in ("equal_interval", "quantiles"):
            # 无条件记录：它们正是被分布证据推翻的"旧默认"，落选理由必须可见
            _reject(m, method_meta[m].caveat_zh)
        return ClassificationChoice(
            method="head_tail",
            k=k,
            authority=method_meta["head_tail"].authority,
            reasons=reasons,
            rejected=rejected,
            source="distribution",
        )

    # 3/4. 推荐集或默认集内裁决
    candidates = pool or ["natural_breaks"]
    range_pos = _range_position(stats)
    near_uniform = range_pos is not None and range_pos < _NEAR_UNIFORM_RANGE_POSITION
    if near_uniform:
        for m in ("equal_interval", "quantiles"):
            if m in candidates:
                chosen = m
                break
        else:
            chosen = candidates[0]
        reasons = [
            f"分布证据：mean≈median（偏离值域 {range_pos:.0%}，n={stats.n}）——近均匀形态",
            method_meta[chosen].best_for_zh,
        ]
    else:
        chosen = "natural_breaks" if "natural_breaks" in candidates else candidates[0]
        if skew is not None:
            reasons = [
                f"分布证据：skew={skew:.0%}（mean={stats.mean:.4g}, "
                f"median={stats.median:.4g}, n={stats.n}）——中等偏态",
            ]
        else:
            reasons = [f"分布证据不完整（n={stats.n}）——采用默认首选"]
        reasons.append(method_meta[chosen].best_for_zh)
    for m in candidates:
        if m != chosen:
            _reject(m, method_meta[m].caveat_zh)

    return ClassificationChoice(
        method=chosen,
        k=k,
        authority=method_meta[chosen].authority,
        reasons=reasons,
        rejected=rejected,
        source="model" if pool else "distribution",
    )


class VisualizationPlan(BaseModel):
    """制图计划一等工件：每步 choice + reason（intent → model → 分类 → 组合）。

    由 planner/工具在成图时构建，随工具结果或 plan evidence 下发——QA 可反查
    「为什么这样画」，项目记忆可按 (map_model, method, k) 指纹固化。
    """

    phenomenon: str = ""
    geometry: str = ""
    analysis_goal: str = ""
    map_model: str = ""
    classification: Optional[ClassificationChoice] = None
    palette: str = ""
    composition_template: str = ""
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    secondary_views: List[Dict[str, Any]] = Field(default_factory=list)

    def add_step(self, step: str, choice: Any, reason: str) -> None:
        self.steps.append({
            "step": step,
            "choice": str(choice),
            "reason": reason,
        })

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


def build_visualization_plan(
    *,
    phenomenon: str = "",
    geometry: str = "",
    analysis_goal: str = "",
    map_model: str = "",
    classification: Optional[ClassificationChoice] = None,
    palette: str = "",
    composition_template: str = "",
    secondary_views: Optional[List[Dict[str, Any]]] = None,
) -> VisualizationPlan:
    """把各步裁决组装成可序列化工件（纯组装，无决策逻辑）。"""
    plan = VisualizationPlan(
        phenomenon=phenomenon,
        geometry=geometry,
        analysis_goal=analysis_goal,
        map_model=map_model,
        classification=classification,
        palette=palette,
        composition_template=composition_template,
        secondary_views=secondary_views or [],
    )
    if phenomenon or analysis_goal:
        plan.add_step(
            "intent",
            f"{phenomenon or '?'}/{analysis_goal or '?'}",
            "意图解析（MapRequestIntent）",
        )
    if map_model:
        plan.add_step("map_model", map_model, "模型库匹配（几何×任务×测度）")
    if classification is not None:
        plan.add_step(
            "classification",
            f"{classification.method}/k={classification.k}",
            "; ".join(classification.reasons),
        )
    if palette:
        plan.add_step("palette", palette, "色板（模型推荐或默认）")
    if composition_template:
        plan.add_step("composition", composition_template, "组合模板（版面语法）")
    return plan
