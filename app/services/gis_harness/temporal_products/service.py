"""temporal_products 会话服务（GOAL 03 M2/M3/M5）。

bind/control/compare/unbind —— 全部经 ``apply_gis_mutation``（origin=
agent；用户面走 REST SetTemporalViewBody origin=user CAS），MapSpec 是
唯一 desired state（temporal 分支）；数据真相只读 session ref。

user-wins（D5）：用户手动 seek/pause → user_pinned=true；此后 agent 的
cursor 类控制被拒绝（回执 correction_hint），直到用户 unpin 或重新 bind。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.temporal_products.frames import (
    buckets_to_contract_frames,
    detect_time_binding,
)
from app.services.gis_harness.temporal_products.time_filter import (
    compile_where_ast,
    equal_interval_breaks,
    time_filter_fingerprint,
    union_window,
)

logger = logging.getLogger("gis_harness.temporal_products")

#: 对照分级默认段数（等距；与专题图常用 5 段分级一致）。
DEFAULT_COMPARE_K = 5


def _extract_fc(data: Any) -> Optional[dict]:
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict) and nested.get("type") == "FeatureCollection":
            return nested
        if data.get("type") == "FeatureCollection":
            return data
    return None


async def _resolve_ref_fc(session_id: str, ref_id: str) -> Tuple[str, Dict, Optional[Dict]]:
    """ref → (resolved, descriptor, fc)；鉴权/缺失语义与 REST 路由一致。"""
    from app.services.session_data import session_data_manager

    resolved = await session_data_manager.resolve_alias(session_id, ref_id)
    descriptor = await session_data_manager.get_ref_descriptor(session_id, resolved)
    res = await session_data_manager.get_ref_data_shared(session_id, resolved)
    if not res.success:
        return resolved, descriptor or {}, None
    return resolved, descriptor or {}, _extract_fc(res.data)


async def _committed_temporal(session_id: str) -> Optional[Dict[str, Any]]:
    from app.services.mapspec.lifecycle_engine import mapspec_lifecycle_engine

    spec = await mapspec_lifecycle_engine.store.get_mapspec(session_id)
    if not isinstance(spec, dict):
        return None
    temporal = spec.get("temporal")
    return temporal if isinstance(temporal, dict) and temporal else None


def _frame_by_id(temporal: Dict[str, Any], frame_id: str) -> Optional[Dict[str, Any]]:
    for f in temporal.get("frames", []):
        if isinstance(f, dict) and f.get("id") == frame_id:
            return f
    return None


def _refusal(code: str, message: str, **extra) -> Dict[str, Any]:
    """typed fallback —— 工具面统一拒绝结构（不写 MapSpec）。"""
    out = {"status": "refused", "code": code, "message": message}
    out.update(extra)
    return out


async def _apply(
    session_id: str,
    intent,
    *,
    actor: str = "temporal_products",
    expected_revision: Optional[int] = None,
    reason: Optional[str] = None,
):
    from app.services.gis_world_state import apply_gis_mutation

    return await apply_gis_mutation(
        session_id, intent, origin="agent", actor=actor,
        expected_revision=expected_revision, reason=reason,
    )


async def bind_temporal_view(
    session_id: str,
    ref_id: str,
    *,
    layer_id: Optional[str] = None,
    time_field: Optional[str] = None,
    granularity: Optional[str] = None,
    timezone: Optional[str] = None,
    semantics: str = "event",
    expected_revision: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """ref → 探测 → 契约 → MapSpec.temporal（bind）。

    单期数据 / 无时间字段 / 表示不统一 → typed refusal，绝不写入半成品
    契约（前端因此不会出现可播放的时间轴 —— 「无数据不伪造图层」）。
    """
    resolved, descriptor, fc = await _resolve_ref_fc(session_id, ref_id)
    if fc is None:
        return _refusal(
            "ref_unavailable",
            f"ref {ref_id!r} 不可读或不是 FeatureCollection（resolved={resolved}）",
        )
    revision = descriptor.get("content_revision")

    tz_name = timezone or "UTC"
    probe = detect_time_binding(
        fc, time_field=time_field, granularity=granularity,
        tz_name=tz_name, semantics=semantics,
    )
    if not probe.ok:
        return _refusal(probe.code, probe.message, time_field=probe.time_field)

    if "single_period" in probe.quality:
        # E2E-3：单期数据必须诚实拒绝动态绑定。
        return _refusal(
            "single_period_unsupported",
            f"数据仅覆盖单一时间期（{probe.buckets[0].label}），无法做真实的时序"
            "播放或两期对比；如需静态展示可直接使用当前图层，但不会伪造动画。",
            time_field=probe.time_field,
            observed_label=probe.buckets[0].label,
        )

    # 既有绑定（重 bind 默认拒绝 —— 数据真相变化应显式 force 重绑）。
    existing = await _committed_temporal(session_id)
    if existing is not None and not force:
        return _refusal(
            "already_bound",
            "temporal 视图已绑定；重新绑定请带 force=true（会重建帧与游标）。",
            current_source_ref=existing.get("source_ref"),
        )

    frames = buckets_to_contract_frames(probe.buckets)
    # 缺省 layer_id 解析：committed spec 中引用该 ref 的图层 id（前端
    # layer.filter 匹配与 atlas 页计数都按 spec 图层 id 绑定）；无匹配
    # 图层时退回 ref id（统计-only 绑定，渲染面诚实为空）。
    effective_layer_id = layer_id
    if not effective_layer_id:
        from app.services.mapspec.lifecycle_engine import mapspec_lifecycle_engine
        spec = await mapspec_lifecycle_engine.store.get_mapspec(session_id)
        if isinstance(spec, dict):
            sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
            for lyr in spec.get("layers") or []:
                if not isinstance(lyr, dict):
                    continue
                sid = lyr.get("source")
                src = sources.get(sid) if isinstance(sid, str) else None
                src_ref = None
                if isinstance(src, dict):
                    src_ref = src.get("ref_id") or src.get("ref")
                if src_ref == resolved or sid == resolved:
                    effective_layer_id = str(lyr.get("id") or "")
                    break
    config: Dict[str, Any] = {
        "schema_version": "temporal_view.v1",
        "enabled": True,
        "layers": [{"layer_id": effective_layer_id or resolved, "source_ref": resolved}],
        "time_field": probe.time_field,
        "time_semantics": probe.semantics,
        "time_representation": probe.representation,
        "time_format_hint": probe.format_hint or None,
        "timezone": tz_name,
        "granularity": probe.granularity,
        "extent": (frames[0]["start"], frames[-1]["end"]),
        "frames": frames,
        "cursor_frame": frames[-1]["id"],
        "playback": {"playing": False, "rate": 1.0},
        "missing_policy": "strict",
        "quality": probe.quality,
        "source_ref": resolved,
        "content_revision": revision,
        "user_pinned": False,
        "render_path": "client_filter",
        "disclosures": [
            "mvt_no_time_index_client_filter",
            *([] if (effective_layer_id and effective_layer_id != resolved)
               else ["layer_binding_ref_alias_tail"]),
        ],
    }

    from app.services.mapspec.intents import SetTemporalViewIntent

    result = await _apply(
        session_id, SetTemporalViewIntent(config=config),
        expected_revision=expected_revision,
        reason=f"temporal bind: {resolved} field={probe.time_field} "
               f"representation={probe.representation} frames={len(frames)}",
    )
    if getattr(result, "is_error", False):
        return {
            "status": "error",
            "code": "mutation_rejected",
            "message": getattr(result, "error_msg", "") or "MapSpec 变更被拒绝",
            "correction_hint": getattr(result, "correction_hint", ""),
        }

    out = dict(config)
    out["status"] = "bound"
    out["frame_count"] = len(frames)
    out["feature_total"] = probe.parsed_values
    out["detection"] = {
        "confidence": probe.confidence,
        "parsed_values": probe.parsed_values,
        "total_values": probe.total_values,
    }
    return out


#: 光标类动作（user_pinned 时 agent 被拒）。
_CURSOR_ACTIONS = {"seek", "step", "play", "pause", "rate"}


async def control_temporal_view(
    session_id: str,
    action: str,
    *,
    frame_id: Optional[str] = None,
    steps: Optional[int] = None,
    rate: Optional[float] = None,
    unpin: bool = False,
    origin: str = "agent",
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """seek/step/play/pause/rate/unpin —— 增量控制（patch 通道）。

    user-wins：origin=agent 且 user_pinned 且动作改光标 → 拒绝 + 提示。
    step 支持 ±N（越界钳到首/末帧并披露）。
    """
    temporal = await _committed_temporal(session_id)
    if temporal is None:
        return _refusal("not_bound", "会话未绑定 temporal 视图（先 temporal_view_bind）")
    if not temporal.get("enabled"):
        return _refusal("disabled", "temporal 视图已停用")

    if origin == "agent" and action == "unpin":
        # P1 修正：解pin是用户的决定权 —— agent 不得代用户解除固定
        # （否则 seek 守卫可被 agent unpin→seek 序列绕过）。
        return _refusal(
            "unpin_user_only",
            "解除时间游标固定是用户操作（user-wins）；Agent 不可代为 unpin。",
        )
    if origin == "agent" and temporal.get("user_pinned") and action in _CURSOR_ACTIONS and not unpin:
        return _refusal(
            "user_pinned",
            "时间游标由用户手动设定（user_pinned），Agent 不覆盖用户显式操作；"
            "用户解除固定（unpin）前 seek/play/pause/rate 均被拒绝。",
            correction_hint="请提示用户在时间轴上解除固定，或仅做非光标操作。",
        )

    frames = temporal.get("frames", [])
    frame_ids = [f["id"] for f in frames]
    cursor_idx = frame_ids.index(temporal["cursor_frame"]) \
        if temporal.get("cursor_frame") in frame_ids else 0

    patch: Dict[str, Any] = {}
    disclosures: List[str] = []

    if action == "seek":
        if frame_id not in frame_ids:
            return _refusal(
                "unknown_frame",
                f"帧 {frame_id!r} 不在真实帧序列 {frame_ids} 中（不虚构期数）",
            )
        patch["cursor_frame"] = frame_id
        if temporal.get("playback", {}).get("playing"):
            patch["playback"] = {"playing": False}  # 手动定位即暂停（防失控播放）
    elif action == "step":
        n = steps if steps is not None else 1
        target = max(0, min(len(frames) - 1, cursor_idx + n))
        if target != cursor_idx + n:
            disclosures.append(f"step_clamped_to_{'first' if target == 0 else 'last'}_frame")
        patch["cursor_frame"] = frame_ids[target]
        if temporal.get("playback", {}).get("playing"):
            patch["playback"] = {"playing": False}
    elif action == "play":
        patch["playback"] = {"playing": True}
    elif action == "pause":
        patch["playback"] = {"playing": False}
    elif action == "rate":
        if rate is None or not (0.25 <= rate <= 8.0):
            return _refusal("invalid_rate", "rate 必须在 [0.25, 8.0]")
        patch["playback"] = {"rate": float(rate)}
    elif action == "unpin":
        patch["user_pinned"] = False
    else:
        return _refusal("unknown_action", f"未知控制动作 {action!r}")

    if origin == "user" and action in _CURSOR_ACTIONS:
        patch["user_pinned"] = True  # 用户手动操作即固定（D5）

    if disclosures:
        patch["disclosures"] = list(temporal.get("disclosures", []))[:7] + disclosures[:1]

    from app.services.mapspec.intents import SetTemporalViewIntent

    result = await _apply(
        session_id, SetTemporalViewIntent(patch=patch),
        expected_revision=expected_revision,
        reason=f"temporal control: {action} frame={patch.get('cursor_frame')} origin={origin}",
    )
    if getattr(result, "is_error", False):
        return {
            "status": "error", "code": "mutation_rejected",
            "message": getattr(result, "error_msg", "") or "MapSpec 变更被拒绝",
            "correction_hint": getattr(result, "correction_hint", ""),
        }

    return {
        "status": "applied",
        "action": action,
        "cursor_frame": patch.get("cursor_frame", temporal.get("cursor_frame")),
        "playback": {**temporal.get("playback", {}), **patch.get("playback", {})},
        "user_pinned": patch.get("user_pinned", temporal.get("user_pinned", False)),
        "disclosures": disclosures,
    }


async def compare_temporal_view(
    session_id: str,
    frame_id_a: Optional[str] = None,
    frame_id_b: Optional[str] = None,
    *,
    value_field: Optional[str] = None,
    k: int = DEFAULT_COMPARE_K,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """两期对照（M5）：cursor/A vs B + 跨两帧 shared 分级。

    分级 = union 窗（两帧并集）上的等距分级（min/max 来自服务端聚合真值，
    与地图/图表同 where 通道）；退化域（min==max 或无值）→ typed 披露，
    仍允许对照但不提供固定分级。
    """
    temporal = await _committed_temporal(session_id)
    if temporal is None or not temporal.get("enabled"):
        return _refusal("not_bound", "会话未绑定启用的 temporal 视图")

    # P1 修正：compare 移动 cursor（patch cursor_frame=fid_a）—— user_pinned
    # 下 agent compare 同样被拒（user-wins 覆盖一切光标移动，无绕过面）。
    if temporal.get("user_pinned"):
        return _refusal(
            "user_pinned",
            "时间游标由用户手动设定（user_pinned），两期对照会移动游标，"
            "Agent 不覆盖；请提示用户解除固定后再对照。",
        )

    frames = temporal.get("frames", [])
    by_id = {f["id"]: f for f in frames}
    fid_a = frame_id_a or temporal.get("cursor_frame")
    fid_b = frame_id_b
    if fid_a not in by_id or fid_b not in by_id:
        return _refusal(
            "unknown_frame",
            f"对照帧必须来自真实帧序列；got a={fid_a!r} b={fid_b!r}",
            available=list(by_id),
        )
    if fid_a == fid_b:
        return _refusal("same_frame", "两期对照的两帧必须不同")

    frame_a, frame_b = by_id[fid_a], by_id[fid_b]
    tf = temporal["time_field"]
    rep = temporal["time_representation"]
    gran = temporal["granularity"]
    tz_name = temporal.get("timezone") or "UTC"
    fmt_hint = temporal.get("time_format_hint") or ""

    classification: Optional[Dict[str, Any]] = None
    classification_note = ""
    if value_field:
        # union 窗聚合（服务端真值；复用 aggregate_features 通道语义）。
        vmin, vmax = await _aggregate_range(
            session_id, temporal["source_ref"], value_field,
            union_window(frame_a, frame_b), tf, rep, gran, tz_name, fmt_hint,
        )
        breaks = None if (vmin is None or vmax is None) else equal_interval_breaks(vmin, vmax, k)
        if breaks is None:
            classification_note = (
                "union 窗上取值域退化（min==max 或无有效值），无法固定分级；"
                "对照仍可用数据本色显示，但颜色差异不可作定量结论。"
            )
        else:
            classification = {
                "breaks": breaks,
                "scope": "shared",
                "method": f"equal_interval_k{k}_union_window",
            }

    patch: Dict[str, Any] = {
        "cursor_frame": fid_a,
        "compare": {"enabled": True, "frame_id_b": fid_b,
                    "classification": classification},
        "playback": {"playing": False},
    }
    if classification_note:
        patch["disclosures"] = list(
            temporal.get("disclosures", []))[:6] + ["compare_degenerate_range"]

    from app.services.mapspec.intents import SetTemporalViewIntent

    result = await _apply(
        session_id, SetTemporalViewIntent(patch=patch),
        expected_revision=expected_revision,
        reason=f"temporal compare: {fid_a} vs {fid_b} k={k}",
    )
    if getattr(result, "is_error", False):
        return {
            "status": "error", "code": "mutation_rejected",
            "message": getattr(result, "error_msg", "") or "MapSpec 变更被拒绝",
        }

    out = {
        "status": "compared",
        "frame_a": frame_a,
        "frame_b": frame_b,
        "classification": classification,
        "where_ast_a": compile_where_ast(frame_a, tf, rep, gran, tz_name, fmt_hint),
        "where_ast_b": compile_where_ast(frame_b, tf, rep, gran, tz_name, fmt_hint),
        "fingerprint_a": time_filter_fingerprint(
            temporal["source_ref"], temporal.get("content_revision"), tf, fid_a, rep),
        "fingerprint_b": time_filter_fingerprint(
            temporal["source_ref"], temporal.get("content_revision"), tf, fid_b, rep),
    }
    if classification_note:
        out["disclosure"] = classification_note
    return out


async def _aggregate_range(
    session_id: str,
    ref_id: str,
    value_field: str,
    window: Dict[str, Any],
    time_field: str,
    representation: str,
    granularity: str,
    tz_name: str = "UTC",
    format_hint: str = "",
) -> Tuple[Optional[float], Optional[float]]:
    """union 窗上 value_field 的 (min, max)（bind/compare 冷路径）。

    与 aggregate 通道同闸（500k 扫描上限）的线性扫描 —— 取值域探测需要
    同时拿 min/max，分组聚合接口反而不经济；比较值与 where AST 编译器
    完全同源（normalize 后的字面值）。
    """
    from app.services.gis_harness.temporal_products.time_filter import (
        normalize_boundary_value,
    )

    _, _, fc = await _resolve_ref_fc(session_id, ref_id)
    if fc is None:
        return None, None
    lo = normalize_boundary_value(window["start"], representation, granularity, tz_name, format_hint)
    hi = normalize_boundary_value(window["end"], representation, granularity, tz_name, format_hint)
    if isinstance(lo, str) or isinstance(hi, str):
        lo_s, hi_s = str(lo), str(hi)
        in_window = lambda t: isinstance(t, str) and lo_s <= t < hi_s  # noqa: E731
    else:
        lo_n, hi_n = float(lo), float(hi)
        in_window = lambda t: isinstance(t, (int, float)) and not isinstance(t, bool) and lo_n <= t < hi_n  # noqa: E731

    scanned = 0
    values: List[float] = []
    for f in fc.get("features", []):
        scanned += 1
        if scanned > 500_000:  # AGG_SCAN_LIMIT 同闸 —— 有界拒绝
            return None, None
        props = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(props, dict):
            continue
        t = props.get(time_field)
        v = props.get(value_field)
        if in_window(t) and isinstance(v, (int, float)) and not isinstance(v, bool):
            values.append(float(v))
    if not values:
        return None, None
    return min(values), max(values)


async def unbind_temporal_view(
    session_id: str,
    *,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """解绑（remove temporal 分支；前端时间轴随 spec 消失）。"""
    from app.services.mapspec.intents import SetTemporalViewIntent

    result = await _apply(
        session_id, SetTemporalViewIntent(remove=True),
        expected_revision=expected_revision, reason="temporal unbind",
    )
    if getattr(result, "is_error", False):
        return {
            "status": "error", "code": "mutation_rejected",
            "message": getattr(result, "error_msg", "") or "MapSpec 变更被拒绝",
        }
    return {"status": "unbound"}


async def get_temporal_view(session_id: str) -> Dict[str, Any]:
    """读面：committed temporal + 当前帧的过滤产物（前端/导出/统计共用）。"""
    temporal = await _committed_temporal(session_id)
    if temporal is None:
        return {"status": "not_bound"}
    tf = temporal.get("time_field", "")
    rep = temporal.get("time_representation", "")
    gran = temporal.get("granularity", "")
    tz_name = temporal.get("timezone") or "UTC"
    fmt_hint = temporal.get("time_format_hint") or ""
    cursor = temporal.get("cursor_frame")
    frame = _frame_by_id(temporal, cursor) if cursor else None
    out: Dict[str, Any] = {"status": "bound", "temporal": temporal}
    if frame is not None:
        out["where_ast"] = compile_where_ast(frame, tf, rep, gran, tz_name, fmt_hint)
        out["fingerprint"] = time_filter_fingerprint(
            temporal.get("source_ref", ""), temporal.get("content_revision"),
            tf, frame["id"], rep)
    compare = temporal.get("compare")
    if compare and compare.get("enabled"):
        fb = _frame_by_id(temporal, compare.get("frame_id_b", ""))
        if fb is not None:
            out["where_ast_compare"] = compile_where_ast(fb, tf, rep, gran, tz_name, fmt_hint)
            out["fingerprint_compare"] = time_filter_fingerprint(
                temporal.get("source_ref", ""), temporal.get("content_revision"),
                tf, fb["id"], rep)
    return out
