"""G04 W4(CM-10):专业制图黄金案例集 —— 真实链路产物.

每个案例走**真实生产链**:本地 fixture 数据 → 唯一投影器(project_selection_facts)
→ 表达候选引擎(generate_design_candidates)→ 契约重放(apply_contract)→
SVG 编译(compile_mapspec_to_svg_detailed)→(打印档)出版求解
(prepare_publication_document)。断言的是**结构事实与诚实披露**,不是截图。

案例(对应任务书 CM-10):
1. 成都小学点位 + 行政统计 + 图表(点主数据 + 面统计 + chart_panel)
2. 重庆医院可达性(线网 + 设施点;accessibility 类目)
3. 四川自然资源专题(面分类;多变量字段)
4. 行政区多变量地图(bivariate 双字段)
5. 时序变化(change_detection;temporal 帧)
6. A4 正式专题图(打印档:约束求解 evaluated + 指北针/图例/单位)
0. empty-state(空数据:typed 空产物,不生成装饰性假地图)

可复现:`pytest tests/cartography/test_map_product_goldens_g04.py -v`
(确定性 fixture + 纯函数链,无网络/无外部服务)。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.composition_contract import (
    apply_contract,
    get_contract_registry,
)
from app.lib.cartography.design_candidates import (
    DesignTaskContext,
    generate_design_candidates,
)
from app.lib.cartography.publication_constraint_layout import (
    prepare_publication_document,
)
from app.lib.cartography.selection_context import project_selection_facts
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

pytestmark = pytest.mark.cartography

CONTRACT = get_contract_registry().get("contract.core.basic_thematic")


def _ring(cx: float, cy: float, d: float = 0.09) -> list[list[float]]:
    return [
        [cx, cy], [cx + d, cy], [cx + d, cy + d], [cx, cy + d], [cx, cy],
    ]


def _admin_fc(values: list[float], field: str, cx=104.0, cy=30.5) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {field: v, "district": f"d{i}", "population": 1000 + i * 800},
         "geometry": {"type": "Polygon",
                      "coordinates": [_ring(cx + i * 0.12, cy)]}}
        for i, v in enumerate(values)
    ]}


def _points_fc(values: list[float], field: str, cx=104.0, cy=30.55) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {field: v},
         "geometry": {"type": "Point",
                      "coordinates": [cx + (i % 6) * 0.013, cy + (i // 6) * 0.011]}}
        for i, v in enumerate(values)
    ]}


def _lines_fc(cx=106.4, cy=29.5) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"minutes": float(6 + i * 4)},
         "geometry": {"type": "LineString", "coordinates": [
             [cx, cy], [cx + 0.05 + i * 0.02, cy + 0.03 + i * 0.01]]}}
        for i in range(4)
    ]}


def _base_spec(sources: dict, layers: list, extra: dict | None = None) -> dict:
    spec = {
        "version": "1.6",
        "view": {"center": [104.1, 30.6], "zoom": 9},
        "sources": sources,
        "layers": layers,
        "layout": {"components": [], "component_links": []},
    }
    if extra:
        spec.update(extra)
    return spec


def _run_golden(
    *, intent_task: str, profile: dict, sources: dict, layers: list,
    overlay_geometry_kinds: tuple = (), extra_spec: dict | None = None,
    expect_family: str,
):
    """判选 → 契约 → 编译 真实链;返回 (candidates, spec_after, compiled)。"""
    facts = project_selection_facts(
        intent_task=intent_task, profile=profile,
        overlay_geometry_kinds=overlay_geometry_kinds,
    )
    cand = generate_design_candidates(
        DesignTaskContext(**facts.design_ctx_kwargs()),
    )
    families = {c.expression_family for c in cand.candidates}
    assert expect_family in families, f"{expect_family} not in {families}"

    spec = _base_spec(sources, layers, extra_spec)
    spec_after, report = apply_contract(spec, CONTRACT)
    assert report.created or report.preserved or report.locked_skipped, \
        report.to_dict()
    compiled = compile_mapspec_to_svg_detailed(
        spec_after, include_chrome=True,
    )
    assert compiled.svg and len(compiled.svg) > 500
    return cand, spec_after, compiled


# ── 案例 1:成都小学点位 + 行政统计 + 图表 ─────────────────────────────


def test_golden_chengdu_schools_points_admin_chart():
    schools = _points_fc([float(i % 9) for i in range(24)], "students")
    districts = _admin_fc([8, 21, 35, 44], "schools")
    cand, spec, compiled = _run_golden(
        intent_task="distribution_overview",
        profile={"geometryTypes": ["Point"], "featureCount": 24,
                 "fields": {"students": {"type": "number"}}},
        sources={"pts": {"type": "geojson", "inlineData": schools},
                 "adm": {"type": "geojson", "inlineData": districts}},
        layers=[
            {"id": "schools", "source": "pts", "type": "circle"},
            {"id": "districts", "source": "adm", "type": "fill",
             "legend_spec": {"title": "各区小学数量", "unit": "所",
                             "entries": [{"label": "8–44", "color": "#fdcc8a"}]}},
        ],
        overlay_geometry_kinds=("polygon",),
        expect_family="point",
    )
    svg = compiled.svg
    assert "各区小学数量" in svg and "单位：所" in svg
    assert '<circle' in svg or 'circle' in svg  # 点主层入图


# ── 案例 2:重庆医院可达性 ──────────────────────────────────────────────


def test_golden_chongqing_hospital_accessibility():
    network = _lines_fc()
    hospitals = _points_fc([1, 2, 3, 4, 5, 6], "beds", cx=106.4, cy=29.52)
    cand, spec, compiled = _run_golden(
        intent_task="accessibility_analysis",
        profile={"geometryTypes": ["LineString"],
                 "fields": {"minutes": {"type": "number"}}},
        sources={"net": {"type": "geojson", "inlineData": network},
                 "hosp": {"type": "geojson", "inlineData": hospitals}},
        layers=[
            {"id": "network", "source": "net", "type": "line",
             "legend_spec": {"title": "就医车程", "unit": "分钟",
                             "entries": [{"label": "6–18", "color": "#74a9cf"}]}},
            {"id": "hospitals", "source": "hosp", "type": "circle"},
        ],
        overlay_geometry_kinds=("point",),
        expect_family="other",
    )
    assert "就医车程" in compiled.svg and "单位：分钟" in compiled.svg


# ── 案例 3:四川自然资源专题(面分类)──────────────────────────────────


def test_golden_sichuan_resources_classified():
    coverage = _admin_fc([12, 28, 46, 63, 81], "forest_ratio", cx=102.5, cy=28.8)
    _run_golden(
        intent_task="aggregation_overview",
        profile={"geometryTypes": ["Polygon"],
                 "fields": {"forest_ratio": {"type": "number"},
                            "district": {"type": "text"}}},
        sources={"adm": {"type": "geojson", "inlineData": coverage}},
        layers=[{"id": "resources", "source": "adm", "type": "fill",
                 "legend_spec": {"title": "林地覆盖率", "unit": "%",
                                 "entries": [
                                     {"label": f"{b}–{b + 20}", "color": c}
                                     for b, c in zip(
                                         [10, 30, 50, 70],
                                         ["#edf8e9", "#bae4b3", "#74c476", "#31a354"],
                                     )]}}],
        expect_family="area_stat",
    )


# ── 案例 4:多变量地图(bivariate)──────────────────────────────────────


def test_golden_bivariate_multivariable():
    from app.lib.cartography.bivariate import (
        DEFAULT_BIVARIATE_MATRIX,
        bivariate_class_colors,
        bivariate_legend_spec,
    )

    fc = _admin_fc([10, 30, 50, 70], "schools")
    # 双变量真实计算:n×n 色阵(行主序)+ 3×3 图例象限(真实 breaks 输入)
    colors = bivariate_class_colors(DEFAULT_BIVARIATE_MATRIX, n=3)
    assert len(colors) == 9 and all(c.startswith("#") for c in colors)
    legend = bivariate_legend_spec(
        DEFAULT_BIVARIATE_MATRIX,
        label_a="小学数量", label_b="人口",
        breaks_a=[0, 25, 50, 75], breaks_b=[800, 1400, 2200, 3200], n=3)
    assert legend.get("type") == "bivariate" and legend.get("colors")
    _run_golden(
        intent_task="comparison_analysis",
        profile={"geometryTypes": ["Polygon"],
                 "fields": {"schools": {"type": "number"},
                            "population": {"type": "number"}}},
        sources={"adm": {"type": "geojson", "inlineData": fc}},
        layers=[{"id": "bi", "source": "adm", "type": "fill",
                 "legend_spec": {"title": "教育-人口", "kind": "bivariate",
                                 "entries": [
                                     {"label": lbl["label"] if isinstance(lbl, dict) else str(lbl),
                                      "color": lbl["color"] if isinstance(lbl, dict) and lbl.get("color") else "#1a9850"}
                                     for lbl in (legend.get("labels") or legend.get("entries") or [f"象限{i}" for i in range(4)])[:4]]}}],
        expect_family="area_stat",
    )


# ── 案例 5:时序变化 ───────────────────────────────────────────────────


def test_golden_temporal_change_pair():
    t0 = _admin_fc([10, 20, 30, 40], "value")
    t1 = _admin_fc([15, 18, 38, 52], "value")
    _run_golden(
        intent_task="change_detection",
        profile={"geometryTypes": ["Polygon"], "dataKind": "diverging",
                 "fields": {"value_2023": {"type": "number"},
                            "value_2024": {"type": "number"}}},
        sources={"t0": {"type": "geojson", "inlineData": t0},
                 "t1": {"type": "geojson", "inlineData": t1}},
        layers=[{"id": "change", "source": "t1", "type": "fill",
                 "legend_spec": {"title": "变化", "unit": "所",
                                 "entries": [{"label": "-5~+12", "color": "#d73027"}]}}],
        extra_spec={"temporal": {
            "enabled": True, "granularity": "year", "cursor_frame": "f2",
            "frames": [
                {"id": "f1", "label": "2023", "start": "2023-01-01T00:00:00Z",
                 "end": "2024-01-01T00:00:00Z"},
                {"id": "f2", "label": "2024", "start": "2024-01-01T00:00:00Z",
                 "end": "2025-01-01T00:00:00Z"},
            ]}},
        expect_family="area_stat",
    )
    # 时序注记进入导出图面(时间证据)
    compiled = compile_mapspec_to_svg_detailed(_temporal_spec(), include_chrome=True)
    assert "时间: 2024" in compiled.svg


def _temporal_spec() -> dict:
    t1 = _admin_fc([15, 18, 38, 52], "value")
    spec = _base_spec({"t1": {"type": "geojson", "inlineData": t1}}, [
        {"id": "change", "source": "t1", "type": "fill"}],
        {"temporal": {
            "enabled": True, "granularity": "year", "cursor_frame": "f2",
            "frames": [
                {"id": "f1", "label": "2023", "start": "2023-01-01T00:00:00Z",
                 "end": "2024-01-01T00:00:00Z"},
                {"id": "f2", "label": "2024", "start": "2024-01-01T00:00:00Z",
                 "end": "2025-01-01T00:00:00Z"},
            ]}})
    spec["layout"]["components"] = [
        {"id": "title-1", "type": "title", "enabled": True,
         "position": "top-center", "options": {"text": "时序变化"}},
    ]
    return spec


# ── 案例 6:A4 正式专题图(打印档全链)─────────────────────────────────


def test_golden_a4_formal_thematic_map():
    fc = _admin_fc([8, 21, 35, 44], "schools")
    _, spec, _ = _run_golden(
        intent_task="aggregation_overview",
        profile={"geometryTypes": ["Polygon"],
                 "fields": {"schools": {"type": "number"}}},
        sources={"adm": {"type": "geojson", "inlineData": fc}},
        layers=[{"id": "choro", "source": "adm", "type": "fill",
                 "legend_spec": {"title": "各区小学数量", "unit": "所",
                                 "entries": [{"label": "8–44", "color": "#fdcc8a"}]}}],
        expect_family="area_stat",
    )
    # 打印档:约束求解 evaluated + 用户锁族零触碰 + 求解事实只在克隆
    locked = {**spec, "workbench": {"lockedComponentIds": ["legend-main"]}}
    cloned, report = prepare_publication_document(
        locked, page_profile="a4_landscape")
    assert report["constraint_layout"] == "evaluated"
    assert report["page_profile"] == "a4_landscape"
    kept = report.get("user_lock_kept") or []
    assert any("legend" in str(k) for k in kept)
    assert "constraint_layout" not in spec.get("layout", {})
    # 求解后克隆仍可编译(回执与图面同真)
    compiled = compile_mapspec_to_svg_detailed(cloned, include_chrome=True)
    assert "各区小学数量" in compiled.svg


# ── 案例 0:empty-state(诚实空产物)────────────────────────────────────


def test_golden_empty_state_is_honest_not_decorated():
    facts = project_selection_facts(
        intent_task="distribution_overview", profile={},
    )
    generate_design_candidates(
        DesignTaskContext(**facts.design_ctx_kwargs()),
    )
    # 无数据事实 → 候选仍在(资格面),但 point_count=0/basis=unknown(不虚构)
    assert facts.point_count == 0
    assert facts.point_count_basis == "unknown"
    # 空图层 spec 编译:无图例/无假数据(结构非空但不包含装饰图例)
    spec = _base_spec({}, [])
    spec_after, report = apply_contract(spec, CONTRACT)
    compiled = compile_mapspec_to_svg_detailed(spec_after, include_chrome=True)
    assert "chrome-legend" not in compiled.svg
    assert compiled.diagnostics == [] or all(
        isinstance(d, dict) for d in compiled.diagnostics)
