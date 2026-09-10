"""Cross-Validation Framework（science-v5 W1）—— 折分配与指标聚合的唯一事实源。

设计（01-architecture.md D1）：

- **splitter 三族**（全确定性，无 RNG）：
  - ``index``：``arange(n) % folds``（kriging V2 既有语义上收）；
  - ``spatial_block``：排序秩 → ⌈√folds⌉ 网格块 → ``block % folds``
    （从 ``kriging._spatial_block_folds`` 上收；kriging re-import 同一
    对象——别名非复制，零第二事实源）；
  - ``temporal_forward``：时间升序**前向链**（fold k 的 train = 严格早于
    test 块首时刻的全部样本）——expanding window，零 future leakage；
    块边界只落在**唯一时间值边界**（同一时刻的样本永不跨折拆分），
    unique 时间值数 < folds → 类型化诚实拒绝。
- **编排**：``run_cross_validation`` 方法无关——调用方提供 fit/predict
  回调（fit_fn(train_idx, train_values) → model，
  predict_fn(model, train_idx, test_idx) → (pred, var|None)），框架负责
  折分配、泄漏守卫、指标聚合与校准统计。
- **泄漏守卫**（构造保证 + report 逐折证据）：
  - spatial_block：train/test 块 id 不相交（网格构造保证，测试钉死）；
  - temporal_forward：逐折 ``max(train_t) < min(test_t)``；
  - 跨折重复坐标计数（聚类采样诚实性披露，不 raise）。
- **诚实退化**：样本 < MIN_CV_SAMPLES → report 不产指标（note 披露）；
  fold 失败计数（不静默吞）。

错误语义：全部 ``ValueError`` 子类（KrigingInputError 同族——科学输入
校验）；时间感知折要求 ``times_sec`` 与样本等长且有限。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
)

# 框架默认下限（kriging 专用路径用自己的 20 下限——更保守，两值不冲突）。
CV_MIN_SAMPLES = 8
# temporal_forward 每测试块的最小样本数（低于则折叠数自动收缩）。
CV_MIN_TEST_BLOCK = 2

CV_SCHEME_VOCABULARY = ("index", "spatial_block", "temporal_forward")


def spatial_block_folds(xy: np.ndarray, folds: int) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic grid-stratified fold assignment (NO RNG).

    Samples are ranked by x and by y (dense ranks via double argsort —
    stable under input reordering), snapped to a ⌈√folds⌉ × ⌈√folds⌉ block
    grid, and each block maps to fold ``block_id % folds``. Clustered
    samples therefore share one fold and are validated against spatially
    distant blocks — the honest error of an extrapolative design.
    Returns ``(fold_id, block_id)``.

    单一事实源：``kriging._spatial_block_folds`` 是本函数的兼容别名。
    """
    xy = np.asarray(xy, dtype=float)
    if xy.ndim != 2 or xy.shape[1] < 2:
        raise DegenerateData("spatial_block_folds 需要 (n, ≥2) 坐标数组")
    n = len(xy)
    if folds < 2:
        raise ValueError(f"folds 必须 ≥2，got {folds}")
    n_grid = max(1, int(math.ceil(math.sqrt(folds))))
    rx = np.argsort(np.argsort(xy[:, 0], kind="stable"), kind="stable")
    ry = np.argsort(np.argsort(xy[:, 1], kind="stable"), kind="stable")
    gx = (rx * n_grid) // max(n, 1)
    gy = (ry * n_grid) // max(n, 1)
    block = gy * n_grid + gx
    return block % folds, block


def temporal_forward_folds(
    times_sec: np.ndarray, folds: int
) -> tuple[np.ndarray, np.ndarray]:
    """时间前向链折分配（确定性；零 future leakage）。

    样本按时间稳定排序后切成 ``folds`` 个连续块：**块边界只落在唯一
    时间值边界**（同一时刻的样本永不跨折拆分——否则 ``max(train_t) ==
    min(test_t)`` 打破严格泄漏断言）。块 0 是纯训练库（fold_id=-1）；
    块 k（k≥1）是 fold k-1 的测试块，训练 = 严格早于该块首时刻的全部
    样本（expanding window）。

    切点选择：候选切点 = 排序后值变化位置；从候选均匀选取
    ``folds-1`` 个（linspace 舍入；候选数 ≥ folds-1 由前置校验保证，
    此时舍入后严格递增——无碰撞）。

    Returns ``(fold_id, block_id)``：fold_id 逐样本（块 0 为 -1）；
    block_id 逐样本所属块（0..folds-1）。

    Raises:
        ValueError: folds < 2，或 unique 时间值数 < folds（无法在不拆分
            同一时刻的前提下构造足够多的测试块）。
        DegenerateData: 时间数组为空/含非有限值。
    """
    t = np.asarray(times_sec, dtype=float)
    if t.ndim != 1 or len(t) == 0:
        raise DegenerateData("temporal_forward_folds 需要非空一维时间数组")
    if not np.isfinite(t).all():
        raise DegenerateData("时间戳含非有限值（须为 epoch/相对秒）")
    if folds < 2:
        raise ValueError(f"folds 必须 ≥2，got {folds}")
    n = len(t)
    order = np.argsort(t, kind="stable")
    sorted_t = t[order]
    n_unique = int(np.unique(t).size)
    if folds > n_unique:
        raise ValueError(
            f"folds={folds} 超过 unique 时间值数 {n_unique}——同一时刻的"
            "样本不可跨折拆分（严格前向链），请降低 folds"
        )
    # 候选切点：排序后值变化位置（同值组内部不可切）
    change = np.nonzero(sorted_t[1:] != sorted_t[:-1])[0] + 1  # in [1, n-1]
    k_cuts = folds - 1
    picks = np.round(np.linspace(0, len(change) - 1, k_cuts)).astype(int)
    cuts = change[picks]                      # 严格递增（1..n-1）
    bounds = np.concatenate([[0], cuts, [n]])
    fold_id = np.full(n, -1, dtype=int)
    block_id = np.full(n, -1, dtype=int)
    for b in range(folds):
        idx = order[bounds[b]:bounds[b + 1]]
        block_id[idx] = b
        if b >= 1:
            fold_id[idx] = b - 1
    return fold_id, block_id


