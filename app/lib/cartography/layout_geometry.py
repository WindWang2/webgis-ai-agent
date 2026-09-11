"""Layout Constraint Geometry V7 — 槽位求解器之上的矩形几何层（Goal 08 Phase D）.

``layout_solver`` V3 是**计数语义**（zone 容量/堆叠预算，无像素假设 ——
像素真值在前端 resolve-layout）。本模块在其输出之上提供**确定性矩形几何**：

- 画布规格（px + DPI + page profile）与安全区（消费 schema
  ``layout.margins``；print 档可加出血 bleed）；
- zone → 矩形分配（6 槽 + none 全画布；带宽高比适配）；
- 浮动组件矩形：用户 x/y 优先（user-wins），越界钳制进安全区，
  floating-floating 重叠**确定性级联推移**（右移 → 换行 → 下移）；
- 结构化几何报告（QA 的 component_outside_canvas / floating overlap
  检查与 AUTO_SAFE repair 建议共用同一真值）。

设计红线：纯函数、无随机、无 IO、单遍不回溯（避免过度复杂求解器）；
像素只在给定 CanvasSpec 时参与计算；同输入永远同输出。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

#: 浮动面板间最小间距（px）。
FLOATING_GAP_PX = 12.0

#: 浮动组件缺省尺寸（px）—— placement 未给 w/h 时（与前端 MIN 160×120
#: 同下限，缺省取更大可用面板，导出语义友好）。
DEFAULT_FLOATING_SIZE = (320.0, 240.0)

#: zone 几何近似常数（画布短边比例；近似前端堆叠行为，非像素真值）。
_TOP_BAND_RATIO = 0.16
_BOTTOM_BAND_RATIO = 0.14
_SIDE_COLUMN_RATIO = 0.30

#: 常规屏幕安全边距缺省（px；margin 字段缺省时的兜底）。
_DEFAULT_SAFE_PX = 8.0

_MM_PER_INCH = 25.4


class Rect(BaseModel):
    """左上原点矩形（px；与前端 placement 坐标同系）。"""

    x: float
    y: float
    w: float
    h: float

    def intersects(self, other: "Rect", *, gap: float = 0.0) -> bool:
        return not (
            self.x + self.w + gap <= other.x
            or other.x + other.w + gap <= self.x
            or self.y + self.h + gap <= other.y
            or other.y + other.h + gap <= self.y
        )

    def clamped_into(self, bounds: "Rect") -> "Rect":
        w = min(self.w, bounds.w)
        h = min(self.h, bounds.h)
        x = min(max(self.x, bounds.x), bounds.x + bounds.w - w)
        y = min(max(self.y, bounds.y), bounds.y + bounds.h - h)
        return Rect(x=x, y=y, w=w, h=h)

    def clamped_position(self, bounds: "Rect") -> "Rect":
        """只钳位置、保持用户尺寸（尺寸诚实 —— 超界由调用方披露）。"""
        x = min(max(self.x, bounds.x),
                max(bounds.x, bounds.x + bounds.w - self.w))
        y = min(max(self.y, bounds.y),
                max(bounds.y, bounds.y + bounds.h - self.h))
        return Rect(x=x, y=y, w=self.w, h=self.h)

    def contains_rect(self, other: "Rect") -> bool:
        return (
            other.x >= self.x - 1e-9
            and other.y >= self.y - 1e-9
            and other.x + other.w <= self.x + self.w + 1e-9
            and other.y + other.h <= self.y + self.h + 1e-9
        )

    def fit_aspect(self, aspect: float) -> "Rect":
        """在矩形内取中心最大内接矩形（w/h = aspect；aspect<=0 透传）。"""
        if aspect <= 0 or self.w <= 0 or self.h <= 0:
            return self
        if self.w / self.h > aspect:
            w = self.h * aspect
            return Rect(x=self.x + (self.w - w) / 2, y=self.y, w=w, h=self.h)
        h = self.w / aspect
        return Rect(x=self.x, y=self.y + (self.h - h) / 2, w=self.w, h=h)


class CanvasSpec(BaseModel):
    """画布规格：px 尺寸 + DPI + page profile（layout_solver 档名同表）。"""

    width: float
    height: float
    dpi: float = 96.0
    page_profile: str = "viewport"

    @property
    def short_edge(self) -> float:
        return min(self.width, self.height)


class SafeArea(BaseModel):
    """安全区内缩（px）。"""

    left: float = _DEFAULT_SAFE_PX
    top: float = _DEFAULT_SAFE_PX
    right: float = _DEFAULT_SAFE_PX
    bottom: float = _DEFAULT_SAFE_PX

    def rect(self, canvas: CanvasSpec) -> Rect:
        return Rect(
            x=self.left,
            y=self.top,
            w=max(0.0, canvas.width - self.left - self.right),
            h=max(0.0, canvas.height - self.top - self.bottom),
        )


class GeometricPlacement(BaseModel):
    component_id: str
    rect: Rect
    moved: bool = False
    reason: str = ""


class GeometryAdjustment(BaseModel):
    """一次确定性调整（repair 建议与 user-wins 守卫消费）。"""

    component_id: str
    kind: str                     # clamped_into_safe_area | overlap_cascade
    from_rect: Optional[Rect] = None
    to_rect: Rect


class GeometryIssue(BaseModel):
    code: str                      # outside_canvas | unresolvable_overlap
    severity: str = "warning"
    message: str
    component_ids: List[str] = Field(default_factory=list)


class GeometryReport(BaseModel):
    placements: List[GeometricPlacement] = Field(default_factory=list)
    adjustments: List[GeometryAdjustment] = Field(default_factory=list)
    issues: List[GeometryIssue] = Field(default_factory=list)

    def rect_for(self, component_id: str) -> Optional[Rect]:
        for p in self.placements:
            if p.component_id == component_id:
                return p.rect
        return None

    def to_bounded_dict(self) -> Dict[str, Any]:
        def _r(rect: Optional[Rect]) -> Optional[Dict[str, float]]:
            if rect is None:
                return None
            return {"x": round(rect.x, 1), "y": round(rect.y, 1),
                    "w": round(rect.w, 1), "h": round(rect.h, 1)}

        return {
            "placements": [
                {"id": p.component_id[:48], "rect": _r(p.rect),
                 "moved": p.moved}
                for p in self.placements[:24]
            ],
            "adjustments": [
                {"id": a.component_id[:48], "kind": a.kind,
                 "to": _r(a.to_rect)}
                for a in self.adjustments[:16]
            ],
            "issues": [i.model_dump() for i in self.issues[:8]],
        }


# ── 安全区 ───────────────────────────────────────────────────────────────


def safe_area_for(
    canvas: CanvasSpec,
    margins: Optional[Dict[str, Any]] = None,
    *,
    bleed_mm: float = 0.0,
) -> SafeArea:
    """schema ``layout.margins``（top/right/bottom/left）→ 安全区。

    语义约定：margins 数值以 **px** 为准（前端 placement 坐标同系）；
    非有限值/负值逐键忽略（脏值披露由 schema 层负责）。print 档可加
    ``bleed_mm``（毫米 → px via dpi），四边均匀外扩内缩。
    """
    area = SafeArea()
    if isinstance(margins, dict):
        for side in ("left", "top", "right", "bottom"):
            val = margins.get(side)
            if isinstance(val, (int, float)) and val == val and val >= 0:
                setattr(area, side, float(val))
    if bleed_mm > 0 and canvas.dpi > 0:
        bleed_px = bleed_mm / _MM_PER_INCH * canvas.dpi
        area.left += bleed_px
        area.top += bleed_px
        area.right += bleed_px
        area.bottom += bleed_px
    # 安全区不得吞掉画布（极端 margins 逐边钳到短边一半）
    cap = canvas.short_edge / 2
    for side in ("left", "top", "right", "bottom"):
        if getattr(area, side) > cap:
            setattr(area, side, cap)
    return area


# ── zone 矩形分配 ────────────────────────────────────────────────────────


def zone_rects(canvas: CanvasSpec, safe: SafeArea) -> Dict[str, Rect]:
    """6 槽位矩形（确定性几何近似；``none`` 槽 = 安全区全幅）。

    拓扑：顶带（top）/底带（bottom）横贯安全区，高按画布短边比例；
    左右列占带内宽度比例，中带归 center。与 layout_solver 的计数语义
    正交 —— 本函数只给几何真值，不改槽位归属。
    """
    inner = safe.rect(canvas)
    top_h = min(inner.h, max(canvas.short_edge * _TOP_BAND_RATIO, 48.0))
    bottom_h = min(inner.h, max(canvas.short_edge * _BOTTOM_BAND_RATIO, 48.0))
    inner.y + top_h
    max(0.0, inner.h - top_h - bottom_h)

    def _columns(y: float, h: float) -> Dict[str, Rect]:
        col_w = inner.w * _SIDE_COLUMN_RATIO
        center_x = inner.x + col_w
        center_w = max(0.0, inner.w - 2 * col_w)
        return {
            "left": Rect(x=inner.x, y=y, w=col_w, h=h),
            "center": Rect(x=center_x, y=y, w=center_w, h=h),
            "right": Rect(x=inner.x + inner.w - col_w, y=y, w=col_w, h=h),
        }

    top = _columns(inner.y, top_h)
    bottom = _columns(inner.y + inner.h - bottom_h, bottom_h)
    return {
        "top-left": top["left"],
        "top-center": top["center"],
        "top-right": top["right"],
        "bottom-left": bottom["left"],
        "bottom-center": bottom["center"],
        "bottom-right": bottom["right"],
        "none": inner,
    }


# ── 浮动矩形 ─────────────────────────────────────────────────────────────


def _floating_rect_of(component: Dict[str, Any]) -> Optional[Rect]:
    placement = component.get("placement")
    if not isinstance(placement, dict) or placement.get("mode") != "floating":
        return None
    x, y = placement.get("x"), placement.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    w = placement.get("width")
    h = placement.get("height")
    w = float(w) if isinstance(w, (int, float)) and w > 0 else DEFAULT_FLOATING_SIZE[0]
    h = float(h) if isinstance(h, (int, float)) and h > 0 else DEFAULT_FLOATING_SIZE[1]
    return Rect(x=float(x), y=float(y), w=w, h=h)


def _z_of(component: Dict[str, Any]) -> float:
    placement = component.get("placement")
    if isinstance(placement, dict):
        z = placement.get("zIndex")
        if isinstance(z, (int, float)):
            return float(z)
    return 40.0


def resolve_floating_rects(
    components: List[Dict[str, Any]],
    canvas: CanvasSpec,
    safe: SafeArea,
) -> GeometryReport:
    """浮动组件确定性布放：user-wins 钳制 + 重叠级联。

    算法（单遍、无随机）：
    1. 输入序保持 spec 顺序；处理序按 (zIndex, id) 升序（低层先放，
       高层被推走 —— 与视觉遮挡语义一致）；
    2. 用户矩形钳进安全区（moved=clamped_into_safe_area）；
    3. 与已放矩形重叠（含 GAP）→ 右移到前一矩形右缘+GAP；越安全区右缘
       → 换行（x 回左缘，y 到最高矩形下缘+GAP）；仍放不下 → 底部追加行；
    4. 级联仍越界（矩形自身大于安全区）→ 钳制保留 + unresolvable_overlap。
    """
    report = GeometryReport()
    bounds = safe.rect(canvas)
    floating = []
    for component in components:
        if not isinstance(component, dict):
            continue
        rect = _floating_rect_of(component)
        if rect is None:
            continue
        floating.append((str(component.get("id") or ""), rect))

    # 处理序按 (zIndex asc, id asc)（低层先放，高层被推走 —— 视觉遮挡语义）
    id_to_z = {}
    for component in components:
        if isinstance(component, dict) and isinstance(component.get("id"), str):
            id_to_z[component["id"]] = _z_of(component)
    floating.sort(key=lambda t: (id_to_z.get(t[0], 40.0), t[0]))

    placed: List[Tuple[str, Rect]] = []
    for cid, rect in floating:
        original = rect.model_copy()
        # user-wins：只钳位置，不缩尺寸（面板大于安全区 → 不可解披露）
        current = rect.clamped_position(bounds)
        moved = False
        reason = ""
        if current != original:
            moved = True
            reason = "clamped_into_safe_area"
        # 级联：有限轮（每轮把矩形推到首个重叠者右侧/下方）。wrap 会把
        # x 重置进高遮挡列 —— 同一遮挡者可能被再次命中，轮数上界不能保证
        # 收敛；循环后**必须复查残余重叠**，仍在即按不可解披露（不允许
        # 静默输出重叠位置）。
        for _ in range(len(placed) ** 2 + 1):
            hit = next(
                (prev for _, prev in placed
                 if current.intersects(prev, gap=FLOATING_GAP_PX)),
                None)
            if hit is None:
                break
            nudged_x = hit.x + hit.w + FLOATING_GAP_PX
            if nudged_x + current.w <= bounds.x + bounds.w + 1e-9:
                current = Rect(x=nudged_x, y=current.y, w=current.w, h=current.h)
            else:
                next_y = hit.y + hit.h + FLOATING_GAP_PX
                current = Rect(x=bounds.x, y=next_y, w=current.w, h=current.h)
            moved = True
            reason = "overlap_cascade"
        residual = any(
            current.intersects(prev, gap=FLOATING_GAP_PX) for _, prev in placed)
        if residual or not bounds.contains_rect(current):
            current = current.clamped_into(bounds)
            report.issues.append(GeometryIssue(
                code="unresolvable_overlap", severity="warning",
                message=f"浮动组件 {cid} 大于安全区或级联后仍越界/重叠，已钳制保留",
                component_ids=[cid]))
            moved = True
            reason = reason or "clamped_into_safe_area"
        placed.append((cid, current))
        if moved:
            report.adjustments.append(GeometryAdjustment(
                component_id=cid, kind=reason or "clamped_into_safe_area",
                from_rect=original, to_rect=current))
        report.placements.append(GeometricPlacement(
            component_id=cid, rect=current, moved=moved, reason=reason))
    return report


# ── 边界检查（QA 消费）────────────────────────────────────────────────────


def check_component_bounds(
    components: List[Dict[str, Any]],
    canvas: CanvasSpec,
    safe: SafeArea,
) -> List[GeometryIssue]:
    """几何越界检查：floating 矩形超出画布（outside_canvas）。

    只检查 mode=floating 且带显式坐标的组件 —— anchor 槽组件由前端
    resolve-layout 负责像素，永远在画布内。
    """
    issues: List[GeometryIssue] = []
    canvas_rect = Rect(x=0.0, y=0.0, w=canvas.width, h=canvas.height)
    for component in components:
        if not isinstance(component, dict):
            continue
        rect = _floating_rect_of(component)
        if rect is None:
            continue
        cid = str(component.get("id") or "")
        if not canvas_rect.intersects(rect):
            issues.append(GeometryIssue(
                code="outside_canvas", severity="error",
                message=f"浮动组件 {cid} 完全在画布之外",
                component_ids=[cid]))
        elif not canvas_rect.contains_rect(rect) or not safe.rect(canvas).contains_rect(rect):
            issues.append(GeometryIssue(
                code="outside_safe_area", severity="warning",
                message=f"浮动组件 {cid} 超出安全区/画布边界",
                component_ids=[cid]))
    return issues


def layout_geometry_report(
    spec: Optional[Dict[str, Any]],
    canvas: Optional[CanvasSpec] = None,
    *,
    bleed_mm: float = 0.0,
) -> GeometryReport:
    """MapSpec → 完整几何报告（resolve_floating_rects + bounds 检查）。

    canvas 缺省时按 viewport 档给 1280×720 兜底（调用方有真值时必须传）。
    """
    components = []
    layout = (spec or {}).get("layout")
    if isinstance(layout, dict) and isinstance(layout.get("components"), list):
        components = [c for c in layout["components"] if isinstance(c, dict)]
    canvas = canvas or CanvasSpec(width=1280, height=720)
    safe = safe_area_for(
        canvas,
        layout.get("margins") if isinstance(layout, dict) else None,
        bleed_mm=bleed_mm)
    report = resolve_floating_rects(components, canvas, safe)
    report.issues.extend(check_component_bounds(components, canvas, safe))
    return report


__all__ = [
    "Rect",
    "CanvasSpec",
    "SafeArea",
    "GeometricPlacement",
    "GeometryAdjustment",
    "GeometryIssue",
    "GeometryReport",
    "safe_area_for",
    "zone_rects",
    "resolve_floating_rects",
    "check_component_bounds",
    "layout_geometry_report",
    "FLOATING_GAP_PX",
]
