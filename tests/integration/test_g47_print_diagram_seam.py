"""G47 → G34 纸面 adapter 集成（WP10 的窄接缝）。

只验证"接缝"本身是否成立，不复制 G34 的排版系统：

1. 布局 artifact → ``PrintDiagramSpec`` 能被 schema **严格校验**（``extra=forbid``）；
2. ``kind="diagram"`` 缺 spec 会被 schema 拒绝（不能靠 kind 混过去）；
3. 编译期只用**一个** scale（contain），圆半径按同一比例缩放 → 纸面面积比例不变；
4. HTML 路径把同一场景内联成自包含 data URI，解回来仍是同一批圆与参考轮廓。
"""
from __future__ import annotations

import base64
import re
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from app.lib.cartography.print_layout_schema import (
    PrintComponent,
    PrintDiagramSpec,
)
from app.services.cartogram_authoring.compute import compute_layout
from app.services.cartogram_authoring.publication import (
    diagram_component,
    diagram_spec_from_layout,
)
from app.services.print_layout_authoring.compile import _compile_diagram
from app.services.print_layout_authoring.html import _diagram_img
from tests.unit.g47_fixtures import frozen, grid_features, plan

V9 = [100, 200, 300, 400, 500, 600, 700, 800, 900]


@pytest.fixture(scope="module")
def layout() -> Dict[str, Any]:
    return compute_layout(plan(), frozen(grid_features(values=V9))).layout


# ── schema 接缝 ─────────────────────────────────────────────────────────

def test_diagram_spec_passes_strict_schema(layout):
    spec = diagram_spec_from_layout(layout)
    parsed = PrintDiagramSpec(**spec)
    assert parsed.fit == "contain"
    assert len(parsed.circles) == 9
    assert parsed.layout_fingerprint.startswith("g47l-")
    assert parsed.truncated is False
    # 半径必须 > 0（``gt=0``），否则校验期就失败
    assert all(c.radius > 0 for c in parsed.circles)


def test_diagram_component_passes_component_schema(layout):
    comp = PrintComponent(**diagram_component(layout, component_id="carto-1"))
    assert comp.kind == "diagram"
    assert comp.diagram is not None
    assert comp.map_slot is None
    assert comp.frame.width > 0 and comp.frame.height > 0


def test_diagram_kind_without_spec_is_rejected():
    with pytest.raises(ValidationError) as ei:
        PrintComponent(id="d1", kind="diagram",
                       frame={"x": 10, "y": 10, "width": 100, "height": 100})
    assert "diagram component requires diagram spec" in str(ei.value)


def test_diagram_spec_cannot_smuggle_geo_fields(layout):
    """示意图组件不能带 CRS/地理范围 —— 否则它会被当成地图槽消费。"""
    payload = dict(diagram_spec_from_layout(layout))
    payload["crs"] = "EPSG:4326"
    with pytest.raises(ValidationError):
        PrintDiagramSpec(**payload)
    payload = dict(diagram_spec_from_layout(layout))
    payload["bbox"] = [104.0, 30.4, 104.5, 30.9]
    with pytest.raises(ValidationError):
        PrintDiagramSpec(**payload)


# ── 编译期：单一 scale ──────────────────────────────────────────────────

def test_compile_uses_one_uniform_scale(layout):
    spec = diagram_spec_from_layout(layout)
    box = {"x": 40.0, "y": 30.0, "width": 600.0, "height": 400.0}
    scene = _compile_diagram(spec, box)

    assert scene["fit"] == "contain"
    extent = float(spec["extent"])
    assert scene["scale"] == pytest.approx(min(box["width"], box["height"]) / extent)
    # 等比 contain：内容落在框内且只受短边约束
    assert scene["coordinate_space"] == "print_canvas_units"
    assert scene["is_geographic"] is False

    by_id = {c["feature_id"]: c for c in scene["circles"]}
    s = scene["scale"]
    for src in spec["circles"]:
        got = by_id[src["feature_id"]]
        assert got["radius"] == pytest.approx(src["radius"] * s, rel=1e-12)
        assert got["x"] == pytest.approx(scene["origin"][0] + src["x"] * s,
                                        rel=1e-12)
        assert got["y"] == pytest.approx(scene["origin"][1] + src["y"] * s,
                                        rel=1e-12)


