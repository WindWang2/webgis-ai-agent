"""V7（Goal 08 Phase D）布局几何层契约测试.

覆盖：安全区（margins 消费/bleed/极端钳制）、zone 矩形分配、浮动矩形
确定性级联（user-wins 钳制/重叠推移/换行/不可解披露）、宽高比适配、
越界检查、完整报告与有界载荷。
"""

from app.lib.cartography.layout_geometry import (
    DEFAULT_FLOATING_SIZE,
    FLOATING_GAP_PX,
    CanvasSpec,
    Rect,
    check_component_bounds,
    layout_geometry_report,
    resolve_floating_rects,
    safe_area_for,
    zone_rects,
)


def _canvas(**kw) -> CanvasSpec:
    kw.setdefault("width", 1280)
    kw.setdefault("height", 720)
    return CanvasSpec(**kw)


def _floating(cid, x, y, w=None, h=None, z=40):
    placement = {"mode": "floating", "x": x, "y": y, "zIndex": z}
    if w is not None:
        placement["width"] = w
    if h is not None:
        placement["height"] = h
    return {"id": cid, "type": "chart_panel", "placement": placement}


class TestRectAlgebra:
    def test_intersects_with_gap(self):
        a = Rect(x=0, y=0, w=100, h=100)
        b = Rect(x=100 + FLOATING_GAP_PX - 1, y=0, w=50, h=50)
        assert a.intersects(b, gap=FLOATING_GAP_PX)
        c = Rect(x=100 + FLOATING_GAP_PX + 0.5, y=0, w=50, h=50)
        assert not a.intersects(c, gap=FLOATING_GAP_PX)

    def test_clamped_into(self):
        r = Rect(x=-50, y=2000, w=300, h=200)
        out = r.clamped_into(Rect(x=0, y=0, w=1280, h=720))
        assert (out.x, out.y) == (0, 720 - 200)
        # 矩形大于边界 → 收缩到边界
        big = Rect(x=0, y=0, w=5000, h=5000)
        out_big = big.clamped_into(Rect(x=10, y=10, w=100, h=80))
        assert (out_big.w, out_big.h) == (100, 80)

    def test_fit_aspect(self):
        r = Rect(x=0, y=0, w=200, h=100)
        wide = r.fit_aspect(2.0)
        assert wide.h == 100 and wide.w == 200
        tall = r.fit_aspect(1.0)
        assert tall.w == 100 and tall.h == 100 and tall.x == 50
        assert r.fit_aspect(0) == r


class TestSafeArea:
    def test_margins_px(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, {"top": 20, "right": 30, "bottom": 40, "left": 10})
        assert (safe.left, safe.top, safe.right, safe.bottom) == (10, 20, 30, 40)
        rect = safe.rect(canvas)
        assert rect.w == 1280 - 10 - 30 and rect.h == 720 - 20 - 40

    def test_dirty_margins_ignored(self):
        safe = safe_area_for(_canvas(), {"top": "20", "right": -5, "bottom": None, "left": float("nan")})
        assert safe.top == 8.0 and safe.right == 8.0

    def test_bleed_mm(self):
        safe = safe_area_for(_canvas(dpi=300), None, bleed_mm=3.0)
        expected = 3.0 / 25.4 * 300
        assert abs(safe.left - (8.0 + expected)) < 1e-6

    def test_extreme_margins_capped_at_half_short_edge(self):
        safe = safe_area_for(_canvas(), {"top": 10_000, "left": 10_000})
        assert safe.top == 360.0 and safe.left == 360.0


