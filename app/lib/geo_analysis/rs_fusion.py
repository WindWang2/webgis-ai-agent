"""SAR×光学联合特征栈 + 晚期证据融合（feature-level / late fusion）。

定位（本方向 ownership：SAR-optical fusion feature stack / fusion recipes）：

- **特征级融合**（``build_joint_feature_stack``）：两模态特征面（如
  NDVI p50/amplitude/Sen 斜率 × VV p50/VH 比值/Sen 斜率）按
  ``optical::<name>`` / ``sar::<name>`` 命名空间合成为联合栈，附逐像元
  覆盖码（none / optical-only / sar-only / both）——单模态缺失是类型化
  覆盖语义，不是 0；
- **晚期证据融合**（``late_evidence_fusion``）：两幅带符号证据面（如
  光学 change_z × SAR log-ratio 变化）的描述性加权融合：可用源按权重
  归一加权（缺源像元不稀释权重），单源像元保留自身证据并标注；
  符号一致性 → agreement 码（一致正向/一致负向/冲突/单源/无）；
- **诚实边界**：这是**描述性融合**，不是概率模型、不训练任何模型；
  融合证据的语义解释（植被胁迫 vs 地表扰动）由调用方在证据块中声明，
  本模块不发明跨模态因果。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np

#: 逐像元联合覆盖码（uint8；稳定码表——新增只追加）。
COVERAGE_NONE = 0
COVERAGE_OPTICAL_ONLY = 1
COVERAGE_SAR_ONLY = 2
COVERAGE_BOTH = 3

_COVERAGE_NAMES = {
    COVERAGE_NONE: "none",
    COVERAGE_OPTICAL_ONLY: "optical_only",
    COVERAGE_SAR_ONLY: "sar_only",
    COVERAGE_BOTH: "both",
}

#: agreement 码（晚期证据融合的符号一致性语义）。
AGREEMENT_NONE = 0
AGREEMENT_OPTICAL_ONLY = 1
AGREEMENT_SAR_ONLY = 2
AGREEMENT_CONSENSUS_POS = 3
AGREEMENT_CONSENSUS_NEG = 4
AGREEMENT_CONFLICT = 5
#: 双侧可用但零信号（|e| ≤ 阈值）——不冒充一致性方向（R1-P3-2）。
AGREEMENT_BOTH_NEUTRAL = 6


# ── 特征级联合栈 ──────────────────────────────────────────────────────

def build_joint_feature_stack(
    optical_features: Mapping[str, np.ndarray],
    sar_features: Mapping[str, np.ndarray],
) -> Dict[str, Any]:
    """两模态特征面 → 命名空间联合栈 + 逐像元覆盖码（有界 meta）。"""
    if not optical_features or not sar_features:
        raise ValueError(
            "联合栈要求两个模态都有特征面（joint 语义）；"
            "单模态分析不需要融合")
    shape = None
    named: Dict[str, np.ndarray] = {}
    for prefix, feats in (("optical", optical_features), ("sar", sar_features)):
        for name, arr in (feats or {}).items():
            a = np.asarray(arr, dtype=float)
            if a.ndim != 2:
                raise ValueError(
                    f"特征 {prefix}::{name} 须为 (H,W) 2D 数组，got {a.shape}")
            if shape is None:
                shape = a.shape
            elif a.shape != shape:
                raise ValueError(
                    f"特征形状不一致：{prefix}::{name} {a.shape} vs {shape}")
            named[f"{prefix}::{name}"] = a

    opt_any = np.zeros(shape, dtype=bool)
    for name, arr in (optical_features or {}).items():
        opt_any |= np.isfinite(np.asarray(arr, dtype=float))
    sar_any = np.zeros(shape, dtype=bool)
    for name, arr in (sar_features or {}).items():
        sar_any |= np.isfinite(np.asarray(arr, dtype=float))
    coverage = np.full(shape, COVERAGE_NONE, dtype=np.uint8)
    coverage[opt_any & ~sar_any] = COVERAGE_OPTICAL_ONLY
    coverage[sar_any & ~opt_any] = COVERAGE_SAR_ONLY
    coverage[opt_any & sar_any] = COVERAGE_BOTH

    total = int(coverage.size)
    fractions = {
        _COVERAGE_NAMES[c]: round(
            float(np.sum(coverage == c)) / total, 6)
        for c in sorted(_COVERAGE_NAMES)
    }
    valid_counts = {
        k: int(np.sum(np.isfinite(v))) for k, v in sorted(named.items())
    }
    return {
        "feature_names": list(named),   # 插入序（optical 域先于 sar 域）
        "features": named,
        "coverage": coverage,
        "meta": {
            "grid": [int(shape[0]), int(shape[1])],
            "n_optical_features": len(list(optical_features or {})),
            "n_sar_features": len(list(sar_features or {})),
            "coverage_fractions": fractions,
            "feature_valid_counts": valid_counts,
            "coverage_codebook": {v: k for k, v in sorted(
                _COVERAGE_NAMES.items())},
            "disclosures": [
                "覆盖码=该像元任一同模态特征有效（逐特征有效性见计数表）",
                "单模态缺失是类型化覆盖语义，不是 0",
            ],
        },
    }


# ── 晚期证据融合 ──────────────────────────────────────────────────────

def late_evidence_fusion(
    evidence_a: np.ndarray,
    evidence_b: np.ndarray,
    *,
    names: Tuple[str, str] = ("optical", "sar"),
    weights: Optional[Mapping[str, float]] = None,
    sign_threshold: float = 0.0,
) -> Dict[str, Any]:
    """两幅带符号证据面的描述性晚期融合（加权 + 符号一致性）。

    - ``fused``：可用源按权归一加权（w_a·a + w_b·b)/(w_a+w_b)；单源像元
      保留自身证据（不与 0 混合——缺源不是 0）；双源缺失 → NaN；
    - ``agreement``：符号一致性码（阈值 ``sign_threshold``；缺省 0 =
      严格符号）；
    - 诚实边界：描述性融合——无概率语义、无模型训练。
    """
    a = np.asarray(evidence_a, dtype=float)
    b = np.asarray(evidence_b, dtype=float)
    if a.ndim != 2 or b.ndim != 2 or a.shape != b.shape:
        raise ValueError(
            f"证据面须为同形 2D 数组：{a.shape} vs {b.shape}")
    w = dict(weights or {names[0]: 1.0, names[1]: 1.0})
    for k, v in w.items():
        if not (np.isfinite(v) and float(v) >= 0):
            raise ValueError(
                f"weight[{k!r}] 必须为非负有限数，got {v!r}")
    wa = float(w.get(names[0], 1.0))
    wb = float(w.get(names[1], 1.0))
    if wa + wb <= 0:
        raise ValueError("weights 全零——融合无意义（typed 拒绝）")

    a_ok = np.isfinite(a)
    b_ok = np.isfinite(b)
    fused = np.full(a.shape, np.nan)
    both = a_ok & b_ok
    fused[both] = (wa * a[both] + wb * b[both]) / (wa + wb)
    fused[a_ok & ~b_ok] = a[a_ok & ~b_ok]
    fused[b_ok & ~a_ok] = b[b_ok & ~a_ok]

    agreement = np.full(a.shape, AGREEMENT_NONE, dtype=np.uint8)
    agreement[a_ok & ~b_ok] = AGREEMENT_OPTICAL_ONLY
    agreement[b_ok & ~a_ok] = AGREEMENT_SAR_ONLY
    # 严格符号（R1-P3-2）：|e| ≤ threshold 的零信号不算方向一致性——
    # 双侧零信号记 both_neutral，不冒充 consensus_negative
    sa = np.where(a_ok, a > sign_threshold, False)
    sb = np.where(b_ok, b > sign_threshold, False)
    na = np.where(a_ok, a < -sign_threshold, False)
    nb = np.where(b_ok, b < -sign_threshold, False)
    agreement[both & (sa & sb)] = AGREEMENT_CONSENSUS_POS
    agreement[both & (na & nb)] = AGREEMENT_CONSENSUS_NEG
    agreement[both & (sa ^ sb)] = AGREEMENT_CONFLICT
    agreement[both & ~(sa | sb | na | nb)] = AGREEMENT_BOTH_NEUTRAL

    counts: Dict[str, int] = {}
    code_names = {
        AGREEMENT_NONE: "none",
        AGREEMENT_OPTICAL_ONLY: f"{names[0]}_only",
        AGREEMENT_SAR_ONLY: f"{names[1]}_only",
        AGREEMENT_CONSENSUS_POS: "consensus_positive",
        AGREEMENT_CONSENSUS_NEG: "consensus_negative",
        AGREEMENT_CONFLICT: "conflict",
        AGREEMENT_BOTH_NEUTRAL: "both_neutral",
    }
    for c, nm in code_names.items():
        counts[nm] = int(np.sum(agreement == c))
    total = int(agreement.size)
    return {
        "fused": fused,
        "agreement": agreement,
        "meta": {
            "weights": {names[0]: wa, names[1]: wb},
            "sign_threshold": float(sign_threshold),
            "agreement_counts": counts,
            "agreement_fractions": {
                k: round(v / total, 6) for k, v in counts.items()
            } if total else {},
            "agreement_codebook": {v: k for k, v in code_names.items()},
            "disclosures": [
                "描述性晚期融合：非概率模型、无训练；缺源像元不与 0 混合",
                "conflict=两模态证据方向相反——解读需人工复核",
            ],
        },
    }
