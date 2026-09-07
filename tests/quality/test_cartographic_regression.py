"""制图跨系统回归认证（ADR-0104 Wave 14）。

不是前端 golden 重写，而是**用服务端权威机器**做跨系统语义认证：
分析产物 → 转换器（analysis_cartography_converter）→ MapSpecLifecycleEngine
提交 → mapspec_store 观测 → 语义评审机器（semantic_checks）/ 终验机器
（completion）/ 纯 Python SVG 导出（mapspec_to_svg）逐环对账。

覆盖面（对应 goal 的九个场景）：
1. expected/observed 图层 parity（转换器决定 id/kind，提交后零丢失零改名）；
2. 可见性/透明度/z-order（含 user-wins 语义）；
3. 图例/色条/图表面板在位（choropleth 必须有图例；连续场必须有 colorbar）；
4. 派生范围（bbox 包含数据、无 NaN、视口建议可导出）；
5. 导出（mapspec_to_svg 纯 Python 路径，无 Node/Chromium，不 REQUIRE_BROWSER）；
6. 超长标注（200+ 字符）；
7. 空图层（空 FC → 类型化降级，绝不静默 complete）；
8. 失效 artifact（stale bound_ref → 过期判定，不静默）；
9. 非法 source 引用（悬空 source / 无载体 source → 类型化发现）。

KNOWN-GAP（xfail strict=False，修复后自动转绿）：
1. 【超长标注】SVG 导出路径（app/services/mapspec_to_svg.py:625-722）把
   200+ 字符标注文本**全量内嵌**，无截断标记、无布局告警通道 —— 地图外
   的读图者会得到溢出图面的文本且系统不披露该退化。
2. 【图例可见性突变】经分析转换器路径提交图层后，引擎的
   ``SetLayoutIntent(legend={"visible": False})`` 被**静默丢弃**：
   is_error=False、mutation_revision 照常递增，但 committed spec 的
   layout.legend 仍为 visible:true；同一状态下 margins 突变可正常提交、
   普通 FC 图层（非转换器产物）路径无此问题。图例隐藏的**评审语义**
   （carto.legend.completeness fail）本身是健全的 —— 本套件用 store 级
   save_mapspec 构造隐藏态单独认证（与 repo 场景测试构造坏 spec 同款手法）。

全部离线、确定、无网络无 LLM 无 sleep；数据一律来自
tests/fixtures/gis_samples.py 合成夹具。
"""
from __future__ import annotations

import re
import shutil
import uuid

import pytest

from tests.fixtures.gis_samples import point_fc, polygon_fc

from app.services.gis_harness.components import build_default_components
from app.services.gis_harness.map_completion import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NEEDS_REPAIR,
    run_map_finalization,
)
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    PatchComponentIntent,
    PatchLayerPresentationIntent,
    ReorderLayersIntent,
    SetLayoutIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


# ── 夹具（与 tests/unit/test_map_product_finalization_scenarios.py 同款）──


@pytest.fixture
async def clean_session():
    sid = f"carto-reg-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


async def _upsert_choropleth(sid: str) -> dict:
    """经真实转换器把 polygon FC 升格为分级填色（choropleth）图层并提交。"""
    from app.lib.cartography.thematic_spec import build_graduated_spec

    fc = polygon_fc(n=8)
    legend_spec = build_graduated_spec(fc, "value")
    assert legend_spec is not None, "8 个非恒定值必须可分级"
    artifact = {"algorithm": "create_thematic_map", "legend_spec": legend_spec, "data": fc}

    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    res = await engine.apply_mutation(
        sid, UpsertLayerIntent(layer={}, source_data=artifact)
    )
    assert not res.is_error, res.error_msg
    return await mapspec_store_instance.get_mapspec(sid)


async def _commit_components(sid: str, components: list) -> None:
    engine = MapSpecLifecycleEngine()
    for c in components:
        await engine.apply_mutation(
            sid,
            PatchComponentIntent(
                component_id=c.id,
                component_type=c.type,
                enabled=c.enabled,
                position=c.position,
                options=c.options,
                upsert=True,
            ),
        )


def _component_types(spec: dict) -> set:
    return {
        c.get("type") for c in ((spec.get("layout") or {}).get("components") or [])
    }


