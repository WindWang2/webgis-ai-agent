"""Spatiotemporal / multi-modal contract（ADR-0119 §3.9；Epic §J）。

遥感时序模型的输入契约：时间维 + 缺失观测策略 + 质量掩膜 + 光学/SAR
极化元数据 + 输出时间语义。**不训练模型**——本模块只定义契约与纯校验。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Sequence, Tuple

from app.lib.modelops.capabilities import (
    MISSING_POLICIES,
    MISSING_POLICY_ERROR,
    MODALITY_OPTICAL_MULTISPECTRAL,
    MODALITY_SAR,
)
from app.lib.modelops.errors import DescriptorError

#: SAR 极化词表（band_order 语义成员）。
SAR_POLARIZATIONS = frozenset({"VV", "VH", "HH", "HV"})


@dataclass(frozen=True)
class TemporalStackSpec:
    """一次时序推理的时间轴声明。"""

    times: Tuple[str, ...]                 # ISO-8601 日期/时刻，升序
    max_length: int = 1
    missing_policy: str = MISSING_POLICY_ERROR
    #: 每 time slice 的质量掩膜（bool arrays；与 times 对齐或为空）。
    quality_masks: Tuple[Any, ...] = ()
    #: 模态声明（optical_multispectral / sar），SAR 时 band_order 用极化词表。
    modalities: Tuple[str, ...] = ()
    #: 输出时间语义：last（预测最后观测之后的情形）| mean | sequence。
    output_time_semantics: str = "last"

    def __post_init__(self) -> None:
        if not self.times:
            raise DescriptorError("temporal inference requires at least one observation")
        if len(self.times) > self.max_length:
            raise DescriptorError(
                f"temporal stack length {len(self.times)} exceeds model max_length {self.max_length}"
            )
        if self.missing_policy not in MISSING_POLICIES:
            raise DescriptorError(f"unknown missing_policy {self.missing_policy!r}")
        if list(self.times) != sorted(self.times):
            raise DescriptorError("temporal times must be ascending ISO-8601")
        if len(set(self.times)) != len(self.times):
            raise DescriptorError("temporal times must be unique")
        if self.quality_masks and len(self.quality_masks) != len(self.times):
            raise DescriptorError("quality_masks must align with times (or be empty)")
        if self.output_time_semantics not in ("last", "mean", "sequence"):
            raise DescriptorError(f"unknown output_time_semantics {self.output_time_semantics!r}")
        unknown_modalities = set(self.modalities) - {
            MODALITY_OPTICAL_MULTISPECTRAL,
            MODALITY_SAR,
            "optical_rgb",
            "dem",
        }
        if unknown_modalities:
            raise DescriptorError(f"unknown temporal modalities {sorted(unknown_modalities)}")

    def missing_positions(self, present: Sequence[bool]) -> Tuple[int, ...]:
        """缺失观测位置（present 与 times 对齐）；按策略裁决。"""
        if len(present) != len(self.times):
            raise DescriptorError("present[] must align with times")
        missing = tuple(i for i, ok in enumerate(present) if not ok)
        if missing and self.missing_policy == MISSING_POLICY_ERROR:
            raise DescriptorError(
                f"missing observations at positions {missing} and missing_policy=error"
            )
        return missing

    def to_payload(self) -> Dict[str, Any]:
        return {
            "times": list(self.times),
            "max_length": self.max_length,
            "missing_policy": self.missing_policy,
            "quality_mask_count": len(self.quality_masks),
            "modalities": list(self.modalities),
            "output_time_semantics": self.output_time_semantics,
        }
