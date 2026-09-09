"""Label Engine Foundation — 确定性标注引擎契约测试。

锁定：
- 点标注 8 候选偏好序（无碰撞选右上）；
- 双点重叠：priority 小者占优位，结果确定性（两遍求解相等）；
- 线标注 keep-upright（反向线段角度翻转后落在 (-90, 90]）；
- repeat_distance 同文本不重复放置；
- polygon 标注落在面内（representative_point 候选）；
- min_zoom 门控 / viewport 完整包含约束 / callout 引线；
- estimate_label_box CJK 加权；
- 规模回归：300 点 < 2s 且 placed+callout+suppressed == 300。

引擎本身无随机数；测试里的 numpy RandomState 仅用于构造规模数据。
"""
from __future__ import annotations

import time

import pytest
from shapely.geometry import Point, Polygon

from app.lib.cartography.label_engine import (
    DECLUTTER_CANDIDATE_OFFSETS,
    MAX_SVG_LABEL_CHARS,
    LabelEngineInput,
    LabelFeature,
    estimate_label_box,
    fit_label_text,
    solve_labels,
    wrap_label_text,
)

pytestmark = pytest.mark.cartography


def _point(fid: str, x: float, y: float, text: str, *, priority: int = 50,
           **kw) -> LabelFeature:
    return LabelFeature(id=fid, text=text, kind="point",
                        geometry=[[x, y]], priority=priority, **kw)


def _by_id(sol, fid: str):
    for p in sol.placements:
        if p.feature_id == fid:
            return p
    return None


def _sup(sol, fid: str):
    for p in sol.suppressed:
        if p.feature_id == fid:
            return p
    return None


# ── 1. 点标注候选偏好序 ──────────────────────────────────────────────────
def test_point_prefers_first_candidate_top_right() -> None:
    # 8 方位、首选右上、代价 = 序号（GIS 惯例）
    assert len(DECLUTTER_CANDIDATE_OFFSETS) == 8
    assert DECLUTTER_CANDIDATE_OFFSETS[0] == (1.0, 1.0)
    assert len({o for o in DECLUTTER_CANDIDATE_OFFSETS}) == 8

    sol = solve_labels(LabelEngineInput(
        features=[_point("p1", 100.0, 100.0, "PA")],
        viewport=[0.0, 0.0, 400.0, 400.0],
    ))
    assert len(sol.suppressed) == 0
    p = _by_id(sol, "p1")
    assert p is not None and p.status == "placed"
    # offset = font_size * 0.75 = 9 → 右上偏移，左下角锚定
    assert p.x == pytest.approx(109.0)
    assert p.y == pytest.approx(109.0)
    assert p.angle == pytest.approx(0.0)
    assert sol.stats["placed"] == 1 and sol.stats["candidates"] == 8


# ── 2. 双点重叠：priority 小者占优位 + 确定性 ────────────────────────────
def test_two_overlapping_points_priority_and_determinism() -> None:
    feats = [
        _point("high", 100.0, 100.0, "PA", priority=10),
        _point("low", 103.0, 103.0, "PB", priority=90),
    ]
    a = solve_labels(LabelEngineInput(features=feats, viewport=[0, 0, 400, 400]))
    b = solve_labels(LabelEngineInput(
        features=list(reversed(feats)), viewport=[0, 0, 400, 400]))
    assert a.model_dump() == b.model_dump()  # 输入乱序不影响结果

    high = _by_id(a, "high")
    low = _by_id(a, "low")
    assert high is not None and high.status == "placed"
    # 高优先级拿到首选右上位
    assert (high.x, high.y) == (pytest.approx(109.0), pytest.approx(109.0))
    # 低优先级让位到次选（非首选位置），未被抑制
    assert low is not None and low.status == "placed"
    assert (low.x, low.y) != (pytest.approx(109.0), pytest.approx(109.0))