@dataclass
class CVReport:
    """方法无关交叉验证报告（结构化 + 可 JSON 化）。"""

    n_samples: int
    folds: int
    folds_used: int
    scheme: str
    rmse: Optional[float] = None
    mae: Optional[float] = None
    bias: Optional[float] = None
    r2: Optional[float] = None
    note: str = ""
    per_fold: list = field(default_factory=list)
    calibration: Optional[dict] = None       # z_score_mean / z_coverage_95 / n
    leakage_check: Optional[dict] = None     # 逐折泄漏守卫证据
    duplicate_crossfold_pairs: int = 0       # 跨折重复坐标对计数（聚类披露）
    fold_failures: int = 0                   # fit/predict 失败折数（诚实计数）

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "n_samples": self.n_samples,
            "folds": self.folds,
            "folds_used": self.folds_used,
            "scheme": self.scheme,
        }
        for key in ("rmse", "mae", "bias", "r2"):
            v = getattr(self, key)
            out[key] = round(v, 6) if v is not None else None
        if self.note:
            out["note"] = self.note
        if self.calibration:
            out["uncertainty_calibration"] = self.calibration
        if self.leakage_check is not None:
            out["leakage_check"] = self.leakage_check
        if self.duplicate_crossfold_pairs:
            out["duplicate_crossfold_pairs"] = self.duplicate_crossfold_pairs
        if self.fold_failures:
            out["fold_failures"] = self.fold_failures
        if self.per_fold:
            out["per_fold"] = self.per_fold
        return out


