"""Scale-aware LOD strategy (ADR-0201 M5).

zoom 驱动的多尺度表达策略纯函数。**落点是既有 spec 能力**（D10 决策）：

- 标注密度 → ``MapSpecLayerLabel.zoomBands``（ADR-0154 既有契约：
  minZoom/maxZoom/topRatio/sizeRatio，≤4 档）。
- 抽稀预算 → ``thresholds.maxFeatures``（既有导出预算通道）。
- terrain 分辨率 → raster-dem 源的 ``maxzoom`` 上限参考。
- 符号尺度 → 符号律（symbol-law）的 zoom 维度的场景级乘数。

本模块不新增 spec 字段 —— 只把"哪个 zoom 该表达多细"的**决策**收敛成
单一可测试的表，消费方按既有通道落地。
"""
from __future__ import annotations

from typing import Any, Dict, List

#: LOD 分档表（[min_zoom, max_zoom) 半开区间；末档 max_zoom 为域上限）。
#: 单调性契约：zoom 越高 → 标注比例/符号尺度/terrain 分辨率不降。
LOD_BANDS = (
    {
        "min_zoom": 3.0,
        "max_zoom": 6.0,
        "label_top_ratio": 0.3,
        "size_ratio": 0.8,
        "terrain_maxzoom": 8,
        "decimate_budget": 800,
    },
    {
        "min_zoom": 6.0,
        "max_zoom": 10.0,
        "label_top_ratio": 0.6,
        "size_ratio": 0.9,
        "terrain_maxzoom": 10,
        "decimate_budget": 2000,
    },
    {
        "min_zoom": 10.0,
        "max_zoom": 14.0,
        "label_top_ratio": 0.85,
        "size_ratio": 1.0,
        "terrain_maxzoom": 12,
        "decimate_budget": 3500,
    },
    {
        "min_zoom": 14.0,
        "max_zoom": 19.0,
        "label_top_ratio": 1.0,
        "size_ratio": 1.1,
        "terrain_maxzoom": 14,
        "decimate_budget": 5000,
    },
)

#: 全域边界（与 zoom 钳制带一致）。
_ZOOM_MIN = 3.0
_ZOOM_MAX = 18.0


def _band_for_zoom(zoom: float) -> Dict[str, Any]:
    z = max(_ZOOM_MIN, min(_ZOOM_MAX, float(zoom)))
    for band in LOD_BANDS:
        if band["min_zoom"] <= z < band["max_zoom"]:
            return band
    return LOD_BANDS[-1]


def lod_for_zoom(zoom: float) -> Dict[str, Any]:
    """zoom → LOD 策略（确定性；越界钳制到 [3, 18]）。"""
    band = _band_for_zoom(zoom)
    return {
        "label_top_ratio": band["label_top_ratio"],
        "symbol_scale": band["size_ratio"],
        "terrain_maxzoom": band["terrain_maxzoom"],
        "decimate_budget": band["decimate_budget"],
    }


def build_label_zoom_bands() -> List[Dict[str, Any]]:
    """LOD 分档 → MapSpecLayerLabel.zoomBands 投影（ADR-0154 契约形状）。

    ≤4 档；相邻档边界连续（band[i].maxZoom == band[i+1].minZoom）；sizeRatio
    乘在符号律的字号基准上（本表只给乘数 —— ADR-0154 同口径）。
    """
    return [
        {
            "minZoom": band["min_zoom"],
            "maxZoom": band["max_zoom"],
            "topRatio": band["label_top_ratio"],
            "sizeRatio": band["size_ratio"],
        }
        for band in LOD_BANDS
    ]


def terrain_maxzoom_for_zoom(zoom: float) -> int:
    """当前 zoom 允许的 terrain 源最大分辨率档（有界 DEM 流量）。"""
    return lod_for_zoom(zoom)["terrain_maxzoom"]
