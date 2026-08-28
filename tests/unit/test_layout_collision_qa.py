"""C4（Cartographic QA）：LAYOUT_COLLISION 语义检查——desired-state 布局证据。

CA-P1-4 回归：布局 QA 此前是空壳（detect_collisions 仅单测调用、
VISUAL_OVERLAP 恒 not_evaluated、前端 36px 启发式自认补丁）。现在
zone 容量 / singleton 重复 / 悬空绑定 / floating 矩形重叠全部进入
evaluate_cartography_semantics 的规则 DSL。
"""
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics


def _spec(components, layers=None):
    return {
        "version": "1.0",
        "sources": {"src1": {"type": "geojson"}},
        "layers": layers or [
            {"id": "layer-1", "type": "circle", "source": "src1", "paint": {}}
        ],
        "layout": {"components": components},
    }


def _layout_check(mapspec):
    report = evaluate_cartography_semantics(mapspec)
    for check in report.checks:
        if check.rule == "LAYOUT_COLLISION":
            return check
    return None


def test_no_components_no_layout_check():
    assert _layout_check(_spec([])) is None


def test_clean_layout_passes_with_evidence():
    components = [
        {"id": "title", "type": "title", "enabled": True, "position": "top-center"},
        {"id": "north", "type": "north_arrow", "enabled": True, "position": "top-right"},
        {"id": "scale", "type": "scale_bar", "enabled": True, "position": "bottom-left"},
    ]
    check = _layout_check(_spec(components))
    assert check is not None
    assert check.status == "pass"


def test_zone_capacity_overflow_warns():
    components = [
        {"id": "c1", "type": "legend", "enabled": True, "position": "top-right"},
        {"id": "c2", "type": "legend", "enabled": True, "position": "top-right"},
    ]
    check = _layout_check(_spec(components))
    assert check is not None
    assert check.status == "warning"
    assert any("top-right" in issue for issue in check.evidence.get("issues", []))


def test_singleton_duplicate_warns():
    components = [
        {"id": "n1", "type": "north_arrow", "enabled": True, "position": "top-right"},
        {"id": "n2", "type": "north_arrow", "enabled": True, "position": "bottom-right"},
    ]
    check = _layout_check(_spec(components))
    assert check is not None
    assert check.status == "warning"
    assert any("singleton" in issue for issue in check.evidence.get("issues", []))


def test_floating_components_exempt_from_zone_capacity():
    """floating 位置归用户手势所有——两个 floating 组件同 zone 不算超容。"""
    components = [
        {
            "id": "chart1", "type": "chart_panel", "enabled": True, "position": "none",
            "placement": {"mode": "floating", "x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
        },
        {
            "id": "chart2", "type": "chart_panel", "enabled": True, "position": "none",
            "placement": {"mode": "floating", "x": 0.5, "y": 0.5, "width": 0.2, "height": 0.2},
        },
    ]
    check = _layout_check(_spec(components))
    assert check is not None
    assert check.status == "pass"


def test_floating_rect_overlap_warns():
    components = [
        {
            "id": "chart1", "type": "chart_panel", "enabled": True, "position": "none",
            "placement": {"mode": "floating", "x": 0.10, "y": 0.10, "width": 0.25, "height": 0.25},
        },
        {
            "id": "stats1", "type": "statistics_panel", "enabled": True, "position": "none",
            "placement": {"mode": "floating", "x": 0.20, "y": 0.20, "width": 0.25, "height": 0.25},
        },
    ]
    check = _layout_check(_spec(components))
    assert check is not None
    assert check.status == "warning"
    assert any("chart1" in issue and "stats1" in issue for issue in check.evidence.get("issues", []))


def test_orphan_component_binding_warns():
    components = [
        {
            "id": "legend1", "type": "legend", "enabled": True, "position": "bottom-right",
            "options": {"layerId": "no-such-layer"},
        },
    ]
    check = _layout_check(_spec(components))
    assert check is not None
    assert check.status == "warning"
    assert any("orphan" in issue for issue in check.evidence.get("issues", []))
