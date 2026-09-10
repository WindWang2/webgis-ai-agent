"""Compatibility Qualifier —— 模型×输入语义资格（ADR-0119 §3.3；Epic §C）。

「能跑」≠ 兼容：qualifier 在任何读取/执行之前判定，失败 typed 且
结构化（每条失败 = CompatibilityFailure，带 fix hint）。判定输入是
**InputProfile**（来自 RasterReader.metadata + 采样 band 统计），不是
数据本身——qualifier 是纯函数。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.lib.modelops.capabilities import (
    MODALITY_OPTICAL_RGB,
    TASK_PROMPTABLE_SEGMENTATION,
)
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import CompatibilityError
from app.lib.modelops.promptable import PromptSpec
from app.lib.modelops.temporal import TemporalStackSpec

#: nodata 占比 > 此值 → NODATA_HEAVY warning（不阻断，进 manifest）。
NODATA_HEAVY_RATIO = 0.5


@dataclass(frozen=True)
class CompatibilityFailure:
    """一条 typed 兼容失败（code 封闭词表；qualifier 唯一产地）。"""

    code: str
    detail: str
    fix_hint: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "detail": self.detail, "fix_hint": self.fix_hint}


# 失败码词表（封闭；测试矩阵 §1 逐码断言）
FAILURE_BAND_COUNT = "BAND_COUNT"
FAILURE_BAND_ORDER = "BAND_ORDER"
FAILURE_MODALITY = "MODALITY"
FAILURE_DTYPE = "DTYPE"
FAILURE_RESOLUTION = "RESOLUTION"
FAILURE_CRS = "CRS"
FAILURE_TEMPORAL_LENGTH = "TEMPORAL_LENGTH"
FAILURE_CHIP_SIZE = "CHIP_SIZE"
FAILURE_CLASS_SCHEMA = "CLASS_SCHEMA"
FAILURE_PROMPT_MODE = "PROMPT_MODE"
WARNING_NODATA_HEAVY = "NODATA_HEAVY"


@dataclass(frozen=True)
class InputProfile:
    """输入栅格的语义画像（RasterReader.metadata + 采样统计）。"""

    width: int
    height: int
    band_count: int
    band_names: Tuple[str, ...] = ()   # 语义名（cog profile / STAC 波段；可为空）
    dtype: str = "float32"
    crs: Optional[str] = None
    m_per_px: float = 0.0              # 0 = 未知（无法做 resolution 判定→warning）
    nodata: Optional[float] = None
    nodata_ratio: float = 0.0          # 采样估计
    temporal_length: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "band_count": self.band_count,
            "band_names": list(self.band_names),
            "dtype": self.dtype,
            "crs": self.crs,
            "m_per_px": self.m_per_px,
            "nodata": self.nodata,
            "nodata_ratio": self.nodata_ratio,
            "temporal_length": self.temporal_length,
        }


@dataclass(frozen=True)
class CompatibilityReport:
    """资格判定结果（verdict + 结构化失败/警告）。"""

    verdict: str                        # "compatible" | "incompatible"
    failures: Tuple[CompatibilityFailure, ...] = ()
    warnings: Tuple[CompatibilityFailure, ...] = ()
    #: 显式重投影需求（R1-C2 ReprojectStage 的输入；None = no-op）。
    reproject: Optional[Dict[str, Any]] = None

    @property
    def compatible(self) -> bool:
        return self.verdict == "compatible"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "failures": [f.to_dict() for f in self.failures],
            "warnings": [w.to_dict() for w in self.warnings],
            "reproject": self.reproject,
        }


def _crs_family(crs: Optional[str]) -> str:
    """CRS 族归一（EPSG:xxxx → 权限码小写比较）。"""
    if not crs:
        return "unknown"
    text = str(crs).strip().upper()
    if text.startswith("EPSG:"):
        return f"epsg:{text.split(':', 1)[1]}"
    return text.lower()


def qualify(
    descriptor: GeoModelDescriptor,
    profile: InputProfile,
    *,
    prompt: Optional[PromptSpec] = None,
    temporal: Optional[TemporalStackSpec] = None,
) -> CompatibilityReport:
    """模型×输入语义资格（纯函数；typed 失败集合，禁止部分通过）。

    重投影裁决（R1-C2）：CRS ∉ crs_requirements 或分辨率 ∉ range 时，
    仅当 descriptor.spatial.resampling_policy 允许（显式非空）才产出
    ``reproject`` 计划（verdict=compatible + reproject 字段）；否则
    CRS/RESOLUTION typed 失败。
    """
    failures: List[CompatibilityFailure] = []
    warnings: List[CompatibilityFailure] = []
    needs_reproject: Optional[Dict[str, Any]] = None

    # ── 波段 ────────────────────────────────────────────────────────
    is_temporal_task = "temporal_forecast" in descriptor.task_types
    if is_temporal_task:
        # 时序源栅格 = C*T 波段（time-major 布局）；按整除关系判定。
        if (
            profile.band_count < descriptor.input_bands
            or profile.band_count % descriptor.input_bands != 0
        ):
            failures.append(
                CompatibilityFailure(
                    FAILURE_BAND_COUNT,
                    f"temporal source has {profile.band_count} bands; expected a "
                    f"multiple of per-slice bands ({descriptor.input_bands})",
                    fix_hint="stack observations time-major (C*T bands)",
                )
            )
    elif profile.band_count != descriptor.input_bands:
        failures.append(
            CompatibilityFailure(
                FAILURE_BAND_COUNT,
                f"model expects {descriptor.input_bands} bands, input has {profile.band_count}",
                fix_hint="select/reorder bands or choose a model matching band count",
            )
        )
    elif descriptor.band_order and profile.band_names:
        missing = [name for name in descriptor.band_order if name not in profile.band_names]
        if missing:
            failures.append(
                CompatibilityFailure(
                    FAILURE_BAND_ORDER,
                    f"model band_order {list(descriptor.band_order)} not satisfiable; "
                    f"input bands missing {missing}",
                    fix_hint="map input band semantics to the model band_order",
                )
            )

    # ── SAR 极化语义（modality=sar 时 band_order 应为极化词表）──────
    if "sar" in descriptor.input_modalities and descriptor.band_order:
        unknown_pol = [b for b in descriptor.band_order if b not in ("VV", "VH", "HH", "HV")]
        if unknown_pol:
            failures.append(
                CompatibilityFailure(
                    FAILURE_BAND_ORDER,
                    f"SAR model band_order contains non-polarization entries {unknown_pol}",
                    fix_hint="declare polarizations (VV/VH/HH/HV) in band_order",
                )
            )

    # ── modality（RGB 模型 vs 多光谱输入等）────────────────────────
    if MODALITY_OPTICAL_RGB in descriptor.input_modalities and profile.band_count > 3:
        failures.append(
            CompatibilityFailure(
                FAILURE_MODALITY,
                f"RGB model vs {profile.band_count}-band multispectral input",
                fix_hint="select the 3 RGB bands explicitly or use a multispectral model",
            )
        )

    # ── dtype（模型接受 float32 归一化输入；int 输入需声明 cast）───
    allowed_dtypes = {"float32", "float64", "uint8", "uint16", "int16", "int32"}
    if profile.dtype not in allowed_dtypes:
        failures.append(
            CompatibilityFailure(
                FAILURE_DTYPE,
                f"input dtype {profile.dtype!r} is not castable to model input",
                fix_hint="convert to uint8/uint16/float32 before inference",
            )
        )

    # ── 分辨率 ──────────────────────────────────────────────────────
    allow_reproject = descriptor.spatial.allow_reproject and bool(
        descriptor.spatial.resampling_policy
    )
    rng = descriptor.spatial.resolution_range
    if rng is not None and profile.m_per_px > 0:
        if not rng.contains(profile.m_per_px):
            if allow_reproject:
                # 显式允许重采样：目标 = 区间中点（声明式、可进指纹）。
                target = round((rng.min_m_per_px + rng.max_m_per_px) / 2, 6)
                needs_reproject = needs_reproject or {}
                needs_reproject["target_m_per_px"] = target
                needs_reproject["resampling"] = descriptor.spatial.resampling_policy
                warnings.append(
                    CompatibilityFailure(
                        "RESOLUTION_RESAMPLE",
                        f"input {profile.m_per_px}m/px outside model range "
                        f"[{rng.min_m_per_px}, {rng.max_m_per_px}]; "
                        f"explicit resample to {target}m/px declared",
                        fix_hint="verify the resampling choice is scientifically acceptable",
                    )
                )
            else:
                failures.append(
                    CompatibilityFailure(
                        FAILURE_RESOLUTION,
                        f"input {profile.m_per_px}m/px outside model range "
                        f"[{rng.min_m_per_px}, {rng.max_m_per_px}] and no resampling_policy",
                        fix_hint="declare resampling_policy or choose a resolution-matched model",
                    )
                )

    # ── CRS ─────────────────────────────────────────────────────────
    if descriptor.spatial.crs_requirements:
        wanted = {_crs_family(c) for c in descriptor.spatial.crs_requirements}
        have = _crs_family(profile.crs)
        if have not in wanted:
            if allow_reproject:
                target_crs = descriptor.spatial.crs_requirements[0]
                needs_reproject = needs_reproject or {}
                needs_reproject["target_crs"] = target_crs
                needs_reproject["resampling"] = descriptor.spatial.resampling_policy
                warnings.append(
                    CompatibilityFailure(
                        "CRS_REPROJECT",
                        f"input CRS {profile.crs!r} not in {sorted(wanted)}; "
                        f"explicit reproject to {target_crs!r} declared",
                        fix_hint="verify the reprojection is scientifically acceptable",
                    )
                )
            else:
                failures.append(
                    CompatibilityFailure(
                        FAILURE_CRS,
                        f"input CRS {profile.crs!r} not in model requirements "
                        f"{sorted(wanted)} and no resampling_policy",
                        fix_hint="reproject explicitly or relax crs_requirements",
                    )
                )

    # ── 时序 ────────────────────────────────────────────────────────
    if temporal is not None:
        if len(temporal.times) > descriptor.temporal.max_length:
            failures.append(
                CompatibilityFailure(
                    FAILURE_TEMPORAL_LENGTH,
                    f"stack length {len(temporal.times)} > model max {descriptor.temporal.max_length}",
                    fix_hint="shorten the stack or use a longer-context model",
                )
            )
        if (
            descriptor.temporal.required_length is not None
            and len(temporal.times) < descriptor.temporal.required_length
        ):
            failures.append(
                CompatibilityFailure(
                    FAILURE_TEMPORAL_LENGTH,
                    f"stack length {len(temporal.times)} < model required "
                    f"{descriptor.temporal.required_length}",
                    fix_hint="add observations or relax required_length",
                )
            )
    elif descriptor.task_types and "temporal_forecast" in descriptor.task_types:
        if profile.temporal_length < 2:
            failures.append(
                CompatibilityFailure(
                    FAILURE_TEMPORAL_LENGTH,
                    "temporal model requires a stacked time dimension (input is single-scene)",
                    fix_hint="stack observations via the temporal contract",
                )
            )

    # ── chip 可行性 ────────────────────────────────────────────────
    chip_w, chip_h = descriptor.spatial.chip_size
    if profile.width < chip_w // 4 or profile.height < chip_h // 4:
        warnings.append(
            CompatibilityFailure(
                FAILURE_CHIP_SIZE,
                f"input {profile.width}x{profile.height} is much smaller than chip "
                f"{chip_w}x{chip_h} (heavy padding)",
                fix_hint="consider a smaller-chip model for tiny rasters",
            )
        )

    # ── prompt 能力门（engine 传入 provider caps 后补第二道；此处对
    #    descriptor 任务语义先判）────────────────────────────────────
    if prompt is not None and TASK_PROMPTABLE_SEGMENTATION not in descriptor.task_types:
        failures.append(
            CompatibilityFailure(
                FAILURE_PROMPT_MODE,
                f"prompt supplied but model tasks {list(descriptor.task_types)} do not "
                "include promptable_segmentation",
                fix_hint="choose a promptable model",
            )
        )

    # ── class schema / 输出几何 ────────────────────────────────────
    if descriptor.class_schema is not None and not descriptor.class_schema.classes:
        failures.append(
            CompatibilityFailure(
                FAILURE_CLASS_SCHEMA,
                "segmentation model lacks class names (class_schema.classes empty)",
                fix_hint="register the model with a named class schema",
            )
        )

    # ── nodata warning ─────────────────────────────────────────────
    if profile.nodata_ratio > NODATA_HEAVY_RATIO:
        warnings.append(
            CompatibilityFailure(
                WARNING_NODATA_HEAVY,
                f"input is {profile.nodata_ratio:.0%} nodata",
                fix_hint="check the AOI coverage; output mask will honor input nodata",
            )
        )

    reproject = None
    if needs_reproject:
        reproject = dict(needs_reproject)
    verdict = "incompatible" if failures else "compatible"
    return CompatibilityReport(
        verdict=verdict,
        failures=tuple(failures),
        warnings=tuple(warnings),
        reproject=reproject,
    )


def report_to_error(report: CompatibilityReport) -> CompatibilityError:
    """report → typed 异常（engine/服务层抛出用）。"""
    return CompatibilityError(
        "; ".join(f"{f.code}: {f.detail}" for f in report.failures),
        failures=[f.to_dict() for f in report.failures],
        correction_hint=report.failures[0].fix_hint if report.failures else "",
    )
