"""Pi 工具面进程级指标（ADR-0180 D5 / T9）。

四个回归门指标的单一事实源：
- ``invalid_tool_name``：面外裸名 / wrap-reject（resolve_pi_tool_call reject）；
- ``schema_validation_rejected``：pre-dispatch 校验闸拒绝；
- ``proxy_wrapped``：经 webgis_execute 代理的长尾调用（fallback 面观测）；
- ``direct_surface``：注册面/原生平名直调。

外加最近一次 per-turn 激活面投影快照（surface_bytes / budget / dropped /
dynamic_count）——由 ``pi_native_surface.compute_turn_active_tools`` 写入。

进程级单调计数 + 有界最近拒绝样本（诊断用）；无锁单写线程安全依赖
GIL 的 ``+=``/赋值原子性（与 tool_metrics.aggregator 同等强度）。
``PI_SURFACE_METRICS=0`` 整体关闭（读值恒零，绝不阻断任何路径）。
"""
from __future__ import annotations

import os
import time
from collections import deque
from typing import Any, Dict

_ENABLED = os.getenv("PI_SURFACE_METRICS", "1") != "0"

#: 最近拒绝样本上限（诊断窗口，防无界）。
_REJECT_SAMPLE_MAX = 32

_counters: Dict[str, int] = {
    "invalid_tool_name": 0,
    "schema_validation_rejected": 0,
    "proxy_wrapped": 0,
    "direct_surface": 0,
}
_reject_samples: deque = deque(maxlen=_REJECT_SAMPLE_MAX)
_last_surface: Dict[str, Any] = {}


def _enabled() -> bool:
    return _ENABLED


def record_invalid_tool_name(tool: str, *, reason: str = "") -> None:
    if not _enabled():
        return
    _counters["invalid_tool_name"] += 1
    _reject_samples.append({
        "kind": "invalid_tool_name",
        "tool": str(tool)[:96],
        "reason": str(reason)[:160],
        "ts": time.time(),
    })


def record_validation_reject(tool: str, issues: list) -> None:
    if not _enabled():
        return
    _counters["schema_validation_rejected"] += 1
    _reject_samples.append({
        "kind": "schema_validation_rejected",
        "tool": str(tool)[:96],
        "issues": [str(i.get("path", ""))[:64] for i in (issues or [])[:8]],
        "ts": time.time(),
    })


def record_surface_call(*, proxied: bool, tool: str = "") -> None:
    """每次真实 dispatch 分类后调用：proxy 长尾 vs 注册面直调。"""
    if not _enabled():
        return
    key = "proxy_wrapped" if proxied else "direct_surface"
    _counters[key] += 1


def record_surface_projection(
    *,
    surface_bytes: int = 0,
    budget: int = 0,
    dropped: int = 0,
    dynamic_count: int = 0,
) -> None:
    """per-turn 激活面投影快照（last-wins，供 /metrics/digest 观测）。

    键面固定 → 就地逐键覆写，不存在「清空后短暂为空」的快照窗口。
    """
    if not _enabled():
        return
    _last_surface.update({
        "surface_bytes": int(surface_bytes),
        "surface_budget": int(budget),
        "budget_dropped": int(dropped),
        "dynamic_count": int(dynamic_count),
        "ts": time.time(),
    })


def snapshot() -> Dict[str, Any]:
    """只读快照（/api/v1/metrics/digest 的 ``pi_surface`` 段）。"""
    out: Dict[str, Any] = {k: int(v) for k, v in _counters.items()}
    total = out["proxy_wrapped"] + out["direct_surface"]
    out["surface_calls_total"] = total
    out["proxy_fallback_rate"] = (
        round(out["proxy_wrapped"] / total, 4) if total else 0.0
    )
    out["last_surface_projection"] = dict(_last_surface)
    out["recent_rejects"] = list(_reject_samples)
    return out


def reset_for_tests() -> None:
    for k in _counters:
        _counters[k] = 0
    _reject_samples.clear()
    _last_surface.clear()
