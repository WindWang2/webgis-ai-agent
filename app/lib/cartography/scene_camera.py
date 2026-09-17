"""Multiscale scene camera planner (ADR-0201 M5).

产品级相机规划纯函数：overview（全域概览）/ detail（局部细察）/
compare（对比主副机位）三档。与 StoryMap 叙事相机（章节弧线、情绪
档位）是不同关注域 —— 本模块只服务数据产品的读图相机。

Web Mercator 数学独立实现（fit-bounds zoom 估算），不依赖前端；
reduced-motion 是可访问性约束（transition_ms = 0），不是画质降级。

确定性：同输入必同输出；无 I/O、无全局状态。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Literal

#: 相机角色词表。
CAMERA_ROLE = ("overview", "detail", "compare")

#: zoom 钳制带（与 StoryMap 相同的 Web Mercator 合法域）。
_ZOOM_MIN, _ZOOM_MAX = 3.0, 18.0
#: 产品级 pitch 上限（MapSpec 硬上限 85；与 StoryMap 叙事上限 60 对齐 ——
#: 更陡的视角把面积要素压成不可辨的细条）。
_PITCH_MAX = 60.0

#: 角色档位：概览低角保持全局结构感；细察给透视增强层次。
_ROLE_PITCH = {"overview": 18.0, "detail": 40.0, "compare": 25.0}
#: 对比机位方位差（同高异向 —— 不引入高度差干扰判读）。
_COMPARE_BEARING_OFFSET = 30.0
#: 3D 场景的 pitch 增强档（仅 scene_mode="3d" 时应用）。
_SCENE_3D_PITCH = {"overview": 25.0, "detail": 50.0, "compare": 35.0}

#: reduced-motion 过渡时长（0 = 无动画，直接跳转）。
_REDUCED_TRANSITION_MS = 0
#: 默认过渡时长。
_DEFAULT_TRANSITION_MS = 800


def _bbox_normalized(bbox) -> tuple:
    """bbox 规范化：经度取最短弧表示，纬度保守钳制。返回 (w, s, e, n)。"""
    w, s, e, n = (float(v) for v in bbox)
    if s > n:
        s, n = n, s
    # 经度：跨 ±180 的表达（w > e）保持原样 —— 由 _lng_span_short_arc 处理。
    if w > 180.0:
        w -= 360.0
    if e > 180.0:
        e -= 360.0
    return w, s, e, n


def _lng_span_short_arc(w: float, e: float) -> float:
    """东西跨度（最短弧，0..360）。w > e 表示跨 ±180。"""
    raw = e - w
    if raw < 0:
        raw += 360.0
    return raw


def _lng_center_short_arc(w: float, e: float) -> float:
    """最短弧经度中点（跨 ±180 时在 180 一侧，不翻转到 0 附近）。"""
    if w <= e:
        return (w + e) / 2.0
    mid = (w + e + 360.0) / 2.0
    if mid > 180.0:
        mid -= 360.0
    return mid


def _lat_center_clamped(s: float, n: float) -> float:
    """纬度中点，钳制在 Mercator 可表达带（±85；极地平投影畸变）。"""
    return max(-85.0, min(85.0, (s + n) / 2.0))


def _fit_zoom(lon_span_deg: float, lat_span_deg: float, width_px: float = 1024.0, height_px: float = 768.0) -> float:
    """fit-bounds zoom 估算（Web Mercator，1024×768 视口基准）。

    以东西向（度）与南北向（Mercator y）较大需求者适配；纬度跨度按
    赤道基准估 y 跨度 —— 高纬地形图幅偏小属保守方向（zoom 偏低 =
    看得更全），不追求像素级精确（规划档位，不是最终相机）。
    """
    lon_rad = max(math.radians(max(lon_span_deg, 1e-4)), 1e-9)
    # Mercator y 跨度（以赤道为基准的保守估计）
    half = max(lat_span_deg, 1e-4) / 2.0
    s = max(-85.0, min(85.0, -half))
    n = max(-85.0, min(85.0, half))

    def _merc_y(lat_deg: float) -> float:
        lat = math.radians(lat_deg)
        return math.log(math.tan(math.pi / 4 + lat / 2))

    lat_rad = max(abs(_merc_y(n) - _merc_y(s)), 1e-9)

    world_px = 512.0  # MapLibre tileSize=512 的世界像素基准
    zoom_x = math.log2(width_px * 2 * math.pi / (lon_rad * world_px))
    zoom_y = math.log2(height_px * 2 * math.pi / (lat_rad * world_px))
    return min(zoom_x, zoom_y)


def plan_scene_camera(
    bbox,
    *,
    role: Literal["overview", "detail", "compare"] = "overview",
    slot: Literal["primary", "secondary"] = "primary",
    scene_mode: Literal["2d", "2.5d", "3d"] = "2d",
    motion: Literal["full", "reduced"] = "full",
) -> Dict[str, Any]:
    """bbox → 场景相机档（center/zoom/pitch/bearing/transition_ms）。

    - overview：全域，低角（结构感优先，避免透视遮挡全局判断）。
    - detail：局部，中高角透视（scene_mode=3d 时更高一档）。
    - compare：主副机位同高异向（bearing 差 30°），高度差会干扰对比判读。
    """
    if role not in CAMERA_ROLE:
        raise ValueError(f"unknown camera role: {role!r}; expected one of {CAMERA_ROLE}")

    w, s, e, n = _bbox_normalized(bbox)
    lon_span = _lng_span_short_arc(w, e)
    lat_span = max(abs(n - s), 1e-4)
    center = [_lng_center_short_arc(w, e), _lat_center_clamped(s, n)]

    # 角色缩放档：overview 全域原生跨度；detail 收紧 60%（聚焦读数）；
    # compare 与 overview 同域（对比的是表达，不是范围）。
    if role == "detail":
        lon_span *= 0.6
        lat_span *= 0.6

    zoom = _fit_zoom(lon_span, lat_span)
    zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, zoom))

    pitch = (_SCENE_3D_PITCH if scene_mode == "3d" else _ROLE_PITCH)[role]
    if scene_mode == "2d":
        pitch = 0.0

    bearing = 0.0
    if role == "compare":
        bearing = 0.0 if slot == "primary" else _COMPARE_BEARING_OFFSET

    transition_ms = _REDUCED_TRANSITION_MS if motion == "reduced" else _DEFAULT_TRANSITION_MS

    return {
        "center": center,
        "zoom": round(zoom, 2),
        "pitch": round(pitch, 1),
        "bearing": round(bearing, 1),
        "transition_ms": transition_ms,
    }
