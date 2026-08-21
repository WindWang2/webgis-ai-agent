"""
Flip-red proof for #676 — must run RED on base code, GREEN after fix.

Covers:
- apply_template composite KeyError vs explicit error/expand
- composite_school_service_area four-way consistency
- combine_map_theme thematic dead slot
- CompositeMapSpecBuilder validate fail-loud and source empty shell
- Iter2 blocking: lifecycle submission (session_id+geojson) + real inlineData + heat palette + no geojson echo
"""
import pytest
from app.tools.registry import ToolRegistry
from app.tools.templates import register_template_tools, combine_map_theme
from app.schemas.template_registry import COMPOSITE_TEMPLATES, get_template_registry
from app.services.mapspec.composite_builder import CompositeMapSpecBuilder


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_template_tools(reg)
    return reg

SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"population": 10, "v": 1}, "geometry": {"type": "Point", "coordinates": [0, 0]}},
        {"type": "Feature", "properties": {"population": 20, "v": 2}, "geometry": {"type": "Point", "coordinates": [1, 1]}},
    ],
}


# 1. apply_template on composite must NOT KeyError 'payload'
@pytest.mark.asyncio
async def test_apply_template_composite_not_payload_keyerror(registry):
    composite_id = "composite_school_service_area"
    result = await registry.dispatch("apply_template", {"template_id": composite_id})
    assert "error" in result or "status" in result
    if "error" in result:
        combined = result.get("error", "") + result.get("message", "") + result.get("correction_hint", "")
        assert "'payload'" not in combined, f"still KeyError payload: {result}"
        assert "layer attributes" not in combined.lower(), f"misleading hint: {result}"
        assert any(k in combined.lower() for k in ["field", "composite", "expand", "pipeline", "thematic", "geojson", "session"]), f"error should guide to correct usage, got: {result}"
    else:
        assert result.get("status") in ("composite_applied", "composite_expanded", "composite_map_assembled", "template_applied") or "composite" in str(result).lower()


@pytest.mark.asyncio
async def test_apply_template_all_composites_no_payload_error(registry):
    for c in COMPOSITE_TEMPLATES:
        cid = c["id"]
        result = await registry.dispatch("apply_template", {"template_id": cid})
        if "error" in result:
            assert "'payload'" not in result["error"], f"{cid} still payload KeyError: {result}"
            assert result.get("code") != "NOT_FOUND" or "'payload'" not in result.get("message", "")


# 2. composite_school_service_area four-way consistency
def test_composite_school_service_area_consistency():
    reg = get_template_registry()
    school = reg.get("composite_school_service_area")
    assert school is not None
    pipeline = school.get("pipeline", {})
    thematic_id = pipeline.get("thematic")
    thematic_tmpl = reg.get(thematic_id) if thematic_id else None
    assert thematic_tmpl is not None, f"pipeline thematic {thematic_id} missing"
    payload = thematic_tmpl.get("payload", {})
    expected_method = payload.get("method")
    expected_palette = payload.get("palette")
    preview = school.get("preview_metadata", {})
    assert preview.get("method") == expected_method, f"preview method {preview.get('method')} != pipeline method {expected_method}"
    assert preview.get("palette") == expected_palette, f"preview palette {preview.get('palette')} != {expected_palette}"
    name = school.get("name", "")
    desc = school.get("description", "")
    assert "样式组合" in desc or "style preset" in desc.lower() or "preset" in desc.lower(), f"description should be honest style preset: {desc}"
    assert "等间隔" not in desc or expected_method == "equal_interval", f"description claims equal_interval but pipeline is {expected_method}: {desc}"
    assert "学区服务范围" not in name or "样式" in name or "预设" in name, f"name still pretends analysis: {name}"


# 3. All 22 composites honest (spot check)
def test_all_composites_honest_no_fake_analysis():
    for c in COMPOSITE_TEMPLATES:
        desc = (c.get("description") or "")
        name = c.get("name") or ""
        has_honest_marker = any(k in desc for k in ["样式组合", "需提供", "不含", "预设", "preset"]) or "样式" in name
        assert has_honest_marker, f"{c['id']} description still fake analysis without honest marker: {desc!r}"


