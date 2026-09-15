"""空间反幻觉守护引擎核心类型（ADR-0195 / docs/dev/spatial-guardrails-spec.md §2）。

只有标准库依赖；层级/策略词表是跨挂载点的稳定机器契约。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class GuardLevel(str, Enum):
    """四级拦截层级：证据标签而非串行流水线（ADR-0195 D2）。"""

    L1_FORMAT_CRS = "L1_FORMAT_CRS"
    L2_GEOGRAPHIC_BOUNDS = "L2_GEOGRAPHIC_BOUNDS"
    L3_LANDMASK_PLAUSIBILITY = "L3_LANDMASK_PLAUSIBILITY"
    L4_TOPOLOGY_CONSISTENCY = "L4_TOPOLOGY_CONSISTENCY"


class DefenseMode(str, Enum):
    """三种防御策略 + 显式通过态（ADR-0195 D3）。"""

    BLOCK = "BLOCK"
    AUTO_FLIP = "AUTO_FLIP"
    WARN_DEGRADE = "WARN_DEGRADE"
    PASS = "PASS"


# 稳定 issue 机器码词表（观测与测试都按 code 断言，不按 message）
CODE_L1_INVALID_COORDINATE = "L1_INVALID_COORDINATE"
CODE_LATLON_INVERTED_AUTO_FLIP = "LATLON_INVERTED_AUTO_FLIP"
CODE_SUSPICIOUS_LATLON_ORDER = "SUSPICIOUS_LATLON_ORDER"
CODE_OCEAN_POINT_ON_LAND_FACILITY = "OCEAN_POINT_ON_LAND_FACILITY"
CODE_SUSPICIOUS_OCEAN_POINT = "SUSPICIOUS_OCEAN_POINT"
CODE_WATER_BODY_FACILITY = "WATER_BODY_FACILITY"
CODE_COASTAL_BAND_UNCERTAIN = "COASTAL_BAND_UNCERTAIN"
CODE_FABRICATED_ADMIN_CODE = "FABRICATED_ADMIN_CODE"
CODE_UNKNOWN_BUT_PLAUSIBLE_ADMIN_CODE = "UNKNOWN_BUT_PLAUSIBLE_ADMIN_CODE"
CODE_ADMIN_PARENT_MISMATCH = "ADMIN_PARENT_MISMATCH"
CODE_GEOFENCE_REDLINE_VIOLATION = "GEOFENCE_REDLINE_VIOLATION"
CODE_BBOX_AREA_BUDGET_EXCEEDED = "BBOX_AREA_BUDGET_EXCEEDED"
CODE_TOPOLOGY_IMPLAUSIBLE = "TOPOLOGY_IMPLAUSIBLE"
CODE_GUARDRAIL_INTERNAL_ERROR = "GUARDRAIL_INTERNAL_ERROR"


@dataclass
class GuardrailIssue:
    """一次校验中的单条发现：层级 + 策略 + 稳定机器码 + 证据。"""

    level: GuardLevel
    mode: DefenseMode
    code: str
    message: str
    location: str
    evidence: dict = field(default_factory=dict)
    corrected: object | None = None

    def to_dict(self) -> dict:
        return {
            "level": self.level.value,
            "mode": self.mode.value,
            "code": self.code,
            "message": self.message,
            "location": self.location,
            "evidence": self.evidence,
            "corrected": self.corrected,
        }


@dataclass
class GuardrailVerdict:
    """网关裁决：无 BLOCK 即 passed（WARN 不阻断）；mutated 表示发生 AUTO_FLIP。"""

    passed: bool = True
    mutated: bool = False
    issues: list[GuardrailIssue] = field(default_factory=list)
    duration_ms: float = 0.0

    def blocking(self) -> list[GuardrailIssue]:
        return [i for i in self.issues if i.mode == DefenseMode.BLOCK]

    def warnings(self) -> list[GuardrailIssue]:
        return [i for i in self.issues if i.mode == DefenseMode.WARN_DEGRADE]

    def first_blocking_code(self) -> str | None:
        blocks = self.blocking()
        return blocks[0].code if blocks else None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "mutated": self.mutated,
            "duration_ms": round(self.duration_ms, 3),
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass(frozen=True)
class GuardrailConfig:
    """守护引擎配置（env 可覆盖；见 spec §2 配置表）。"""

    enabled: bool = True
    auto_flip_min_confidence: float = 0.60
    ocean_confirm_km: float = 150.0
    max_bbox_km2: float = 250_000.0
    teleport_km: float = 300.0
    redlines_json: str = ""

    @classmethod
    def from_env(cls) -> "GuardrailConfig":
        def _f(name: str, default: float) -> float:
            raw = os.getenv(name, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        return cls(
            enabled=os.getenv("SPATIAL_GUARDRAILS", "1") != "0",
            auto_flip_min_confidence=_f(
                "SPATIAL_GUARDRAILS_AUTO_FLIP_MIN_CONFIDENCE", 0.60
            ),
            ocean_confirm_km=_f("SPATIAL_GUARDRAILS_OCEAN_CONFIRM_KM", 150.0),
            max_bbox_km2=_f("SPATIAL_GUARDRAILS_MAX_BBOX_KM2", 250_000.0),
            teleport_km=_f("SPATIAL_GUARDRAILS_TELEPORT_KM", 300.0),
            redlines_json=os.getenv("SPATIAL_GUARDRAILS_REDLINES_JSON", "").strip(),
        )


def guardrails_enabled() -> bool:
    """进程级 kill switch（每次调用现读 env，测试与运维无需重建单例）。"""
    return os.getenv("SPATIAL_GUARDRAILS", "1") != "0"
