"""optical/SAR acquisition alignment —— 跨模态获取对齐引擎。

定位（本方向 ownership：optical/SAR acquisition alignment + gap model）：

- ``lakehouse.rs_cube`` 要求**预对齐**网格并把错位 typed 拒绝
  （"对齐是上游职责"）；本模块就是那个**上游**：跨模态获取的配对
  计划（AlignmentPlan）+ 类型化缺口账；
- **对齐是计划，不是静默重采样**（V6 红线）：网格恒等（crs/width/
  height/transform，与 ``_geometry_identity`` 同四键）不一致 →
  ``DegenerateData`` 列出不一致键 + 修正提示，绝不隐式 warp；
- **不伪造资产**：配不上的获取（超出容差）不产生任何 ref——只进
  ``unpaired_*`` 与 joint 缺口账（``joint_missing_slots``），联合分析
  的缺口是显式槽位而非 0 填充；未配对 SAR 仍可做 SAR-only 分析
  （诚实区分 joint 缺口与单模态可用性）；
- **配对语义**：每个光学时刻找最近 SAR（|dt| ≤ tolerance_days）；
  一景 SAR 只服务一期光学（先到先得、按时间序确定性贪心），防止
  同一后向散射观测被重复计入多期联合特征；
- 输入资产已声明的 ``gap_code``（云/layover 等）原样穿越对齐——对齐
  不掩盖上游质量语义；声明缺口的光学观测不计入有效配对率。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict

from app.lib.geo_analysis.rs_cube_descriptor import (
    CubeAsset,
    TemporalRasterCubeDescriptor,
    build_cube_descriptor,
    parse_time_iso,
)
from app.lib.gis.scientific_errors import DegenerateData

#: 契约版本（additive 演进）。
ALIGNMENT_CONTRACT_VERSION = "1.0"

#: 配对容差合法域（天）：SAR 重访 6-12 天 × 云延迟窗口的实用上界。
ALIGNMENT_TOLERANCE_DAYS_MIN = 0
ALIGNMENT_TOLERANCE_DAYS_MAX = 45

#: 网格恒等参与键（与 lakehouse RS 对齐四键一致）。
_GRID_KEYS = ("crs", "width", "height", "transform")


class AlignmentPair(BaseModel):
    """一次光学×SAR 配对（确定性顺序；dt 带符号，负 = SAR 先于光学）。"""

    model_config = ConfigDict(extra="forbid")

    optical_ref: str
    sar_ref: str
    optical_time_iso: str
    sar_time_iso: str
    dt_days: float

    @property
    def abs_dt_days(self) -> float:
        return abs(self.dt_days)


class AlignmentPlan(BaseModel):
    """跨模态对齐计划：配对表 + 未配对账 + 对齐后描述符 + 覆盖摘要。"""

    model_config = ConfigDict(extra="allow")

    contract_version: str = ALIGNMENT_CONTRACT_VERSION
    cube_id: str
    tolerance_days: int
    pairs: Tuple[AlignmentPair, ...] = ()
    unpaired_optical_times: Tuple[str, ...] = ()
    unpaired_sar_times: Tuple[str, ...] = ()
    aligned_descriptor: TemporalRasterCubeDescriptor
    coverage: Dict[str, Any] = {}
    disclosures: Tuple[str, ...] = ()


def _observation_assets(d: TemporalRasterCubeDescriptor):
    return [a for a in d.assets if a.role in ("optical", "sar")]


def _observation_times(d: TemporalRasterCubeDescriptor, role: str):
    ts = sorted({
        parse_time_iso(a.time_iso)
        for a in _observation_assets(d) if a.role == role
    })
    return ts


def _iso_of(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _check_grid_identity(
    optical: TemporalRasterCubeDescriptor,
    sar: TemporalRasterCubeDescriptor,
) -> None:
    """网格恒等校验：四键逐键相等，不一致 typed 拒绝（绝不静默重采样）。"""
    go, gs = optical.grid, sar.grid
    mismatched = []
    if go.crs != gs.crs:
        mismatched.append(f"crs: {go.crs!r} vs {gs.crs!r}")
    if go.width != gs.width:
        mismatched.append(f"width: {go.width} vs {gs.width}")
    if go.height != gs.height:
        mismatched.append(f"height: {go.height} vs {gs.height}")
    if go.transform != gs.transform:
        mismatched.append(f"transform: {go.transform} vs {gs.transform}")
    if mismatched:
        raise DegenerateData(
            "光学/SAR 网格恒等不一致——对齐引擎不做隐式重采样（V6 红线）；"
            "请先经显式 warp/再投影把两模态配准到公共网格: "
            + "; ".join(mismatched),
            correction_hint=(
                "用 geo_raster 窗口化底座做显式重采样/配准后再对齐；"
                "或把对齐容差内的跨模态配对留给 acquisition 级计划"),
        )


def _greedy_pairs(
    optical_times: Tuple[float, ...],
    sar_times: Tuple[float, ...],
    optical_valid: set,
    optical_refs: Dict[float, Tuple[str, ...]],
    sar_refs: Dict[float, Tuple[str, ...]],
    tolerance_days: int,
) -> Tuple[Tuple[AlignmentPair, ...], Tuple[str, ...], Tuple[str, ...]]:
    """时间升序确定性贪心：每期光学取最近未占用 SAR（|dt| ≤ 容差）。

    配对携带**真实资产 ref**（同刻多波段取排序首个——确定性 canonical）；
    一景 SAR（时刻）至多服务一期光学；声明缺口的时刻不消耗配对。
    """
    tol_sec = float(tolerance_days) * 86400.0
    used: set = set()
    pairs = []
    unpaired_optical = []
    iso_of = _iso_of
    for t in sorted(optical_times):
        t_iso = iso_of(t)
        if t not in optical_valid:
            # 声明为缺口的观测时刻不消耗 SAR 配对（质量语义优先）
            unpaired_optical.append(t_iso)
            continue
        candidates = sorted(
            (s for s in sar_times if s not in used
             and abs(s - t) <= tol_sec),
            key=lambda s: (abs(s - t), s))
        if not candidates:
            unpaired_optical.append(t_iso)
            continue
        s = candidates[0]
        used.add(s)
        pairs.append(AlignmentPair(
            optical_ref=optical_refs[t][0], sar_ref=sar_refs[s][0],
            optical_time_iso=t_iso, sar_time_iso=iso_of(s),
            dt_days=round((s - t) / 86400.0, 6),
        ))
    unpaired_sar = tuple(
        iso_of(s) for s in sorted(sar_times) if s not in used)
    return tuple(pairs), tuple(unpaired_optical), unpaired_sar


def align_acquisitions(
    optical: TemporalRasterCubeDescriptor,
    sar: TemporalRasterCubeDescriptor,
    *,
    tolerance_days: int = 3,
    aligned_cube_id: Optional[str] = None,
    label: str = "aligned_optical_sar",
) -> AlignmentPlan:
    """构建光学×SAR 获取对齐计划（配对 + 类型化缺口账 + 联合描述符）。"""
    if not isinstance(tolerance_days, int) or bool(
            tolerance_days < ALIGNMENT_TOLERANCE_DAYS_MIN
            or tolerance_days > ALIGNMENT_TOLERANCE_DAYS_MAX):
        raise ValueError(
            f"tolerance_days 必须是 [{ALIGNMENT_TOLERANCE_DAYS_MIN}, "
            f"{ALIGNMENT_TOLERANCE_DAYS_MAX}] 的整数，got {tolerance_days!r}")
    _check_grid_identity(optical, sar)

    # 光学时刻级有效语义：时刻有效 ⟺ 该时刻至少一个无 gap 的观测资产
    # （多波段同刻的波段级质量由下游特征层裁决）；声明缺口的时刻进缺口账
    # 且不消耗 SAR 配对。配对 ref 取同刻排序首个（确定性 canonical）。
    optical_times = _observation_times(optical, "optical")
    sar_times = _observation_times(sar, "sar")
    optical_refs_by_time: Dict[float, Tuple[str, ...]] = {}
    for a in _observation_assets(optical):
        if a.role != "optical":
            continue
        t = parse_time_iso(a.time_iso)
        optical_refs_by_time[t] = tuple(sorted(
            optical_refs_by_time.get(t, ()) + (a.ref,)))
    sar_refs_by_time: Dict[float, Tuple[str, ...]] = {}
    for a in _observation_assets(sar):
        if a.role != "sar":
            continue
        t = parse_time_iso(a.time_iso)
        sar_refs_by_time[t] = tuple(sorted(
            sar_refs_by_time.get(t, ()) + (a.ref,)))
    fully_invalid = {
        t for t in optical_times
        if not any(
            a.gap_code is None
            for a in _observation_assets(optical)
            if a.role == "optical" and parse_time_iso(a.time_iso) == t)
    }
    optical_valid_times = set(optical_times) - fully_invalid

    pairs, unpaired_optical, unpaired_sar = _greedy_pairs(
        optical_times, sar_times, optical_valid_times,
        optical_refs_by_time, sar_refs_by_time, tolerance_days)

    joint_missing = len(unpaired_optical)
    n_valid_optical = len(optical_valid_times)
    denom = n_valid_optical
    pair_rate = (len(pairs) / denom) if denom else 0.0

    aligned_id = aligned_cube_id or _derive_aligned_id(optical.cube_id,
                                                       sar.cube_id)
    merged_assets: Tuple[CubeAsset, ...] = tuple(optical.assets) + tuple(
        sar.assets)
    aligned = build_cube_descriptor(
        cube_id=aligned_id,
        grid=optical.grid,
        assets=merged_assets,
        label=label,
        source_version=";".join(
            v for v in (optical.source_version, sar.source_version) if v),
        lineage=(optical.cube_id, sar.cube_id),
        disclosures=(
            f"对齐容差 ±{tolerance_days} 天（最近邻、一景 SAR 至多服务一期"
            f"光学）；joint 缺口槽位 {joint_missing}"
            f"（未配对光学时刻，不伪造资产）",
        ),
    )

    disclosures = []
    if unpaired_optical:
        disclosures.append(
            f"{len(unpaired_optical)} 个光学时刻无容差内 SAR——联合分析缺口"
            f"（missing_acquisition 语义；单模态分析不受影响）")
    if unpaired_sar:
        disclosures.append(
            f"{len(unpaired_sar)} 个 SAR 获取未配对——仅可用于 SAR-only 分析")

    coverage: Dict[str, Any] = {
        "tolerance_days": int(tolerance_days),
        "n_optical_times": len(optical_times),
        "n_sar_times": len(sar_times),
        "valid_optical_assets": n_valid_optical,
        "n_pairs": len(pairs),
        "pair_rate": round(pair_rate, 6),
        "joint_missing_slots": joint_missing,
        "unpaired_sar_times": len(unpaired_sar),
        "max_abs_dt_days": (
            max(p.abs_dt_days for p in pairs) if pairs else None),
        "median_abs_dt_days": (
            sorted(p.abs_dt_days for p in pairs)[len(pairs) // 2]
            if pairs else None),
    }
    return AlignmentPlan(
        cube_id=aligned_id,
        tolerance_days=int(tolerance_days),
        pairs=pairs,
        unpaired_optical_times=unpaired_optical,
        unpaired_sar_times=unpaired_sar,
        aligned_descriptor=aligned,
        coverage=coverage,
        disclosures=tuple(disclosures),
    )


def _derive_aligned_id(a: str, b: str) -> str:
    merged = f"{a[:28]}-x-{b[:28]}"
    return merged[:64]
