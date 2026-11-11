"""G27 P3 组件科学语义 —— A12 / A17 / A18 / A11 / A13。

组件消费同一 EffectiveScalePortrayal；不新增 variant、不改图例完整数据域、
不因档位切换改变布局，也不把渲染抽稀当作分析抽样。
"""
from __future__ import annotations

from app.lib.cartography import scale_portrayal as sp

AUTO = {"version": sp.SCALE_POLICY_VERSION, "mode": "auto_lod"}

COMPONENTS = [
    {"id": "lg", "type": "legend", "variant": "boxed",
     "placement": {"x": 0.02, "y": 0.02, "width": 0.2, "height": 0.3}},
    {"id": "cb", "type": "continuous_colorbar", "variant": "academic"},
    {"id": "sb", "type": "scale_bar", "variant": "dual_unit"},
    {"id": "lb", "type": "label_layer"},
]
SUPPORTED = {
    "legend": ("minimal", "boxed", "academic"),
    "continuous_colorbar": ("academic",),
    "scale_bar": ("minimal", "boxed", "academic", "dual_unit"),
}


def _decision(zoom: float):
    return sp.resolve_effective_scale_portrayal(
        zoom=zoom, layer={"id": "L"}, policy=AUTO,
        frame={"center": [116.4, 39.9]})


def test_a12_legend_and_colorbar_keep_full_domain_across_all_bands():
    snapshots = []
    for zoom in (0.0, 3.0, 6.0, 10.0, 14.0, 19.0, 24.0):
        d = _decision(zoom)
        states = sp.component_portrayal_state(
            d, COMPONENTS, supported_variants=SUPPORTED)
        by_id = {s["id"]: s for s in states}
        assert by_id["lg"]["state"] == "full_domain"
        assert by_id["cb"]["state"] == "full_domain"
        assert by_id["lb"]["state"] == "full_domain"
        assert by_id["lg"]["variant"] == "boxed"       # 不新增/不换 variant
        assert by_id["cb"]["variant"] == "academic"
        # 完整数据域说明在场（不写回 source / 分析 / 统计 count）
        assert any("完整数据域" in x for x in by_id["lg"]["disclosures"])
        snapshots.append((by_id["lg"]["state"], by_id["lg"]["variant"],
                          by_id["cb"]["state"], by_id["cb"]["variant"]))
    assert len(set(snapshots)) == 1                    # 档位切换零变化


def test_a17_unsupported_variant_is_disclosed_not_invented():
    d = _decision(10.0)
    comps = [{"id": "x", "type": "legend", "variant": "not_a_real_variant"}]
    state = sp.component_portrayal_state(
        d, comps, supported_variants=SUPPORTED)[0]
    assert state["variant"] == "not_a_real_variant"    # 原样保留，不替换
    assert any("UNSUPPORTED_VARIANT" in x for x in state["disclosures"])


def test_a18_component_layout_rect_never_touched():
    """输出不含任何 placement 变更；输入组件不被原地修改。"""
    import copy

    original = copy.deepcopy(COMPONENTS)
    for zoom in (3.0, 19.0):
        states = sp.component_portrayal_state(_decision(zoom), COMPONENTS)
        assert all("placement" not in s for s in states)
        assert all(set(s) <= {"id", "type", "variant", "state",
                              "band_id", "disclosures"} for s in states)
    assert COMPONENTS == original


def test_a16_scale_bar_only_annotates_with_supported_crs():
    mercator = sp.component_portrayal_state(
        _decision(10.0), COMPONENTS)[2]
    assert mercator["state"] == "scale_annotated"

    non_mercator = sp.resolve_effective_scale_portrayal(
        zoom=10.0, layer={"id": "L"}, policy=AUTO,
        crs="EPSG:4326", frame={"center": [116.4, 39.9]})
    state = sp.component_portrayal_state(non_mercator, COMPONENTS)[2]
    assert state["state"] == "scale_unavailable"
    assert any("不套固定公式" in x for x in state["disclosures"])


def test_a24_component_state_is_bounded():
    many = [{"id": f"c{i}", "type": "legend"} for i in range(200)]
    states = sp.component_portrayal_state(_decision(10.0), many)
    assert len(states) <= 32
    assert all(len(s["disclosures"]) <= 3 for s in states)
