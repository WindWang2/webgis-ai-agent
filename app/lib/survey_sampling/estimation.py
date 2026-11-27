"""Design-consistent estimation —— 分层简单随机抽样的设计一致估计（WP4）。

数学契约（自包含推导；与 Olofsson et al. 2014, DOI 10.1016/j.rse.2014.02.015
的分层估计量一致；Agent 已对照原文核对公式结构）：

**总体与设计**：N 个**等面积**抽样单元（像元），按地图类别分层 H 层；
层 h 有 N_h 个单元，W_h = N_h/N = A_h/A（等面积像元下单元数权与面积权
逐位相等）。层内 SRS **不放回**抽 n_h。参考标签独立采集（禁止从预测图
自动抄作真值）。**本估计器强约束 strata == 地图类别**（设计层按地图类
划分）——因此估计误差矩阵的行和是**已知常数** W_h（层内全部单元的
地图类相同），UA 分母无抽样误差，公式特化且精确：

  p̂_hj = W_h · n_{hj}/n_h                     （矩阵 cell；Σ_ij p̂_ij = 1）
  q_h  = n_{h,ref(h)}/n_h   （层 h 内判读正确比例）
  OA   = Σ_h W_h q_h                          （Eq.4 口径）
  UA_i = q_i                                  （行和已知 ⇒ 无 B 项）
  p̂_·j = Σ_h W_h · n_{hj}/n_h                 （参考类 j 面积比例）
  PA_j = p̂_jj / p̂_·j = W_j q_j / p̂_·j
  Â_j  = A_total · p̂_·j

**方差**（层内 SRS 不放回的经验方差；stratum 间独立，W_h 已知常数）：

  v_h(p) = (1 − n_h/N_h) · p(1−p)/(n_h − 1)      （单比例，Eq.3 口径）
  V(OA)  = Σ_h W_h² v_h(q_h)
  V(UA_i) = v_h(q_i)·W_i²/W_i² = v_i(q_i)        （Eq.5 口径）
  V(PA_j) = (1/B²)[(1−f)²·V(A) + f²·Σ_{h≠j} W_h² v_h(p_{hj})]
            其中 f = PA_j，A = W_j q_j，B = p̂_·j；
            Cov(A,B) = V(A)（A 是 B 的组成项，同层同 cell 完全共线）
            ⇒ 推开 = (1/B²)[(1−f)²V(A) + f²Σ_{h≠j}W_h²v_h(p_{hj})]

**诚实边界**（不伪造统计）：

- n_h < 2 → 该层方差分量不可估 → 受影响指标 evaluable=false（点估计保留）；
- 任一层 n_h = 0 → 总体级指标（OA/UA/PA/面积）**全部不可估**（层贡献
  未知质量，忽略整层不是估计而是伪造）→ status="not_evaluated"；
- 所有层 n_h < 2 → 抛 :class:`EstimationNotEvaluable`（无任何可估方差）；
- 参考不可判/非响应单元不进矩阵，计数披露 + 状态降级 partial；
- CI 允许越界 [0,1]：原始区间永远给出，越界时附裁剪副本（``ci_clipped``），
  不静默截断（稀有类小 n 的越界是真实统计事实）；
- n_h < 30 → ``normal_approx_ok[h]=False`` 披露（建议阈值，非硬拒绝）。

全部纯 numpy，确定性，oracle 可手算（tests: 手算/全查归零/覆盖模拟）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.lib.gis.scientific_errors import DegenerateData, InsufficientSamples

#: 95% 双侧正态分位（避免 scipy 依赖）
Z_95 = 1.959963984540054

#: 正态近似建议的层内最小响应样本数（披露阈值，非硬拒绝）
NORMAL_APPROX_MIN_N = 30

#: 状态词表（goal §2 严格区分）
STATUS_SUPPORTED = "supported"
STATUS_PARTIAL = "partial"
STATUS_NOT_EVALUATED = "not_evaluated"
STATUS_FAILED = "failed"


class EstimationNotEvaluable(InsufficientSamples):
    """估计完全不可估（无任何层 n_h≥2；拒绝输出总体推断）。"""

    scientific_code = "SURVEY_ESTIMATION_NOT_EVALUABLE"


def _jsonable(matrix: np.ndarray) -> List[List[Optional[float]]]:
    """NaN/Inf → None（严格 JSON 合法；空层行是"不可估"不是 0）。"""

    def _one(x: float) -> Optional[float]:
        return float(x) if math.isfinite(float(x)) else None

    return [[_one(v) for v in row] for row in matrix.tolist()]


@dataclass(frozen=True)
class MetricValue:
    """单个统计量：点估计 + 标准误 + 95% CI（含越界披露）。"""

    estimate: Optional[float]
    se: Optional[float]
    ci_low: Optional[float]
    ci_high: Optional[float]
    evaluable: bool = True
    #: CI 越出界时的裁剪副本；原始区间永远保留（诚实披露）。
    ci_low_clipped: Optional[float] = None
    ci_high_clipped: Optional[float] = None
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "estimate": self.estimate,
            "se": self.se,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "evaluable": self.evaluable,
        }
        if not self.evaluable:
            out["reason"] = self.reason
        if self.ci_low_clipped is not None or self.ci_high_clipped is not None:
            out["ci_clipped"] = {
                "low": self.ci_low_clipped,
                "high": self.ci_high_clipped,
            }
        return out


_NOT_EVAL_NO_RESPONSE = MetricValue(
    None, None, None, None, evaluable=False,
    reason="stratum has zero responded samples — population-level metric "
           "not estimable")
_NOT_EVAL_NO_VARIANCE = MetricValue(
    None, None, None, None, evaluable=False,
    reason="variance not estimable (some strata n_h<2)")
_NOT_EVAL_ZERO_ROW = MetricValue(
    None, None, None, None, evaluable=False,
    reason="mapped class has zero responded samples")
_NOT_EVAL_ZERO_COLUMN = MetricValue(
    None, None, None, None, evaluable=False,
    reason="reference class has zero estimated area share "
           "(no responded unit labelled this class)")


@dataclass
class StratifiedEstimate:
    """一次设计一致估计的完整产物。

    两套事实并存（goal WP4）：矩阵 cell 的**样本数**（``sample_counts``）
    与**估计比例**（``matrix``）不混用；面积估计由比例 × 总面积导出。
    """

    #: 设计一致加权误差矩阵 (H×R)；空层行为 NaN（序列化为 null）
    matrix: np.ndarray
    #: 每个 cell 的响应样本数（(H×R) int；抽样事实，非估计）
    sample_counts: np.ndarray
    #: 每层响应样本数 n_h 与计划样本数（(H,) int）
    n_responded: np.ndarray
    n_planned: np.ndarray
    #: 层权 W_h 与层总体单元数 N_h（(H,)）
    W: np.ndarray
    N: np.ndarray
    strata: List[str]
    ref_classes: List[str]
    overall_accuracy: MetricValue
    user_accuracy: Dict[str, MetricValue]       # 地图类 i → UA
    producer_accuracy: Dict[str, MetricValue]   # 参考类 j → PA
    area_fraction: Dict[str, MetricValue]       # 参考类 j → 面积比例
    area_estimate: Dict[str, MetricValue]       # 参考类 j → 面积（×A_total）
    #: supported | partial | not_evaluated | failed
    status: str
    #: 降级/不可估原因（机器可读短语；有界披露）
    limitations: List[str] = field(default_factory=list)
    #: 排除单元（无可用参考标签）按层计数 + 总数 + 原因分布
    excluded_per_stratum: Optional[np.ndarray] = None
    excluded_total: int = 0
    excluded_reasons: Dict[str, int] = field(default_factory=dict)
    #: 每层正态近似是否满足建议阈值 (H,)
    normal_approx_ok: Optional[np.ndarray] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "limitations": (
                list(self.limitations[:32])
                + ([f"…and {len(self.limitations) - 32} more"]
                   if len(self.limitations) > 32 else [])),
            "strata": list(self.strata),
            "ref_classes": list(self.ref_classes),
            "matrix": _jsonable(self.matrix),
            "sample_counts": self.sample_counts.tolist(),
            "n_responded": self.n_responded.tolist(),
            "n_planned": self.n_planned.tolist(),
            "excluded_per_stratum": (
                self.excluded_per_stratum.tolist()
                if self.excluded_per_stratum is not None else None),
            "excluded_total": self.excluded_total,
            "excluded_reasons": dict(self.excluded_reasons),
            "weights_W": self.W.tolist(),
            "population_N": self.N.tolist(),
            "overall_accuracy": self.overall_accuracy.as_dict(),
            "user_accuracy": {k: v.as_dict() for k, v in self.user_accuracy.items()},
            "producer_accuracy": {k: v.as_dict() for k, v in self.producer_accuracy.items()},
            "area_fraction": {k: v.as_dict() for k, v in self.area_fraction.items()},
            "area_estimate": {k: v.as_dict() for k, v in self.area_estimate.items()},
            "normal_approx_ok": (
                self.normal_approx_ok.tolist()
                if self.normal_approx_ok is not None else None),
            "estimator": "stratified_srs_wo_replacement_weighted",
            "estimator_version": "survey-estimation/1.0.0",
            "reference": "Olofsson et al. 2014 DOI 10.1016/j.rse.2014.02.015",
        }


@dataclass(frozen=True)
class StratifiedCounts:
    """估计器输入：按层的参考标签响应计数 + 排除计数。

    ``counts[h, j]``：层 h（=地图类 h）中参考判读为类 j 的**响应**单元数。
    ``n_planned[h]``：设计计划样本量（披露用；方差用响应量 n_h）。
    ``excluded[h]``：已抽到但无可用参考标签（未访问/不可判/拒绝）的数量。
    ``N[h]``：层总体单元数（等面积像元为整数）。
    ``W[h]``：层权（面积比例；调用方保证 ΣW=1、N=n_planned+excluded 兼容）。
    """

    strata: List[str]
    ref_classes: List[str]
    W: np.ndarray
    N: np.ndarray
    counts: np.ndarray          # (H, R) int ≥0
    n_planned: np.ndarray       # (H,) int ≥0
    excluded: np.ndarray        # (H,) int ≥0（无可用参考标签）
    excluded_reasons: Dict[str, int] = None  # type: ignore[assignment]
    #: 非响应顶替率（层内主样本非响应数/计划数；选择机制偏离披露）
    substitution_rate: Dict[str, float] = None  # type: ignore[assignment]
    #: 响应缺口率（(计划−响应)/计划；替补耗尽的洞）
    shortfall_rate: Dict[str, float] = None  # type: ignore[assignment]
    #: 设计声明的非响应上限（超出 ⇒ 拒绝总体推断）
    max_nonresponse_rate: float = 0.2

    def __post_init__(self) -> None:
        H, R = len(self.strata), len(self.ref_classes)
        if self.counts.shape != (H, R):
            raise ValueError(
                f"counts shape {self.counts.shape} != (n_strata, n_ref)=({H},{R})")
        for name, arr in (("W", self.W), ("N", self.N),
                          ("n_planned", self.n_planned),
                          ("excluded", self.excluded)):
            if np.asarray(arr).shape != (H,):
                raise ValueError(f"{name} shape {np.asarray(arr).shape} != ({H},)")
        if np.any(self.counts < 0) or np.any(np.asarray(self.excluded) < 0):
            raise ValueError("counts/excluded must be non-negative")
        if not np.isclose(float(np.sum(self.W)), 1.0, atol=1e-9):
            raise DegenerateData(
                f"stratum weights must sum to 1 (got {float(np.sum(self.W)):.12f})",
                correction_hint="rebuild the sampling frame; weights derive "
                                "from stratum areas",
            )
        # 响应量不得超过层总体、总体须 ≥1（R1 P2-1：负 fpc 会产 NaN/假 0）
        n_resp = self.counts.sum(axis=1)
        if np.any(np.asarray(self.N) < 1):
            raise DegenerateData(
                "stratum population N_h must be ≥1 for every stratum "
                f"(got {self.N.tolist()})",
                correction_hint="rebuild the frame; N_h derives from valid "
                                "pixel counts",
            )
        if np.any(n_resp > np.asarray(self.N)):
            bad = [f"{self.strata[h]}:{int(n_resp[h])}>{self.N[h]:.0f}"
                   for h in np.where(n_resp > np.asarray(self.N))[0][:8]]
            raise DegenerateData(
                "responded samples exceed stratum population (n_resp > N_h) "
                f"for {bad} — analysis set is inconsistent with the frame",
                correction_hint="check samples.json against the frozen frame; "
                                "n_resp comes from responded labels only",
            )
        # 层名集合 == 参考字典集合（帧不变式：空类已拒 ⇒ 每个字典类都是层；
        # 集合不等 = 类字典漂移 → 阻断统计，goal WP3）
        if set(self.strata) != set(self.ref_classes):
            raise DegenerateData(
                "stratum names and reference class dictionary differ — "
                "class dictionary drift between design and labels "
                f"(strata-only: {sorted(set(self.strata) - set(self.ref_classes))[:8]}, "
                f"dict-only: {sorted(set(self.ref_classes) - set(self.strata))[:8]})",
                correction_hint="rebuild the frame/design against the current "
                                "class dictionary",
            )

    @property
    def n_responded(self) -> np.ndarray:
        """每层响应样本数 n_h = Σ_j counts[h, j]。"""
        return self.counts.sum(axis=1).astype(np.int64)


def _v_of(n_h: int, N_h: float, p: float) -> Optional[float]:
    """单比例 SRSnwr 经验方差 (1−n/N)·p(1−p)/(n−1)；n<2 不可估 → None。"""
    if n_h < 2:
        return None
    fpc = 1.0 - float(n_h) / float(N_h) if N_h > 0 else 0.0
    if fpc < 0.0:
        # n_h>N_h（R1 P2-1）：上游 __post_init__ 已挡；此处防御性拒绝
        raise DegenerateData(
            f"finite population correction negative (n_h={n_h} > N_h={N_h}) "
            "— analysis set exceeds the frozen frame",
            correction_hint="reconcile samples with the frame before "
                            "estimating",
        )
    return fpc * p * (1.0 - p) / (n_h - 1)


def _ci(est: float, se: Optional[float], *, bound: Optional[Tuple[float, float]]) -> MetricValue:
    """正态近似 CI；越界附裁剪副本（原始保留）。"""
    if se is None:
        return MetricValue(est, None, None, None, evaluable=False,
                           reason=_NOT_EVAL_NO_VARIANCE.reason)
    lo, hi = est - Z_95 * se, est + Z_95 * se
    clip_lo: Optional[float] = None
    clip_hi: Optional[float] = None
    if bound is not None and (lo < bound[0] or hi > bound[1]):
        clip_lo = float(max(bound[0], lo))
        clip_hi = float(min(bound[1], hi))
    return MetricValue(float(est), float(se), float(lo), float(hi),
                       ci_low_clipped=clip_lo, ci_high_clipped=clip_hi)


def stratified_estimate(
    counts_input: StratifiedCounts, *,
    total_area: float,
) -> StratifiedEstimate:
    """设计一致估计主入口（公式见模块 docstring 推导）。

    ``total_area``：抽样框总面积（层面积单位一致；等面积像元下
    = Σ N_h × 像元面积）。面积估计 = 比例 × total_area。

    全部层 n_h<2 时抛 :class:`EstimationNotEvaluable`（无任何可估方差，
    不输出假矩阵）；任一层 n_h=0 时返回 status="not_evaluated"（总体级
    指标全部 evaluable=false），矩阵仍显示可用层事实。
    """
    W = np.asarray(counts_input.W, dtype=np.float64)
    N = np.asarray(counts_input.N, dtype=np.float64)
    counts = np.asarray(counts_input.counts, dtype=np.int64)
    n_resp = counts_input.n_responded
    H, R = counts.shape
    strata, refs = list(counts_input.strata), list(counts_input.ref_classes)
    ref_idx = {c: k for k, c in enumerate(refs)}

    limitations: List[str] = []
    status = STATUS_SUPPORTED
    excluded = np.asarray(counts_input.excluded, dtype=np.int64)
    excluded_total = int(excluded.sum())
    excluded_reasons = dict(counts_input.excluded_reasons or {})

    # ── 覆盖审计（诚实降级；先算事实再定状态）───────────────────
    zero_strata = [strata[h] for h in range(H) if n_resp[h] == 0]
    one_strata = [strata[h] for h in range(H) if n_resp[h] == 1]
    skipped_strata = [strata[h] for h in range(H)
                      if int(counts_input.n_planned[h]) == 0]
    if skipped_strata:
        limitations.append(
            f"allocation skipped strata {skipped_strata[:8]} (n_planned=0) — "
            "these map classes have zero coverage; redesign required for "
            "population inference")
        status = STATUS_NOT_EVALUATED
    if zero_strata:
        limitations.append(
            f"no responded samples in strata {zero_strata[:8]} — all "
            "population-level metrics are not estimable")
        status = STATUS_NOT_EVALUATED
    if one_strata:
        limitations.append(
            f"strata with n_h=1 (variance contribution not estimable): "
            f"{one_strata[:8]}")
        if status == STATUS_SUPPORTED:
            status = STATUS_PARTIAL
    if excluded_total > 0:
        limitations.append(
            f"{excluded_total} sampled units excluded (no usable reference "
            "label); inference assumes missing-at-random within strata — "
            "disclosed, not verified")
        if status == STATUS_SUPPORTED:
            status = STATUS_PARTIAL
    if int(counts_input.n_planned.sum()) == 0:
        raise EstimationNotEvaluable(
            "design allocated zero samples — nothing to estimate",
            correction_hint="create a design with positive per-stratum "
                            "allocation (see preview_sample_size)",
        )

    # ── 非响应机制审计（goal WP2：选择机制偏离 ⇒ 拒绝总体推断）──────
    sub_rate = counts_input.substitution_rate or {}
    short_rate = counts_input.shortfall_rate or {}
    max_nr = float(counts_input.max_nonresponse_rate)
    exceeded = [strata[h] for h in range(H)
                if float(sub_rate.get(strata[h], 0.0)) > max_nr
                or float(short_rate.get(strata[h], 0.0)) > max_nr]
    if exceeded:
        limitations.append(
            f"nonresponse/substitution exceeds declared max "
            f"({max_nr:.0%}) in strata {exceeded[:8]} — the selection "
            "mechanism no longer satisfies the SRS assumption; population "
            "inference refused")
        status = STATUS_NOT_EVALUATED
    elif any(float(sub_rate.get(s, 0.0)) > 0 for s in strata):
        limitations.append(
            "substituted units in analysis set (pre-drawn backups, reasons "
            "disclosed); inference assumes missing-at-random within strata")
        if status == STATUS_SUPPORTED:
            status = STATUS_PARTIAL

    # ── 加权矩阵 p̂_hj = W_h·n_{hj}/n_h（空层行 NaN）──────────────
    safe_denom = np.where(n_resp > 0, n_resp, 1)
    p_hat = W[:, None] * (counts.astype(np.float64) / safe_denom[:, None])
    p_hat[n_resp == 0, :] = np.nan
    if all(n_resp[h] < 2 for h in range(H)):
        raise EstimationNotEvaluable(
            "no stratum has n_h≥2 responded samples — the weighted error "
            "matrix and population accuracy are not estimable",
            correction_hint="increase per-stratum allocation or collect more "
                            "reference labels",
            guidance=(
                "check n_responded per stratum in survey progress",
                "redesign with preview_sample_size (min ≥2, recommend ≥30)",
                "collect reference labels for pending sample units",
            ),
        )

    # ── 每层正确比例 q_h 与面积比例 p̂_·j ─────────────────────────
    diag_col = np.array([ref_idx[s] for s in strata])           # (H,)
    q_h = np.full(H, np.nan)                                    # 层内正确比例
    for h in range(H):
        if n_resp[h] > 0:
            q_h[h] = counts[h, diag_col[h]] / n_resp[h]
    col_frac_h = np.full((H, R), np.nan)                        # 层内类 j 比例
    for h in range(H):
        if n_resp[h] > 0:
            col_frac_h[h, :] = counts[h] / n_resp[h]

    #: 总体级指标可估性：任何"整层无覆盖/无响应/机制越限"都使总体推断
    #: 不可估（忽略未知质量的层不是估计而是伪造）
    any_zero = (len(zero_strata) > 0 or len(skipped_strata) > 0
                or status == STATUS_NOT_EVALUATED)
    #: 不可估 reason 按真实诱因分发（R1 P3-1：机器可读短语不再误导排障）
    if skipped_strata:
        not_eval_reason = ("allocation skipped strata (n_planned=0) — "
                           "zero coverage map classes")
    elif exceeded:
        not_eval_reason = ("nonresponse/substitution exceeds the declared "
                           "design maximum — selection mechanism violated")
    elif zero_strata:
        not_eval_reason = _NOT_EVAL_NO_RESPONSE.reason
    else:
        not_eval_reason = _NOT_EVAL_NO_RESPONSE.reason

    # ── OA = Σ_h W_h q_h ────────────────────────────────────────
    if any_zero:
        overall = MetricValue(None, None, None, None, evaluable=False,
                              reason=not_eval_reason)
    else:
        est = float(np.dot(W, q_h))
        var_terms: List[Optional[float]] = []
        for h in range(H):
            v_h = _v_of(int(n_resp[h]), N[h], float(q_h[h]))
            var_terms.append(None if v_h is None else W[h] ** 2 * v_h)
        se = _sum_se(var_terms)
        overall = _ci(est, se, bound=(0.0, 1.0))

    # ── UA_i = q_i（行和已知 W_i）────────────────────────────────
    user_accuracy: Dict[str, MetricValue] = {}
    for i, name in enumerate(strata):
        if n_resp[i] == 0:
            user_accuracy[name] = _NOT_EVAL_ZERO_ROW
            continue
        var_i = _v_of(int(n_resp[i]), N[i], float(q_h[i]))
        user_accuracy[name] = _ci(
            float(q_h[i]),
            math.sqrt(var_i) if var_i is not None else None,
            bound=(0.0, 1.0))

    # ── PA_j = W_j q_j / p̂_·j；V = (1/B²)[(1−f)²V(A) + f²Σ_{h≠j}W_h²v_h] ──
    producer_accuracy: Dict[str, MetricValue] = {}
    area_fraction: Dict[str, MetricValue] = {}
    for j, cname in enumerate(refs):
        # 列面积比例 p̂_·j（逐层线性组合）
        if any_zero:
            area_fraction[cname] = MetricValue(
                None, None, None, None, evaluable=False,
                reason=not_eval_reason)
        else:
            est_b = float(np.dot(W, col_frac_h[:, j]))
            var_terms_b: List[Optional[float]] = []
            for h in range(H):
                v_h = _v_of(int(n_resp[h]), N[h], float(col_frac_h[h, j]))
                var_terms_b.append(None if v_h is None else W[h] ** 2 * v_h)
            area_fraction[cname] = _ci(est_b, _sum_se(var_terms_b),
                                       bound=(0.0, 1.0))
        # PA_j：分子只由层 s_row（=地图类 cname 的层）贡献，分母为全层列和
        s_row = strata.index(cname)
        if n_resp[s_row] == 0:
            producer_accuracy[cname] = MetricValue(
                None, None, None, None, evaluable=False,
                reason=not_eval_reason if cname in zero_strata
                else _NOT_EVAL_ZERO_ROW.reason)
            continue
        if (area_fraction[cname].estimate is None
                or not area_fraction[cname].evaluable):
            producer_accuracy[cname] = MetricValue(
                None, None, None, None, evaluable=False,
                reason=(not_eval_reason if any_zero
                        else _NOT_EVAL_NO_VARIANCE.reason))
            continue
        a_est = float(W[s_row] * q_h[s_row])
        b_est = float(area_fraction[cname].estimate)
        if b_est <= 0.0:
            producer_accuracy[cname] = _NOT_EVAL_ZERO_COLUMN
            continue
        f = a_est / b_est
        v_s = _v_of(int(n_resp[s_row]), N[s_row], float(q_h[s_row]))
        # V(A) = W_s²·v_s(q_s)（A = W_s·q_s；A⊂B 完全共线 ⇒ Cov(A,B)=V(A)）
        v_a = None if v_s is None else W[s_row] ** 2 * v_s
        other = 0.0
        any_var_missing = v_a is None
        for h in range(H):
            if h == s_row:
                continue  # 分子层方差已含于 V(A)
            v_h = (_v_of(int(n_resp[h]), N[h], float(col_frac_h[h, j]))
                   if n_resp[h] >= 2 else None)
            if v_h is None:
                any_var_missing = True
                continue
            other += W[h] ** 2 * v_h
        if v_a is None or any_var_missing:
            producer_accuracy[cname] = MetricValue(
                f, None, None, None, evaluable=False,
                reason=_NOT_EVAL_NO_VARIANCE.reason)
            continue
        var_pa = ((1.0 - f) ** 2 * v_a + f ** 2 * other) / (b_est ** 2)
        producer_accuracy[cname] = _ci(f, math.sqrt(max(var_pa, 0.0)),
                                       bound=(0.0, 1.0))

    # ── 面积（×total_area；CI 界 [0, total_area]）────────────────
    area_estimate: Dict[str, MetricValue] = {}
    for cname, mv in area_fraction.items():
        if mv.evaluable:
            area_estimate[cname] = MetricValue(
                mv.estimate * total_area, mv.se * total_area,
                mv.ci_low * total_area, mv.ci_high * total_area,
                evaluable=True,
                ci_low_clipped=(max(0.0, mv.ci_low) * total_area
                                if mv.ci_low_clipped is not None else None),
                ci_high_clipped=(min(1.0, mv.ci_high) * total_area
                                 if mv.ci_high_clipped is not None else None),
            )
        else:
            area_estimate[cname] = mv

    normal_approx_ok = n_resp >= NORMAL_APPROX_MIN_N

    return StratifiedEstimate(
        matrix=p_hat, sample_counts=counts,
        n_responded=n_resp, n_planned=np.asarray(counts_input.n_planned, dtype=np.int64),
        W=W, N=N, strata=strata, ref_classes=refs,
        overall_accuracy=overall, user_accuracy=user_accuracy,
        producer_accuracy=producer_accuracy,
        area_fraction=area_fraction, area_estimate=area_estimate,
        status=status, limitations=limitations,
        excluded_per_stratum=excluded, excluded_total=excluded_total,
        excluded_reasons=excluded_reasons,
        normal_approx_ok=normal_approx_ok,
    )


def _sum_se(var_terms: List[Optional[float]]) -> Optional[float]:
    """独立方差分量求和 → 合成 SE；任一分量不可估 → None（诚实）。"""
    total = 0.0
    for v in var_terms:
        if v is None:
            return None
        total += v
    return math.sqrt(max(total, 0.0))