# ── 3. 线标注 keep-upright ───────────────────────────────────────────────
def test_line_keep_upright_flips_reversed_angle() -> None:
    vp = [0.0, 0.0, 400.0, 400.0]
    fwd = solve_labels(LabelEngineInput(
        features=[LabelFeature(id="r1", text="River", kind="line",
                               geometry=[[10.0, 10.0], [110.0, 110.0]])],
        viewport=vp))
    rev = solve_labels(LabelEngineInput(
        features=[LabelFeature(id="r2", text="River", kind="line",
                               geometry=[[110.0, 110.0], [10.0, 10.0]])],
        viewport=vp))
    pf = _by_id(fwd, "r1")
    pr = _by_id(rev, "r2")
    assert pf is not None and pr is not None
    # 正向 45°；反向 atan2 = -135° → 翻转后同为 45°，均落在 (-90, 90]
    assert pf.angle == pytest.approx(45.0)
    assert pr.angle == pytest.approx(45.0)
    for ang in (pf.angle, pr.angle):
        assert -90.0 < ang <= 90.0


# ── 4. repeat_distance：同文本不重复放置 ─────────────────────────────────
def _hwy_line(fid: str, y: float) -> LabelFeature:
    return LabelFeature(id=fid, text="HWY 1", kind="line",
                        geometry=[[0.0, y], [100.0, y]])


def test_repeat_distance_blocks_same_text() -> None:
    sol = solve_labels(LabelEngineInput(
        features=[_hwy_line("a", 10.0), _hwy_line("b", 40.0)],
        viewport=[0.0, 0.0, 300.0, 100.0],
        repeat_distance=500.0,  # 远大于两条线的间距 → 第二条不得重复
    ))
    assert _by_id(sol, "a") is not None
    sup = _sup(sol, "b")
    assert sup is not None and sup.reason == "repeat_distance"


def test_repeat_distance_zero_allows_both() -> None:
    sol = solve_labels(LabelEngineInput(
        features=[_hwy_line("a", 10.0), _hwy_line("b", 40.0)],
        viewport=[0.0, 0.0, 300.0, 100.0],
        repeat_distance=0.0,  # 关闭重复控制 → 两条都可放置（盒子不相交）
    ))
    assert _by_id(sol, "a") is not None
    assert _by_id(sol, "b") is not None
    assert sol.suppressed == []


# ── 5. polygon 标注落在面内 ──────────────────────────────────────────────
def test_polygon_label_inside_polygon() -> None:
    ring = [[0.0, 0.0], [40.0, 0.0], [40.0, 40.0], [0.0, 40.0]]
    poly = Polygon(ring)
    sol = solve_labels(LabelEngineInput(
        features=[LabelFeature(id="park", text="公园", kind="polygon",
                               geometry=ring)],
        viewport=[-10.0, -10.0, 60.0, 60.0],
    ))
    p = _by_id(sol, "park")
    assert p is not None and p.status == "placed"
    assert poly.contains(Point(p.x, p.y))
    # 质心 + representative_point 两个候选
    assert sol.stats["candidates"] == 2


# ── 6. min_zoom 门控 ─────────────────────────────────────────────────────
def test_min_zoom_gate() -> None:
    feat = _point("metro", 100.0, 100.0, "M", min_zoom=12.0)
    out = solve_labels(LabelEngineInput(features=[feat],
                                        viewport=[0, 0, 400, 400], zoom=10.0))
    sup = _sup(out, "metro")
    assert sup is not None and sup.reason == "below_min_zoom"
    assert _by_id(out, "metro") is None

    at = solve_labels(LabelEngineInput(features=[feat],
                                       viewport=[0, 0, 400, 400], zoom=12.0))
    assert _by_id(at, "metro") is not None  # zoom == min_zoom 时参与


# ── 7. callout：位移超限时引线标注 ───────────────────────────────────────
def test_callout_when_displacement_exceeded() -> None:
    # 两个多边形标签先占满低优先级点的全部 8 个候选位与 max_displacement 位，
    # callout 阶段放宽盒碰撞 → 放置 + 引线（锚点 → 标签左下角）。
    feats = [
        LabelFeature(id="zone-1", text="西湖公园", kind="polygon",
                     geometry=[[101.5, 106.5], [103.5, 106.5],
                               [103.5, 108.5], [101.5, 108.5]], priority=10),
        LabelFeature(id="zone-2", text="西湖公园", kind="polygon",
                     geometry=[[131.0, 131.0], [133.0, 131.0],
                               [133.0, 133.0], [131.0, 133.0]], priority=10),
        _point("pt-low", 100.0, 100.0, "PA", priority=90),
    ]
    sol = solve_labels(LabelEngineInput(features=feats,
                                        viewport=[0.0, 0.0, 160.0, 160.0]))
    p = _by_id(sol, "pt-low")
    assert p is not None and p.status == "callout"
    assert p.leader is not None and len(p.leader) == 2
    assert p.leader[0] == pytest.approx([100.0, 100.0])   # 锚点
    assert p.leader[1] == pytest.approx([124.0, 124.0])   # 标签左下角（+max_displacement）
    assert p.reason == "displacement_exceeded"
    assert sol.stats["callout"] == 1