# ── 场景 1：expected/observed 图层 parity ────────────────────────────────


@pytest.mark.parametrize("builder,expected_kind", [
    (point_fc, "circle"),
    (polygon_fc, "fill"),
], ids=["point-circle", "polygon-fill"])
async def test_analysis_artifact_layer_parity_expected_vs_observed(
    clean_session, builder, expected_kind
):
    """分析产物 → 转换器 → 提交 → 观测：id/kind 逐一相等（无静默丢层/改名）。"""
    from app.services.analysis_cartography_converter import (
        convert_analysis_to_mapspec_layer,
    )

    fc = builder()
    artifact = {"algorithm": "create_thematic_map", "data": fc}
    expected_layer, inline, warnings = convert_analysis_to_mapspec_layer(dict(artifact))
    assert expected_layer["type"] == expected_kind
    assert not [w for w in warnings if "no_geometries" in w]

    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    res = await engine.apply_mutation(
        clean_session, UpsertLayerIntent(layer={}, source_data=artifact)
    )
    assert not res.is_error, res.error_msg

    spec = await mapspec_store_instance.get_mapspec(clean_session)
    observed = [(l["id"], l["type"]) for l in spec["layers"]]
    assert observed == [(expected_layer["id"], expected_layer["type"])], observed
    # source 面也必须在册且带派生 profile（观测不缺证据）
    src = (spec.get("sources") or {}).get(expected_layer["source"]) or {}
    assert src.get("profile", {}).get("featureCount") == len(fc["features"])
    # 交叉认证：几何种类 × 图层种类在评审机器下通过（kind parity 的语义面）
    from app.lib.cartography.semantic_checks import evaluate_cartography_semantics

    report = evaluate_cartography_semantics(spec)
    geo = [c for c in report.checks if c.rule == "GEOMETRY_LAYER_TYPE"]
    assert geo and all(c.status == "pass" for c in geo), \
        [c.to_dict() for c in geo]


# ── 场景 2：可见性 / 透明度（expected vs observed 状态）──────────────────


async def test_presentation_patch_expected_state_matches_observed(clean_session):
    """agent 呈现面补丁：visible/opacity 的期望态与提交后观测态逐字段相等。"""
    fc = point_fc(n=6)
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    layer = {"id": "pts", "source": "s-pts", "type": "circle",
             "paint": {"circle-color": "#e53e3e", "circle-radius": 5},
             "layout": {"visibility": "visible"}}
    await engine.apply_mutation(
        clean_session, UpsertLayerIntent(layer=layer, source_data=fc)
    )

    await engine.apply_mutation(
        clean_session,
        PatchLayerPresentationIntent(layer_id="pts", visible=False, opacity=0.3),
    )
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    ly = next(l for l in spec["layers"] if l["id"] == "pts")
    assert (ly.get("layout") or {}).get("visibility") == "none"
    paint = ly.get("paint") or {}
    observed_opacity = paint.get("circle-opacity", paint.get("opacity"))
    assert observed_opacity == 0.3

    # 复原：期望可见 → 观测可见（同一条通道，双向可对账）
    await engine.apply_mutation(
        clean_session, PatchLayerPresentationIntent(layer_id="pts", visible=True)
    )
    spec2 = await mapspec_store_instance.get_mapspec(clean_session)
    ly2 = next(l for l in spec2["layers"] if l["id"] == "pts")
    assert (ly2.get("layout") or {}).get("visibility") != "none"


async def test_zorder_reorder_expected_vs_observed(clean_session):
    """z-order：ReorderLayersIntent 的请求顺序 = 提交后的观测顺序。"""
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    for lid, src in (("base", "s-a"), ("top", "s-b")):
        await engine.apply_mutation(
            clean_session,
            UpsertLayerIntent(
                layer={"id": lid, "source": src, "type": "circle",
                       "paint": {"circle-color": "#333"}},
                source_data=point_fc(n=5),
            ),
        )
    await engine.apply_mutation(
        clean_session, ReorderLayersIntent(layer_ids=["top", "base"])
    )
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    assert [l["id"] for l in spec["layers"]] == ["top", "base"]


