"""G47 独立评审缺陷回归（REVIEW.md P0/P1/P2 逐条钉住）。

每个用例对应评审里的一条发现，**先复现失败面，再断言修复后的事实**；不从实现
内部取值自证（面积/重叠/坐标都独立复算），否则缺陷回来时测试仍会绿。
"""
from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from g47_fixtures import (  # noqa: E402
    DEFAULT_VALUES,
    frozen,
    grid_features,
    plan,
)

from app.services.cartogram_authoring.compute import compute_layout  # noqa: E402
from app.services.cartogram_authoring.quality import (  # noqa: E402
    Circle,
    GateSpec,
    evaluate_layout,
)

V9 = DEFAULT_VALUES
PATH_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _layout(**kwargs):
    return compute_layout(plan(**kwargs), frozen(grid_features(values=V9))).layout


# ── P0-1：6 个 Pi 工具必须真的注册（非法 memory_class 曾让后 3 个静默消失）──

def test_all_six_pi_tools_register_without_swallowing_errors():
    from app.tools.cartogram_tools import register_cartogram_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_cartogram_tools(registry)  # 不许抛，也不许被上层 except 吞掉
    names = {n for n in registry._tools if n.startswith("webgis_dorling_cartogram_")}
    assert names == {
        "webgis_dorling_cartogram_inspect",
        "webgis_dorling_cartogram_plan",
        "webgis_dorling_cartogram_run",
        "webgis_dorling_cartogram_status",
        "webgis_dorling_cartogram_apply",
        "webgis_dorling_cartogram_export",
    }


# ── P0-2：参考轮廓必须落在示意图画布内，且不能是投影米坐标 ──

def test_reference_outlines_live_inside_the_diagram_canvas():
    layout = _layout()
    extent = float(layout["extent"]["width"])
    points = [p for outline in layout["reference"]["outlines"]
              for ring in outline["rings"] for p in ring]
    assert points, "参考轮廓不能为空 —— 否则『原地理参照』是假的"
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    # 声明是 diagram 空间，就不能出现投影米量级
    assert max(abs(v) for v in xs + ys) < 1e5
    assert min(xs) >= 0.0 and max(xs) <= extent
    assert min(ys) >= 0.0 and max(ys) <= extent


def test_reference_outlines_keep_the_original_grid_shape():
    """参照轮廓要保留"九宫格"的相对形状（不是被拉伸/裁剪的碎线）。"""
    layout = _layout()
    outlines = layout["reference"]["outlines"]
    assert len(outlines) == 9
    boxes = []
    for outline in outlines:
        pts = [p for ring in outline["rings"] for p in ring]
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        boxes.append((max(xs) - min(xs), max(ys) - min(ys)))
    widths = sorted(w for w, _h in boxes)
    heights = sorted(h for _w, h in boxes)
    # 九个等大单元：经度方向同宽（严格相等，x 方向为线性投影）；纬度方向因
    # Mercator 在高纬略放大，允许 1% 的真实几何差异 —— 但这足以证明"没有被
    # 逐轴拉伸"（各向异性会带来数量级差异）。
    assert widths[-1] - widths[0] < 1e-6
    assert heights[-1] == pytest.approx(heights[0], rel=0.01)


# ── P0-3：面积门必须复核**落盘后**的半径，而不是内存里的理想值 ──

def test_area_gate_recomputes_from_serialized_radius():
    layout = _layout()
    coefficient = float(layout["area_scale"]["coefficient"])
    worst = 0.0
    for circle in layout["circles"]:
        expected = math.pi * coefficient * float(circle["value"])
        rel = abs(math.pi * float(circle["radius"]) ** 2 - expected) / expected
        worst = max(worst, rel)
    reported = float(layout["metrics"]["area"]["max_rel_error"])
    assert reported == pytest.approx(worst, abs=1e-9)
    assert layout["metrics"]["area"]["pass"] is True