def test_compile_preserves_area_ratio_on_paper(layout):
    """纸面半径比平方 == 数量比：排版不得改变读者比较面积的依据。"""
    spec = diagram_spec_from_layout(layout)
    scene = _compile_diagram(spec, {"x": 0.0, "y": 0.0, "width": 500.0,
                                    "height": 500.0})
    circles = sorted(scene["circles"], key=lambda c: c["value"])
    base, top = circles[0], circles[-1]
    # 半径按落盘精度（6 位小数）量化，比例误差 ~1e-8 是量化下界而非排版失真；
    # 这里允许 1e-6，仍然能抓住"非等比拉伸"这类真实缺陷（那种误差是数量级的）。
    assert (top["radius"] / base["radius"]) ** 2 == pytest.approx(
        top["value"] / base["value"], rel=1e-6)


def test_compile_keeps_reference_outlines_and_disclosures(layout):
    spec = diagram_spec_from_layout(layout)
    scene = _compile_diagram(spec, {"x": 0.0, "y": 0.0, "width": 500.0,
                                    "height": 500.0})
    assert len(scene["reference_paths"]) == len(spec["reference_paths"]) > 0
    assert scene["legend"]["unit"] == "人"
    assert any("示意布局" in d for d in scene["disclosures"])
    assert scene["layout_fingerprint"] == layout["metrics"]["layout_fingerprint"]


def test_compile_rejects_nothing_but_reports_truncation(layout):
    spec = dict(diagram_spec_from_layout(layout))
    spec["truncated"] = True
    scene = _compile_diagram(spec, {"x": 0.0, "y": 0.0, "width": 100.0,
                                    "height": 100.0})
    assert scene["truncated"] is True


# ── HTML 路径：自包含 data URI ──────────────────────────────────────────

def test_diagram_html_is_selfcontained_svg_data_uri(layout):
    comp = diagram_component(layout, component_id="carto-1")
    scene = _compile_diagram(comp["diagram"],
                            {"x": 0.0, "y": 0.0, "width": 480.0, "height": 480.0})
    node = {"id": "carto-1", "kind": "diagram",
            "box": {"x": 0.0, "y": 0.0, "width": 480.0, "height": 480.0},
            "diagram": scene}
    html = _diagram_img(node)

    assert 'data-g47-diagram="carto-1"' in html
    match = re.search(r'src="data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)"', html)
    assert match, "必须内联为自包含 SVG data URI"
    svg = base64.b64decode(match.group(1)).decode("utf-8")

    assert svg.count('data-feature-id="') == len(scene["circles"]) == 9
    # 每个圆的半径就是编译后的纸面半径（不是原始示意图半径）
    for c in scene["circles"]:
        assert f'r="{float(c["radius"]):.3f}"' in svg
    # viewBox = 组件框；width/height:100% 不改变坐标比例
    assert 'viewBox="0.00 0.00 480.00 480.00"' in svg
    assert "width:100%;height:100%" in html
    assert "EPSG" not in svg


def test_diagram_html_escapes_labels(layout):
    """标签来自源数据，必须转义（否则用户数据里的 ``<`` 会破坏 SVG）。"""
    hostile = compute_layout(
        plan(), frozen(grid_features(
            values=V9, extra_props={"name": "<script>alert(1)</script>"}))).layout
    comp = diagram_component(hostile, component_id="x")
    scene = _compile_diagram(comp["diagram"],
                            {"x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0})
    node = {"id": "x", "kind": "diagram",
            "box": {"x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0},
            "diagram": scene}
    html = _diagram_img(node)
    match = re.search(r'src="data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)"', html)
    svg = base64.b64decode(match.group(1)).decode("utf-8")
    assert "<script>" not in svg
