"""叙事编译（ADR-0196 §2）——证据链/消息 → StoryMapSpec，纯函数无 IO。

真相源是证据链（GisTraceChain / ReplayTrace 形状的 trace dict）：18 规范
阶段按弧分桶（S1–2 引言、S3–8 宏观、S9–12 解剖、S13–16 推演、S17–18
建言），空桶跳过保序输出；消息文本只在 trace 缺席时降级使用。
"""
from __future__ import annotations

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
    estimate_duration,
    narration_text,
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
            steps.append(TraceStep(stage_id=stage_id,
                                   ts=float(rec.get("ts", idx)), payload=payload))
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


def _iter_coord_points(value: Any, seen: Optional[set] = None):
    """深走 JSON 树收集 GeoJSON 坐标点（抗 schema 漂移，按值扫描）。"""
    if seen is None:
        seen = set()
    if isinstance(value, (int, float)) or value is None:
        return
    if isinstance(value, str):
        return
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, Mapping):
        coords = value.get("coordinates")
        if isinstance(coords, (list, tuple)):
            point = _flatten_point(coords)
            if point:
                yield point
        for v in value.values():
            yield from _iter_coord_points(v, seen)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_coord_points(item, seen)


def _flatten_point(coords: Sequence[Any]) -> Optional[List[float]]:
    """[x, y(, z…)] / [[x,y],…] / 嵌套环 → 折出首个数值点。"""
    if len(coords) >= 2 and all(isinstance(c, (int, float)) for c in coords[:2]):
        return [float(coords[0]), float(coords[1])]
    for item in coords:
        if isinstance(item, (list, tuple)):
            point = _flatten_point(item)
            if point:
                return point
    return None


def _payload_bbox(payload: Mapping[str, Any]) -> Optional[List[float]]:
    for key in ("bbox", "extent"):
        val = payload.get(key)
        if (isinstance(val, (list, tuple)) and len(val) == 4
                and all(isinstance(v, (int, float)) for v in val)):
            w, s, e, n = (float(v) for v in val)
            return [min(w, e), min(s, n), max(w, e), max(s, n)]
    center = payload.get("center")
    if (isinstance(center, (list, tuple)) and len(center) >= 2
            and all(isinstance(v, (int, float)) for v in center[:2])):
        x, y = float(center[0]), float(center[1])
        return [x, y, x, y]
    xs: List[float] = []
    ys: List[float] = []
    for point in _iter_coord_points(payload):
        xs.append(point[0])
        ys.append(point[1])
    if xs:
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _union_bbox(boxes: Sequence[Optional[Sequence[float]]]) -> Optional[List[float]]:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
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
            lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _build_chapter(arc_role: ArcRole, steps: Sequence[TraceStep],
                   fallback_bbox: Optional[Sequence[float]]):
    bbox = _union_bbox([_payload_bbox(s.payload) for s in steps]) or fallback_bbox \
        or _DEFAULT_BBOX
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
    emit("introduction", str(first.get("content", "")), 0)
    if len(messages) > 1:
        emit("recommendation", str(messages[-1].get("content", "")),
             len(messages) - 1)
    return chapters, keyframes, widgets, audios


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

    if trace is not None:
        steps = normalize_trace(trace)
        session_id = session_id or str(trace.get("session_id", "") or "")
        turn_id = turn_id or str(trace.get("turn_id", "") or "")
        all_widgets: List[LinkedWidget] = []
        chapters, keyframes, audios = [], [], []
        session_bbox = _union_bbox([_payload_bbox(s.payload) for s in steps])
        for arc_role in NARRATIVE_ARC:
            bucket = [s for s in steps if s.stage_id in ARC_STAGE_BUCKETS[arc_role]]
            if not bucket:
                continue
            chapter, kf, ws, audio = _build_chapter(arc_role, bucket, session_bbox)
            chapters.append(chapter)
            keyframes.append(kf)
            all_widgets.extend(ws)
            audios.append(audio)
        summary = next((str(s.payload["final_text"]).strip() for s in steps
                        if isinstance(s.payload.get("final_text"), str)
                        and s.payload["final_text"].strip()), "")
        if not summary:
            summary = next((str(s.payload[k]) for s in steps for k in _SUMMARY_KEYS
                            if isinstance(s.payload.get(k), str)
                            and s.payload[k].strip()), "")
    else:
        chapters, keyframes, all_widgets, audios = _chapters_from_messages(list(messages))
        summary = str(messages[-1].get("content", "")).strip()

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
