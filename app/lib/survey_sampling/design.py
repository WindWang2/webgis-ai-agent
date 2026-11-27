"""Survey design contract —— 抽样调查设计合同与样本量预览（WP1）。

设计合同回答 goal §4 的全部必答项：总体/抽样框/目标时相/分层、抽样
单位、选样方式、随机种子、层面积/总体单元数、纳入概率/权重、估计
目标及限制。**设计锁定后不可变**（canonical JSON + sha256 digest，
：func:`design_digest`），任何字段漂移 ⇒ digest 不同 ⇒ 估计失效重验。

v1 仅交付**数学上可证**的设计类型：``stratified_srs``（按地图类分层、
层内简单随机**不放回**）。自适应/系统/簇设计若方差不可证，本模块
拒绝创建（typed error）而不是给出无法证明的方差公式。

分配策略（可解释默认建议，非硬规则；Olofsson §5.2/§5.3）：
- ``equal``：每层等额（稀有类保底，不因稀有跳过——goal 硬约束）；
- ``proportional``：按层权 W_h 分配（需层权先验）；
- ``neyman``：n_h ∝ W_h·S_h（S_h=√(v̄_h(1−v̄_h))，v̄_h 为层内先验
  变异；无先验时 0.5 保守）；
- ``target_se``：按目标 OA 标准反推总量（Eq.13 口径）后按策略分配。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

import numpy as np

from app.lib.data.fingerprints import canonical_dumps, sha256_hex
from app.lib.gis.scientific_errors import DegenerateData, InsufficientSamples

#: v1 支持且可证的设计类型（唯一）；扩展需先证明方差公式
DESIGN_TYPES = ("stratified_srs",)

#: 层内最小样本建议（披露值；hard 最小是 2 —— 方差可估的必要条件）
MIN_N_PER_STRATUM_HARD = 2
MIN_N_PER_STRATUM_RECOMMENDED = 30

AllocationStrategy = Literal["equal", "proportional", "neyman"]


@dataclass(frozen=True)
class StratumSpec:
    """层规格：层 = 地图类（v1 强约束）。

    ``N_units``：层总体单元数（等面积像元数，整数）。
    ``area``：层总面积（与 total_area 同单位，m² 为主）。
    """

    stratum_id: str          # 地图类 id（与类别字典一致）
    N_units: int             # ≥1
    area: float              # >0（等面积口径；NoData/掩膜外不入框）

    def __post_init__(self) -> None:
        if not self.stratum_id:
            raise ValueError("stratum_id must be non-empty")
        if self.N_units < 1:
            raise DegenerateData(
                f"stratum {self.stratum_id!r} has N_units={self.N_units} "
                "(<1) — empty strata cannot support population inference",
                correction_hint="rebuild the frame; NoData/masked cells are "
                                "excluded, zero-unit classes must be absent",
            )
        if not (self.area > 0):
            raise DegenerateData(
                f"stratum {self.stratum_id!r} has non-positive area",
                correction_hint="use an equal-area CRS for the frame",
            )


def _validate_strata(strata: List[StratumSpec]) -> None:
    if not strata:
        raise DegenerateData(
            "design requires at least one stratum",
            correction_hint="build the frame from a classified raster first",
        )
    ids = [s.stratum_id for s in strata]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate stratum ids: {ids}")


def design_weights(strata: List[StratumSpec]) -> np.ndarray:
    """层权 W_h = area_h / Σ area（面积权；等面积像元下 = 单元数权）。"""
    _validate_strata(strata)
    areas = np.array([s.area for s in strata], dtype=np.float64)
    total = float(areas.sum())
    if not np.isfinite(total) or total <= 0:
        raise DegenerateData("total frame area must be positive and finite")
    return areas / total


def design_digest(payload: Dict[str, Any]) -> str:
    """设计不可变快照 digest（canonical JSON + sha256；跨端一致）。"""
    return sha256_hex(canonical_dumps(payload))


@dataclass(frozen=True)
class SurveyDesign:
    """冻结的抽样设计快照（锁定后不可变；变更 = 新设计版本）。"""

    design_id: str
    design_type: str                      # DESIGN_TYPES 之一
    frame_ref: str                        # 冻结抽样框 ref/digest
    frame_crs: str                        # 框 CRS（等面积声明）
    strata: List[StratumSpec]
    allocation: Dict[str, int]            # stratum_id → n_h（计划样本量）
    backup_per_stratum: int               # 预抽替补数/层（非响应策略）
    seed: int
    target_time: str                      # 目标时相（ISO 日期或版本标签）
    objective: str                        # 估计目标描述
    max_nonresponse_rate: float           # 非响应上限（0<rate<1；披露阈值）
    source_version: str                   # 分类图源版本（漂移检测）
    class_dictionary_version: str
    total_area: float                     # 框总面积（层面积同单位）
    notes: str = ""
    digest: str = ""
    #: 盲标默认开：解释员不可见预测类/层名（偏差防护；goal WP3）
    blind_labelling: bool = True

    def __post_init__(self) -> None:
        if self.design_type not in DESIGN_TYPES:
            raise DegenerateData(
                f"unsupported design type {self.design_type!r} — only "
                f"{DESIGN_TYPES} has a provable variance for now",
                correction_hint="use stratified_srs; adaptive/systematic/"
                                "cluster designs are marked not_evaluated "
                                "until their variance is proven",
            )
        _validate_strata(self.strata)
        allocated = set(self.allocation)
        stratum_ids = {s.stratum_id for s in self.strata}
        if allocated != stratum_ids:
            raise ValueError(
                f"allocation keys {sorted(allocated)} != strata "
                f"{sorted(stratum_ids)} — every stratum must be assigned "
                "(rare classes are never skipped)")
        for sid, n_h in self.allocation.items():
            if n_h < 0:
                raise ValueError(f"negative allocation for {sid}")
            spec = next(s for s in self.strata if s.stratum_id == sid)
            total_draw = n_h + self.backup_per_stratum
            if total_draw > spec.N_units:
                raise InsufficientSamples(
                    f"stratum {sid}: n_h+backup ({total_draw}) exceeds "
                    f"population N_h={spec.N_units}",
                    correction_hint="reduce n_h or backup_per_stratum",
                )
        if not (0.0 < self.max_nonresponse_rate < 1.0):
            raise ValueError(
                f"max_nonresponse_rate must be in (0,1), got "
                f"{self.max_nonresponse_rate}")

    # ── 不可变快照 digest ───────────────────────────────────────
    def canonical_payload(self) -> Dict[str, Any]:
        return {
            "design_id": self.design_id,
            "design_type": self.design_type,
            "frame_ref": self.frame_ref,
            "frame_crs": self.frame_crs,
            "strata": [
                {"stratum_id": s.stratum_id, "N_units": s.N_units,
                 "area": s.area}
                for s in self.strata
            ],
            "allocation": dict(self.allocation),
            "backup_per_stratum": self.backup_per_stratum,
            "seed": self.seed,
            "target_time": self.target_time,
            "objective": self.objective,
            "max_nonresponse_rate": self.max_nonresponse_rate,
            "source_version": self.source_version,
            "class_dictionary_version": self.class_dictionary_version,
            "total_area": self.total_area,
            "notes": self.notes,
            "blind_labelling": self.blind_labelling,
        }

    def with_digest(self) -> "SurveyDesign":
        """填充 digest（不可变 dataclass → dataclasses.replace）。"""
        import dataclasses

        return dataclasses.replace(
            self, digest=design_digest(self.canonical_payload()))


def preview_sample_size(
    strata: List[StratumSpec], *,
    strategy: AllocationStrategy = "equal",
    n_total: Optional[int] = None,
    target_se_oa: Optional[float] = None,
    prior_precision: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """样本量预览（**无副作用**；返回建议分配 + 可解释依据）。

    - ``strategy="target_se"`` 不存在：目标精度走 ``target_se_oa`` 参数
      （Eq.13 口径）反推 n_total 再按 ``strategy`` 分配。
    - ``prior_precision``：层内先验正确率 v̄_h（缺省 0.5 保守，
      S_h=0.5 最大变异）。
    - 每层下限：``MIN_N_PER_STRATUM_HARD``（方差可估必要条件）；
      建议值 ``MIN_N_PER_STRATUM_RECOMMENDED`` 作为披露建议，不足时
      在 ``warnings`` 披露而不静默抬高（预算由用户裁决）。

    Olofsson Eq.13：n = (Σ_h W_h S_h / S)²，S_h = √(v̄_h(1−v̄_h))。
    """
    _validate_strata(strata)
    W = design_weights(strata)
    prior = prior_precision or {}
    v_bar = np.array([
        float(prior.get(s.stratum_id, 0.5)) for s in strata], dtype=np.float64)
    if np.any((v_bar <= 0) | (v_bar >= 1)):
        raise ValueError("prior_precision values must be in (0,1)")
    S_h = np.sqrt(v_bar * (1.0 - v_bar))                     # (H,)
    warnings: List[str] = []

    if n_total is None:
        if target_se_oa is None or not (0.0 < target_se_oa < 1.0):
            raise ValueError(
                "provide n_total or target_se_oa in (0,1)")
        weighted_s = float(np.dot(W, S_h))
        n_total = int(np.ceil((weighted_s / target_se_oa) ** 2))
    if n_total < MIN_N_PER_STRATUM_HARD * len(strata):
        raise InsufficientSamples(
            f"n_total={n_total} < hard minimum "
            f"{MIN_N_PER_STRATUM_HARD}×{len(strata)} strata",
            correction_hint="increase the budget; every stratum needs ≥2 "
                            "samples for an estimable variance",
        )

    n_total = min(int(n_total), int(sum(s.N_units for s in strata)))

    if strategy == "equal":
        base, rem = divmod(n_total, len(strata))
        alloc = {s.stratum_id: base for s in strata}
        for k in range(rem):                                  # 余数按层序
            alloc[strata[k].stratum_id] += 1
    elif strategy == "proportional":
        quotas = n_total * W
        alloc = _largest_remainder(strata, quotas)
    elif strategy == "neyman":
        quotas = n_total * W * S_h / float(np.dot(W, S_h))
        alloc = _largest_remainder(strata, quotas)
    else:  # pragma: no cover — Literal 保护
        raise ValueError(f"unknown allocation strategy {strategy!r}")

    # 硬下限（预算允许时抬到方差可估；超出预算披露）
    for sid in list(alloc):
        if alloc[sid] < MIN_N_PER_STRATUM_HARD:
            shortfall = MIN_N_PER_STRATUM_HARD - alloc[sid]
            if sum(alloc.values()) + shortfall <= sum(s.N_units for s in strata):
                alloc[sid] = MIN_N_PER_STRATUM_HARD
            else:
                warnings.append(
                    f"stratum {sid} below hard minimum "
                    f"{MIN_N_PER_STRATUM_HARD} (budget exhausted)")
    # 建议值披露（不静默抬高——预算裁决权在用户）
    thin = [sid for sid, n in alloc.items()
            if n < MIN_N_PER_STRATUM_RECOMMENDED]
    if thin:
        warnings.append(
            f"strata {thin[:8]} below recommended n_h="
            f"{MIN_N_PER_STRATUM_RECOMMENDED} (normal approximation less "
            "reliable; disclosed, not enforced)")

    return {
        "strategy": strategy,
        "n_total": int(sum(alloc.values())),
        "allocation": alloc,
        "weights_W": {s.stratum_id: float(W[k])
                      for k, s in enumerate(strata)},
        "prior_precision": {s.stratum_id: float(v_bar[k])
                            for k, s in enumerate(strata)},
        "target_se_oa": target_se_oa,
        "warnings": warnings,
        "rationale": {
            "equal": "每层等额：稀有类保底，不因稀有类跳过（goal 硬约束）",
            "proportional": "按层面积权分配：每单元纳入概率恒等（self-weighting）",
            "neyman": "Neyman：n_h ∝ W_h·S_h，固定总量下最小化 OA 方差",
        }[strategy],
        "formula": "Olofsson et al. 2014 Eq.13（目标 SE 反推总量）",
    }


def _largest_remainder(
    strata: List[StratumSpec], quotas: np.ndarray,
) -> Dict[str, int]:
    """大盘尼分配（floor + 小数部分降序 +1，平列按层序）——总量恰为 n。"""
    floors = np.floor(quotas).astype(int)
    remaining = int(round(float(quotas.sum()))) - int(floors.sum())
    order = np.argsort(-(quotas - floors), kind="stable")
    extra = np.zeros(len(strata), dtype=int)
    extra[order[:remaining]] = 1
    return {s.stratum_id: int(floors[k] + extra[k])
            for k, s in enumerate(strata)}
