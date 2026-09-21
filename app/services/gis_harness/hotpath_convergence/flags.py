"""Kill-switches / opt-in flags for hot-path convergence (Direction 04).

SkillPolicy: reuse ``GIS_SKILL_POLICY`` (D02) — default ON.
Mission bind: ``GIS_MISSION_HOTPATH`` opt-in (default OFF) AND ``GIS_MISSION_RUNTIME``.
Claim ingest: ``GIS_CLAIM_INGEST`` — default ON (process-local ClaimStore only).
Capability dispatch bind: ``GIS_CAPABILITY_DISPATCH_BIND`` — default ON (#1395).

方向 09（ADR-0204）：全部热路径 flag 的盘点真相迁至
``flag_registry.REGISTRY``（含双向一致性测试）；本模块的布尔门保持原样，
仅 re-export registry 供既有 importer 使用。
"""
from __future__ import annotations

import os

from app.services.gis_harness.hotpath_convergence.flag_registry import (  # noqa: F401
    REGISTRY,
    HotpathFlag,
    get_flag,
    registered_envs,
)

MISSION_HOTPATH_ENV = "GIS_MISSION_HOTPATH"
CLAIM_INGEST_ENV = "GIS_CLAIM_INGEST"


def _env_truthy(name: str, default: str) -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw not in ("0", "false", "off", "no")


def skill_policy_enabled() -> bool:
    """Reuse D02 kill-switch (default ON)."""
    from app.services.gis_harness.skills.policy import policy_enabled

    return policy_enabled()


def mission_runtime_gate_enabled() -> bool:
    from app.services.mission_runtime.service import mission_runtime_enabled

    return mission_runtime_enabled()


def mission_hotpath_enabled() -> bool:
    """Opt-in auto Mission create/reuse on multi-step GIS turns (default OFF)."""
    if not _env_truthy(MISSION_HOTPATH_ENV, "0"):
        return False
    return mission_runtime_gate_enabled()


def claim_ingest_enabled() -> bool:
    """Process-local claim ingest on settle (default ON; kill with 0)."""
    return _env_truthy(CLAIM_INGEST_ENV, "1")


def capability_dispatch_bind_enabled() -> bool:
    """Re-export — default ON (#1395 decision-chain bind at dispatch)."""
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        capability_dispatch_bind_enabled as _enabled,
    )

    return _enabled()


__all__ = [
    "CLAIM_INGEST_ENV",
    "MISSION_HOTPATH_ENV",
    "capability_dispatch_bind_enabled",
    "claim_ingest_enabled",
    "mission_hotpath_enabled",
    "mission_runtime_gate_enabled",
    "skill_policy_enabled",
]
