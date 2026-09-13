"""Governor 配置：env 旋钮 + provisional 预算 manifest（ADR-0182 D5/D12）。

env 直读（``os.getenv``）与 ``tool_dispatch_service.py`` 的
``TOOL_WAVE_CONCURRENCY`` 同惯例 —— 不触 ``app/core/config.py``（并行线热区
最小化，D3）。manifest 对齐 ``observability/budgets.py`` 的封闭词表纪律：
key 声明即注册，未登记 key 不产生任何行为。

模式语义（D5）：
- ``enforce``（默认）：硬限触发真实 accept_with_limits/defer/degrade/reject；
- ``observe``：一切决策照常计算但只留痕，dispatch 恒放行（回滚 kill-switch）。
无论何种模式，governor 内部异常一律 fail-open（绝不阻断业务路径）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from app.services.governor.contract import (
    Dimension,
    ResourceBudget,
    Subsystem,
)

logger = logging.getLogger(__name__)

#: manifest 默认位置（仓库根相对；与 config/perf_budgets.json 同目录惯例）
GOVERNOR_MANIFEST_PATH = Path("config") / "governor_budgets.json"

_BACKPRESSURE_SUBSYSTEMS = (
    "raster", "browser", "export", "external", "llm",
)


class GovernorMode(str, Enum):
    ENFORCE = "enforce"
    OBSERVE = "observe"


def _env_int(name: str, default: int, *, floor: int = 0) -> int:
    try:
        return max(floor, int(os.getenv(name) or default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, *, floor: float = 0.0) -> float:
    try:
        return max(floor, float(os.getenv(name) or default))
    except (TypeError, ValueError):
        return default


class GovernorConfig:
    """进程级 governor 配置快照（线程安全只读；测试可重建）。"""

    def __init__(self) -> None:
        self.mode = GovernorMode(
            (os.getenv("GOVERNOR_MODE") or GovernorMode.ENFORCE.value).strip().lower()
        )
        # ---- 全局背压水位（R5；heavy/light 双档）--------------------------
        self.global_heavy_concurrency = _env_int("GOVERNOR_GLOBAL_HEAVY", 3, floor=1)
        self.global_raster_concurrency = _env_int("GOVERNOR_GLOBAL_RASTER", 2, floor=1)
        self.global_browser_concurrency = _env_int("GOVERNOR_GLOBAL_BROWSER", 2, floor=1)
        self.global_export_concurrency = _env_int("GOVERNOR_GLOBAL_EXPORT", 1, floor=1)
        # ---- 会话层（R4/R5/R6）------------------------------------------
        self.session_heavy_concurrency = _env_int("GOVERNOR_SESSION_HEAVY", 1, floor=1)
        self.session_concurrency = _env_int("GOVERNOR_SESSION_CONCURRENCY", 4, floor=1)
        self.queue_max_wait_s = _env_float("GOVERNOR_QUEUE_MAX_WAIT_S", 45.0)
        # ---- 公平（R6）--------------------------------------------------
        self.aging_threshold_s = _env_float("GOVERNOR_AGING_THRESHOLD_S", 10.0)
        self.small_bypass_max_wait_s = _env_float("GOVERNOR_SMALL_BYPASS_MAX_WAIT_S", 2.0)
        # ---- RetryBudget（R10）------------------------------------------
        self.retry_tokens_per_session = _env_int("GOVERNOR_RETRY_TOKENS_SESSION", 12, floor=0)
        self.retry_tokens_global = _env_int("GOVERNOR_RETRY_TOKENS_GLOBAL", 120, floor=0)
        # ---- 准入保守性（R3）--------------------------------------------
        #: 全局在飞 memory 占位上限（adjudged memory 总和；0 = 不限）
        self.global_memory_pressure_bytes = int(
            _env_float("GOVERNOR_GLOBAL_MEMORY_BYTES", 6 * 1024**3)
        )
        self.slo_breach_soft_limit = _env_int("GOVERNOR_SLO_BREACH_SOFT", 20, floor=1)
        #: storage pressure 高水位（0-1，artifact LRU 占比；来自只读视图）
        self.storage_pressure_high = _env_float("GOVERNOR_STORAGE_PRESSURE_HIGH", 0.9)

    def as_dict(self) -> Dict:
        return {
            "mode": self.mode.value,
            "global_heavy_concurrency": self.global_heavy_concurrency,
            "global_raster_concurrency": self.global_raster_concurrency,
            "global_browser_concurrency": self.global_browser_concurrency,
            "global_export_concurrency": self.global_export_concurrency,
            "session_heavy_concurrency": self.session_heavy_concurrency,
            "session_concurrency": self.session_concurrency,
            "queue_max_wait_s": self.queue_max_wait_s,
            "aging_threshold_s": self.aging_threshold_s,
            "retry_tokens_per_session": self.retry_tokens_per_session,
            "retry_tokens_global": self.retry_tokens_global,
            "global_memory_pressure_bytes": self.global_memory_pressure_bytes,
        }


def load_manifest(path: Optional[Path] = None) -> Dict[str, ResourceBudget]:
    """读取 provisional 预算 manifest（JSON → 按 scope 的 ResourceBudget 表）。

    格式（与 config/perf_budgets.json 同风格）::

        {"version": "rg.v1", "budgets": [
            {"scope": "session", "limits": {"memory_bytes": 2.0e9,
             "wall_time_s": 3600, "context_tokens": 400000},
             "max_heavy_concurrent": 2, "provisional": true,
             "source": "calibration:2026-09-14"}
        ]}

    未登记 scope 使用内置缺省（保守 provisional 值）；未知 dimension key 报
    ValueError（封闭词表，杜绝拼写漂移）。
    """
    manifest_path = Path(path) if path else _repo_root() / GOVERNOR_MANIFEST_PATH
    with open(manifest_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict) or not isinstance(raw.get("budgets"), list):
        raise ValueError("governor budget manifest must be {version, budgets: [...]}")
    budgets: Dict[str, ResourceBudget] = {}
    for entry in raw["budgets"]:
        scope = str(entry.get("scope", "")).strip()
        if not scope:
            raise ValueError(f"budget entry missing scope: {entry!r}")
        limits: Dict[Dimension, float] = {}
        for key, val in (entry.get("limits") or {}).items():
            try:
                dim = Dimension(key)
            except ValueError as exc:
                raise ValueError(f"unknown dimension key in manifest: {key!r}") from exc
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(f"invalid limit value for {key}: {val!r}")
            limits[dim] = float(val)
        budgets[scope] = ResourceBudget(
            scope=scope,
            scope_id=str(entry.get("scope_id", scope)),
            limits=limits,
            max_heavy_concurrent=entry.get("max_heavy_concurrent"),
            max_browser_renders=entry.get("max_browser_renders"),
            max_exports=entry.get("max_exports"),
            max_external_calls=entry.get("max_external_calls"),
            max_retry_tokens=entry.get("max_retry_tokens"),
            provisional=bool(entry.get("provisional", True)),
            source=str(entry.get("source", "manifest")),
        )
    return budgets


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


_default_config: Optional[GovernorConfig] = None
_default_lock = threading.Lock()


def get_governor_config() -> GovernorConfig:
    global _default_config
    with _default_lock:
        if _default_config is None:
            _default_config = GovernorConfig()
        return _default_config


def reset_governor_config_for_tests(config: Optional[GovernorConfig] = None) -> GovernorConfig:
    global _default_config
    with _default_lock:
        _default_config = config or GovernorConfig()
        return _default_config


def backpressure_subsystems() -> tuple:
    """背压子系统的封闭词表（subsystem 通道名）。"""
    return _BACKPRESSURE_SUBSYSTEMS


__all__ = [
    "GOVERNOR_MANIFEST_PATH",
    "GovernorMode",
    "GovernorConfig",
    "load_manifest",
    "get_governor_config",
    "reset_governor_config_for_tests",
    "backpressure_subsystems",
    "Subsystem",
]