# ── 8. viewport 外框被拒绝 ───────────────────────────────────────────────
def test_viewport_containment_rejects_outside_boxes() -> None:
    feats = [
        _point("inner", 80.0, 80.0, "PA", allow_callout=False),
        _point("corner", 98.0, 98.0, "PB", allow_callout=False),
    ]
    sol = solve_labels(LabelEngineInput(features=feats,
                                        viewport=[0.0, 0.0, 100.0, 100.0]))
    # inner：右上出界但正下方候选可放
    inner = _by_id(sol, "inner")
    assert inner is not None and inner.status == "placed"
    # corner：8 候选框全部越过视口边界 → 拒绝（无位移/引线兜底）
    sup = _sup(sol, "corner")
    assert sup is not None and sup.reason == "collision"
    assert _by_id(sol, "corner") is None
    assert sol.stats["placed"] == 1 and sol.stats["suppressed"] == 1


# ── 9. estimate_label_box：CJK 加权 ──────────────────────────────────────
def test_estimate_label_box_cjk_weighting() -> None:
    lw, lh = estimate_label_box("ABCD", 12.0)
    cw, ch = estimate_label_box("四个字四", 12.0)  # 等字符数中文
    assert lw == pytest.approx(4 * 12.0 * 0.6)
    assert cw == pytest.approx(4 * 12.0 * 1.0)
    assert cw > lw
    assert lh == ch == pytest.approx(12.0 * 1.2)
    # 混合文本按字符加权
    mw, _ = estimate_label_box("A中", 12.0)
    assert mw == pytest.approx(12.0 * (0.6 + 1.0))


# ── 10. 规模回归：300 点 < 2s，守恒 ─────────────────────────────────────
def test_scale_regression_300_points_within_2s() -> None:
    import numpy as np

    rng = np.random.RandomState(42)  # 仅构造测试数据；引擎本身无随机
    xs = rng.uniform(5.0, 795.0, 300)
    ys = rng.uniform(5.0, 595.0, 300)
    feats = [
        _point(f"pt-{i:03d}", float(x), float(y), f"P{i}")
        for i, (x, y) in enumerate(zip(xs, ys))
    ]
    start = time.perf_counter()
    sol = solve_labels(LabelEngineInput(features=feats,
                                        viewport=[0.0, 0.0, 800.0, 600.0]))
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0
    total = len(sol.placements) + len(sol.suppressed)
    assert total == 300
    assert sol.stats["placed"] + sol.stats["callout"] + sol.stats["suppressed"] == 300
    # 确定性：规模输入两遍求解完全一致
    again = solve_labels(LabelEngineInput(features=feats,
                                          viewport=[0.0, 0.0, 800.0, 600.0]))
    assert sol.model_dump() == again.model_dump()