class TestZoneRects:
    def test_six_zones_plus_canvas(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        zones = zone_rects(canvas, safe)
        assert set(zones) == {
            "top-left", "top-center", "top-right",
            "bottom-left", "bottom-center", "bottom-right", "none"}
        inner = safe.rect(canvas)
        assert zones["none"] == inner
        # 顶带在底带之上
        assert zones["top-left"].y + zones["top-left"].h <= zones["bottom-left"].y
        # 中列在左右列之间
        assert zones["top-center"].x >= zones["top-left"].x + zones["top-left"].w - 1e-9

    def test_deterministic(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, {"top": 10})
        assert zone_rects(canvas, safe) == zone_rects(canvas, safe)


class TestFloatingRects:
    def test_user_rect_kept_when_clean(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        report = resolve_floating_rects(
            [_floating("c1", 500, 300, 320, 240)], canvas, safe)
        placement = report.rect_for("c1")
        assert placement == Rect(x=500, y=300, w=320, h=240)
        assert report.adjustments == []

    def test_out_of_bounds_clamped(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        report = resolve_floating_rects(
            [_floating("c1", 2000, -100, 320, 240)], canvas, safe)
        rect = report.rect_for("c1")
        assert rect.x + rect.w <= 1280 - 8
        assert rect.y == 8
        assert report.adjustments[0].kind == "clamped_into_safe_area"

    def test_overlap_cascade_moves_right(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        report = resolve_floating_rects([
            _floating("a", 100, 100, 300, 200, z=10),
            _floating("b", 100 + 150, 100 + 50, 300, 200, z=20),
        ], canvas, safe)
        a = report.rect_for("a")
        b = report.rect_for("b")
        assert not a.intersects(b, gap=FLOATING_GAP_PX)
        assert b.x == a.x + a.w + FLOATING_GAP_PX
        assert report.adjustments[-1].kind == "overlap_cascade"

    def test_overlap_cascade_wraps_to_next_row(self):
        canvas = _canvas(width=800, height=600)
        safe = safe_area_for(canvas, None)
        report = resolve_floating_rects([
            _floating("a", 700, 100, 90, 200, z=10),   # 右缘贴安全区
            _floating("b", 650, 100, 200, 100, z=20),  # 与 a 重叠 → 右移放不下 → 换行
        ], canvas, safe)
        b = report.rect_for("b")
        assert b.x == 8  # 回到安全区左缘
        assert b.y >= 100 + 200 + FLOATING_GAP_PX - 1e-6

    def test_z_order_processing_low_first(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        # 高 z 的 c 在前（spec 序），但处理序按 z：先 low 后 high → high 被推走
        report = resolve_floating_rects([
            _floating("high", 100, 100, 200, 100, z=90),
            _floating("low", 100, 100, 200, 100, z=10),
        ], canvas, safe)
        high = report.rect_for("high")
        low = report.rect_for("low")
        assert not low.intersects(high, gap=FLOATING_GAP_PX)
        assert high.x > low.x  # 低层保持用户位置，高层被推走

    def test_huge_panel_reports_unresolvable(self):
        canvas = _canvas(width=200, height=200)
        safe = safe_area_for(canvas, None)
        report = resolve_floating_rects([
            _floating("big", 0, 0, 1000, 800)], canvas, safe)
        assert any(i.code == "unresolvable_overlap" for i in report.issues)

    def test_default_size_when_missing(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        comp = {"id": "d", "type": "chart_panel",
                "placement": {"mode": "floating", "x": 50, "y": 50}}
        report = resolve_floating_rects([comp], canvas, safe)
        rect = report.rect_for("d")
        assert (rect.w, rect.h) == DEFAULT_FLOATING_SIZE

    def test_deterministic(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        comps = [
            _floating("a", 100, 100, 300, 200, z=10),
            _floating("b", 300, 150, 300, 200, z=20),
            _floating("c", 300, 350, 300, 200, z=30),
        ]
        r1 = resolve_floating_rects(comps, canvas, safe)
        r2 = resolve_floating_rects(comps, canvas, safe)
        assert r1.to_bounded_dict() == r2.to_bounded_dict()

    def test_non_floating_components_ignored(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        report = resolve_floating_rects([
            {"id": "t", "type": "title", "position": "top-center"}], canvas, safe)
        assert report.placements == []


class TestBoundsChecks:
    def test_outside_canvas_error(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        issues = check_component_bounds(
            [_floating("lost", -5000, -5000, 320, 240)], canvas, safe)
        assert issues[0].code == "outside_canvas"
        assert issues[0].severity == "error"

    def test_partially_outside_safe_area_warning(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        issues = check_component_bounds(
            [_floating("edge", 1200, 100, 320, 240)], canvas, safe)
        assert issues[0].code == "outside_safe_area"

    def test_clean_rect_no_issues(self):
        canvas = _canvas()
        safe = safe_area_for(canvas, None)
        issues = check_component_bounds(
            [_floating("ok", 100, 100, 320, 240)], canvas, safe)
        assert issues == []


class TestFullReport:
    def test_mapspec_report(self):
        spec = {
            "layout": {
                "margins": {"top": 16, "right": 16, "bottom": 16, "left": 16},
                "components": [
                    {"id": "t", "type": "title", "position": "top-center"},
                    _floating("chart", 1200, 600, 320, 240),
                ],
            },
        }
        report = layout_geometry_report(spec, _canvas())
        assert report.rect_for("chart") is not None
        assert any(a.kind in ("clamped_into_safe_area", "overlap_cascade")
                   for a in report.adjustments)

    def test_default_canvas_fallback(self):
        report = layout_geometry_report(None)
        assert report.placements == []

    def test_bounded_payload(self):
        spec = {"layout": {"components": [_floating("c", 10, 10)]}}
        payload = layout_geometry_report(spec, _canvas()).to_bounded_dict()
        assert set(payload) == {"placements", "adjustments", "issues"}
        assert payload["placements"][0]["id"] == "c"


class TestCascadeWrapRegression:
    """review P1 回归：wrap 重置 x 会再入高遮挡列 —— 残余重叠必须显式
    披露（unresolvable_overlap），不允许静默输出重叠位置。"""

    def test_wrap_into_tall_blocker_discloses_unresolvable(self):
        canvas = _canvas(width=800, height=600)
        safe = safe_area_for(canvas, None)
        report = resolve_floating_rects([
            _floating("tall", 8, 8, 500, 2500, z=10),     # 高遮挡列
            _floating("r1", 520, 8, 260, 100, z=20),      # 右缘堆叠
            _floating("r2", 520, 120, 260, 100, z=30),
            _floating("incoming", 300, 300, 200, 100, z=40),
        ], canvas, safe)
        # 无论级联是否收敛：最终 placement 不得残余重叠，或必须披露不可解
        final = report.rect_for("incoming")
        others = [report.rect_for(i) for i in ("tall", "r1", "r2")]
        residual = any(final.intersects(o, gap=FLOATING_GAP_PX) for o in others)
        disclosed = any(i.code == "unresolvable_overlap"
                        and "incoming" in i.component_ids for i in report.issues)
        assert not residual or disclosed

    def test_deterministic_under_wrap(self):
        canvas = _canvas(width=800, height=600)
        safe = safe_area_for(canvas, None)
        comps = [
            _floating("tall", 8, 8, 500, 2500, z=10),
            _floating("r1", 520, 8, 260, 100, z=20),
            _floating("r2", 520, 120, 260, 100, z=30),
            _floating("incoming", 300, 300, 200, 100, z=40),
        ]
        r1 = resolve_floating_rects(comps, canvas, safe)
        r2 = resolve_floating_rects(comps, canvas, safe)
        assert r1.to_bounded_dict() == r2.to_bounded_dict()