def test_area_gate_can_actually_fail_on_quantisation():
    """把半径量化到远超落盘精度，门必须从 pass 变 fail（不是恒真）。"""
    exact = math.sqrt(2.0)   # k=2, v=1 → r=sqrt(2)=1.41421…
    circles = [
        Circle(feature_id="a", x=0.0, y=0.0, radius=exact, value=1.0,
               anchor=(0.0, 0.0)),
        Circle(feature_id="b", x=100.0, y=0.0, radius=30.0, value=450.0,
               anchor=(100.0, 0.0)),
    ]
    gates = GateSpec(max_area_rel_error=0.001, max_pair_overlap_ratio=1.0,
                     max_overlapping_pair_ratio=1.0, max_displacement_ratio=10.0)
    strict = evaluate_layout(circles=circles, coefficient=2.0, gates=gates,
                             adjacency_edges=[], max_radius=30.0,
                             radius_decimals=2)
    loose = evaluate_layout(circles=circles, coefficient=2.0, gates=gates,
                            adjacency_edges=[], max_radius=30.0,
                            radius_decimals=6)
    assert strict.area["pass"] is False
    assert loose.area["pass"] is True


# ── P1-4：scope 过滤必须先于统计（总量/k/计数都只看纳入单元）──

def test_excluded_unit_does_not_enter_total_or_coefficient():
    layout = _layout(exclude_ids=["r2c2"],
                     exclude_reasons={"r2c2": "统计口径不同（测试）"})
    assert layout["quantity"]["total"] == 3600
    assert layout["quantity"]["counts"]["total_units"] == 8
    assert layout["area_scale"]["max_value"] == 800
    assert len(layout["circles"]) == 8


def test_only_the_remaining_max_drives_the_common_coefficient():
    layout = _layout(include_ids=["r0c0", "r0c1", "r0c2"])
    assert layout["quantity"]["total"] == 600
    assert layout["area_scale"]["max_value"] == 300
    factor = float(layout["area_scale"]["normalization_factor"])
    radii = {c["feature_id"]: float(c["radius"]) for c in layout["circles"]}
    # 相同 k，半径比 = sqrt(值比) —— 与图面归一无关
    assert radii["r0c2"] / radii["r0c0"] == pytest.approx(math.sqrt(3.0), rel=1e-3)
    assert max(radii.values()) == pytest.approx(36.0 * factor, rel=1e-3)


def test_scope_exclusion_actually_removes_a_bad_value():
    """评审的原话：提示让用户"用 exclude_ids 排除"，排除后却仍报同一个错。"""
    features = grid_features(values=V9)
    features[8]["properties"]["pop"] = "n/a"  # r2c2
    with pytest.raises(Exception) as exc:
        compute_layout(plan(), frozen(features))
    assert getattr(exc.value, "code", "") == "FIELD_NOT_NUMERIC"

    built = compute_layout(
        plan(exclude_ids=["r2c2"], exclude_reasons={"r2c2": "坏值（测试）"}),
        frozen(features))
    assert len(built.layout["circles"]) == 8
    assert built.layout["quantity"]["total"] == 3600


# ── P1-5：两道重叠门互不派生；max_pair_overlap_ratio 报真实最大值 ──

def test_fraction_gate_is_not_dead_and_reports_true_max():
    """重叠介于「相切容差」与硬门之间：硬门不破，占比门必须能独立破。"""
    radius = 10.0
    spacing = 18.3
    circles = [
        Circle(feature_id=f"c{i}", x=i * spacing, y=0.0, radius=radius,
               value=100.0, anchor=(i * spacing, 0.0))
        for i in range(3)
    ]
    gates = GateSpec(max_area_rel_error=1.0, max_pair_overlap_ratio=0.05,
                     max_overlapping_pair_ratio=0.0, max_displacement_ratio=99.0)
    report = evaluate_layout(circles=circles, coefficient=radius ** 2 / 100.0,
                             gates=gates, adjacency_edges=[], max_radius=radius)

    # 真实重叠率必须被如实报出（旧实现把它压成 0，因为只统计"超阈对"）
    expected = (2 * radius ** 2 * math.acos(spacing / (2 * radius))
                - spacing * math.sqrt(radius ** 2 - (spacing / 2) ** 2))
    expected_ratio = expected / (math.pi * radius ** 2)
    assert (report.overlap["fraction_pair_threshold"]
            < expected_ratio < gates.max_pair_overlap_ratio)
    assert report.overlap["max_pair_overlap_ratio"] == pytest.approx(
        expected_ratio, rel=1e-3)
    # 硬门没破，但"超容差对数占比"破了 → 两道门独立
    assert report.overlap["pair_cap_violated"] is False
    assert report.overlap["fraction_cap_violated"] is True
    assert report.gates["overlap_pair_cap"] is True
    assert report.gates["overlap_fraction_cap"] is False
    assert report.status == "fail"


