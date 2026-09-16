"""SpatialWatch 求值引擎（纯函数 + 显式状态传递）。

纪律：
- 纯函数：``evaluate_watch(watch, state, event) -> WatchEvalResult``——所有
  状态显式进出（WatchState 由调用方持久化），无隐藏时钟/IO；求值时钟取
  ``event.occurred_at``（确定性重放：同一事件流 ⇒ 同一触发序列）。
- 谓词语义：已设置谓词 **全部满足** 才 fire；consecutive_n 基于其余谓词的
  连续命中计数；window 基于 kind/subject 匹配事件的时间窗计数（有界）。
- enter/exit 是**边沿触发**（状态迁移才 fire），状态 inside_aoi 持久化。
- cooldown：fire 意向成立但距上次 fire < cooldown_s → 不触发、不刷新
  last_fired_at；其余簿记照常推进。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional

from app.services.spatial_events.contracts import (
    SpatialEventEnvelope,
    SpatialWatch,
    WatchCondition,
    WatchState,
)

#: window 计数环上限（防状态无界膨胀）
_MAX_WINDOW_TIMES = 128


class WatchEvalResult:
    __slots__ = ("fired", "reason", "state", "detail")

    def __init__(
        self,
        *,
        fired: bool,
        reason: str,
        state: WatchState,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.fired = fired
        self.reason = reason
        self.state = state
        self.detail = detail or {}


def watch_matches_event(watch: SpatialWatch, event: SpatialEventEnvelope) -> bool:
    """watch ↔ 事件匹配（org/kind/subject/session/project）。租户红线在此。"""
    if watch.org_id != event.org_id:
        return False
    if event.kind.value not in watch.kinds:
        return False
    if watch.subject_key_prefix and not event.subject_key.startswith(
        watch.subject_key_prefix
    ):
        return False
    if watch.session_id and event.session_id != watch.session_id:
        return False
    if watch.project_id and event.project_id != watch.project_id:
        return False
    return True


def _resolve_path(payload: Dict[str, Any], path: str) -> Any:
    node: Any = payload
    for seg in path.split("."):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


def _point_inside(geom: Any, lon: float, lat: float) -> bool:
    try:
        from shapely.geometry import Point, shape

        return bool(shape(geom).contains(Point(lon, lat)))
    except Exception:  # noqa: BLE001 — malformed AOI ⇒ 不满足（fail-closed）
        return False


def _bbox_inside(bbox, lon: float, lat: float) -> bool:
    minx, miny, maxx, maxy = bbox
    return bool(minx <= lon <= maxx and miny <= lat <= maxy)


def _eval_metric(
    cond: WatchCondition, state: WatchState, payload: Dict[str, Any]
) -> tuple[bool, str, float, float]:
    """返回 (met, reason, new_last_value, hit_value)。"""
    raw = _resolve_path(payload, cond.metric_name or "")
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False, "metric_missing", state.last_metric_value or 0.0, 0.0
    baseline = state.last_metric_value
    op = cond.metric_op.value if cond.metric_op else ""
    threshold = float(cond.metric_value or 0.0)
    if op == "delta_pct_ge":
        if baseline is None:
            return False, "no_baseline", value, value
        if baseline == 0:
            met = value != 0
        else:
            delta_pct = abs(value - baseline) / abs(baseline) * 100.0
            met = delta_pct >= threshold
        return met, "metric_delta", value, value
    met = {
        "gt": lambda: value > threshold,
        "gte": lambda: value >= threshold,
        "lt": lambda: value < threshold,
        "lte": lambda: value <= threshold,
    }.get(op, lambda: False)()
    return met, "metric_threshold", value, value


def _eval_aoi(
    cond: WatchCondition,
    state: WatchState,
    payload: Dict[str, Any],
    resolve_aoi: Optional[Callable[[str], Any]],
) -> tuple[bool, str, Optional[bool]]:
    """返回 (met, reason, new_inside)。new_inside=None 表示本次无位置可判。"""
    pos = _resolve_path(payload, cond.position_path or "position")
    try:
        lon, lat = float(pos[0]), float(pos[1])  # type: ignore[index]
    except (TypeError, ValueError, IndexError):
        return False, "position_missing", state.inside_aoi
    if cond.aoi_bbox is not None:
        inside = _bbox_inside(cond.aoi_bbox, lon, lat)
    elif cond.aoi_ref:
        if resolve_aoi is None:
            return False, "aoi_ref_unresolvable", state.inside_aoi
        try:
            geom = resolve_aoi(cond.aoi_ref)
        except Exception:  # noqa: BLE001 — AOI ref 解析失败按不可判
            return False, "aoi_ref_unresolvable", state.inside_aoi
        inside = _point_inside(geom, lon, lat)
    else:
        return False, "aoi_unconfigured", state.inside_aoi
    mode = cond.aoi_mode
    if mode == "enter":
        met = inside and state.inside_aoi is not True
    else:  # exit
        met = (not inside) and state.inside_aoi is True
    return met, "aoi_edge", inside


def _eval_version(
    cond: WatchCondition, state: WatchState, payload: Dict[str, Any]
) -> tuple[bool, str, Optional[str]]:
    raw = _resolve_path(payload, cond.version_property or "")
    if raw is None:
        return False, "version_missing", state.last_version
    version = str(raw)
    if state.last_version is None:
        return False, "no_prior_version", version
    if version == state.last_version:
        return False, "version_unchanged", version
    return True, "version_changed", version


def evaluate_watch(
    watch: SpatialWatch,
    state: WatchState,
    event: SpatialEventEnvelope,
    *,
    resolve_aoi: Optional[Callable[[str], Any]] = None,
) -> WatchEvalResult:
    """求值一个事件对一个 watch 的影响（纯函数；状态显式回传）。"""
    cond = watch.condition
    new_state = state.model_copy(deep=True)
    now: datetime = event.occurred_at

    # window 簿记：kind/subject 匹配事件计入窗口（与其余谓词独立）
    window_met = True
    if cond.window_s is not None:
        times = [
            t for t in new_state.window_event_times
            if (now - t).total_seconds() <= cond.window_s
        ]
        times.append(now)
        new_state.window_event_times = times[-_MAX_WINDOW_TIMES:]
        window_met = len(new_state.window_event_times) >= int(
            cond.window_min_events or 1
        )

    failures: list[str] = []
    met = True

    if cond.metric_name or cond.metric_op:
        m, reason, new_last, _ = _eval_metric(cond, new_state, event.payload)
        new_state.last_metric_value = new_last
        met = met and m
        if not m:
            failures.append(reason)

    if cond.aoi_bbox is not None or cond.aoi_ref or cond.position_path:
        m, reason, inside = _eval_aoi(cond, new_state, event.payload, resolve_aoi)
        if inside is not None:
            new_state.inside_aoi = inside
        met = met and m
        if not m:
            failures.append(reason)

    if cond.version_property:
        m, reason, new_v = _eval_version(cond, new_state, event.payload)
        new_state.last_version = new_v
        met = met and m
        if not m:
            failures.append(reason)

    if cond.window_s is not None and not window_met:
        met = False
        failures.append("window_not_full")

    # consecutive_hits 总是维护（谓词命中 +1 / 未命中清零）；
    # consecutive_n 只是基于它的门槛
    if met:
        new_state.consecutive_hits += 1
    else:
        new_state.consecutive_hits = 0
    if cond.consecutive_n and new_state.consecutive_hits < cond.consecutive_n:
        met = False
        failures.append("consecutive_not_reached")

    new_state.last_event_id = event.event_id

    if met:
        if (
            state.last_fired_at is not None
            and (now - state.last_fired_at).total_seconds() < watch.cooldown_s
        ):
            return WatchEvalResult(
                fired=False, reason="cooldown", state=new_state
            )
        new_state.last_fired_at = now
        return WatchEvalResult(
            fired=True, reason="fired", state=new_state,
            detail={"subject_key": event.subject_key},
        )
    return WatchEvalResult(
        fired=False, reason=failures[0] if failures else "not_met",
        state=new_state,
    )
