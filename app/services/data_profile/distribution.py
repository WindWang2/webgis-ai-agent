"""Data Profile V9 —— H3 空间分布摘要（P2）。

任务书：矢量/栅格统一画像中的「空间分布摘要（H3 聚合直方图）」。

- 输入 GeoJSON features（点/线/面统一取代表坐标 —— 面取外环首点、线取
  中点附近顶点，诚实标注 ``representative_point`` 口径）；
- 聚合到 H3 cell（默认 resolution=7，≈5km 尺度）；**有界**：独立 cell 数
  超预算自动降分辨率重聚合（分辨率下限 3），仍超则截断 + ``truncated`` 旗标；
- h3 依赖缺席/失败 → ``{"available": False, "reason": ...}`` 诚实降级
  （h3 是仓库硬依赖，理论仅极端环境触发）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_RESOLUTION = 7
_MIN_RESOLUTION = 3
_MAX_FEATURES = 20000
_MAX_CELLS = 512


def _representative_point(geometry: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """geometry → (lng, lat) 代表点；空/非有限 → None。"""
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    gtype = str(geometry.get("type") or "")

    def first_pos(node: Any) -> Optional[Tuple[float, float]]:
        if not isinstance(node, (list, tuple)) or not node:
            return None
        head = node[0]
        if isinstance(head, (int, float)) and len(node) >= 2 \
                and isinstance(node[1], (int, float)):
            return (float(node[0]), float(node[1]))
        return first_pos(head)

    def _mid_any(node: Any) -> Optional[Tuple[float, float]]:
        if not isinstance(node, (list, tuple)) or not node:
            return None
        mid = node[len(node) // 2]
        got = first_pos(mid) if isinstance(mid, list) else None
        if got:
            return got
        return _mid_any(mid)

    try:
        if gtype in ("Point", "MultiPoint"):
            return first_pos(coords)
        if gtype in ("LineString", "MultiLineString"):
            return _mid_any(coords)
        if gtype in ("Polygon", "MultiPolygon"):
            return first_pos(coords)
    except (TypeError, ValueError):
        return None
    return None


def spatial_distribution(
    features: List[Dict[str, Any]],
    *,
    resolution: int = _DEFAULT_RESOLUTION,
    max_features: int = _MAX_FEATURES,
    max_cells: int = _MAX_CELLS,
) -> Dict[str, Any]:
    """features → H3 直方图 + 热点摘要（有界、纯函数）。"""
    try:
        import h3
    except Exception as exc:  # noqa: BLE001 — 诚实降级
        return {"available": False, "reason": f"h3_unavailable: {exc}"}

    skipped = 0
    res = int(resolution)
    counts: Dict[str, int] = {}
    truncated = False

    def bucket(target_res: int) -> Dict[str, int]:
        nonlocal skipped
        b: Dict[str, int] = {}
        for f in features[:max_features]:
            geometry = f.get("geometry") if isinstance(f, dict) else None
            pt = _representative_point(geometry) if isinstance(geometry, dict) else None
            if pt is None:
                skipped += 1
                continue
            try:
                cell = h3.latlng_to_cell(pt[1], pt[0], target_res)
            except (ValueError, TypeError):
                skipped += 1
                continue
            b[cell] = b.get(cell, 0) + 1
        return b

    counts = bucket(res)
    while len(counts) > max_cells and res > _MIN_RESOLUTION:
        res -= 1
        counts = bucket(res)
    if len(counts) > max_cells:
        # 分辨率到底仍超预算：保 top 计数、诚实截断
        counts = dict(sorted(counts.items(), key=lambda kv: -kv[1])[:max_cells])
        truncated = True

    total = sum(counts.values())
    if total == 0:
        return {"available": True, "resolution": res, "cell_count": 0,
                "features_scanned": 0, "features_without_position": skipped,
                "cells": {}, "top_cells": [], "hotspot_share": None,
                "coarsened": res != int(resolution), "truncated": truncated}
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
    hotspot_share = top[0][1] / total if top else None
    return {
        "available": True,
        "resolution": res,
        "cell_count": len(counts),
        "features_scanned": min(len(features), max_features),
        "features_without_position": skipped,
        "cells": dict(list(counts.items())[:max_cells]),
        "top_cells": [{"cell": c, "count": n, "share": round(n / total, 4)}
                      for c, n in top],
        "hotspot_share": round(hotspot_share, 4) if hotspot_share is not None else None,
        "coarsened": res != int(resolution),
        "truncated": truncated,
    }


__all__ = ["spatial_distribution"]
