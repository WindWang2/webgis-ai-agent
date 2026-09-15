"""Durable GIS Mission Runtime (ADR-0197) — ownership/lifecycle envelope.

Pi remains Agent Host. Workflow Runtime, Swarm, Artifact Registry, and
Governor remain authoritative subsystems; this package adds durable Mission
ledger, lease/fencing, swarm durability bridge, and recovery.
"""
from __future__ import annotations

from typing import Any

_LAZY: dict[str, tuple[str, str]] = {
    "MissionState": ("app.services.mission_runtime.contracts", "MissionState"),
    "MissionRecord": ("app.services.mission_runtime.contracts", "MissionRecord"),
    "OperationClass": ("app.services.mission_runtime.contracts", "OperationClass"),
    "MissionStore": ("app.services.mission_runtime.store", "MissionStore"),
    "FencingError": ("app.services.mission_runtime.store", "FencingError"),
    "MissionRuntimeService": (
        "app.services.mission_runtime.service", "MissionRuntimeService"),
    "get_mission_runtime": (
        "app.services.mission_runtime.service", "get_mission_runtime"),
    "mission_runtime_enabled": (
        "app.services.mission_runtime.service", "mission_runtime_enabled"),
    "DurableSwarmBridge": (
        "app.services.mission_runtime.swarm_bridge", "DurableSwarmBridge"),
    "MissionRecoveryCoordinator": (
        "app.services.mission_runtime.recovery", "MissionRecoveryCoordinator"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(name)
    mod_name, attr = target
    import importlib
    mod = importlib.import_module(mod_name)
    val = getattr(mod, attr)
    globals()[name] = val
    return val


__all__ = list(_LAZY.keys())