# 4. combine_map_theme thematic dead slot — with field should emit legend_spec (or be removed)
@pytest.mark.asyncio
async def test_combine_thematic_with_field_emits_legend_spec_or_removed():
    from app.tools.templates import CombineMapThemeArgs
    import inspect
    sig = inspect.signature(combine_map_theme)
    has_field_param = "field" in sig.parameters
    has_thematic_in_schema = "thematic" in CombineMapThemeArgs.model_fields

    if has_thematic_in_schema and has_field_param:
        res = await combine_map_theme(thematic="tmpl_th_pop_choro", field="population", layer_id="test_layer", geojson=SAMPLE_GEOJSON)
        mapspec = res["mapspec"]
        layer = mapspec["layers"][0]
        assert "legend_spec" in layer, f"thematic with field should produce legend_spec, got keys {list(layer.keys())}: {layer}"
        assert layer["legend_spec"]["field"] == "population"
        assert "thematic" not in layer, "old dead key 'thematic' should be replaced by legend_spec"
        src = mapspec["sources"]["source_test_layer"]
        assert src.get("inlineData", {}).get("features"), "source should carry real inlineData when geojson provided"
    elif not has_thematic_in_schema:
        builder = CompositeMapSpecBuilder()
        mapspec = builder.assemble({"thematic": "tmpl_th_pop_choro"}, layer_id="x")
        layer = mapspec["layers"][0]
        assert "legend_spec" not in layer and "thematic" not in layer, "thematic dead slot should be gone"
    else:
        pytest.fail("thematic slot is still dead: field param missing or thematic not wired to legend_spec")


def test_builder_thematic_str_without_field_still_dead_or_clean():
    builder = CompositeMapSpecBuilder()
    builder.with_thematic("tmpl_th_pop_choro")
    mapspec = builder.assemble({}, layer_id="x")
    layer = mapspec["layers"][0]
    assert "legend_spec" not in layer or layer.get("legend_spec", {}).get("field") is None


# 5. Builder validate must fail-loud, not warn-and-return
def test_builder_validate_fail_loud():
    _builder = CompositeMapSpecBuilder()
    from app.services.mapspec.coordinator import validate
    invalid_mapspec = {"version": "1.0", "sources": {}, "layers": [{"id": "l1", "source": "missing", "type": "fill", "paint": {}}]}
    res = validate(invalid_mapspec)
    assert not res["success"]
    import unittest.mock as mock
    with mock.patch("app.services.mapspec.composite_builder.validate_mapspec", return_value={"success": False, "errors": [{"code": "MOCK_FAIL", "message": "mock"}]}):
        b2 = CompositeMapSpecBuilder()
        with pytest.raises((ValueError, RuntimeError, Exception)):
            b2.assemble({}, layer_id="y")


# 6. Builder source must carry real inlineData when geojson provided; without it empty is honest fallback
def test_builder_source_not_empty_shell():
    builder = CompositeMapSpecBuilder()
    mapspec = builder.assemble({"geojson": SAMPLE_GEOJSON}, layer_id="my_layer")
    source_id = "source_my_layer"
    assert source_id in mapspec["sources"]
    src = mapspec["sources"][source_id]
    assert src.get("inlineData", {}).get("features"), f"source should carry real inlineData when geojson provided, got {src}"
    assert src["inlineData"]["features"][0]["properties"]["v"] == 1
    builder2 = CompositeMapSpecBuilder()
    mapspec2 = builder2.assemble({}, layer_id="my_layer2")
    src2 = mapspec2["sources"]["source_my_layer2"]
    assert src2.get("inlineData") == {"type": "FeatureCollection", "features": []}, "without geojson, empty shell is honest fallback"


# 7. Iter2 blocking: composite requires session_id
@pytest.mark.asyncio
async def test_composite_requires_session_id(registry):
    result = await registry.dispatch("apply_template", {"template_id": "composite_population_density_analysis", "field": "population", "geojson": SAMPLE_GEOJSON})
    assert "error" in result
    combined = result.get("error", "") + result.get("message", "") + result.get("correction_hint", "")
    assert "session_id" in combined.lower() or "session" in combined.lower(), f"should require session_id: {result}"


# 8. Iter2 blocking: composite requires geojson
@pytest.mark.asyncio
async def test_composite_requires_geojson(registry):
    result = await registry.dispatch("apply_template", {"template_id": "composite_population_density_analysis", "field": "population", "session_id": "sess-xyz"})
    assert "error" in result
    combined = result.get("error", "") + result.get("message", "") + result.get("correction_hint", "")
    assert "geojson" in combined.lower(), f"should require geojson: {result}"


@pytest.mark.asyncio
async def test_composite_requires_geojson_even_with_session_and_field(registry):
    result = await registry.dispatch("apply_template", {"template_id": "composite_school_service_area", "field": "population", "session_id": "sess-abc"})
    assert "error" in result
    assert "geojson" in (result.get("error", "") + result.get("message", "")).lower()


