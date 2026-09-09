"""Rendered-State Observation Ladder（ADR-0119 决策 D9）。

V5 基线（baseline G7）：observation 校验散落判定（mounted/visible/
source_status/render_complete/feature_count/chart rendered+data_points），
但没有**统一状态阶梯**与诚实三态词表；workflow runtime 消费
blocked/degraded/partial 时无标准化出口。

V6 状态阶梯（封闭词表，从弱到强）::

    unknown → pending → mounted → loaded → rendered → data_present
            → semantically_correct

- ``unknown``：无遥测（旧客户端/前端离线）—— 诚实缺席，不假通过；
- ``pending``：观察在途 / revision 未盖章；
- ``mounted``：图层挂载且可见；
- ``loaded``：数据源收敛（source_converged 且非 error）；
- ``rendered``：render_complete=true；
- ``data_present``：feature_count>0（或 chart rendered 且 data_points>0
  —— viewport-scoped 计数为 0 不谎报 data_present）；
- ``semantically_correct``：requested intent ↔ actual rendered 逐层核对
  通过（由调用方传入 intent_verified —— finalizer 的结论，本模块不重复
  实现核对）。

聚合与消费：
- ``aggregate_observation_state``：多图层取最弱态（最弱链法定真实）；
- ``to_workflow_health``：workflow runtime 的统一消费词汇
  （ok / degraded / blocked / partial）—— 只读投影，非第二状态源。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

#: 阶梯（弱 → 强，封闭词表）。
LADDER: tuple = (
    "unknown",
    "pending",
    "mounted",
    "loaded",
    "rendered",
    "data_present",
    "semantically_correct",
)
_LADDER_INDEX = {s: i for i, s in enumerate(LADDER)}

#: workflow health 词汇（WorkflowInstance runtime projection 消费）。
HEALTH_OK = "ok"
HEALTH_DEGRADED = "degraded"
HEALTH_PARTIAL = "partial"
HEALTH_BLOCKED = "blocked"


def _rank(state: str) -> int:
    return _LADDER_INDEX.get(state, 0)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def layer_observation_state(
    entry: Optional[Dict[str, Any]], *, intent_verified: bool = False,
) -> str:
    """单图层观察条目 → 阶梯态（纯函数；缺字段按弱态，绝不猜强）。"""
    if not isinstance(entry, dict) or not entry:
        return "unknown"
    if entry.get("mounted") is not True:
        return "pending"
    state = "mounted"
    if entry.get("source_status") == "error":
        return "pending"          # 源错误：停留在 pending（诚实阻塞面）
    if entry.get("source_converged") is True:
        state = "loaded"          # 缺席 = 未证 → 停留 mounted（不猜强）
    if entry.get("render_complete") is True:
        state = "rendered"
        fc = entry.get("feature_count")
        if _is_num(fc) and int(fc) > 0:
            state = "data_present"
    # intent 核对只升级 data_present（rendered=画了但数据在场未证 ——
    # 意图核对不能替代数据在场的证据）
    if intent_verified and state == "data_present":
        state = "semantically_correct"
    return state


def chart_observation_state(entry: Optional[Dict[str, Any]]) -> str:
    """chart 观察条目 → 阶梯态（rendered+data_points ≥ data_present）。"""
    if not isinstance(entry, dict) or not entry:
        return "unknown"
    if entry.get("rendered") is not True:
        return "pending"
    pts = entry.get("data_points")
    if _is_num(pts) and int(pts) > 0:
        return "data_present"
    return "rendered"


def aggregate_observation_state(states: Iterable[str]) -> str:
    """多目标聚合 = 最弱链法（任一 unknown → 整体 unknown 诚实缺席）。"""
    vals = [s if s in _LADDER_INDEX else "unknown" for s in states if s]
    if not vals:
        return "unknown"
    return min(vals, key=_rank)


def build_observation_summary(
    observation: Optional[Dict[str, Any]],
    *,
    intent_verified: bool = False,
) -> Dict[str, Any]:
    """observation payload → 有界状态摘要（进 map_product / 披露面）。

    输出键：``layers``（逐层态）、``charts``（逐 chart 态）、
    ``aggregate``（最弱态）、``health``（workflow 词汇）、
    ``observed_revision``。全部可由权威 observation 重建 —— rebuildable
    projection，非第二事实源。"""
    if not isinstance(observation, dict):
        return {"aggregate": "unknown", "health": HEALTH_BLOCKED,
                "layers": {}, "charts": {}}
    layers: Dict[str, str] = {}
    for lid, entry in list((observation.get("layers") or {}).items())[:32]:
        layers[str(lid)[:64]] = layer_observation_state(
            entry if isinstance(entry, dict) else None,
            intent_verified=intent_verified)
    charts: List[str] = [
        chart_observation_state(c if isinstance(c, dict) else None)
        for c in (observation.get("charts") or [])[:16]
    ]
    aggregate = aggregate_observation_state(
        list(layers.values()) + charts) if (layers or charts) else "unknown"
    return {
        "layers": layers,
        "charts": charts,
        "aggregate": aggregate,
        "health": to_workflow_health(aggregate),
        "observed_revision": observation.get("revision"),
    }


def to_workflow_health(state: str) -> str:
    """阶梯态 → workflow 统一消费词汇（blocked/degraded/partial/ok）。

    - unknown → blocked（无证据不得放行 —— finalizer 纪律）；
    - pending/mounted/loaded → degraded（未到位即降级；源错误停在
      pending → degraded —— 硬错误的 error 级披露由 finalizer findings
      承担，本词汇只表达状态强度）；
    - rendered → partial（画出来了但数据在场未证）；
    - data_present / semantically_correct → ok。
    """
    s = (state or "").lower()
    if s == "unknown":
        return HEALTH_BLOCKED
    if s in ("pending", "mounted", "loaded"):
        return HEALTH_DEGRADED
    if s == "rendered":
        return HEALTH_PARTIAL
    if s in ("data_present", "semantically_correct"):
        return HEALTH_OK
    return HEALTH_BLOCKED


__all__ = [
    "LADDER",
    "HEALTH_OK",
    "HEALTH_DEGRADED",
    "HEALTH_PARTIAL",
    "HEALTH_BLOCKED",
    "layer_observation_state",
    "chart_observation_state",
    "aggregate_observation_state",
    "build_observation_summary",
    "to_workflow_health",
]
