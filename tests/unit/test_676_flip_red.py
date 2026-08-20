"""
Flip-red proof for #676 — must run RED on base code, GREEN after fix.

Covers:
- apply_template composite KeyError vs explicit error/expand
- composite_school_service_area four-way consistency
- combine_map_theme thematic dead slot
- CompositeMapSpecBuilder validate fail-loud and source empty shell
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


# 1. apply_template on composite must NOT KeyError 'payload'
@pytest.mark.asyncio
async def test_apply_template_composite_not_payload_keyerror(registry):
    # Pick one composite that definitely has no payload
    composite_id = "composite_school_service_area"
    result = await registry.dispatch("apply_template", {"template_id": composite_id})
    # Current bug: result is NOT_FOUND "'payload'" with layer-attributes hint
    # Fixed: must be either composite-expanded success or explicit missing-field error
    # with a hint that points to correct usage (field/combine_map_theme), not layer attributes
    assert "error" in result or "status" in result
    if "error" in result:
        # Must NOT be the misleading "'payload'" KeyError (registry wraps KeyError as NOT_FOUND + layer-attributes hint)
        combined = result.get("error", "") + result.get("message", "") + result.get("correction_hint", "")
        assert "'payload'" not in combined, f"still KeyError payload: {result}"
        assert "layer attributes" not in combined.lower(), f"misleading hint: {result}"
        # Should guide to field / expand_composite / combine_map_theme
        assert any(k in combined.lower() for k in ["field", "composite", "expand", "pipeline", "thematic"]), f"error should guide to correct usage, got: {result}"
    else:
        # success path — must be composite-related
        assert result.get("status") in ("composite_applied", "composite_expanded", "composite_map_assembled", "template_applied") or "composite" in str(result).lower()


@pytest.mark.asyncio
async def test_apply_template_all_composites_no_payload_error(registry):
    for c in COMPOSITE_TEMPLATES:
        cid = c["id"]
        result = await registry.dispatch("apply_template", {"template_id": cid})
        # No composite should ever surface "'payload'" KeyError
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
    # Thematic template's method/palette
    payload = thematic_tmpl.get("payload", {})
    expected_method = payload.get("method")
    expected_palette = payload.get("palette")
    preview = school.get("preview_metadata", {})
    # preview method must match pipeline thematic method (currently categorical vs quantiles mismatch)
    assert preview.get("method") == expected_method, f"preview method {preview.get('method')} != pipeline method {expected_method}"
    assert preview.get("palette") == expected_palette, f"preview palette {preview.get('palette')} != {expected_palette}"
    # name/description must be honest style-preset, not fake analysis
    name = school.get("name", "")
    desc = school.get("description", "")
    # Should mention style combination, not pretend to compute service area
    assert "样式组合" in desc or "style preset" in desc.lower() or "preset" in desc.lower(), f"description should be honest style preset: {desc}"
    # Must not promise non-existent analysis verbatim (old description had 等间隔分级 but pipeline is quantiles)
    # After fix, description should be consistent with method; we check it mentions the correct method or is generic
    assert "等间隔" not in desc or expected_method == "equal_interval", f"description claims equal_interval but pipeline is {expected_method}: {desc}"
    # Name should not be bare "学校学区服务范围" that implies analysis; should contain 样式/预设 or be generic
    assert "学区服务范围" not in name or "样式" in name or "预设" in name, f"name still pretends analysis: {name}"


# 3. All 22 composites honest (spot check)
def test_all_composites_honest_no_fake_analysis():
    for c in COMPOSITE_TEMPLATES:
        desc = (c.get("description") or "")
        name = c.get("name") or ""
        # Old descriptions claimed to perform洪水/AHP/学区/适宜性计算 that pipeline never executes.
        # Honest rewrite must contain 样式组合 or 明示需提供字段/不含分析
        has_honest_marker = any(k in desc for k in ["样式组合", "需提供", "不含", "预设", "preset"]) or "样式" in name
        assert has_honest_marker, f"{c['id']} description still fake analysis without honest marker: {desc!r}"


# 4. combine_map_theme thematic dead slot — with field should emit legend_spec (or be removed)
def test_combine_thematic_with_field_emits_legend_spec_or_removed():
    # Current builder: with_thematic(str) never sets field → thematic branch never taken
    # After fix, either (a) field param makes legend_spec appear, or (b) thematic param removed from schema
    from app.tools.templates import CombineMapThemeArgs
    import inspect
    sig = inspect.signature(combine_map_theme)
    has_field_param = "field" in sig.parameters
    has_thematic_in_schema = "thematic" in CombineMapThemeArgs.model_fields

    if has_thematic_in_schema and has_field_param:
        # Option A: thematic should now be effective with field
        builder = CompositeMapSpecBuilder()
        # Simulate combine_map_theme with field
        res = combine_map_theme(thematic="tmpl_th_pop_choro", field="population", layer_id="test_layer")
        mapspec = res["mapspec"]
        layer = mapspec["layers"][0]
        # Must have legend_spec (not dead thematic key) and field matches
        assert "legend_spec" in layer, f"thematic with field should produce legend_spec, got keys {list(layer.keys())}: {layer}"
        assert layer["legend_spec"]["field"] == "population"
        assert "thematic" not in layer, "old dead key 'thematic' should be replaced by legend_spec"
    elif not has_thematic_in_schema:
        # Option B: thematic removed — builder should not accept it, or combine_map_theme should error/ignore
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
    # Without field, no legend/thematic should be emitted (honest — needs field)
    assert "legend_spec" not in layer or layer.get("legend_spec", {}).get("field") is None


# 5. Builder validate must fail-loud, not warn-and-return
def test_builder_validate_fail_loud():
    _builder = CompositeMapSpecBuilder()
    # Craft an invalid mapspec by directly manipulating internal to trigger validate failure
    # Easiest: assemble normally then mutate to invalid source ref and re-validate via builder path
    # Instead we test builder's assemble does not silently return invalid mapspec when we force invalid state
    # Force invalid by setting a layer with missing source (monkey patch after assemble? easier: call validate directly)
    from app.services.mapspec.coordinator import validate
    invalid_mapspec = {"version": "1.0", "sources": {}, "layers": [{"id": "l1", "source": "missing", "type": "fill", "paint": {}}]}
    res = validate(invalid_mapspec)
    assert not res["success"]
    # Builder should raise on invalid, not return it
    # We test by making builder produce invalid mapspec: e.g., set internal _basemap to invalid? Simpler: patch validate to fail
    import unittest.mock as mock
    with mock.patch("app.services.mapspec.composite_builder.validate_mapspec", return_value={"success": False, "errors": [{"code": "MOCK_FAIL", "message": "mock"}]}):
        b2 = CompositeMapSpecBuilder()
        with pytest.raises((ValueError, RuntimeError, Exception)):
            b2.assemble({}, layer_id="y")


# 6. Builder source must not be constant empty FeatureCollection shell
def test_builder_source_not_empty_shell():
    builder = CompositeMapSpecBuilder()
    mapspec = builder.assemble({}, layer_id="my_layer")
    source_id = "source_my_layer"
    assert source_id in mapspec["sources"]
    src = mapspec["sources"][source_id]
    # Old code: inlineData = {"type":"FeatureCollection","features":[]} constant empty shell
    # Fixed: should reference target data (dataPath/layerRef/url) or be populated with provided geojson
    is_empty_shell = src.get("inlineData") == {"type": "FeatureCollection", "features": []}
    assert not is_empty_shell, f"source is still empty shell: {src}"
    # Must indicate reference to target data
    has_reference = any(k in src for k in ["dataPath", "url", "layerRef", "ref"]) or (src.get("inlineData") and src["inlineData"].get("features"))
    assert has_reference, f"source should reference target data, got {src}"
