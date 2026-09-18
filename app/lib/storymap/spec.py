"""StoryMapSpec 领域模型（ADR-0196 §1）——唯一的跨端叙事契约。

演进纪律对齐 MapSpec ``_SpecModel``：pydantic v2 + ``extra="allow"``，
未知键 round-trip 保留，版本只加字段不改语义（additive-only）。
"""
from __future__ import annotations

import math
import re
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

STORYMAP_SPEC_SCHEMA_VERSION = "1.0"

# 叙事弧（引言 → 宏观态势 → 重点解剖 → 动态推演 → 决策建言），保序即章节序。
ArcRole = Literal[
    "introduction",
    "macro_situation",
    "focus_dissection",
    "dynamic_simulation",
    "recommendation",
]
NARRATIVE_ARC: Tuple[ArcRole, ...] = (
    "introduction",
    "macro_situation",
    "focus_dissection",
    "dynamic_simulation",
    "recommendation",
)

ARC_TITLES_ZH = {
    "introduction": "引言",
    "macro_situation": "宏观态势",
    "focus_dissection": "重点解剖",
    "dynamic_simulation": "动态推演",
    "recommendation": "决策建言",
}

# 相机硬边界：pitch 封顶 60°（任务规格；MapView 词表允许 85 但叙事镜头不用）
PITCH_MIN, PITCH_MAX = 0.0, 60.0
BEARING_MIN, BEARING_MAX = -180.0, 180.0
ZOOM_MIN, ZOOM_MAX = 3.0, 18.0

_MD_STRIP_RULES = (
    (re.compile(r"```[a-z]*\n?"), ""),
    (re.compile(r"^#{1,6}\s+", re.M), ""),
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"\*(.+?)\*"), r"\1"),
    (re.compile(r"\[(.+?)\]\([^)]*\)"), r"\1"),
    (re.compile(r"`([^`]*)`"), r"\1"),
)

# 解说词朗读速率（中文字/秒）——v1 经验常数，TTS 接入时可按音色覆写。
_NARRATION_CHARS_PER_SECOND = 4.0

# 载荷深度上限：真实 trace/spec 载荷嵌套 <<30 层；恶意 ~1500 层嵌套会让
# 递归走子/脱敏/json 序列化撞 RecursionError → 500。统一在边界判定 4xx。
MAX_PAYLOAD_DEPTH = 64


def assert_json_depth(value: object, *, max_depth: int = MAX_PAYLOAD_DEPTH) -> None:
    """JSON 树深度门卫（迭代遍历，自身不递归）——超限抛 ValueError。

    深度定义：根容器为 1，每下钻一层 Mapping/list/tuple +1。标量不增深。
    """
    seen: set = set()
    stack: list = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(
                f"payload exceeds maximum nesting depth {max_depth}"
            )
        if isinstance(node, dict):
            oid = id(node)
            if oid in seen:
                continue
            seen.add(oid)
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, (list, tuple)):
            oid = id(node)
            if oid in seen:
                continue
            seen.add(oid)
            stack.extend((v, depth + 1) for v in node)