def run_cross_validation(
    coords_metric: np.ndarray,
    values: np.ndarray,
    fit_fn: Callable[[np.ndarray, np.ndarray], Any],
    predict_fn: Callable[[Any, np.ndarray, np.ndarray], tuple],
    *,
    scheme: str = "spatial_block",
    folds: int = 5,
    times_sec: Optional[np.ndarray] = None,
    min_samples: int = CV_MIN_SAMPLES,
    on_fold_error: str = "skip",
) -> CVReport:
    """方法无关 CV 编排（fit/predict 回调由调用方提供，无第二事实源）。

    Args:
        coords_metric: (n, 2) 米制坐标（spatial_block 需要；index/
            temporal_forward 亦接受但仅用于重复坐标披露）。
        fit_fn(train_idx, train_values) → model：在全量数组的训练子集上拟合。
        predict_fn(model, train_idx, test_idx) → (pred, var|None)：**只在
            训练子集条件上**预测测试子集（train_idx 是本折训练掩膜——
            防自条件泄漏的契约关键：测试样本绝不能进入自己的条件集）；
            var 非 None 时启用 z-score 校准统计（非有限 z 过滤——与
            kriging 既有行为一致）。
        on_fold_error: "skip"（计数后继续，默认）| "raise"（首个失败即抛）。

    scheme="temporal_forward" 时 ``times_sec`` 必须提供（与样本等长）。
    """
    if scheme not in CV_SCHEME_VOCABULARY:
        raise ValueError(
            f"scheme 必须是 {CV_SCHEME_VOCABULARY} 之一，got {scheme!r}")
    if on_fold_error not in ("skip", "raise"):
        raise ValueError(f"on_fold_error 必须是 'skip'|'raise'，got {on_fold_error!r}")
    coords_metric = np.asarray(coords_metric, dtype=float)
    values = np.asarray(values, dtype=float)
    n = len(values)
    if coords_metric.ndim != 2 or len(coords_metric) != n:
        raise DegenerateData("坐标与样本数量不一致")
    if n < 2:
        raise InsufficientSamples(f"CV 至少需要 2 个样本，got {n}")
    if n < min_samples:
        return CVReport(
            n_samples=n, folds=0, folds_used=0, scheme=scheme,
            note=(
                f"样本量 {n} < {min_samples}，无法进行可靠的交叉验证；"
                "不确定性仅由模型方差表达。"),
        )

    if scheme == "temporal_forward":
        if times_sec is None:
            raise ValueError("temporal_forward scheme 需要 times_sec")
        times_sec = np.asarray(times_sec, dtype=float)
        if len(times_sec) != n:
            raise DegenerateData("时间数组与样本数量不一致")
        # folds 收缩：目标每测试块 ≥ CV_MIN_TEST_BLOCK 样本（同值时间组
        # 偏斜时可能个别块更小——统计弱但诚实）；unique 值约束由
        # temporal_forward_folds 校验。
        usable = min(folds, max(2, n // (CV_MIN_TEST_BLOCK + 1)))
        fold_id, block_id = temporal_forward_folds(times_sec, usable)
    elif scheme == "spatial_block":
        usable = max(2, min(folds, n // 4))
        fold_id, block_id = spatial_block_folds(coords_metric, usable)
    else:
        usable = max(2, min(folds, n // 4))
        fold_id = np.arange(n) % usable
        block_id = None

    # 跨折重复坐标计数（聚类采样诚实性披露；坐标全等 1e-9 容差）
    dup_pairs = 0
    if n > 1:
        seen: dict[tuple, set] = {}
        for i in range(n):
            key = (round(float(coords_metric[i, 0]), 6),
                   round(float(coords_metric[i, 1]), 6))
            seen.setdefault(key, set()).add(int(fold_id[i]))
        dup_pairs = sum(
            1 for folds_hit in seen.values() if len(folds_hit) > 1)

    errs: list[float] = []
    z_scores: list[float] = []
    per_fold: list[dict] = []
    leakage: dict[str, bool] = {}
    folds_used = 0
    fold_failures = 0
    n_folds_present = int(fold_id.max()) + 1 if (fold_id >= 0).any() else 0
    for f in range(n_folds_present):
        test = fold_id == f
        train = ~test & (fold_id >= 0)
        if scheme == "temporal_forward":
            # 前向链：训练 = 严格早于测试块首时刻的全部样本（含更早
            # 测试块的样本——expanding window 语义）
            test_times = times_sec[test]
            train = times_sec < float(test_times.min())
        if not test.any() or not train.any():
            continue
        # 泄漏守卫证据（构造保证，逐折记录）
        if scheme == "temporal_forward":
            leakage[f"fold_{f}"] = bool(
                float(times_sec[train].max()) < float(times_sec[test].min()))
        elif scheme == "spatial_block" and block_id is not None:
            leakage[f"fold_{f}"] = bool(
                np.intersect1d(block_id[train], block_id[test]).size == 0)
        try:
            model = fit_fn(train, values[train])
            pred, var = predict_fn(model, train, test)
        except Exception:
            fold_failures += 1
            if on_fold_error == "raise":
                raise
            continue
        folds_used += 1
        fold_err = np.asarray(pred, dtype=float) - values[test]
        errs.extend(fold_err.tolist())
        if var is not None:
            var_arr = np.asarray(var, dtype=float)
            z = fold_err / np.sqrt(np.maximum(var_arr, 0.0))
            z_scores.extend(z[np.isfinite(z)].tolist())
        entry: dict = {
            "fold": int(f),
            "rmse": round(float(np.sqrt(np.mean(fold_err ** 2))), 6),
            "n_test": int(test.sum()),
            "n_train": int(train.sum()),
        }
        if block_id is not None and scheme != "temporal_forward":
            entry["block_ids"] = sorted(
                int(b) for b in np.unique(block_id[test]))
        per_fold.append(entry)

    if not errs:
        return CVReport(
            n_samples=n, folds=usable, folds_used=0, scheme=scheme,
            fold_failures=fold_failures,
            note="所有折的拟合均失败，无法给出交叉验证指标。",
            leakage_check=(leakage or None),
        )
    e = np.asarray(errs)
    ss_res = float(np.sum(e ** 2))
    ss_tot = float(np.sum((values - values.mean()) ** 2))
    calibration: Optional[dict] = None
    if z_scores:
        z = np.asarray(z_scores)
        calibration = {
            "z_score_mean": round(float(np.mean(z)), 6),
            "z_coverage_95": round(float(np.mean(np.abs(z) <= 1.96)), 6),
            "n": int(z.size),
        }
    return CVReport(
        rmse=float(np.sqrt(np.mean(e ** 2))),
        mae=float(np.mean(np.abs(e))),
        bias=float(np.mean(e)),
        r2=(1.0 - ss_res / ss_tot) if ss_tot > 0 else None,
        n_samples=n,
        folds=usable,
        folds_used=folds_used,
        scheme=scheme,
        per_fold=per_fold,
        calibration=calibration,
        leakage_check=(leakage if leakage else None),
        duplicate_crossfold_pairs=dup_pairs,
        fold_failures=fold_failures,
    )