# ── P1-6：纸面 adapter 必须对参照路径套同一个 scale/origin ──

def test_print_scene_transforms_reference_paths_into_component_box():
    from app.services.cartogram_authoring.publication import diagram_spec_from_layout
    from app.services.print_layout_authoring.compile import _compile_diagram

    layout = _layout()
    spec = diagram_spec_from_layout(layout)
    box = {"x": 20.0, "y": 30.0, "width": 600.0, "height": 400.0}
    scene = _compile_diagram(spec, box)
    assert scene["reference_paths"]
    xs, ys = [], []
    for path in scene["reference_paths"]:
        numbers = [float(m.group(0)) for m in PATH_NUMBER.finditer(path)]
        xs.extend(numbers[0::2])
        ys.extend(numbers[1::2])
    # 组件框内（含统一 contain 的居中偏移）
    assert min(xs) >= box["x"] - 1e-6
    assert max(xs) <= box["x"] + box["width"] + 1e-6
    assert min(ys) >= box["y"] - 1e-6
    assert max(ys) <= box["y"] + box["height"] + 1e-6
    # 不再是 0..extent 的原始示意坐标
    assert max(xs) - min(xs) <= box["width"] + 1e-6


def test_print_scene_keeps_reference_and_circles_in_the_same_frame():
    from app.services.cartogram_authoring.publication import diagram_spec_from_layout
    from app.services.print_layout_authoring.compile import _compile_diagram

    layout = _layout()
    spec = diagram_spec_from_layout(layout)
    box = {"x": 0.0, "y": 0.0, "width": 500.0, "height": 500.0}
    scene = _compile_diagram(spec, box)
    numbers = [float(m.group(0))
               for path in scene["reference_paths"]
               for m in PATH_NUMBER.finditer(path)]
    ref_cx = (min(numbers[0::2]) + max(numbers[0::2])) / 2.0
    circle_cx = sum(float(c["x"]) for c in scene["circles"]) / len(scene["circles"])
    assert ref_cx == pytest.approx(circle_cx, abs=25.0)


# ── P2-7：area_scale 两套量纲混用 ──

def test_area_scale_min_radius_is_in_diagram_units():
    layout = _layout()
    coefficient = float(layout["area_scale"]["coefficient"])
    min_value = min(float(c["value"]) for c in layout["circles"])
    min_radius = min(float(c["radius"]) for c in layout["circles"])
    assert float(layout["area_scale"]["min_radius"]) == pytest.approx(
        min_radius, abs=1e-3)
    assert float(layout["area_scale"]["min_radius"]) == pytest.approx(
        math.sqrt(coefficient * min_value), rel=1e-3)


# ── P2-11：图例参考圆必须保持半径比例（不逐圆 clamp）──

def test_legend_reference_circles_keep_their_radius_ratio():
    from app.services.cartogram_authoring.render import render_svg

    layout = _layout(overrides={"area_scale": {"max_radius": 0.6, "extent": 1000.0}})
    svg = render_svg(layout)
    svg = svg.decode("utf-8") if isinstance(svg, bytes) else svg
    tags = re.findall(r'<circle[^>]*data-legend-value="[^"]*"[^>]*>', svg)
    radii = []
    for tag in tags:
        match = re.search(r'\br="([0-9.]+)"', tag)
        assert match, tag
        radii.append(float(match.group(1)))
    assert len(radii) >= 2
    declared = [float(r["radius"]) for r in layout["legend"]["reference_circles"]]
    assert declared[0] > 0
    # 统一系数 ⇒ 每个参考圆的 实际/声明 比值相同（旧实现逐圆 max(1.0,r) 会把
    # 三个小参考圆压成同一半径，比例彻底丢失）
    ratios = [a / d for a, d in zip(radii, declared)]
    for ratio in ratios:
        assert ratio == pytest.approx(ratios[0], rel=1e-6)
    assert radii[0] > radii[1] > radii[2]