def strip_surrogates(value: object) -> object:
    """递归剥除字符串中的孤立代理字符（\\ud800–\\udfff 不成对者）。

    孤立代理字符经 ensure_ascii=False 序列化后无法 UTF-8 编码 → 响应期
    500。encode('utf-8','ignore') 精确剥除**编码失败**的码元：合法字符
    （含代理对折合成的 emoji）原样保留。输入深度已由 assert_json_depth
    约束，递归安全。
    """
    if isinstance(value, str):
        return value.encode("utf-8", "ignore").decode("utf-8")
    if isinstance(value, dict):
        return {k: strip_surrogates(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [strip_surrogates(v) for v in value]
    return value


class _StoryModel(BaseModel):
    """StoryMap 模型族基类：未知键保留（additive-only 演进）。"""

    model_config = ConfigDict(extra="allow")


def narration_text(markdown: str) -> str:
    """剥 markdown 语法留正文（解说词/时长估算共用）。"""
    text = markdown or ""
    for pattern, repl in _MD_STRIP_RULES:
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text).strip()


def estimate_duration(markdown: str) -> float:
    """按剥语法后的字数估算朗读时长（秒），下限 2s，上限 120s。"""
    n = len(narration_text(markdown))
    return float(min(120, max(2, math.ceil(n / _NARRATION_CHARS_PER_SECOND))))


class CameraKeyframe(_StoryModel):
    """单个相机关键帧：章内归一进度 t 处的观察位姿。"""

    # allow_inf_nan=False：NaN/Infinity 任何形态都不进契约（它们在 JSON 响应
    # 序列化阶段会炸成 500；在插值阶段会污染整条轨迹）。说明放注释不放
    # docstring —— docstring 会进 OpenAPI schema description（契约面字节漂移）。
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    chapter_id: str
    t: float = Field(0.0, ge=0.0, le=1.0)
    center: List[float] = Field(..., min_length=2, max_length=2)  # [lng, lat]
    zoom: float = Field(4.0, ge=ZOOM_MIN, le=ZOOM_MAX)
    pitch: float = Field(0.0, ge=PITCH_MIN, le=PITCH_MAX)
    bearing: float = Field(0.0, ge=BEARING_MIN, le=BEARING_MAX)
    easing: Literal["linear", "ease_in_out"] = "ease_in_out"


class StoryChapter(_StoryModel):
    """一个叙事章节：弧角色 + 正文 + 素材追溯 + 联动引用。"""

    id: str
    title: str
    narrative: str  # markdown
    arc_role: ArcRole
    source_stage_ids: List[int] = Field(default_factory=list)  # 证据链阶段追溯
    linked_widget_ids: List[str] = Field(default_factory=list)
    highlight_refs: List[str] = Field(default_factory=list)
    duration_hint_s: Optional[float] = Field(None, ge=0.0)

    @model_validator(mode="after")
    def _default_duration(self) -> "StoryChapter":
        if self.duration_hint_s is None:
            self.duration_hint_s = estimate_duration(self.narrative)
        return self


class LinkedWidget(_StoryModel):
    """章节联动图表/表格/KPI（data 为 recharts 兼容形状，原样透传前端）。"""

    id: str
    kind: Literal["chart", "table", "kpi", "stats"]
    ref: str = ""
    title: str = ""
    chapter_id: str = ""
    data: dict = Field(default_factory=dict)


class AudioNarrative(_StoryModel):
    """章节解说词（v1 只产文本与时长；TTS 是后向扩展位）。"""

    chapter_id: str
    text: str
    lang: str = "zh-CN"
    duration_hint_s: float = Field(2.0, ge=0.0)
    voice: Optional[str] = None


class StoryMapMetadata(_StoryModel):
    title: str = "StoryMap 专报"
    session_id: str = ""
    turn_id: str = ""
    generated_at: str = ""
    summary: str = ""
    theme: str = "dark-carto"
    language: str = "zh-CN"
    engine_version: str = "storymap-1.0"


class StoryMapSpec(_StoryModel):
    """一次分析推演的完整空间叙事规范。"""

    schema_version: str = STORYMAP_SPEC_SCHEMA_VERSION
    metadata: StoryMapMetadata = Field(default_factory=StoryMapMetadata)
    chapters: List[StoryChapter] = Field(..., min_length=1)
    camera_keyframes: List[CameraKeyframe] = Field(default_factory=list)
    linked_widgets: List[LinkedWidget] = Field(default_factory=list)
    audio_narrative: List[AudioNarrative] = Field(default_factory=list)

    @model_validator(mode="after")
    def _keyframes_reference_real_chapters(self) -> "StoryMapSpec":
        known = set()
        for chapter in self.chapters:
            if not chapter.id or not chapter.id.strip():
                raise ValueError("chapter id must be non-empty")
            if chapter.id in known:
                raise ValueError(f"duplicate chapter id {chapter.id!r}")
            known.add(chapter.id)
        for kf in self.camera_keyframes:
            if kf.chapter_id not in known:
                raise ValueError(
                    f"camera keyframe references unknown chapter {kf.chapter_id!r}"
                )
        return self
