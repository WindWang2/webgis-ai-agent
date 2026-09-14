"""StoryMap 服务壳（ADR-0196 §5）——承载需要 DB/会话 IO 的编排。

算法本体全部在 ``app/lib/storymap``（纯函数、无 IO）；本包只做装载、
组装与委托，保证叙事规则可在无 DB 的单测里锁定。
"""
from app.lib.storymap.camera_planner import (
    ARC_PITCH_BASE,
    build_camera_track,
    plan_camera_for_bbox,
    validate_track,
)
from app.lib.storymap.export_packager import (
    BUNDLE_SCHEMA_VERSION,
    build_story_bundle,
    bundle_to_json,
    render_standalone_html,
    sanitize_dict,
)
from app.lib.storymap.story_compiler import compile_story_map, normalize_trace
from app.services.storymap.camera_planner import bbox_from_geojson
from app.services.storymap.export_packager import layers_to_featurecollections
from app.services.storymap.story_compiler import compile_for_session

__all__ = [
    "ARC_PITCH_BASE",
    "BUNDLE_SCHEMA_VERSION",
    "bbox_from_geojson",
    "build_camera_track",
    "build_story_bundle",
    "bundle_to_json",
    "compile_for_session",
    "compile_story_map",
    "layers_to_featurecollections",
    "normalize_trace",
    "plan_camera_for_bbox",
    "render_standalone_html",
    "sanitize_dict",
    "validate_track",
]
