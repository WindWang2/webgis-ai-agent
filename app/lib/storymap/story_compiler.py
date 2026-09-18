"""叙事编译（ADR-0196 §2）——证据链/消息 → StoryMapSpec，纯函数无 IO。

真相源是证据链（GisTraceChain / ReplayTrace 形状的 trace dict）：18 规范
阶段按弧分桶（S1–2 引言、S3–8 宏观、S9–12 解剖、S13–16 推演、S17–18
建言），空桶跳过保序输出；消息文本只在 trace 缺席时降级使用。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.lib.runtime.gis_trace import Stage
from app.lib.storymap.camera_planner import plan_camera_for_bbox
from app.lib.storymap.spec import (
    ARC_TITLES_ZH,
    NARRATIVE_ARC,
    ArcRole,
    AudioNarrative,
    CameraKeyframe,
    LinkedWidget,
    StoryChapter,
    StoryMapMetadata,
    StoryMapSpec,
    assert_json_depth,
    estimate_duration,
    narration_text,
    strip_surrogates,
)

# 证据链阶段 → 叙事弧桶（S1–18 对齐 ADR-0103 §十）。
ARC_STAGE_BUCKETS: Dict[ArcRole, frozenset] = {
    "introduction": frozenset({1, 2}),
    "macro_situation": frozenset({3, 4, 5, 6, 7, 8}),
    "focus_dissection": frozenset({9, 10, 11, 12}),
    "dynamic_simulation": frozenset({13, 14, 15, 16}),
    "recommendation": frozenset({17, 18}),
}

_STAGE_NAME_TO_ID = {s.name: int(s) for s in Stage}

# trace payload 里依次寻找章节摘要句的键。
_SUMMARY_KEYS = ("summary", "conclusion", "verdict", "final_text")
_STATS_KEYS = ("stats", "metrics")

# 产物 ref 词表（与前端 REF_RE 同源）：ref:chart-* / table / stats / grid / admin
_REF_RE = re.compile(r"\bref:(chart|table|stats|grid|admin)-[A-Za-z0-9_-]{1,64}\b")
_REF_KIND = {"chart": "chart", "table": "table", "stats": "stats",
             "grid": "table", "admin": "table"}

# 全无素材时的兜底视角（陆桥视角，zoom 会被 clamp 到下限）。
_DEFAULT_BBOX = (73.0, 18.0, 135.0, 53.0)

_MAX_STAT_BULLETS = 6

# 键缺失哨兵（与"显式 null"区分：缺失 → 序号兜底，显式 null → ValueError）。
_MISSING = object()


@dataclass
class TraceStep:
    """归一化后的单条证据步骤。"""

    stage_id: int
    ts: float
    payload: Dict[str, Any] = field(default_factory=dict)


def normalize_trace(raw: Mapping[str, Any]) -> List[TraceStep]:
    """三种输入形状 → 统一 TraceStep 列表（保输入序，不猜不重排）。

    1. GisTraceChain.as_dict()：``stages: [{stage|stage_id, ts, **payload}]``；
    2. ReplayTrace 形状：user_input / tool_calls / artifacts / final_text；
    其余形状抛 ValueError（显式失败优于猜测）。
    """
    steps: List[TraceStep] = []
    stages = raw.get("stages")
    if isinstance(stages, list):
        for idx, rec in enumerate(stages):
            if not isinstance(rec, Mapping):
                continue
            stage_field = rec.get("stage_id", rec.get("stage"))
            stage_id = _resolve_stage_id(stage_field)
            if stage_id is None:
                continue
            payload = {k: v for k, v in rec.items()
                       if k not in ("stage", "stage_id", "ts")}
            raw_ts = rec.get("ts", _MISSING)
            steps.append(TraceStep(
                stage_id=stage_id,
                ts=float(idx) if raw_ts is _MISSING else _coerce_ts(raw_ts),
                payload=payload,
            ))
        return steps

    if "user_input" in raw or "tool_calls" in raw or "final_text" in raw:
        ts = 0.0
        if raw.get("user_input"):
            steps.append(TraceStep(1, ts, {"user_intent": raw["user_input"]}))
            ts += 1
        for call in raw.get("tool_calls") or []:
            if isinstance(call, Mapping):
                payload = {"tool_name": call.get("tool_name", "tool")}
                args = call.get("args")
                if isinstance(args, Mapping):
                    payload.update(args)
                steps.append(TraceStep(9, ts, payload))
                ts += 1
        for artifact in raw.get("artifacts") or []:
            if isinstance(artifact, Mapping):
                steps.append(TraceStep(12, ts, dict(artifact)))
                ts += 1
        outcome = raw.get("outcome")
        if isinstance(outcome, Mapping) and outcome.get("verdict"):
            steps.append(TraceStep(17, ts, {"verdict": outcome["verdict"]}))
            ts += 1
        if raw.get("final_text"):
            steps.append(TraceStep(18, ts, {"final_text": raw["final_text"]}))
        return steps

    raise ValueError("unrecognized trace shape: expected GisTraceChain or "
                     "ReplayTrace dict (stages / user_input+tool_calls)")


def _resolve_stage_id(stage_field: Any) -> Optional[int]:
    if isinstance(stage_field, bool):
        return None
    if isinstance(stage_field, int):
        return stage_field if 1 <= stage_field <= 18 else None
    if isinstance(stage_field, str):
        return _STAGE_NAME_TO_ID.get(stage_field.strip().upper())
    return None


def _coerce_ts(value: Any) -> float:
    """ts 安全转换：键缺失用序号兜底（调用侧处理）；显式给出但不可用 → ValueError。

    （None / 字符串 / NaN 都属"给出但不可用"——路由映射 422，绝不 TypeError 500。）
    """
    if value is None:
        raise ValueError("trace ts must be a finite number when provided")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid trace ts: {value!r}")
    try:
        number = float(value)
    except OverflowError as exc:  # float(10**400) 溢出 → 统一走契约 422
        raise ValueError(f"non-finite trace ts: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"non-finite trace ts: {value!r}")
    return number


def iter_coord_points(value: Any, seen: Optional[set] = None):
    """深走 JSON 树收集 GeoJSON 坐标点（抗 schema 漂移，按值扫描）。

    覆盖**全部顶点**：凡 ``coordinates`` 键下的任意嵌套层级的 ``[x, y]``
    数值对都被折出（Polygon/LineString/MultiPolygon/GeometryCollection
    一并覆盖）；非 coordinates 键下的数值数组（图表 values 等）不误采。
    """
    if seen is None:
        seen = set()
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return
    if isinstance(value, str):
        return
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, Mapping):
        coords = value.get("coordinates")
        if isinstance(coords, (list, tuple)):
            yield from _iter_points_in_coords(coords)
        for v in value.values():
            yield from iter_coord_points(v, seen)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_coord_points(item, seen)


def _iter_points_in_coords(coords: Sequence[Any]):
    """coordinates 子树 → 逐顶点 [x, y]（含全部嵌套层级）。"""
    if (len(coords) >= 2 and _is_finite_num(coords[0])
            and _is_finite_num(coords[1])):
        yield [float(coords[0]), float(coords[1])]
        return
    for item in coords:
        if isinstance(item, (list, tuple)):
            yield from _iter_points_in_coords(item)


def _is_finite_num(v: Any) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:  # math.isfinite(10**400) 抛 OverflowError 而非 False
        return False


def _payload_bbox(payload: Mapping[str, Any]) -> Optional[List[float]]:
    """payload → [w, s, e, n]（跨 ±180° 保留 e<w 的顺序语义交由规划器展开）。

    取值优先级：bbox/extent → center(+zoom 合成跨度) → GeoJSON 全顶点。
    非有限数（NaN/Infinity）一律视为缺省，绝不进契约。
    """
    for key in ("bbox", "extent"):
        val = payload.get(key)
        if (isinstance(val, (list, tuple)) and len(val) == 4
                and all(_is_finite_num(v) for v in val)):
            w, s, e, n = (float(v) for v in val)
            if e < w:  # 跨反子午线：保留展开语义（planner 统一 +360）
                return [w, min(s, n), e, max(s, n)]
            return [min(w, e), min(s, n), max(w, e), max(s, n)]
    center = payload.get("center")
    if (isinstance(center, (list, tuple)) and len(center) >= 2
            and _is_finite_num(center[0]) and _is_finite_num(center[1])):
        x, y = float(center[0]), float(center[1])
        zoom = payload.get("zoom")
        if _is_finite_num(zoom):
            # Clamp before exponentiation: ±1e308 is finite but 2**(zoom-1)
            # overflows/underflows the float domain and turns a bad payload into
            # a 500. The clamp bounds the synthesized span to the planner's
            # usable range without inventing a camera outside the contract.
            bounded_zoom = min(30.0, max(-20.0, float(zoom)))
            span = 360.0 / (2.0 ** (bounded_zoom - 1.0))
            return [x - span / 2.0, y - span / 4.0, x + span / 2.0, y + span / 4.0]
        return [x, y, x, y]
    xs: List[float] = []
    ys: List[float] = []
    for point in iter_coord_points(payload):
        xs.append(point[0])
        ys.append(point[1])
    if xs:
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _union_bbox(boxes: Sequence[Optional[Sequence[float]]]) -> Optional[List[float]]:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    # 含跨反子午线盒（e<w）时统一展开到无环绕域再做并集。
    if any(b[2] < b[0] for b in boxes):
        boxes = [[b[0], b[1], b[2] + 360.0 if b[2] < b[0] else b[2], b[3]]
                 for b in boxes]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _ref_kind(ref: str) -> str:
    m = re.match(r"ref:([a-z]+)-", ref)
    return _REF_KIND.get(m.group(1), "chart") if m else "chart"


def _harvest_widgets(steps: Sequence[TraceStep], arc_role: ArcRole):
    """从桶内步骤收集联动图表（ref 去重，chart dict 优先为 data）。"""
    widgets: List[LinkedWidget] = []
    seen: set = set()
    for step in steps:
        chart = step.payload.get("chart")
        ref = step.payload.get("ref") if isinstance(step.payload.get("ref"), str) else ""
        if isinstance(chart, Mapping):
            wid = ref or f"widget-chart-{len(widgets) + 1}"
            if wid not in seen:
                seen.add(wid)
                widgets.append(LinkedWidget(
                    id=wid, kind="chart", ref=ref,
                    title=str(step.payload.get("title") or ""),
                    chapter_id=f"arc-{arc_role}",
                    data=dict(chart)))
        for match in _REF_RE.finditer(" ".join(
                v for v in step.payload.values() if isinstance(v, str))):
            ref = match.group(0)
            if ref in seen:
                continue
            seen.add(ref)
            widgets.append(LinkedWidget(id=ref, kind=_ref_kind(ref), ref=ref,
                                        chapter_id=f"arc-{arc_role}"))
    return widgets


def _chapter_narrative(arc_role: ArcRole, steps: Sequence[TraceStep]) -> str:
    title = ARC_TITLES_ZH[arc_role]
    lines = [f"## {title}"]
    summary = next((str(step.payload[k]) for step in steps
                    for k in _SUMMARY_KEYS
                    if isinstance(step.payload.get(k), (str, int, float))
                    and str(step.payload[k]).strip()), "")
    if arc_role == "introduction" and not summary:
        summary = next((str(step.payload["user_intent"]) for step in steps
                        if isinstance(step.payload.get("user_intent"), str)
                        and step.payload["user_intent"].strip()), "")
    if summary:
        lines.append(summary.strip())
    stats = next((step.payload[k] for step in steps for k in _STATS_KEYS
                  if isinstance(step.payload.get(k), Mapping)), None)
    if isinstance(stats, Mapping):
        for i, (k, v) in enumerate(stats.items()):
            if i >= _MAX_STAT_BULLETS:
                break
            lines.append(f"- {k}: {_format_stat_value(v)}")
    return "\n".join(lines)


def _format_stat_value(value: Any) -> str:
    """统计值渲染：标量原样；结构值走紧凑 JSON（避免 Python repr 落进正文）。"""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _build_chapter(arc_role: ArcRole, steps: Sequence[TraceStep],
                   fallback_bbox: Optional[Sequence[float]],
                   step_bboxes: Sequence[Optional[List[float]]]):
    bbox = _union_bbox(step_bboxes) or fallback_bbox or _DEFAULT_BBOX
    narrative = _chapter_narrative(arc_role, steps)
    widgets = _harvest_widgets(steps, arc_role)
    view = plan_camera_for_bbox(bbox, arc_role)
    view.pop("aspect", None)
    keyframe = CameraKeyframe(chapter_id=f"arc-{arc_role}", t=0.0, **view)
    refs = []
    for w in widgets:
        if w.ref:
            refs.append(w.ref)
    chapter = StoryChapter(
        id=f"arc-{arc_role}",
        title=ARC_TITLES_ZH[arc_role],
        narrative=narrative,
        arc_role=arc_role,
        source_stage_ids=sorted({s.stage_id for s in steps}),
        linked_widget_ids=[w.id for w in widgets],
        highlight_refs=refs,
    )
    audio = AudioNarrative(
        chapter_id=chapter.id,
        text=narration_text(narrative),
        duration_hint_s=estimate_duration(narrative),
    )
    return chapter, keyframe, widgets, audio


def _chapters_from_messages(messages: Sequence[Mapping[str, Any]]):
    """无 trace 降级：首条消息→引言，最后一条→建言（单条只出引言）。"""
    if not messages:
        raise ValueError("cannot compile from an empty message list")
    chapters, keyframes, widgets, audios = [], [], [], []

    def emit(role: ArcRole, content: str, source_idx: int):
        chapter, kf, ws, audio = _build_chapter_from_text(role, content, source_idx)
        chapters.append(chapter)
        keyframes.append(kf)
        widgets.extend(ws)
        audios.append(audio)

    first = messages[0]
    emit("introduction", _message_content(first), 0)
    if len(messages) > 1:
        emit("recommendation", _message_content(messages[-1]), len(messages) - 1)
    return chapters, keyframes, widgets, audios


def _message_content(message: Mapping[str, Any]) -> str:
    """消息正文安全取值：None/非字符串一律折叠为空串（绝不渲染 'None'）。"""
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _build_chapter_from_text(arc_role: ArcRole, content: str, source_idx: int):
    narrative = f"## {ARC_TITLES_ZH[arc_role]}\n{content.strip() or '（无正文）'}"
    view = plan_camera_for_bbox(_DEFAULT_BBOX, arc_role)
    view.pop("aspect", None)
    chapter = StoryChapter(
        id=f"arc-{arc_role}",
        title=ARC_TITLES_ZH[arc_role],
        narrative=narrative,
        arc_role=arc_role,
        source_stage_ids=[source_idx],
    )
    audio = AudioNarrative(chapter_id=chapter.id, text=narration_text(narrative),
                           duration_hint_s=estimate_duration(narrative))
    return chapter, CameraKeyframe(chapter_id=chapter.id, t=0.0, **view), [], audio


def compile_story_map(
    trace: Optional[Mapping[str, Any]] = None,
    messages: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    session_id: str = "",
    turn_id: str = "",
    title: Optional[str] = None,
) -> StoryMapSpec:
    """证据链（优先）或消息列表 → StoryMapSpec。二者必居其一。"""
    if trace is None and not messages:
        raise ValueError("compile_story_map requires a trace or messages")

    # Boundary guards shared by the stateless and session paths: deep JSON can
    # only produce a contract 422, and lone surrogate code units are stripped
    # before any text can reach response serialization or an offline bundle.
    if trace is not None:
        assert_json_depth(trace)
        trace = strip_surrogates(trace)  # type: ignore[assignment]
    if messages:
        assert_json_depth(messages)
        messages = strip_surrogates(messages)  # type: ignore[assignment]
    if title is not None:
        title = strip_surrogates(title)  # type: ignore[assignment]

    if trace is not None:
        steps = normalize_trace(trace)
        session_id = session_id or str(trace.get("session_id", "") or "")
        turn_id = turn_id or str(trace.get("turn_id", "") or "")
        all_widgets: List[LinkedWidget] = []
        chapters, keyframes, audios = [], [], []
        # Single-pass extraction: every payload's vertex scan is reused for the
        # session union and that step's chapter union. Re-scanning each bucket
        # doubled the worst-case GeoJSON walk with identical output.
        step_bboxes = [_payload_bbox(s.payload) for s in steps]
        session_bbox = _union_bbox(step_bboxes)
        for arc_role in NARRATIVE_ARC:
            bucket_indexes = [
                i for i, s in enumerate(steps)
                if s.stage_id in ARC_STAGE_BUCKETS[arc_role]
            ]
            if not bucket_indexes:
                continue
            bucket = [steps[i] for i in bucket_indexes]
            bucket_bboxes = [step_bboxes[i] for i in bucket_indexes]
            chapter, kf, ws, audio = _build_chapter(
                arc_role, bucket, session_bbox, bucket_bboxes,
            )
            chapters.append(chapter)
            keyframes.append(kf)
            all_widgets.extend(ws)
            audios.append(audio)
        if not chapters:
            # 规格承诺：trace 有形状但桶全空 → 给了 messages 就降级两章；
            # 两样都没有有效素材才显式失败（路由映射 422）。降级路径的
            # metadata.summary 与 messages-only 编译一致：取末条消息正文。
            if not messages:
                raise ValueError(
                    "trace contains no recognizable stage records and no "
                    "messages were provided for fallback"
                )
            chapters, keyframes, all_widgets, audios = _chapters_from_messages(list(messages))
            summary = _message_content(messages[-1]).strip()
        else:
            summary = next((str(s.payload["final_text"]).strip() for s in steps
                            if isinstance(s.payload.get("final_text"), str)
                            and s.payload["final_text"].strip()), "")
            if not summary:
                summary = next((str(s.payload[k]) for s in steps for k in _SUMMARY_KEYS
                                if isinstance(s.payload.get(k), str)
                                and s.payload[k].strip()), "")
    else:
        chapters, keyframes, all_widgets, audios = _chapters_from_messages(list(messages))
        summary = _message_content(messages[-1]).strip()

    return StoryMapSpec(
        metadata=StoryMapMetadata(
            title=title or "StoryMap 空间叙事专报",
            session_id=session_id,
            turn_id=turn_id,
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            summary=summary,
        ),
        chapters=chapters,
        camera_keyframes=keyframes,
        linked_widgets=all_widgets,
        audio_narrative=audios,
    )
