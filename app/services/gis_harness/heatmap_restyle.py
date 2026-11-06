"""Recolor an existing heatmap layer without re-querying its source data."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lib.cartography.palettes import (
    NATIVE_HEATMAP_COLORS,
    build_heatmap_legend_spec,
    heatmap_paint,
)

_PALETTE_ALIASES = {
    "blue": "blues",
    "blues": "blues",
    "蓝色": "blues",
    "蓝": "blues",
    "classic": "classic",
    "magma": "magma",
    "viridis": "viridis",
    "thermal": "thermal",
}


def resolve_heatmap_palette(name: str) -> Optional[str]:
    """Map a user/model palette token onto a native heatmap family.

    Unknown tokens return ``None``. Callers must not fall back to classic:
    a failed recolor is not a successful blue map.
    """
    token = str(name or "").strip().lower()
    if not token:
        return None
    resolved = _PALETTE_ALIASES.get(token) or _PALETTE_ALIASES.get(str(name or "").strip())
    if resolved in NATIVE_HEATMAP_COLORS:
        return resolved
    if token in NATIVE_HEATMAP_COLORS:
        return token
    return None


def recolor_heatmap_layer(layer: Dict[str, Any], palette: str) -> Dict[str, Any]:
    """Return a copy of ``layer`` with heatmap paint and legend updated.

    Source id, feature data, and visibility are left alone.
    """
    updated = dict(layer)
    radius_px = 30
    heatmap = updated.get("heatmap")
    if isinstance(heatmap, dict):
        raw = heatmap.get("radius_px")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            radius_px = int(raw)
        heatmap = dict(heatmap)
        heatmap["palette"] = palette
        updated["heatmap"] = heatmap
    paint = dict(updated.get("paint") or {})
    paint.update(heatmap_paint(palette, radius_px=radius_px))
    updated["paint"] = paint
    legend = updated.get("legend_spec")
    min_val = 0.0
    max_val = 1.0
    if isinstance(legend, dict):
        if isinstance(legend.get("min"), (int, float)):
            min_val = float(legend["min"])
        if isinstance(legend.get("max"), (int, float)):
            max_val = float(legend["max"])
    updated["legend_spec"] = build_heatmap_legend_spec(palette, min_val, max_val)
    return updated


async def recolor_session_heatmaps(session_id: str, palette_name: str) -> Dict[str, Any]:
    """Apply ``palette_name`` to every heatmap layer already on the MapSpec.

    Does not read session feature payloads and does not call a data tool.
    """
    from app.lib.cancellation import checkpoint
    from app.services.mapspec_store import mapspec_store

    palette = resolve_heatmap_palette(palette_name)
    if palette is None:
        return {
            "success": False,
            "code": "unknown_heatmap_palette",
            "message": f"未知热力色带 {palette_name!r}；可用 classic/magma/viridis/thermal/blues",
        }
    checkpoint()
    spec = await mapspec_store.get_mapspec(session_id) or {}
    layers = [ly for ly in (spec.get("layers") or []) if isinstance(ly, dict)]
    heatmaps = [ly for ly in layers if str(ly.get("type") or "") == "heatmap"]
    if not heatmaps:
        return {
            "success": False,
            "code": "heatmap_layer_missing",
            "message": "当前地图没有热力图层，不能只改颜色",
        }
    updated_ids: List[str] = []
    for layer in heatmaps:
        checkpoint()
        painted = recolor_heatmap_layer(layer, palette)
        res = await mapspec_store.layer_upsert(session_id, painted)
        if not res.get("success"):
            return {
                "success": False,
                "code": "heatmap_restyle_failed",
                "message": "热力色带写入 MapSpec 失败",
                "layer_id": layer.get("id"),
            }
        updated_ids.append(str(painted.get("id") or ""))
    return {
        "success": True,
        "code": "heatmap_palette_applied",
        "palette": palette,
        "layer_ids": updated_ids,
        "requery": False,
    }