# 9. Iter2: successful composite with all params commits via store and carries real inlineData, no top-level geojson echo
@pytest.mark.asyncio
async def test_composite_success_commits_and_has_real_inlineData(registry):
    import unittest.mock as mock
    mock_res = {
        "success": True,
        "is_compiled": True,
        "mapspec_fingerprint": "fp-xyz",
        "mapspec": {"version": "1.0", "sources": {"source_default_layer": {"type": "geojson", "inlineData": SAMPLE_GEOJSON}}, "layers": [{"id": "default_layer", "source": "source_default_layer", "type": "fill", "paint": {}, "legend_spec": {"type": "graduated", "field": "population"}}], "view": {}, "basemap": {"providerId": "carto-positron"}, "layout": {}, "thresholds": {}},
        "layer": {"id": "default_layer", "source": "source_default_layer", "type": "fill", "paint": {}, "legend_spec": {"type": "graduated", "field": "population"}},
    }
    with mock.patch("app.services.mapspec_store.mapspec_store.layer_upsert", new=mock.AsyncMock(return_value=mock_res)) as mock_upsert:
        result = await registry.dispatch("apply_template", {"template_id": "composite_population_density_analysis", "field": "population", "geojson": SAMPLE_GEOJSON, "session_id": "sess-test-123", "layer_id": "default_layer"})
        assert result.get("status") == "composite_applied", f"should be composite_applied: {result}"
        assert "error" not in result
        assert "geojson" not in result, f"top-level geojson echo should be removed: {list(result.keys())}"
        assert result.get("committed") is True, f"apply composite success must have committed True: {result}"
        assert mock_upsert.called, "should have called mapspec_store.layer_upsert"
        call_kwargs = mock_upsert.call_args
        assert call_kwargs[0][0] == "sess-test-123"
        assert call_kwargs[0][2] == SAMPLE_GEOJSON or call_kwargs[0][2].get("features")
        mapspec = result.get("mapspec")
        assert mapspec is not None
        src = list(mapspec.get("sources", {}).values())[0]
        has_real = bool(src.get("inlineData", {}).get("features") or src.get("ref") or src.get("ref_id"))
        assert has_real, f"mapspec source should have real data after commit: {src}"
        assert result.get("is_compiled") is True
        assert result.get("session_id") == "sess-test-123"


# 9b. combine_map_theme committed semantics
@pytest.mark.asyncio
async def test_combine_map_theme_committed_true_with_session_and_geojson():
    import unittest.mock as mock
    mock_res = {
        "success": True,
        "is_compiled": True,
        "mapspec_fingerprint": "fp-combine",
        "mapspec": {"version": "1.0", "sources": {"source_test_layer": {"type": "geojson", "inlineData": SAMPLE_GEOJSON}}, "layers": [{"id": "test_layer", "source": "source_test_layer", "type": "fill", "paint": {}}], "view": {}, "basemap": {"providerId": "carto-positron"}, "layout": {}, "thresholds": {}},
        "layer": {"id": "test_layer", "source": "source_test_layer", "type": "fill", "paint": {}},
    }
    with mock.patch("app.services.mapspec_store.mapspec_store.layer_upsert", new=mock.AsyncMock(return_value=mock_res)):
        res = await combine_map_theme(thematic="tmpl_th_pop_choro", field="population", layer_id="test_layer", geojson=SAMPLE_GEOJSON, session_id="sess-combine-1")
        assert res.get("status") == "composite_applied"
        assert res.get("committed") is True, f"with session+geojson should be committed True: {res}"
        assert res.get("session_id") == "sess-combine-1"


@pytest.mark.asyncio
async def test_combine_map_theme_not_committed_without_session():
    res = await combine_map_theme(thematic="tmpl_th_pop_choro", field="population", layer_id="test_layer", geojson=SAMPLE_GEOJSON)
    assert res.get("status") == "composite_map_assembled"
    assert res.get("committed") is False, f"without session should be committed False: {res}"
    assert "summary" in res and "已组装未提交" in res["summary"], f"should have summary about not committed: {res}"

    # Also without geojson (even with session/synthetic breaks) — still not committed
    import unittest.mock as mock
    with mock.patch("app.services.mapspec_store.mapspec_store.layer_upsert", new=mock.AsyncMock(return_value={"success": True})) as m:
        res2 = await combine_map_theme(thematic="tmpl_th_pop_choro", field="population", layer_id="test_layer2")
        assert res2.get("committed") is False
        assert not m.called, "without geojson, should not call layer_upsert even with field"


