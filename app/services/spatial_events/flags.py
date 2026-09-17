"""Spatial Events feature flags（生产默认全关——Oracle-6：feature-off 行为零变化）。

分层开关（DECISIONS D6）：
- ``GIS_SPATIAL_EVENT_RUNTIME``：总开关（ingest/ledger/worker/adapters）。默认 **0**。
- ``GIS_SPATIAL_EVENT_MISSION_BRIDGE``：watch fire 触发 mission 变更。默认 **0**。
- ``GIS_SPATIAL_EVENT_INVALIDATION``：事件驱动受影响子图/evidence 失效。默认 **0**。
- ``GIS_SPATIAL_EVENT_GOVERNOR_GATE``：trigger 副作用过 governor 背压门。默认 **0**。
旋钮：
- ``GIS_SPATIAL_EVENT_WORKER_INTERVAL_S``（默认 2.0；<=0 关轮询）
- ``GIS_SPATIAL_EVENT_BATCH``（默认 32）
- ``GIS_SPATIAL_EVENT_WEBHOOK_SECRET``（默认空 = webhook 路由禁用）
"""
from __future__ import annotations

import os

_FALSE = ("0", "false", "False", "")


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default) not in _FALSE


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def runtime_enabled() -> bool:
    return _env_flag("GIS_SPATIAL_EVENT_RUNTIME", "0")


def mission_bridge_enabled() -> bool:
    return (
        runtime_enabled()
        and _env_flag("GIS_SPATIAL_EVENT_MISSION_BRIDGE", "0")
    )


def invalidation_enabled() -> bool:
    return (
        runtime_enabled()
        and _env_flag("GIS_SPATIAL_EVENT_INVALIDATION", "0")
    )


def governor_gate_enabled() -> bool:
    return _env_flag("GIS_SPATIAL_EVENT_GOVERNOR_GATE", "0")


def worker_interval_s() -> float:
    return _env_float("GIS_SPATIAL_EVENT_WORKER_INTERVAL_S", 2.0)


def batch_size() -> int:
    return max(1, min(_env_int("GIS_SPATIAL_EVENT_BATCH", 32), 256))


def max_attempts() -> int:
    return max(1, _env_int("GIS_SPATIAL_EVENT_MAX_ATTEMPTS", 5))


def webhook_secret() -> str:
    """部署级 webhook 密钥（兼容保留；仅对 default org 生效，见下）。"""
    return os.getenv("GIS_SPATIAL_EVENT_WEBHOOK_SECRET", "")


def webhook_default_org() -> str:
    """部署级密钥唯一允许的 org（空 = 部署级密钥不授权任何租户）。"""
    return os.getenv("GIS_SPATIAL_EVENT_WEBHOOK_DEFAULT_ORG", "")


def webhook_org_secret(org_id: str) -> str:
    """per-org webhook 密钥（租户红线：单一部署级密钥不得跨租户写入）。

    env 名：``GIS_SPATIAL_EVENT_WEBHOOK_SECRET__ORG_<净化大写 org>``
    （非字母数字映射为下划线）。空串 = 该 org 未启用 webhook。
    """
    safe = "".join(
        ch if ch.isalnum() else "_" for ch in str(org_id or "").upper()
    )[:64]
    return os.getenv(f"GIS_SPATIAL_EVENT_WEBHOOK_SECRET__ORG_{safe}", "")


def stale_claim_s() -> float:
    return _env_float("GIS_SPATIAL_EVENT_STALE_CLAIM_S", 120.0)