# ── P0-4（Pi 旅程实测）：layout_ref 必须按 ref 游标直传 ──
#
# registry 的「透明解引用」阶段会把像 `ref:g47layout-...` 的参数当成会话数据
# 别名，就地替换成载荷（dict）；`layout_ref: str` 随即在 Pydantic 校验处报
# `Input should be a valid string`。生产链上布局必然存在 ⇒ apply/export 从
# Pi/SSE 调用**永远**失败（旅程实测两工具皆 step_error）。声明 ref_cursor 是
# registry 提供的正解，不需要给核心加硬编码。

def test_layout_ref_arguments_declare_ref_cursor_semantics():
    from app.tools.cartogram_tools import ApplyArgs, ExportArgs
    from app.tools.registry import ToolRegistry

    for model in (ApplyArgs, ExportArgs):
        declared = ToolRegistry._declared_ref_cursor_keys(model)
        assert "layout_ref" in declared, (
            f"{model.__name__}.layout_ref 未声明 json_schema_extra="
            "{'ref_cursor': True} —— registry 会把它解引用成载荷")


def test_declared_layout_ref_survives_transparent_resolution():
    """模拟解引用阶段：声明过的字段必须原样保留字符串引用。"""
    from app.tools.cartogram_tools import ApplyArgs
    from app.tools.registry import ToolRegistry

    skip_keys = {"ref_id", "layer_ref", "layer_id", "plan_id", "before_ref"}
    skip_keys |= ToolRegistry._declared_ref_cursor_keys(ApplyArgs)
    args = {"layer_id": "g47-layer",
            "layout_ref": "ref:g47layout-0123456789abcdef",
            "expected_revision": 1}
    # 解引用只跳过 skip_keys 里的键；未声明的 layout_ref 会被替换（复现面）
    resolved = {k: (v if k in skip_keys else {"resolved": True})
                for k, v in args.items()}
    assert resolved["layout_ref"] == args["layout_ref"]
    assert isinstance(resolved["layout_ref"], str)


# ── P3：预算预检必须先于投影/邻接（最重的一步）──

def test_budget_preflight_runs_before_expensive_geography(monkeypatch):
    from app.services.cartogram_authoring import compute as compute_module
    from app.services.cartogram_authoring.errors import AuthoringError

    def _must_not_prepare(*_args, **_kwargs):
        raise AssertionError("prepare_geography 在预算预检之前被执行")

    monkeypatch.setattr(compute_module, "prepare_geography", _must_not_prepare)
    with pytest.raises(AuthoringError) as ei:
        compute_module.compute_layout(
            plan(max_features=5), frozen(grid_features(values=V9)))
    assert ei.value.code == "BUDGET_EXCEEDED"


# ── P3：PNG 栅格化的超时/OSError 必须是 typed EXPORT_FAILED ──

@pytest.mark.parametrize("exc", [
    subprocess.TimeoutExpired(cmd="rsvg-convert", timeout=120),
    OSError("rsvg-convert vanished"),
])
def test_render_png_wraps_subprocess_failures_in_export_failed(monkeypatch, exc):
    from app.services.cartogram_authoring import render as render_module
    from app.services.cartogram_authoring.errors import AuthoringError

    monkeypatch.setattr(render_module.shutil, "which",
                        lambda _name: "/usr/bin/rsvg-convert")

    def _raise(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(render_module.subprocess, "run", _raise)
    with pytest.raises(AuthoringError) as ei:
        render_module.render_png({"extent": {"width": 100.0}, "circles": []})
    assert ei.value.code == "EXPORT_FAILED"


# ── P3：SVG 半径不能把小圆量化为 0.00（回读/可见性契约）──

def test_svg_draws_sub_hundredth_radii_with_artifact_precision():
    from app.services.cartogram_authoring.render import render_svg

    layout = {
        "extent": {"width": 1000.0},
        "status": "converged",
        "publication_ready": True,
        "circles": [{"feature_id": "tiny", "label": "Tiny", "x": 500.0,
                     "y": 500.0, "radius": 0.0012, "value": 1.0}],
    }
    svg = render_svg(layout)
    tag = re.search(r'<circle data-feature-id="tiny"[^>]*>', svg)
    assert tag, svg
    r = float(re.search(r'\br="([0-9.]+)"', tag.group(0)).group(1))
    assert r > 0.0
    assert r == pytest.approx(0.0012, abs=1e-9)

