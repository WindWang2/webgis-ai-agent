"""DevicePlan / ResourceEstimate / batch 数学（ADR-0119 §3.5）。

GeoCompute seam：ModelOps 自持 device/VRAM 维度（BudgetLimits 无 GPU 维，
基线 Q5）；bytes/concurrency 记账语义与 ResourceGovernor 对齐（本模块
只定义契约与纯数学，不做 governor import——引擎层负责记账接线）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class ResourceEstimate:
    """provider 自报的资源估计（engine/auto-tune 消费）。"""

    vram_bytes: int
    host_ram_bytes: int
    recommended_batch: int
    est_seconds_per_chip: float = 0.0
    #: extension/remote 的内存不可观测（RLIMIT/不可见）→ externally_enforced。
    externally_enforced: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "vram_bytes": self.vram_bytes,
            "host_ram_bytes": self.host_ram_bytes,
            "recommended_batch": self.recommended_batch,
            "est_seconds_per_chip": self.est_seconds_per_chip,
            "externally_enforced": self.externally_enforced,
        }


@dataclass(frozen=True)
class DevicePlan:
    """一次推理的资源计划（typed 冻结值）。"""

    device: str
    batch: int = 1
    vram_bytes: int = 0
    host_ram_bytes: int = 0
    est_seconds: float = 0.0
    #: 资源记账口径（"provider_visible" | "externally_enforced"）。
    accounting: str = "provider_visible"

    def as_dict(self) -> Dict[str, object]:
        return {
            "device": self.device,
            "batch": self.batch,
            "vram_bytes": self.vram_bytes,
            "host_ram_bytes": self.host_ram_bytes,
            "est_seconds": self.est_seconds,
            "accounting": self.accounting,
        }


def batch_for_budget(
    *,
    chip_hw: Tuple[int, int],
    input_channels: int,
    output_channels: int,
    bytes_budget: int,
    max_batch: int,
    recommended_batch: Optional[int] = None,
    bytes_per_element: int = 4,
) -> int:
    """自适应批尺寸（纯数学）：输入+概率输出双份 × 预算约束。

    bytes/chip ≈ (C_in + C_out) × H × W × 4（float32 输入 + float32 概率），
    乘 2 的安全系数覆盖中间缓冲。下限 1。
    """
    h, w = chip_hw
    per_chip = max(1, (input_channels + max(1, output_channels)) * h * w * bytes_per_element * 2)
    by_budget = max(1, bytes_budget // per_chip)
    batch = min(by_budget, max_batch)
    if recommended_batch:
        batch = min(batch, recommended_batch)
    return max(1, batch)