async def test_user_wins_hide_disclosed_not_overridden(clean_session):
    """user-wins 语义：用户显式隐藏结果层是**期望态** —— 终验以 warning
    披露、零修复突变、仍 complete（绝不静默改回或永续 needs_repair）。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation_batch

    spec = await _upsert_choropleth(clean_session)
    lid = spec["layers"][0]["id"]
    await _commit_components(
        clean_session,
        build_default_components(
            primary_cartography="administrative_choropleth", title="分级填色"
        ),
    )
    poi_ref = await session_data_manager.store(clean_session, point_fc(n=5), prefix="geojson")
    chapter = {
        "plan_id": f"plan-{uuid.uuid4().hex[:8]}",
        "query": "分区数值分级图",
        "recipe_id": "choropleth_map",
        "data_requirements": [
            {"capability": "poi_query", "purpose": "底数",
             "status": "available", "bound_ref": poi_ref, "optional": False},
        ],
        "analysis_steps": [],
        "map_layers": [{"role": "primary", "layer_id": lid, "enabled": True}],
        "template_selection": {"composition_template_id": "composition.minimal_interactive"},
        "components": [],
    }

    state = await session_data_manager.get_map_state(clean_session)
    await apply_gis_mutation_batch(
        clean_session,
        [PatchLayerPresentationIntent(layer_id=lid, visible=False)],
        origin="user",
        actor="cartographic-regression",
        expected_revision=int(state.get("_cartographic_mutation_revision") or 0),
    )

    result = await run_map_finalization(
        clean_session, chapter=chapter, reason="wave14_user_wins"
    )
    hidden = [f for f in result.findings if f.code == "layer_hidden"]
    assert hidden and hidden[0].severity == "warning", \
        [f.to_dict() for f in result.findings]
    assert not any(r.startswith("show_layer:") for r in result.repairs_applied)
    assert result.status == STATUS_COMPLETE


# ── 场景 3：图例 / 色条 / 图表面板在位 ───────────────────────────────────


async def test_choropleth_requires_legend_and_semantic_review_certifies(clean_session):
    """choropleth：图例是必需品。

    - 图例通道在位时，评审机器判 legend 完备通过、图例×样式零漂移；
    - 显式隐藏地图级图例 → carto.legend.completeness 类型化 fail（不是
      静默的"看起来没图例也行"）。
    """
    from app.lib.cartography.semantic_checks import evaluate_cartography_semantics

    spec = await _upsert_choropleth(clean_session)

    report = evaluate_cartography_semantics(spec)
    drift = [f for f in report.findings if f.check == "LEGEND_STYLE_EQUIVALENCE"
             and f.severity == "error"]
    assert not drift, [f.to_dict() for f in drift]
    legend_checks = [c for c in report.checks if c.rule == "carto.legend.completeness"]
    assert legend_checks and all(c.status == "pass" for c in legend_checks)

    # 图例隐藏态的评审认证：经 store 级 save 构造（引擎突变路径的静默丢弃
    # 见 KNOWN-GAP #2 的 xfail —— 与 repo 场景测试构造坏 spec 同款手法）。
    spec_hidden = dict(spec)
    spec_hidden["layout"] = dict(spec.get("layout") or {})
    spec_hidden["layout"]["legend"] = {"visible": False}
    await mapspec_store_instance.save_mapspec(clean_session, spec_hidden)
    spec2 = await mapspec_store_instance.get_mapspec(clean_session)
    assert (spec2.get("layout", {}).get("legend") or {}).get("visible") is False

    report2 = evaluate_cartography_semantics(spec2)
    hidden = [c for c in report2.checks if c.rule == "carto.legend.completeness"]
    assert hidden and all(c.status == "fail" for c in hidden), \
        [c.to_dict() for c in hidden]
    assert report2.ok is False


@pytest.mark.xfail(
    strict=False,
    reason=(
        "KNOWN-GAP #2（图例可见性突变）：经分析转换器路径（UpsertLayerIntent "
        "layer={} + analysis artifact）提交图层后，引擎的 "
        "SetLayoutIntent(legend={{'visible': False}}) 被静默丢弃 —— "
        "is_error=False、mutation_revision 递增，但 committed layout.legend "
        "仍 visible:true（同状态 margins 突变正常、plain FC 图层路径正常）。"
        "修复（突变如实落账或显式拒绝）后本测试自动转绿。"
    ),
)
async def test_legend_hide_mutation_is_observed_after_converter_upsert(clean_session):
    """期望态（隐藏地图图例）必须等于提交后的观测态 —— 静默丢弃即违规。"""
    spec = await _upsert_choropleth(clean_session)
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        clean_session, SetLayoutIntent(legend={"visible": False})
    )
    assert not res.is_error, res.error_msg
    observed = await mapspec_store_instance.get_mapspec(clean_session)
    observed_visible = (observed.get("layout", {}).get("legend") or {}).get("visible")
    assert observed_visible is False, (
        f"SetLayout legend 突变被静默丢弃: layout.legend={observed_visible!r}"
    )


async def test_colorbar_for_continuous_field_and_chart_panel_on_request(clean_session):
    """连续场（heatmap 面）→ colorbar 组件必需；图表请求 → chart_panel 在位。

    组件集经真实推导器（build_default_components，模型库权威）+ 生命周期
    引擎提交，从 committed spec 观测。
    """
    fc = point_fc(n=6)
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        UpsertLayerIntent(
            layer={"id": "heat", "source": "s-heat", "type": "heatmap",
                   "paint": {"heatmap-weight": 1},
                   "layout": {"visibility": "visible"}},
            source_data=fc,
        ),
    )
    await _commit_components(
        clean_session,
        build_default_components(
            primary_cartography="visual_heatmap",
            title="密度连续场",
            extra_types=["chart_panel"],
        ),
    )
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    types = _component_types(spec)
    assert {"continuous_colorbar", "chart_panel", "title"} <= types, types

    # 对照：choropleth 的图例组件是离散 legend，而不是 colorbar
    await _commit_components(
        clean_session,
        build_default_components(
            primary_cartography="administrative_choropleth", title="分级填色"
        ),
    )
    spec2 = await mapspec_store_instance.get_mapspec(clean_session)
    types2 = _component_types(spec2)
    assert "legend" in types2, types2


# ── 场景 4：派生范围（bbox 包含数据、无 NaN）─────────────────────────────


async def test_derived_extent_sane_contains_data_no_nan(clean_session):
    """引擎派生范围三重认证：有限有序、包含全部要素坐标、视口建议居中。"""
    import math

    fc = point_fc(n=12)
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        UpsertLayerIntent(
            layer={"id": "pts", "source": "s-pts", "type": "circle",
                   "paint": {"circle-color": "#e53e3e"}},
            source_data=fc,
        ),
    )
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    profile = (spec["sources"]["s-pts"]).get("profile") or {}
    bbox = profile.get("bbox")
    assert isinstance(bbox, list) and len(bbox) == 4
    assert all(math.isfinite(v) for v in bbox), bbox
    minx, miny, maxx, maxy = bbox
    assert minx <= maxx and miny <= maxy
    lons = [f["geometry"]["coordinates"][0] for f in fc["features"]]
    lats = [f["geometry"]["coordinates"][1] for f in fc["features"]]
    assert minx <= min(lons) and maxx >= max(lons)
    assert miny <= min(lats) and maxy >= max(lats)
    # 视口建议由 bbox 派生（前端据此聚焦），中心必须落在数据范围内
    view = spec.get("view") or {}
    center = view.get("center")
    assert center and all(math.isfinite(float(c)) for c in center)
    assert minx <= center[0] <= maxx and miny <= center[1] <= maxy
    assert isinstance(view.get("zoom"), (int, float))


# ── 场景 5：导出（服务端 SVG 路径，无浏览器）─────────────────────────────


async def test_svg_export_pure_python_deterministic(clean_session):
    """committed MapSpec → compile_mapspec_to_svg：可渲染、几何在图、双跑逐位一致。"""
    from app.services.mapspec_to_svg import compile_mapspec_to_svg

    spec = await _upsert_choropleth(clean_session)
    svg1 = compile_mapspec_to_svg(spec, width=400, height=300)
    svg2 = compile_mapspec_to_svg(spec, width=400, height=300)
    assert svg1 == svg2, "导出必须确定（同 spec 双跑逐位一致）"
    assert svg1.startswith("<svg"), "产出必须是 SVG 文档"
    assert "<path" in svg1, "面图层几何必须落到图上"
    assert "</svg>" in svg1


# ── 场景 6：超长标注（200+ 字符）─────────────────────────────────────────


def _long_label_mapspec(label_chars: int = 220) -> dict:
    fc = point_fc(n=3)
    long_label = "地" * label_chars
    for f in fc["features"]:
        f["properties"]["label"] = long_label
    return {
        "sources": {"s1": {"type": "geojson", "inlineData": fc}},
        "layers": [{
            "id": "labeled",
            "type": "symbol",
            "source": "s1",
            "layout": {"visibility": "visible", "text-field": "{label}"},
            "paint": {"text-color": "#1a202c"},
        }],
    }


def test_long_label_svg_export_does_not_crash():
    """200+ 字符标注：导出不得崩溃，且必须仍是合法 SVG 文档。"""
    from app.services.mapspec_to_svg import compile_mapspec_to_svg

    svg = compile_mapspec_to_svg(_long_label_mapspec(), width=400, height=300)
    assert svg.startswith("<svg") and "</svg>" in svg
    assert "<text" in svg


@pytest.mark.xfail(
    strict=False,
    reason=(
        "KNOWN-GAP #1（超长标注）：mapspec_to_svg 的文本路径把 200+ 字符标注"
        "全量内嵌（:625-722 无截断/省略），也没有任何布局告警披露 —— 语义上"
        "图面必然溢出而系统不声明该退化。修复（截断标记或告警披露）后自动转绿。"
    ),
)
def test_long_label_truncation_or_layout_warning_disclosed():
    """超长标注必须有界化结局：截断落图，或以警告/披露字段声明。"""
    from app.services.mapspec_to_svg import compile_mapspec_to_svg

    spec = _long_label_mapspec()
    full_label = "地" * 220
    svg = compile_mapspec_to_svg(spec, width=400, height=300)
    # 两条可接受的诚实结局（满足其一即可）：
    # (a) 截断：全文不被原样嵌入；
    disclosed = full_label not in svg
    # (b) 披露：编译产物带布局告警/截断标记通道。
    warning_channel = bool(
        re.search(r"truncat|overflow|label_too_long", svg, flags=re.IGNORECASE)
    )
    assert disclosed or warning_channel, (
        "超长标注被全量内嵌且无任何截断/告警披露 —— 静默退化"
    )


# ── 场景 7：空图层（空 FC → 类型化降级，绝不静默 complete）───────────────


async def test_empty_layer_source_is_typed_degradation_not_silent(clean_session):
    """两道认证面：
    (a) 语义评审：空 source → EMPTY_DATA error + RESULT_DATA_PRESENCE fail，
        评审整体绝不 ok；
    (b) 终验机器：零要素 artifact → empty_result warning finding（显式降级）。
    """
    from app.lib.cartography.semantic_checks import evaluate_cartography_semantics

    empty_fc = {"type": "FeatureCollection", "features": []}
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        UpsertLayerIntent(
            layer={"id": "empty", "source": "s-empty", "type": "circle",
                   "paint": {"circle-color": "#333"}},
            source_data=empty_fc,
        ),
    )
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    report = evaluate_cartography_semantics(spec)
    codes = {c.rule: c.status for c in report.checks}
    assert codes.get("RESULT_DATA_PRESENCE") == "fail", codes
    assert any(f.check == "EMPTY_DATA" and f.severity == "error"
               for f in report.findings), [f.to_dict() for f in report.findings]
    assert report.ok is False, "空数据源绝不能拿到 complete 评审结论"

    # (b) 终验面：零要素 artifact 绑定在案 → empty_result 显式披露
    empty_ref = await session_data_manager.store(clean_session, empty_fc, prefix="geojson")
    await _commit_components(
        clean_session,
        build_default_components(primary_cartography="point_overlay", title="空数据"),
    )
    chapter = {
        "plan_id": f"plan-{uuid.uuid4().hex[:8]}",
        "query": "空数据图",
        "recipe_id": "poi_map",
        "data_requirements": [
            {"capability": "poi_query", "purpose": "x",
             "status": "available", "bound_ref": empty_ref, "optional": False},
        ],
        "analysis_steps": [],
        "map_layers": [{"role": "primary", "layer_id": "empty", "enabled": True}],
        "template_selection": {"composition_template_id": "composition.minimal_interactive"},
        "components": [],
    }
    result = await run_map_finalization(
        clean_session, chapter=chapter, reason="wave14_empty_layer"
    )
    assert any(f.code == "empty_result" for f in result.findings), \
        [f.to_dict() for f in result.findings]


# ── 场景 8：失效 artifact（stale bound_ref）──────────────────────────────


async def test_stale_artifact_ref_is_detected_not_silent(clean_session):
    """bound_ref 指向已不存在的 artifact → artifact_expired error → 不 complete。"""
    spec = await _upsert_choropleth(clean_session)
    lid = spec["layers"][0]["id"]
    await _commit_components(
        clean_session,
        build_default_components(
            primary_cartography="administrative_choropleth", title="分级填色"
        ),
    )
    chapter = {
        "plan_id": f"plan-{uuid.uuid4().hex[:8]}",
        "query": "引用已失效产物的图",
        "recipe_id": "choropleth_map",
        "data_requirements": [
            {"capability": "poi_query", "purpose": "x", "status": "available",
             "bound_ref": "ref:geojson-evicted-0000", "optional": False},
        ],
        "analysis_steps": [],
        "map_layers": [{"role": "primary", "layer_id": lid, "enabled": True}],
        "template_selection": {"composition_template_id": "composition.minimal_interactive"},
        "components": [],
    }
    result = await run_map_finalization(
        clean_session, chapter=chapter, reason="wave14_stale_artifact"
    )
    codes = {f.code for f in result.findings}
    assert "artifact_expired" in codes, [f.to_dict() for f in result.findings]
    assert result.status in (STATUS_FAILED, STATUS_NEEDS_REPAIR)
    assert not (result.status == STATUS_COMPLETE)


# ── 场景 9：非法 source 引用（悬空 / 无载体）─────────────────────────────


def test_invalid_source_references_typed_detection():
    """评审机器的两类非法 source 引用都是类型化 error：
    - 悬空：layer 引用不存在于 sources 的 id（SOURCE_LAYER_REF）；
    - 无载体：source 有登记但无 inlineData/ref/url/dataPath 任何运行时载体
      （SOURCE_ADDRESSABILITY）；两者都必须让评审整体不再 ok。
    """
    from app.lib.cartography.semantic_checks import evaluate_cartography_semantics

    fc = point_fc(n=3)

    dangling = {
        "sources": {"s-real": {"type": "geojson", "inlineData": fc}},
        "layers": [{"id": "ghost", "type": "circle", "source": "s-ghost",
                    "paint": {"circle-color": "#333"}}],
    }
    r1 = evaluate_cartography_semantics(dangling)
    assert any(f.check == "SOURCE_LAYER_REF" and f.severity == "error"
               for f in r1.findings), [f.to_dict() for f in r1.findings]
    assert r1.ok is False

    carrierless = {
        "sources": {"s-empty-meta": {"type": "geojson"}},
        "layers": [{"id": "lyr", "type": "circle", "source": "s-empty-meta",
                    "paint": {"circle-color": "#333"}}],
    }
    r2 = evaluate_cartography_semantics(carrierless)
    addr = [c for c in r2.checks if c.rule == "SOURCE_ADDRESSABILITY"]
    assert addr and all(c.status == "fail" for c in addr), \
        [c.to_dict() for c in addr]
    assert r2.ok is False


def test_malformed_inline_source_geometry_flagged_by_review():
    """畸形内联数据（声明 FC 却没有 features 列表）不被当成可渲染成功：
    要素计数证据缺失 → RESULT_DATA_PRESENCE 不得是 pass。"""
    from app.lib.cartography.semantic_checks import evaluate_cartography_semantics

    malformed = {
        "sources": {"s-bad": {"type": "geojson", "inlineData": {"type": "FeatureCollection"}}},
        "layers": [{"id": "lyr", "type": "circle", "source": "s-bad",
                    "paint": {"circle-color": "#333"}}],
    }
    report = evaluate_cartography_semantics(malformed)
    presence = [c for c in report.checks if c.rule == "RESULT_DATA_PRESENCE"]
    assert presence, "数据在位性必须被评审"
    assert all(c.status != "pass" for c in presence), \
        [c.to_dict() for c in presence]
    assert report.ok is False
