"""StoryMap 纯逻辑层（ADR-0196）——无 IO、可离线单测锁定。

模块分工：
- ``spec``            StoryMapSpec 领域模型（跨端契约，additive-only 演进）；
- ``story_compiler``  证据链/消息 → 章节归纳（叙事弧分桶 + 文本/统计/图表提炼）；
- ``camera_planner``  bbox → 相机关键帧 + Catmull-Rom/贝塞尔平滑轨迹 + 连续性闸；
- ``export_packager`` 脱敏 + 自包含单文件 JSON/HTML StoryBundle。

需要 DB/会话 IO 的服务壳在 ``app/services/storymap/``（对齐制图先例：
规则本体在 lib，服务编排在 services）。
"""
from app.lib.storymap.spec import (
    NARRATIVE_ARC,
    STORYMAP_SPEC_SCHEMA_VERSION,
    AudioNarrative,
    CameraKeyframe,
    LinkedWidget,
    StoryChapter,
    StoryMapMetadata,
    StoryMapSpec,
    estimate_duration,
    narration_text,
)

__all__ = [
    "NARRATIVE_ARC",
    "STORYMAP_SPEC_SCHEMA_VERSION",
    "AudioNarrative",
    "CameraKeyframe",
    "LinkedWidget",
    "StoryChapter",
    "StoryMapMetadata",
    "StoryMapSpec",
    "estimate_duration",
    "narration_text",
]
