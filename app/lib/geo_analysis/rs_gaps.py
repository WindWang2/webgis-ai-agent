"""Typed gap model —— 类型化缺口账 + 逐像元缺口平面 + 覆盖卡。

定位（本方向 ownership：quality masks / gap semantics / disclosure）：

- **槽位账**（descriptor 级）：联合分析的每个光学时刻是一个槽位——
  ``paired`` / ``missing_acquisition``（无容差内 SAR）/ 声明缺口
  （cloud 等）。显式枚举"什么时间没有什么"，缺测是**一等语义槽位**，
  不是 0；显式 ``expected_times`` 是调用方声明的计划轴（无启发式）；
- **像元缺口平面**（栈级）：``(T,H,W) uint8`` 码平面，0=有效，非 0 =
  ``GAP_CODE_IDS`` 中的类型化缺口。与值平面**解耦**（绝不改写原始值，
  绝不把缺口切片置 0）；声明缺口（整片语义）优先于像元级 nodata；
- **覆盖卡**：把对齐覆盖 + 缺口计数 + source_version 聚合成单一有界
  JSON 卡，供 evidence / MapProduct 表格通道消费；
- 诚实边界：本模块不做云判识 / layover 计算（那是 cloud_qc_basic /
  layover_shadow_mask 的职责），只消费声明语义并计数披露。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

from app.lib.geo_analysis.rs_alignment import AlignmentPlan
from app.lib.geo_analysis.rs_cube_descriptor import (
    GAP_CODES,
    TemporalRasterCubeDescriptor,
    parse_time_iso,
)

#: 缺口码 → uint8 id（0 = 有效；id 表稳定——新增码只追加，不改既有 id）。
GAP_CODE_IDS: Dict[str, int] = {
    "valid": 0,
    "below_quality": 1,
    "nodata": 2,
    "cloud": 3,
    "cloud_shadow": 4,
    "missing_acquisition": 5,
    "layover": 6,
    "sar_shadow": 7,
    "off_grid": 8,
    "unregistered": 9,
}
_ID_TO_CODE: Dict[int, str] = {v: k for k, v in GAP_CODE_IDS.items()}


def _iso_of(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ── 槽位账（descriptor / plan 级）─────────────────────────────────────

def joint_slot_table(plan: AlignmentPlan) -> Tuple[Dict[str, Any], ...]:
    """联合分析槽位表：每个光学时刻的联合可用语义（有界、JSON 安全）。

    码优先级：**声明缺口**（上游 QC：cloud 等——获取存在但不可用）
    > ``paired``（容差内配对成立）> ``missing_acquisition``（无容差内
    SAR 的 joint 缺口槽位）。缺测是一等槽位，不是 0。
    """
    paired_times = {p.optical_time_iso for p in plan.pairs}
    declared = _declared_optical_times(plan.aligned_descriptor)
    all_times = sorted(set(paired_times) | set(plan.unpaired_optical_times))
    out = []
    for t in all_times:
        if t in declared:
            code = declared[t]
        elif t in paired_times:
            code = "paired"
        else:
            code = "missing_acquisition"
        out.append({"time": t, "code": code})
    return tuple(out)


def _declared_optical_times(
        d: TemporalRasterCubeDescriptor) -> Dict[str, str]:
    """声明了 gap_code 的光学时刻（canonical ISO；同刻多资产任一声明即时刻级）。"""
    declared: Dict[str, str] = {}
    for a in d.assets:
        if a.role == "optical" and a.gap_code is not None:
            declared.setdefault(_canon(a.time_iso), a.gap_code)
    return declared


def cadence_gap_report(
    d: TemporalRasterCubeDescriptor,
    *,
    expected_times: Sequence[str],
) -> Dict[str, Any]:
    """显式计划轴缺口报告：expected - observed_valid = missing_acquisition。

    ``expected_times`` 由调用方声明（如逐月计划轴）；本函数不做任何
    间距启发式（间距启发式披露见 temporal_cube.slice_spacing_report）。
    """
    expected = [str(t) for t in (expected_times or [])]
    expected_norm = [_canon(t) for t in expected]
    observed_valid = {
        _canon(a.time_iso)
        for a in d.assets
        if a.role in ("optical", "sar") and a.gap_code is None
    }
    missing = [t for t in expected_norm if t not in observed_valid]
    unexpected = sorted(set(observed_valid) - set(expected_norm))
    n_exp = len(expected_norm)
    n_missing = len(missing)
    return {
        "n_expected": n_exp,
        "n_observed_valid": len(observed_valid & set(expected_norm)),
        "missing_times": missing,
        "unexpected_times": unexpected,
        "missing_ratio": round(n_missing / n_exp, 6) if n_exp else 0.0,
        "disclosure": (
            "missing = expected_times 中无有效观测的时刻"
            "（missing_acquisition 语义；不插值填充）"),
    }


def _canon(time_iso: str) -> str:
    return _iso_of(parse_time_iso(time_iso))


# ── 像元缺口平面（栈级）───────────────────────────────────────────────

def build_gap_mask(
    stack: np.ndarray,
    *,
    declared_codes: Optional[Sequence[Optional[str]]] = None,
    quality: Optional[np.ndarray] = None,
) -> np.ndarray:
    """构建 (T,H,W) uint8 缺口码平面（0=有效；与值平面解耦）。

    优先级：整片声明缺口 > 像元级 below_quality（quality=0）> nodata
    （非有限值）> valid。声明码必须 ∈ GAP_CODES。
    """
    arr = np.asarray(stack, dtype=float)
    if arr.ndim != 3:
        raise ValueError(f"gap mask 需要 (T,H,W) 栈，got shape {arr.shape}")
    t_len = arr.shape[0]
    declared = list(declared_codes or [])
    if declared and len(declared) != t_len:
        raise ValueError(
            f"declared_codes 形状 {len(declared)} 与栈时间轴 {t_len} 不一致")
    for c in declared:
        if c is not None and c not in GAP_CODE_IDS:
            raise ValueError(
                f"声明缺口码 {c!r} 不在 GAP_CODES 封闭词表")
    out = np.zeros(arr.shape, dtype=np.uint8)
    if quality is not None:
        q = np.asarray(quality, dtype=float)
        if q.shape != arr.shape:
            raise ValueError(
                f"quality 形状 {q.shape} 与栈 {arr.shape} 不一致")
        out[(q <= 0.0) | ~np.isfinite(q)] = GAP_CODE_IDS["below_quality"]
    invalid = ~np.isfinite(arr)
    out[invalid & (out == 0)] = GAP_CODE_IDS["nodata"]
    for i, c in enumerate(declared):
        if c is not None:
            out[i] = GAP_CODE_IDS[c]     # 整片声明优先（切片级语义）
    return out


def gap_report(mask: np.ndarray) -> Dict[str, Any]:
    """缺口平面 → 有界计数/比率报告（JSON 安全）。"""
    m = np.asarray(mask)
    total = int(m.size)
    counts: Dict[str, int] = {}
    for code_id, code in _ID_TO_CODE.items():
        if code == "valid":
            continue
        n = int(np.sum(m == code_id))
        if n:
            counts[code] = n
    n_valid = int(np.sum(m == 0))
    return {
        "total_pixels": total,
        "n_valid": n_valid,
        "valid_ratio": round(n_valid / total, 6) if total else 0.0,
        "counts": {k: counts[k] for k in sorted(counts)},
        "ratios": {
            k: round(counts[k] / total, 6) for k in sorted(counts)
        } if total else {},
        "codebook": {c: GAP_CODE_IDS[c] for c in sorted(counts)},
        "disclosure": "缺口像元保持类型化语义，不插值、不置 0",
    }


# ── 覆盖卡（plan 级聚合）──────────────────────────────────────────────

def build_coverage_card(plan: AlignmentPlan) -> Dict[str, Any]:
    """对齐计划 → 单一有界覆盖卡（alignment + 缺口 + source_version）。"""
    d = plan.aligned_descriptor
    gap_counts: Dict[str, int] = {}
    for a in d.assets:
        if a.role in ("optical", "sar") and a.gap_code is not None:
            gap_counts[a.gap_code] = gap_counts.get(a.gap_code, 0) + 1
    versions = sorted({
        v for v in (
            d.source_version,
            *(a.source_version for a in d.assets if a.source_version),
        ) if v
    })
    for v in d.source_version.split(";"):
        if v.strip() and v.strip() not in versions:
            versions.append(v.strip())
    versions.sort()
    return {
        "cube_id": d.cube_id,
        "contract": {
            "descriptor": d.contract_version,
            "alignment": plan.contract_version,
        },
        "grid": d.grid.identity(),
        "alignment": {
            k: plan.coverage.get(k)
            for k in ("tolerance_days", "n_pairs", "pair_rate",
                      "joint_missing_slots", "max_abs_dt_days")
        },
        "gap_counts": {k: gap_counts[k] for k in sorted(gap_counts)},
        "joint_missing_slots": plan.coverage.get("joint_missing_slots", 0),
        "unpaired_sar_times": plan.coverage.get("unpaired_sar_times", 0),
        "n_time_steps": len(d.times_sec),
        "n_assets": len(d.assets),
        "source_versions": versions,
        "disclosures": list(d.disclosures) + list(plan.disclosures),
        "disclosure": "覆盖卡是有界 JSON（refs-only）；缺测=类型化槽位",
    }
