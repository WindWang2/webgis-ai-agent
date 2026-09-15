"""StoryMap 打包服务壳（ADR-0196 §5.1）——图层组装后委托 lib 打包。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from app.lib.storymap.export_packager import (  # noqa: F401  (re-export)
    BUNDLE_SCHEMA_VERSION,
    build_story_bundle,
    bundle_to_json,
    render_standalone_html,
    sanitize_dict,
)


def layers_to_featurecollections(
    rows: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """图层行（{name?, geojson?}）→ FeatureCollection 列表（跳过无几何行）。"""
    fcs: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        geojson = row.get("geojson")
        if isinstance(geojson, Mapping) and geojson.get("type") == "FeatureCollection":
            # 行名（DB 权威）覆盖 FC 自带 name；无行名时保留 FC 原值
            fc = dict(geojson)
            if row.get("name"):
                fc["name"] = str(row["name"])
            fcs.append(fc)
    return fcs


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "build_story_bundle",
    "bundle_to_json",
    "layers_to_featurecollections",
    "render_standalone_html",
    "sanitize_dict",
]
