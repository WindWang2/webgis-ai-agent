"""WP02/WP03 — 有版本的媒体计划 + 观测桶到显示时间的确定映射。

三种身份绝不混淆：
- ``observation``：temporal 契约里的真实观测桶（含缺期披露，不新增观测）；
- ``digital_frame``：按该观测窗口真实渲染、可独立解码的 PNG；
- ``encoded_frame``：媒体容器的一帧（可由一个停留拆成多张相同画面，
  或被编码器把相邻相同画面合并；manifest 始终能映射回观测与时长）。

时间科学：
- 直接复用契约帧的 ``[start, end)`` 与 ``timezone``，不重解析原始字段；
- 数据时间单位（年/月/日/epoch）与展示毫秒、编码 fps 互不等同；
- 一切显示时长量化为整数毫秒，累计误差可计算；
- 缺期只可 skip / 明确空态 / 明确沿用，绝不插值新数值或几何。
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import (
    MAX_DWELL_MS, MAX_ENCODE_RAW_BYTES, MAX_ENCODED_FRAMES, MAX_FPS,
    MAX_OBSERVATIONS, MAX_PROPORTIONAL_GAP_MS, MAX_SEGMENTS,
    MAX_TEMP_BYTES, MAX_TOTAL_DISPLAY_MS, MIN_DWELL_MS, SCHEMA_VERSION,
    TemporalMediaError,
)
from .freeze import canonical_json, sha256_text

_MILLIS = 1000


def parse_instant(value: str) -> datetime:
    """契约 ISO instant（Z 或偏移）→ aware datetime；与 time_filter 同口径。"""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _contract_tz(temporal: Dict[str, Any]) -> ZoneInfo:
    name = str(temporal.get("timezone") or "UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TemporalMediaError(
            "INVALID_TEMPORAL_CONTRACT", f"invalid contract timezone {name!r}",
            status_code=422) from exc


def _replace_year(dt: datetime, delta: int) -> datetime:
    try:
        return dt.replace(year=dt.year + delta)
    except ValueError:  # 2/29
        return dt.replace(year=dt.year + delta, day=28)


def _add_months(dt: datetime, months: int) -> datetime:
    total = dt.year * 12 + (dt.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    import calendar
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def next_period(dt: datetime, granularity: str) -> datetime:
    """按契约粒度推下一期起点（缺期推算只用于**披露**，不生成新观测）。"""
    if granularity == "year":
        return _replace_year(dt, 1)
    if granularity == "quarter":
        return _add_months(dt, 3)
    if granularity == "month":
        return _add_months(dt, 1)
    if granularity == "week":
        return dt + timedelta(weeks=1)
    if granularity == "day":
        return dt + timedelta(days=1)
    if granularity == "hour":
        return dt + timedelta(hours=1)
    raise TemporalMediaError(
        "INVALID_TEMPORAL_CONTRACT", f"unsupported granularity {granularity!r}",
        status_code=422)


def _period_label(dt: datetime, granularity: str) -> str:
    if granularity == "year":
        return f"{dt.year:04d}"
    if granularity == "quarter":
        return f"{dt.year:04d}-Q{(dt.month - 1) // 3 + 1}"
    if granularity == "month":
        return f"{dt.year:04d}-{dt.month:02d}"
    if granularity == "week":
        return dt.strftime("%G-W%V")
    if granularity == "day":
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%dT%H:%M")


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def detect_gaps(observations: List[Dict[str, Any]],
                temporal: Dict[str, Any]) -> List[Dict[str, Any]]:
    """在两个真实观测之间枚举**缺期**（不是观测桶），供展示与披露。"""
    granularity = str(temporal.get("granularity") or "year")
    tz = _contract_tz(temporal)
    gaps: List[Dict[str, Any]] = []
    for prev, nxt in zip(observations, observations[1:]):
        cursor = parse_instant(prev["start"]).astimezone(tz)
        target = parse_instant(nxt["start"]).astimezone(tz)
        missing = 0
        while True:
            following = next_period(cursor, granularity)
            if following >= target or missing >= 64:
                break
            gaps.append({
                "id": f"gap-{prev['id']}-{missing}",
                "after_observation_id": prev["id"],
                "before_observation_id": nxt["id"],
                "label": _period_label(following, granularity),
                "start": _iso_z(following),
                "end": _iso_z(next_period(following, granularity)),
                "kind": "missing_period",
                "missing_index": missing,
            })
            cursor = following
            missing += 1
        if missing:
            gaps[-1]["missing_count_after"] = missing
    return gaps


def select_observations(frames: List[Dict[str, Any]],
                        args: Any) -> Dict[str, Any]:
    """按真实时间顺序选择有限观测子集；省略数必须明示，不静默截断。"""
    ordered = sorted(frames, key=lambda f: parse_instant(f["start"]))
    by_id = {str(f["id"]): f for f in ordered}
    ids: Optional[List[str]] = getattr(args, "frame_ids", None)
    if ids:
        unknown = [str(i) for i in ids if str(i) not in by_id]
        if unknown:
            raise TemporalMediaError(
                "UNKNOWN_FRAME_ID", "requested frame id is not a real observation bucket",
                status_code=422, details={"unknown": unknown[:12]})
        wanted = set(str(i) for i in ids)
        selected = [f for f in ordered if str(f["id"]) in wanted]
    else:
        start_id = getattr(args, "from_frame_id", None)
        end_id = getattr(args, "to_frame_id", None)
        lo = by_id.get(str(start_id)) if start_id else ordered[0]
        hi = by_id.get(str(end_id)) if end_id else ordered[-1]
        if lo is None or hi is None:
            raise TemporalMediaError(
                "UNKNOWN_FRAME_ID", "range endpoint is not a real observation bucket",
                status_code=422)
        i, j = ordered.index(lo), ordered.index(hi)
        if i > j:
            i, j = j, i
        selected = ordered[i:j + 1]
    if not selected:
        raise TemporalMediaError("EMPTY_SELECTION",
                                 "no real observation buckets selected", status_code=422)
    if len(selected) > MAX_OBSERVATIONS:
        raise TemporalMediaError(
            "RESOURCE_BUDGET_EXCEEDED",
            f"selected {len(selected)} observations exceeds media cap "
            f"{MAX_OBSERVATIONS}; narrow frame_ids / from..to",
            status_code=413,
            details={"selected": len(selected), "cap": MAX_OBSERVATIONS})
    omitted = [str(f["id"]) for f in ordered if f not in selected]
    return {
        "observations": selected,
        "omitted_observations": len(omitted),
        "omitted_frame_ids": omitted[:24],
        "total_observations": len(ordered),
        "actual_span_ms": int(round(
            (parse_instant(selected[-1]["end"])
             - parse_instant(selected[0]["start"])).total_seconds() * _MILLIS)),
        "selection": ("explicit_ids" if ids else
                      "range" if (args.from_frame_id or args.to_frame_id) else "all"),
    }


def _proportional_durations(observations: List[Dict[str, Any]],
                            segments: List[Dict[str, Any]],
                            base_dwell_ms: int) -> Dict[str, Any]:
    """按真实观测间隔缩放：Fraction 精确比值 → 整数毫秒量化。"""
    real_intervals: List[int] = []
    for prev, nxt in zip(observations, observations[1:]):
        real_intervals.append(max(1, int(round(
            (parse_instant(nxt["start"]) - parse_instant(prev["start"]))
            .total_seconds() * _MILLIS))))
    if not real_intervals:
        real_intervals = [int(round(
            (parse_instant(observations[0]["end"])
             - parse_instant(observations[0]["start"])).total_seconds() * _MILLIS)) or 1]
    reference = sorted(real_intervals)[len(real_intervals) // 2]
    compressed: List[str] = []
    exact: List[Fraction] = []
    for seg in segments:
        if seg["kind"] != "observation":
            exact.append(Fraction(int(base_dwell_ms)))
            continue
        obs_idx = int(seg["observation_index"])
        interval = real_intervals[min(obs_idx, len(real_intervals) - 1)]
        capped = min(interval, MAX_PROPORTIONAL_GAP_MS)
        if capped != interval:
            compressed.append(seg["observation_id"])
        value = Fraction(int(base_dwell_ms)) * Fraction(int(capped), int(reference))
        value = max(Fraction(MIN_DWELL_MS), min(Fraction(MAX_DWELL_MS), value))
        exact.append(value)
    # 最大余数法：整数毫秒量化后把舍入误差分配回帧，使累计和等于精确 Fraction 和。
    floors = [int(x) for x in exact]
    target = int(sum(exact) + Fraction(1, 2))
    remainder = max(0, target - sum(floors))
    if remainder:
        order = sorted(range(len(exact)),
                       key=lambda i: float(exact[i] - int(exact[i])), reverse=True)
        for i in order[:remainder]:
            floors[i] += 1
    durations = floors
    quantization_error_ms = float(sum(x - int(x) for x in exact))
    return {
        "durations": durations,
        "quantization_residual_ms": sum(durations) - target,
        "rule": "d_i = clamp(dwell_ms * min(real_interval_i, MAX_PROPORTIONAL_GAP_MS)"
                " / median(real_interval), MIN_DWELL_MS, MAX_DWELL_MS)",
        "reference_interval_ms": int(reference),
        "real_intervals_ms": real_intervals,
        "compressed_observations": compressed,
        "quantization_error_ms": round(quantization_error_ms, 6),
    }


def _repeats(duration_ms: int, fps: int) -> List[int]:
    """一个停留拆成 N 张相同编码帧；整数毫秒分配，累计误差为零。"""
    if fps <= 0 or duration_ms <= MIN_DWELL_MS:
        return [duration_ms]
    count = max(1, min(MAX_FPS * 10,
                       math.ceil(duration_ms * fps / 1000)))
    base, remainder = divmod(duration_ms, count)
    return [base + (1 if i < remainder else 0) for i in range(count)]


def build_display_segments(selection: Dict[str, Any], gaps: List[Dict[str, Any]],
                           args: Any, temporal: Dict[str, Any]) -> Dict[str, Any]:
    """观测/缺期 → 显示段（含时长、时间标注、编码帧映射的确定合同）。"""
    observations = selection["observations"]
    missing_display = str(args.missing_display)
    order = str(args.order)
    granularity = str(temporal.get("granularity") or "year")

    entries: List[Dict[str, Any]] = []
    gap_after: Dict[str, List[Dict[str, Any]]] = {}
    for gap in gaps:
        gap_after.setdefault(str(gap["after_observation_id"]), []).append(gap)
    for idx, obs in enumerate(observations):
        entries.append({
            "kind": "observation", "observation_index": idx,
            "observation_id": str(obs["id"]),
            "observation_label": str(obs["label"]),
            "observation_start": str(obs["start"]),
            "observation_end": str(obs["end"]),
            "start": str(obs["start"]), "end": str(obs["end"]),
            "label": str(obs["label"]), "held_from": None,
            "missing_period": False,
        })
        if missing_display in ("empty", "hold"):
            for gap in gap_after.get(str(obs["id"]), []):
                entries.append({
                    "kind": "empty" if missing_display == "empty" else "held",
                    "observation_index": idx, "observation_id": None,
                    "observation_label": str(gap["label"]),
                    "observation_start": str(gap["start"]),
                    "observation_end": str(gap["end"]),
                    "start": str(gap["start"]), "end": str(gap["end"]),
                    "label": str(gap["label"]),
                    "held_from": str(obs["id"]) if missing_display == "hold" else None,
                    "missing_period": True, "gap_id": str(gap["id"]),
                })
    if order == "reverse":
        entries = list(reversed(entries))
        for entry in entries:
            if entry["kind"] != "held":
                continue
            # 倒序时「沿用」的是播放方向上前一帧真实观测（不改名伪装新观测）。
            pos = entries.index(entry)
            earlier = next((e for e in reversed(entries[:pos])
                            if e["kind"] == "observation"), None)
            entry["held_from"] = earlier["observation_id"] if earlier else None

    if len(entries) > MAX_SEGMENTS:
        raise TemporalMediaError(
            "RESOURCE_BUDGET_EXCEEDED",
            f"{len(entries)} display segments exceeds cap {MAX_SEGMENTS}; "
            "use missing_display='skip' or narrow the selection",
            status_code=413)

    if str(args.timing_policy) == "proportional":
        timing = _proportional_durations(observations, entries, int(args.dwell_ms))
        durations = timing["durations"]
    else:
        timing = {
            "rule": "d_i = dwell_ms (equal duration per real observation bucket)",
            "reference_interval_ms": None, "real_intervals_ms": [],
            "compressed_observations": [], "quantization_error_ms": 0.0,
        }
        durations = [int(args.dwell_ms)] * len(entries)

    total = sum(durations)
    scaled = False
    if total > MAX_TOTAL_DISPLAY_MS:
        factor = Fraction(MAX_TOTAL_DISPLAY_MS, total)
        durations = [max(MIN_DWELL_MS, int(Fraction(d) * factor))
                     for d in durations]
        total = sum(durations)
        scaled = True
        if total > MAX_TOTAL_DISPLAY_MS:
            raise TemporalMediaError(
                "RESOURCE_BUDGET_EXCEEDED",
                f"total display {total} ms exceeds cap {MAX_TOTAL_DISPLAY_MS} ms",
                status_code=413)

    time_display = str(args.time_display)
    segments: List[Dict[str, Any]] = []
    encoded_total = 0
    for idx, (entry, duration) in enumerate(zip(entries, durations)):
        if time_display == "none":
            display_label = ""
        elif entry["kind"] == "empty":
            display_label = f"{entry['label']}（缺期·无观测）"
        elif entry["kind"] == "held":
            source = next((e["observation_label"] for e in entries
                           if e["observation_id"] == entry["held_from"]), "")
            display_label = f"{entry['label']}（缺期·沿用 {source} 观测）" if source \
                else f"{entry['label']}（缺期·沿用）"
        elif time_display == "label_window":
            display_label = (f"{entry['label']} [{entry['observation_start']}"
                             f" ~ {entry['observation_end']})")
        else:
            display_label = entry["label"]
        reps = _repeats(duration, int(args.fps))
        encoded_total += len(reps)
        segments.append({
            "index": idx,
            "kind": entry["kind"],
            "observation_id": entry["observation_id"],
            "observation_label": entry["observation_label"],
            "observation_start": entry["observation_start"],
            "observation_end": entry["observation_end"],
            "window": {"start": entry["start"], "end": entry["end"]},
            "time_label": display_label,
            "identity": ("real_observation" if entry["kind"] == "observation"
                         else "held_from:" + str(entry["held_from"])
                         if entry["kind"] == "held" else "missing_period_empty"),
            "missing_period": bool(entry["missing_period"]),
            "held_from": entry["held_from"],
            "gap_id": entry.get("gap_id"),
            "duration_ms": int(duration),
            "encoded_repeat": len(reps),
            "encoded_durations_ms": reps,
        })
    if encoded_total > MAX_ENCODED_FRAMES:
        raise TemporalMediaError(
            "RESOURCE_BUDGET_EXCEEDED",
            f"{encoded_total} encoded frames exceeds cap {MAX_ENCODED_FRAMES}; "
            "lower fps / dwell / frame count",
            status_code=413,
            details={"encoded_frames": encoded_total, "cap": MAX_ENCODED_FRAMES})
    return {
        "segments": segments,
        "timing": {
            **timing,
            "policy": str(args.timing_policy),
            "dwell_ms": int(args.dwell_ms),
            "fps": int(args.fps),
            "total_display_ms": total,
            "last_segment_hold_ms": durations[-1],
            "scaled_to_cap": scaled,
            "quantization": "integer milliseconds, exact cumulative sum",
        },
        "order": order,
        "direction": "source_time_ascending" if order == "chronological"
                     else "source_time_descending_explicit",
        "missing_display": missing_display,
        "time_display": time_display,
        "granularity": granularity,
        "timezone": str(temporal.get("timezone") or "UTC"),
        "encoded_frame_count": encoded_total,
        "observation_count": len(observations),
        "missing_period_count": len(gaps),
        "missing_period_labels": [g["label"] for g in gaps][:32],
    }


def _plan_core(plan: Dict[str, Any]) -> Dict[str, Any]:
    """计划 hash 覆盖用户所见全部字段 + 冻结指纹（不含 volatile id/时间戳）。"""
    keys = (
        "schema_version", "owner", "session_id", "expected_revision",
        "mapspec_revision", "frozen_fingerprints", "temporal_contract",
        "observations", "gaps", "display", "canvas", "format", "loop",
        "partial", "disclosures", "budgets", "capabilities_digest",
    )
    return {k: plan.get(k) for k in keys}


def plan_hash_of(plan: Dict[str, Any]) -> str:
    return sha256_text(canonical_json(_plan_core(plan)))


def build_plan(frozen: Dict[str, Any], args: Any, owner: str,
               capabilities: Dict[str, Any]) -> Dict[str, Any]:
    """预览与提交共用：用户看到的计划与实际编码采用的计划是同一份。"""
    renderer = capabilities.get("renderer") or {}
    if not renderer.get("available"):
        raise TemporalMediaError(
            "PNG_RASTERIZER_UNAVAILABLE",
            "server-side SVG rasterizer (rsvg-convert) is not installed; "
            "offline temporal media frames cannot be rendered",
            status_code=503)
    temporal = frozen["temporal"]
    representation = str(temporal.get("time_representation") or "")
    from app.lib.cartography.mapspec_schema import TEMPORAL_REPRESENTATIONS
    if representation not in TEMPORAL_REPRESENTATIONS:
        raise TemporalMediaError(
            "MIXED_TIME_REPRESENTATION",
            "unknown/mixed time representation; refusing to guess a time axis",
            status_code=422, details={"representation": representation})
    if str(args.format) not in capabilities.get("limits", {}).get("media_formats", []):
        raise TemporalMediaError("UNSUPPORTED_FORMAT",
                                 f"media format {args.format!r} is not open",
                                 status_code=422)
    if str(args.format) == "gif" and int(args.fps) > 0:
        # GIF 时间分辨率 10ms 且 writer 会合并相邻相同 P 帧；fps 拆帧既无意义
        # 又会与容器时长映射冲突。typed 拒绝而不是交付一个必然核验失败的作品。
        raise TemporalMediaError(
            "GIF_FPS_UNSUPPORTED",
            "GIF is limited to 10ms duration steps and merges identical frames; "
            "use fps=0 (one encoded frame per display dwell) or choose apng",
            status_code=422)
    available = next((f for f in capabilities.get("formats", [])
                      if f.get("name") == args.format), None)
    if not available or not available.get("available"):
        raise TemporalMediaError(
            "FORMAT_UNAVAILABLE",
            f"format {args.format!r} unavailable: {available.get('reason') if available else 'not probed'}",
            status_code=503)
    support = frozen["layer_support"]
    unsupported = list(support.get("unsupported_layers") or [])
    unbound = list(frozen.get("unbound_raster") or [])
    omitted = unsupported + [
        {"layer_id": str(u.get("layer_id") or ""),
         "reason": str(u.get("code") or "raster_binding_unresolved")}
        for u in unbound]
    partial = bool(omitted)
    if partial and str(args.partial_policy) == "blocking":
        raise TemporalMediaError(
            "BLOCKED_INCOMPLETE",
            "the frozen map contains layers that cannot be reproduced offline; "
            "explicitly choose partial_policy='allow_partial' to disclose them",
            status_code=409, details={"omitted_layers": omitted[:12]})

    selection = select_observations(list(temporal["frames"]), args)
    gaps = detect_gaps(selection["observations"], temporal)
    gaps_truncated = any(int(g.get("missing_index") or 0) >= 63 for g in gaps)
    selection["gaps_truncated"] = gaps_truncated
    display = build_display_segments(selection, gaps, args, temporal)
    if str(args.timing_policy) == "proportional":
        display["timing"]["interval_assignment"] = (
            "last observation reuses the preceding real interval; "
            "intervals are data-time milliseconds, not display milliseconds")
    width, height = int(args.width), int(args.height)
    # 工作量（解码像素总量）≠ 常驻内存：APNG 走 streaming writer，任意时刻
    # 只常驻一张 RGBA 帧；GIF 的 Pillow append_images 需按帧数计入 P 模式缓冲。
    raw_bytes = display["encoded_frame_count"] * width * height * 4
    if str(args.format) == "apng":
        encoder_resident_bytes = width * height * 4
    else:
        # GIF：Pillow 需同时驻留 P 帧（1 B/px）与逐帧 RGB 中间量（3 B/px），
        # 按 3× 实测口径计入，而不是乐观地只算 1 B/px。
        encoder_resident_bytes = display["encoded_frame_count"] * width * height * 3
    if encoder_resident_bytes > MAX_ENCODE_RAW_BYTES:
        raise TemporalMediaError(
            "RESOURCE_BUDGET_EXCEEDED",
            f"encoder resident pixel budget {encoder_resident_bytes} B exceeds "
            f"cap {MAX_ENCODE_RAW_BYTES} B",
            status_code=413,
            details={"resident_bytes": encoder_resident_bytes,
                     "cap": MAX_ENCODE_RAW_BYTES, "workload_bytes": raw_bytes})
    unique_keys = {
        (s["window"]["start"], s["window"]["end"], s["time_label"],
         s["held_from"], s["kind"]) for s in display["segments"]}
    temp_est = len(unique_keys) * width * height * 4 + raw_bytes
    if temp_est > MAX_TEMP_BYTES:
        raise TemporalMediaError(
            "RESOURCE_BUDGET_EXCEEDED",
            f"estimated temporary disk {temp_est} B exceeds cap {MAX_TEMP_BYTES} B",
            status_code=413, details={"estimated": temp_est})

    disclosures: List[str] = [
        f"omitted_observations={selection['omitted_observations']}",
        f"missing_periods={len(gaps)}",
        f"actual_span_ms={selection['actual_span_ms']}",
        "display_ms != data_time_unit != fps",
        "fixed_viewport_and_style:no_per_frame_fit_or_reclassification",
    ]
    if display["order"] == "reverse":
        disclosures.append("explicit_reverse_playback_source_times_unchanged")
    if display["timing"]["scaled_to_cap"]:
        disclosures.append("total_display_scaled_to_hard_cap")
    for obs in display["timing"].get("compressed_observations") or []:
        disclosures.append(f"proportional_gap_compressed:{obs}")
    if args.missing_display == "hold":
        disclosures.append("held_display_reuses_prior_picture_with_real_observation_label")
    if args.fps:
        disclosures.append("one_dwell_may_encode_multiple_identical_frames")
    if gaps_truncated:
        disclosures.append("gaps_truncated:>64_missing_periods_not_fully_enumerated")
    if partial:
        disclosures.append("partial:omitted_layers_declared_and_removed_from_frames")
    if str(args.format) == "gif":
        disclosures.append("gif_global_palette_256_colors_and_10ms_duration_quantization")
        if not bool(args.loop):
            disclosures.append(
                "gif_loop_count_1_is_container_repetition_count_not_apng_num_plays")

    plan: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": uuid.uuid4().hex,
        "owner": owner,
        "session_id": frozen["session_id"],
        "expected_revision": getattr(args, "expected_revision", None),
        "mapspec_revision": frozen["mapspec_revision"],
        "frozen_fingerprints": {
            "document": frozen["document_fingerprint"],
            "temporal": frozen["temporal_fingerprint"],
            "style": frozen["style_fingerprint"],
            "viewport": frozen["viewport_fingerprint"],
            "source_ref": frozen["source_ref"],
            "content_revision": frozen["content_revision"],
            "renderer": frozen["renderer"],
            "font": frozen["font_version"],
        },
        "temporal_contract": {
            "schema_version": temporal.get("schema_version"),
            "time_field": temporal.get("time_field"),
            "time_semantics": temporal.get("time_semantics"),
            "time_representation": representation,
            "time_format_hint": temporal.get("time_format_hint"),
            "granularity": temporal.get("granularity"),
            "timezone": temporal.get("timezone"),
            "extent": list(temporal.get("extent") or []),
            "render_path": temporal.get("render_path"),
            "missing_policy": temporal.get("missing_policy"),
            "quality": list(temporal.get("quality") or []),
            "disclosures": list(temporal.get("disclosures") or []),
            "source_ref": frozen["source_ref"],
            "content_revision": frozen["content_revision"],
            "user_pinned": bool(temporal.get("user_pinned")),
        },
        "observations": [
            {"id": str(f["id"]), "label": str(f["label"]),
             "start": str(f["start"]), "end": str(f["end"]),
             "feature_count": f.get("feature_count")}
            for f in selection["observations"]],
        "gaps": gaps,
        "selection": {k: v for k, v in selection.items()
                      if k != "observations"},
        "display": display,
        "canvas": {"width": width, "height": height,
                   "background": "#ffffff", "dpi": 150},
        "format": str(args.format),
        "loop": {"enabled": bool(args.loop), "count": 0 if args.loop else 1},
        "partial": {
            "allowed": str(args.partial_policy) == "allow_partial",
            "is_partial": partial,
            "omitted_layers": omitted,
        },
        "disclosures": disclosures,
        "budgets": {
            "max_observations": MAX_OBSERVATIONS,
            "max_segments": MAX_SEGMENTS,
            "max_encoded_frames": MAX_ENCODED_FRAMES,
            "max_total_display_ms": MAX_TOTAL_DISPLAY_MS,
            "max_encode_raw_bytes": MAX_ENCODE_RAW_BYTES,
            "max_temp_bytes": MAX_TEMP_BYTES,
        },
        "capabilities_digest": sha256_text(canonical_json({
            "renderer": capabilities.get("renderer"),
            "pillow": capabilities.get("pillow_version"),
            "format": args.format,
        })),
        "resource_estimate": {
            "unique_digital_frames": len(unique_keys),
            "encoded_frames": display["encoded_frame_count"],
            "raw_pixel_bytes": raw_bytes,
            "encoder_resident_bytes": encoder_resident_bytes,
            "estimated_temp_bytes": temp_est,
            "total_display_ms": display["timing"]["total_display_ms"],
            "render_pass_per_unique_frame": 1,
            "encoder_processes": 0,
        },
        "frozen": frozen,
    }
    plan["plan_hash"] = plan_hash_of(plan)
    return plan


def assert_plan_hash(plan: Dict[str, Any], submitted: str) -> None:
    if plan_hash_of(plan) != submitted:
        raise TemporalMediaError(
            "PLAN_VERSION_MISMATCH",
            "submitted plan_hash does not match the current frozen plan; "
            "preview again before submitting (unknown version refused)",
            status_code=409,
            details={"submitted": str(submitted)[:64],
                     "current": plan.get("plan_hash")})


__all__ = [
    "parse_instant", "next_period", "detect_gaps", "select_observations",
    "build_display_segments", "build_plan", "plan_hash_of", "assert_plan_hash",
]