# 9c. apply composite also committed (symmetry)
@pytest.mark.asyncio
async def test_apply_composite_committed_true_symmetry(registry):
    import unittest.mock as mock
    mock_res = {
        "success": True,
        "is_compiled": True,
        "mapspec_fingerprint": "fp-apply-sym",
        "mapspec": {"version": "1.0", "sources": {"source_x": {"type": "geojson", "inlineData": SAMPLE_GEOJSON}}, "layers": [{"id": "x", "source": "source_x", "type": "fill", "paint": {}}], "view": {}, "basemap": {"providerId": "carto-positron"}, "layout": {}, "thresholds": {}},
        "layer": {"id": "x", "source": "source_x", "type": "fill", "paint": {}},
    }
    with mock.patch("app.services.mapspec_store.mapspec_store.layer_upsert", new=mock.AsyncMock(return_value=mock_res)):
        result = await registry.dispatch("apply_template", {"template_id": "composite_vegetation_health", "field": "population", "geojson": SAMPLE_GEOJSON, "session_id": "sess-sym-1", "layer_id": "x"})
        assert result.get("committed") is True


# 10. Heatmap legend palette consistency
def test_heatmap_legend_uses_heat_palette():
    """#717: the composite heatmap path now shares the SINGLE palette/paint
    contract with the analysis chain — legend colors are the native
    NATIVE_HEATMAP_COLORS stops that also drive heatmap-color, and
    heatmap-color is present (the old literal paint failed
    LEGEND_STYLE_EQUIVALENCE by construction)."""
    builder = CompositeMapSpecBuilder()
    builder.with_thematic("tmpl_th_heatmap")
    builder._thematic.field = "weight"
    mapspec = builder.assemble({}, layer_id="hm_layer")
    layer = mapspec["layers"][0]
    assert layer["type"] == "heatmap"
    assert "legend_spec" in layer, "heatmap should emit legend_spec"
    from app.lib.cartography.palettes import heatmap_legend_colors
    assert layer["legend_spec"]["palette_colors"] == heatmap_legend_colors("classic"), (
        f"heatmap legend must use the shared native stops, got {layer['legend_spec']['palette_colors']}"
    )
    paint = layer["paint"]
    assert "heatmap-color" in paint, "composite heatmap paint must carry heatmap-color"
    expr_colors = [v for v in paint["heatmap-color"][3:] if isinstance(v, str) and v.startswith("#")]
    assert expr_colors == heatmap_legend_colors("classic"), (
        "legend palette_colors must equal the opaque heatmap-color stops"
    )


def test_composite_choropleth_data_driven_breaks():
    """#717: with geojson+field, composite thematics classify through the
    single engine — breaks overlap the real data range instead of the
    synthetic [0,1] domain that failed CLASSIFICATION_DOMAIN_COVERAGE for any
    real field."""
    import random

    rng = random.Random(717)
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"population": rng.randint(120, 900)},
             "geometry": {"type": "Point", "coordinates": [float(i), float(i)]}}
            for i in range(40)
        ],
    }
    builder = CompositeMapSpecBuilder()
    builder.with_thematic("tmpl_th_pop_choro")
    mapspec = builder.assemble(
        {}, layer_id="pop_layer", field="population", geojson=fc,
    )
    layer = mapspec["layers"][0]
    ls = layer["legend_spec"]
    assert ls["type"] == "graduated"
    lo, hi = min(f["properties"]["population"] for f in fc["features"]), \
        max(f["properties"]["population"] for f in fc["features"])
    assert ls["breaks"][0] <= lo and ls["breaks"][-1] >= hi - 1e-9, (
        f"data-driven breaks must cover the data range [{lo},{hi}], got {ls['breaks']}"
    )
    assert any(b > 1.0 for b in ls["breaks"]), "breaks must not be the synthetic [0,1] domain"
    # paint driven by the same spec
    assert layer["paint"].get("color", {}).get("stops"), "paint must carry step stops"


def test_composite_heatmap_paint_matches_analysis_contract():
    """#717: composite heatmap paint == palettes.heatmap_paint output with the
    radius clamped into the contract window (no second styling system)."""
    from app.lib.cartography.palettes import heatmap_paint
    from app.lib.cartography.heatmap_contract import clamp_radius_px

    builder = CompositeMapSpecBuilder()
    builder.with_thematic("tmpl_th_heatmap")
    builder._thematic.field = "w"
    builder._thematic.radius = 95  # outside [4,80] — must clamp
    mapspec = builder.assemble({}, layer_id="hm2")
    layer = mapspec["layers"][0]
    expected = heatmap_paint("classic", clamp_radius_px(95))
    for key in ("heatmap-color", "heatmap-intensity", "heatmap-radius"):
        assert layer["paint"].get(key) == expected.get(key), (
            f"composite {key} must equal the contract paint"
        )