# ── 11. 文本适配契约（W3）：fit / wrap / label_too_long ──────────────────
class TestLabelTextFittingContract:
    """W3 文本适配契约：截断/换行纯函数 + solve 的 label_too_long 抑制理由。

    fit/wrap 是纯函数（无随机、无 locale 依赖），确定性 = 同输入两次调用
    完全相等。截断按 Unicode code point（与 TS 侧 string slice 在 BMP 内
    语义一致，跨孪生 parity 用 W10 corpus 锁定）。
    """

    def test_max_svg_label_chars_constant_shared(self) -> None:
        # W4（SVG 编译器）/W5（前端）共享口径：60 字符
        assert MAX_SVG_LABEL_CHARS == 60

    def test_fit_short_text_passthrough(self) -> None:
        fitted, truncated = fit_label_text("北京市", max_chars=60)
        assert (fitted, truncated) == ("北京市", False)
        # 确定性：两次调用完全相等
        assert fit_label_text("北京市") == ("北京市", False)

    def test_fit_exact_boundary_no_truncation(self) -> None:
        text = "A" * 60
        assert fit_label_text(text, max_chars=60) == (text, False)

    def test_fit_truncates_to_max_chars_with_ellipsis(self) -> None:
        text = "A" * 100
        fitted, truncated = fit_label_text(text, max_chars=60)
        assert truncated is True
        assert fitted == "A" * 59 + "…"
        assert len(fitted) == 60
        # 确定性
        assert fit_label_text(text, max_chars=60) == (fitted, True)

    def test_fit_cjk_counts_code_points(self) -> None:
        # 截断按 Unicode code point：每个 CJK 字符计 1，与 len()/切片语义一致
        text = "城" * 70
        fitted, truncated = fit_label_text(text, max_chars=60)
        assert truncated is True
        assert fitted == "城" * 59 + "…"
        assert len(fitted) == 60

    def test_wrap_fits_single_line(self) -> None:
        assert wrap_label_text("hello", max_chars=60) == ["hello"]
        assert wrap_label_text("", max_chars=60) == [""]

    def test_wrap_is_width_aware_for_cjk(self) -> None:
        # 宽度口径沿用 estimate_label_box：CJK 1.0em / 其他 0.6em。
        # max_chars=60 → 每行 36em 预算 = 60 个窄字符位 → 36 个 CJK 字符。
        lines = wrap_label_text("城" * 37, max_chars=60, max_lines=2)
        assert lines == ["城" * 36, "城"]

    def test_wrap_latin_line_capacity(self) -> None:
        lines = wrap_label_text("A" * 200, max_chars=60, max_lines=2)
        assert len(lines) == 2
        # 每行至多 60 个窄字符（36em 预算）；放不下 → 最后行截断
        assert lines[0] == "A" * 60
        assert lines[1] == "A" * 60
        assert len("".join(lines)) < 200

    def test_wrap_max_lines_one_truncates(self) -> None:
        lines = wrap_label_text("AB" * 50, max_chars=10, max_lines=1)
        assert len(lines) == 1
        assert lines[0] == "AB" * 5  # 6em 预算 = 10 个窄字符位

    def test_wrap_deterministic(self) -> None:
        text = "混合text文本" * 30
        assert wrap_label_text(text, max_chars=60, max_lines=2) == \
            wrap_label_text(text, max_chars=60, max_lines=2)

    def test_solve_suppressed_overlong_label_gets_label_too_long(self) -> None:
        # 90 字符长文本 → 巨型标签框必然出界/碰撞；抑制理由诚实标注
        # label_too_long（而非笼统的 collision）
        feats = [
            _point("corner", 98.0, 98.0, "X" * 90, allow_callout=False),
        ]
        sol = solve_labels(LabelEngineInput(features=feats,
                                            viewport=[0.0, 0.0, 100.0, 100.0]))
        sup = _sup(sol, "corner")
        assert sup is not None and sup.status == "suppressed"
        assert sup.reason == "label_too_long"

    def test_solve_short_label_collision_reason_unchanged(self) -> None:
        # 对照组：同几何、短文本 → 维持既有 collision 词表
        feats = [
            _point("corner", 98.0, 98.0, "XM", allow_callout=False),
        ]
        sol = solve_labels(LabelEngineInput(features=feats,
                                            viewport=[0.0, 0.0, 100.0, 100.0]))
        sup = _sup(sol, "corner")
        assert sup is not None and sup.reason == "collision"

    def test_solve_gate_reasons_take_precedence_over_label_too_long(self) -> None:
        # 门控（empty_text / below_min_zoom）先于放置循环，长文本不覆盖其理由
        feat = _point("gated", 100.0, 100.0, "X" * 90, min_zoom=12.0)
        sol = solve_labels(LabelEngineInput(features=[feat],
                                            viewport=[0, 0, 400, 400], zoom=10.0))
        sup = _sup(sol, "gated")
        assert sup is not None and sup.reason == "below_min_zoom"
