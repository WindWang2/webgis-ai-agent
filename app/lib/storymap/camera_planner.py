"""相机规划与轨迹平滑（ADR-0196 §3）——纯函数，无 IO。

轨迹插值：中心点走 Catmull-Rom → 三次贝塞尔（局部支撑、C1 连续）；
zoom/pitch 同构样条；bearing 走最短弧。零长 leg 与单关键帧是显式奇点，
采样器直接输出重合采样，不除零；全部输出过有限性清洗。
``validate_track`` 以相邻采样最大跳变为闸，锁定"无突变"。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence

from app.lib.storymap.spec import ArcRole

# 弧角色 → 基准俯仰角（°）：宏观俯视、解剖低空倾斜（任务规格 0–60°）。
ARC_PITCH_BASE: Dict[str, float] = {
    "introduction": 18.0,
    "macro_situation": 10.0,
    "focus_dissection": 45.0,
    "dynamic_simulation": 32.0,
    "recommendation": 22.0,
}

# 弧角色 → 构图方位偏角（°）：解剖/推演给轻微斜视增强立体感。
ARC_BEARING_BIAS: Dict[str, float] = {
    "focus_dissection": 15.0,
    "dynamic_simulation": -20.0,
}

ZOOM_MIN, ZOOM_MAX = 3.0, 18.0
PITCH_MAX = 60.0
# 跨度收缩阈值（°）：极小 bbox 追加俯仰强调微观；超大 bbox 压平为平面叙事。
_PITCH_MICRO_SPAN = 0.05
_PITCH_MICRO_BOOST = 10.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _normalize_bearing(b: float) -> float:
    return ((b + 180.0) % 360.0) - 180.0


def _shortest_bearing_delta(b1: float, b2: float) -> float:
    """bearing 最短弧 delta ∈ (−180, 180]：跨 ±180° 不产生大回环。"""
    return ((b2 - b1 + 540.0) % 360.0) - 180.0


def plan_camera_for_bbox(
    bbox: Sequence[float],
    arc_role: ArcRole | str,
    *,
    aspect: float = 16.0 / 9.0,
) -> Dict[str, object]:
    """图层要素包围盒 → 该章最佳观察位姿。

    zoom 取经纬张角口径（log2(360/lon_span)+1）——故事镜头以经纬张角为准，
    不做 cos 修正（极区同跨度不虚高 zoom）；pitch 由弧角色基准 + 跨度收缩
    微调，封顶 60°。
    """
    w, s, e, n = (float(v) for v in bbox)
    cx, cy = (w + e) / 2.0, (s + n) / 2.0
    lon_span = max(abs(e - w), 1e-6)
    lat_span = max(abs(n - s), 1e-6)
    span = max(lon_span, lat_span * 0.75)

    zoom = _clamp(math.log2(360.0 / lon_span) + 1.0, ZOOM_MIN, ZOOM_MAX)
    base = ARC_PITCH_BASE.get(str(arc_role), 20.0)
    pitch = base + (_PITCH_MICRO_BOOST if span < _PITCH_MICRO_SPAN else 0.0)
    pitch = _clamp(pitch, 0.0, PITCH_MAX)
    bearing = _normalize_bearing(ARC_BEARING_BIAS.get(str(arc_role), 0.0))
    return {
        "center": [round(cx, 6), round(cy, 6)],
        "zoom": round(zoom, 4),
        "pitch": round(pitch, 3),
        "bearing": bearing,
        "easing": "ease_in_out",
        "aspect": aspect,
    }


def cubic_bezier_ease(
    x: float, p1: Sequence[float] = (0.25, 0.1), p2: Sequence[float] = (0.25, 1.0)
) -> float:
    """CSS 风格三次贝塞尔缓动：给定 x ∈ [0,1] 解参数 t 求 y。

    Newton–Raphson 主解，8 轮内不收敛则二分兜底——两条路径都保证有限返回。
    """
    x = _clamp(float(x), 0.0, 1.0)
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])

    def bx(t: float) -> float:
        return 3.0 * (1 - t) ** 2 * t * x1 + 3.0 * (1 - t) * t * t * x2 + t * t * t

    def by(t: float) -> float:
        return 3.0 * (1 - t) ** 2 * t * y1 + 3.0 * (1 - t) * t * t * y2 + t * t * t

    def dbx(t: float) -> float:
        return (3.0 * (1 - t) ** 2 * x1 + 6.0 * (1 - t) * t * (x2 - x1) +
                3.0 * t * t * (1.0 - x2))

    t = x
    for _ in range(8):
        err = bx(t) - x
        if abs(err) < 1e-7:
            return by(t)
        d = dbx(t)
        if abs(d) < 1e-9:
            break
        t = _clamp(t - err / d, 0.0, 1.0)
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if bx(mid) < x:
            lo = mid
        else:
            hi = mid
    return by((lo + hi) / 2.0)


@dataclass
class CameraSample:
    """轨迹上的一个采样点（t 全局单调 ∈ [0,1]）。"""

    t: float
    center: List[float]
    zoom: float
    pitch: float
    bearing: float


def _cr_bezier(p0: float, p1: float, p2: float, p3: float) -> tuple:
    """Catmull-Rom 控制段转三次贝塞尔控制点（端点复制补 pad 后 C1 连续）。"""
    return (
        p1,
        p1 + (p2 - p0) / 6.0,
        p2 - (p3 - p1) / 6.0,
        p2,
    )


def _bez(cs: tuple, s: float) -> float:
    u = 1.0 - s
    return (u * u * u * cs[0] + 3.0 * u * u * s * cs[1] +
            3.0 * u * s * s * cs[2] + s * s * s * cs[3])


def _finite(v: float, fallback: float) -> float:
    return v if math.isfinite(v) else fallback


def build_camera_track(
    keyframes: Sequence,
    *,
    samples_per_leg: int = 16,
) -> List[CameraSample]:
    """关键帧序列 → 平滑轨迹采样。

    输入顺序即轨迹顺序（调用方按叙事弧序给出）。每 leg 用 Catmull-Rom→
    贝塞尔插 center/zoom/pitch，bearing 走最短弧缓动；输出 t 全局单调。
    """
    kfs = list(keyframes)
    if not kfs:
        return []
    if len(kfs) == 1:
        k0 = kfs[0]
        return [CameraSample(0.0, list(k0.center), float(k0.zoom),
                             float(k0.pitch), float(k0.bearing))]

    samples_per_leg = max(1, int(samples_per_leg))
    n_legs = len(kfs) - 1
    out: List[CameraSample] = []
    ease = (0.25, 0.1, 0.25, 1.0)

    for i in range(n_legs):
        a, b = kfs[i], kfs[i + 1]
        pa0 = kfs[max(i - 1, 0)]
        pb3 = kfs[min(i + 2, len(kfs) - 1)]
        lng_cs = _cr_bezier(pa0.center[0], a.center[0], b.center[0], pb3.center[0])
        lat_cs = _cr_bezier(pa0.center[1], a.center[1], b.center[1], pb3.center[1])
        zoom_cs = _cr_bezier(float(pa0.zoom), float(a.zoom), float(b.zoom), float(pb3.zoom))
        pitch_cs = _cr_bezier(float(pa0.pitch), float(a.pitch), float(b.pitch), float(pb3.pitch))
        b1, b2 = float(a.bearing), float(b.bearing)
        b_delta = _shortest_bearing_delta(b1, b2)

        for k in range(samples_per_leg):
            s = k / samples_per_leg
            ce = cubic_bezier_ease(s, ease[:2], ease[2:])
            center = [
                _finite(_bez(lng_cs, s), a.center[0]),
                _finite(_bez(lat_cs, s), a.center[1]),
            ]
            out.append(CameraSample(
                t=(i + s) / n_legs,
                center=center,
                zoom=_finite(_bez(zoom_cs, ce), float(a.zoom)),
                pitch=_finite(_bez(pitch_cs, ce), float(a.pitch)),
                bearing=_normalize_bearing(b1 + b_delta * ce),
            ))

    last = kfs[-1]
    out.append(CameraSample(
        t=1.0,
        center=[float(last.center[0]), float(last.center[1])],
        zoom=float(last.zoom),
        pitch=float(last.pitch),
        bearing=_normalize_bearing(float(last.bearing)),
    ))
    return out


def validate_track(
    track: Sequence[CameraSample],
    *,
    max_center_jump_deg: float = 1.0,
    max_zoom_jump: float = 1.0,
    max_pitch_jump: float = 15.0,
    max_bearing_jump: float = 30.0,
) -> List[str]:
    """连续性闸：相邻采样逐维跳变超阈即记违规（空轨迹合法）。"""
    violations: List[str] = []
    for i in range(1, len(track)):
        prev, cur = track[i - 1], track[i]
        center_jump = math.hypot(cur.center[0] - prev.center[0],
                                 cur.center[1] - prev.center[1])
        if center_jump > max_center_jump_deg:
            violations.append(
                f"sample[{i}] center jump {center_jump:.3f}° > {max_center_jump_deg}°"
            )
        for dim, val, limit, label in (
            ("zoom", abs(cur.zoom - prev.zoom), max_zoom_jump, "zoom"),
            ("pitch", abs(cur.pitch - prev.pitch), max_pitch_jump, "pitch"),
            ("bearing", abs(_shortest_bearing_delta(prev.bearing, cur.bearing)),
             max_bearing_jump, "bearing"),
        ):
            if val > limit:
                violations.append(f"sample[{i}] {label} jump {val:.3f} > {limit}")
    return violations
