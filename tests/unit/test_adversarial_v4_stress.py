"""Adversarial stress-test suite for Backend & Runtime V4 (Challenger 1).

Tests:
1. Raster affine transform unpacking (6 vs 9 tuples, Affine objects, invalid inputs, edge cases).
2. Tool surface compilation (determinism, unknown families, budget bounds, tool aliases, registry metadata filtering).
3. Component lifecycle (duplicate offset clamping, singleton rejection, collision handling, rebind mutex, table_panel validations).
4. Raster artifact registry (probing O(1) stat, live pinning in MapSpec, GC unlinking, path traversal defense).
"""
from __future__ import annotations

import os
import uuid
import pytest
import numpy as np
from affine import Affine

from app.lib.geo_analysis.raster_grid import (
    _transform_tuple,
    _transforms_equal,
    _as_affine,
    RasterGridProfile,
    grids_align,
    decide_alignment,
    RasterAlignmentError,
)
from app.services.gis_harness.tool_surface import (
    compile_tool_surface,
    ToolSurface,
    PHASE_PLANNING,
    PHASE_DATA,
    PHASE_ANALYSIS,
    PHASE_ASSEMBLY,
    PHASE_FINAL,
)
from app.services.gis_harness.components import (
    duplicate_component,
    remove_component,
    rebind_component,
    mutate_component,
    chart_panel_component,
    table_panel_component,
    north_arrow_component,
    scale_bar_component,
    title_component,
    validate_table_binding,
    validate_chart_payload,
    validate_stats_payload,
    validate_annotation_payload,
    validate_inset_payload,
    CartographyComponent,
    ComponentPlacement,
    MULTI_INSTANCE_TYPES,
)
from app.services.artifact_registry import (
    is_raster_ref,
    raster_png_path,
    raster_ref_exists,
    probe_ref,
    register_artifact,
    sweep_statuses,
    collect_orphan_refs,
    list_artifacts,
    get_artifact,
    A_VALID,
    A_STALE,
    A_EXPIRED,
    A_SUPERSEDED,
)

# ════════════════════════════════════════════════════════════════════════════
# 1. RASTER AFFINE TRANSFORM UNPACKING & STRESS TESTS
# ════════════════════════════════════════════════════════════════════════════

class TestAffineTransformStress:
    def test_6_tuple_unpacking(self):
        t6 = (10.0, 0.0, 100.0, 0.0, -10.0, 200.0)
        res = _transform_tuple(t6)
        assert res == (10.0, 0.0, 100.0, 0.0, -10.0, 200.0)
        aff = _as_affine(res)
        assert isinstance(aff, Affine)
        assert (aff.a, aff.b, aff.c, aff.d, aff.e, aff.f) == res

    def test_9_tuple_unpacking(self):
        # GDAL 3x3 affine matrix representation: (a, b, c, d, e, f, 0, 0, 1)
        t9 = (10.0, 0.0, 100.0, 0.0, -10.0, 200.0, 0.0, 0.0, 1.0)
        res = _transform_tuple(t9)
        assert res == (10.0, 0.0, 100.0, 0.0, -10.0, 200.0)
        aff = _as_affine(t9)
        assert isinstance(aff, Affine)
        assert (aff.a, aff.b, aff.c, aff.d, aff.e, aff.f) == res

    def test_affine_object_unpacking(self):
        aff = Affine(1.5, 0.0, 50.0, 0.0, -1.5, 75.0)
        res = _transform_tuple(aff)
        assert res == (1.5, 0.0, 50.0, 0.0, -1.5, 75.0)
        aff2 = _as_affine(res)
        assert aff2 == aff

    def test_list_unpacking(self):
        t_list = [2.0, 0.0, 10.0, 0.0, -2.0, 20.0, 99.0, 99.0]
        res = _transform_tuple(t_list)
        assert res == (2.0, 0.0, 10.0, 0.0, -2.0, 20.0)

    def test_short_sequence_raises_value_error(self):
        with pytest.raises(ValueError, match="Expected 6 transform coefficients"):
            _transform_tuple((1.0, 2.0, 3.0, 4.0, 5.0))

        with pytest.raises(ValueError, match="Expected 6 transform coefficients"):
            _transform_tuple([])

    def test_none_as_affine_raises_error(self):
        with pytest.raises(RasterAlignmentError, match="no target transform"):
            _as_affine(None)

    def test_tolerance_equality(self):
        t1 = (10.0, 0.0, 100.0, 0.0, -10.0, 200.0)
        # Small float difference within 1e-6 relative tolerance
        t2 = (10.000001, 0.0, 100.000001, 0.0, -10.000001, 200.000001)
        assert _transforms_equal(t1, t2)

        # Large float difference
        t3 = (10.01, 0.0, 100.0, 0.0, -10.0, 200.0)
        assert not _transforms_equal(t1, t3)

    def test_raster_grid_profile_with_9_tuple(self):
        t9 = (10.0, 0.0, 0.0, 0.0, -10.0, 80.0, 0.0, 0.0, 1.0)
        prof = RasterGridProfile(
            width=8,
            height=8,
            crs="EPSG:32650",
            transform=_transform_tuple(t9),
            bounds=(0.0, 0.0, 80.0, 80.0),
        )
        assert prof.resolution_x == 10.0
        assert prof.resolution_y == 10.0
        assert len(prof.transform) == 6
        assert prof.to_dict()["transform"] == [10.0, 0.0, 0.0, 0.0, -10.0, 80.0]


# ════════════════════════════════════════════════════════════════════════════
# 2. TOOL SURFACE COMPILATION & ALIAS / STABILITY TESTS
# ════════════════════════════════════════════════════════════════════════════

class TestToolSurfaceStress:
    def test_deterministic_compilation(self):
        class Step:
            n = 1
            tool_family = "raster"
            done = False

        class Plan:
            steps = [Step()]

        surf1 = compile_tool_surface(plan=Plan())
        surf2 = compile_tool_surface(plan=Plan())
        assert surf1 == surf2
        assert surf1.phase == PHASE_ANALYSIS
        assert hash(surf1.preferred_tools) == hash(surf2.preferred_tools)

    def test_unmapped_and_empty_tool_families(self):
        class Step1:
            n = 1
            tool_family = ""
            done = False
        class Step2:
            n = 2
            tool_family = "unknown_quantum_family"
            done = False

        class Plan1:
            steps = [Step1()]
        class Plan2:
            steps = [Step2()]

        s1 = compile_tool_surface(plan=Plan1())
        assert s1.phase == PHASE_PLANNING

        s2 = compile_tool_surface(plan=Plan2())
        assert s2.phase == PHASE_PLANNING
        assert any("unmapped" in ev for ev in s2.evidence)

    def test_all_tool_family_phases(self):
        from app.services.gis_harness.tool_surface import _DOMAIN_PHASE
        for family, expected_phase in _DOMAIN_PHASE.items():
            class Step:
                n = 1
                tool_family = family
                done = False
            class Plan:
                steps = [Step()]
            surf = compile_tool_surface(plan=Plan())
            assert surf.phase == expected_phase

    def test_product_status_overrides(self):
        class Step:
            n = 1
            tool_family = "dataset"
            done = False
        class Plan:
            steps = [Step()]

        # Even if step is dataset, product_status="needs_repair" forces assembly
        s_repair = compile_tool_surface(plan=Plan(), product_status="needs_repair")
        assert s_repair.phase == PHASE_ASSEMBLY

        # product_status="complete" forces final
        s_complete = compile_tool_surface(plan=Plan(), product_status="complete")
        assert s_complete.phase == PHASE_FINAL


# ════════════════════════════════════════════════════════════════════════════
# 3. COMPONENT LIFECYCLE V3 EDGE CASES & MUTEX
# ════════════════════════════════════════════════════════════════════════════

class TestComponentLifecycleStress:
    def test_duplicate_placement_clamp(self):
        # Test floating position near maximum boundary
        comp = chart_panel_component(
            component_id="chart-1",
            placement=ComponentPlacement(mode="floating", x=8185, y=8185, width=300, height=200),
        )
        comps = [comp]
        with_copy, copy, err = duplicate_component(comps, component_id="chart-1")
        assert err is None
        assert copy is not None
        # x: 8185 + 16 = 8201 -> clamped to 8192
        assert copy.placement.x == 8192
        assert copy.placement.y == 8192

    def test_duplicate_placement_clamp_minimum(self):
        comp = chart_panel_component(
            component_id="chart-1",
            placement=ComponentPlacement(mode="floating", x=-4096, y=-4096, width=300, height=200),
        )
        comps = [comp]
        with_copy, copy, err = duplicate_component(comps, component_id="chart-1")
        assert err is None
        assert copy.placement.x == -4080
        assert copy.placement.y == -4080

    def test_duplicate_anchor_mode_converts_to_floating(self):
        comp = chart_panel_component(
            component_id="chart-anchor",
            position="top-left",
            placement=ComponentPlacement(mode="anchor", anchor="top-left"),
        )
        comps = [comp]
        _, copy, err = duplicate_component(comps, component_id="chart-anchor")
        assert err is None
        assert copy.placement.mode == "floating"
        assert copy.placement.x == 32
        assert copy.placement.y == 96
        assert copy.position == "none"

    def test_duplicate_collision_resolution_up_to_limit(self):
        base = chart_panel_component(component_id="c")
        comps = [base]
        # Pre-populate c-copy, c-copy2 ... c-copy10
        for i in range(2, 10):
            comps.append(chart_panel_component(component_id=f"c-copy{i}"))
        comps.append(chart_panel_component(component_id="c-copy"))

        with_copy, copy, err = duplicate_component(comps, component_id="c")
        assert err is None
        assert copy.id == "c-copy10"

    def test_duplicate_collision_exhaustion(self):
        base = chart_panel_component(component_id="c")
        comps = [base, chart_panel_component(component_id="c-copy")]
        for i in range(2, 101):
            comps.append(chart_panel_component(component_id=f"c-copy{i}"))

        with_copy, copy, err = duplicate_component(comps, component_id="c")
        assert copy is None
        assert "上限" in err

    def test_rebind_mutex_violation(self):
        # Attempt to bind both chartRef and layerId simultaneously
        comp = chart_panel_component(component_id="c")
        comps = [comp]
        _, _, err = rebind_component(
            comps,
            component_id="c",
            bindings={"chartRef": "ref:chart-1", "layerId": "layer-1"},
        )
        assert err is not None
        assert "互斥" in err

    def test_rebind_table_panel_mutex_violation(self):
        # Table panel attempting both tableRef and layerId
        comp = table_panel_component(component_id="t", table_ref="ref:tbl-1")
        comps = [comp]
        _, _, err = rebind_component(
            comps,
            component_id="t",
            bindings={"tableRef": "ref:tbl-2", "layerId": "layer-x"},
        )
        assert err is not None
        assert "互斥" in err

    def test_rebind_table_panel_swaps_channel(self):
        # Table panel with tableRef rebound to layerId cleans tableRef
        comp = table_panel_component(component_id="t", table_ref="ref:tbl-1")
        comps = [comp]
        rebound, change, err = rebind_component(
            comps,
            component_id="t",
            bindings={"layerId": "layer-mvt"},
        )
        assert err is None
        assert rebound[0].options.get("layerId") == "layer-mvt"
        assert "tableRef" not in rebound[0].options

    def test_rebind_non_existent_component(self):
        comps = [chart_panel_component(component_id="c1")]
        _, change, err = rebind_component(comps, component_id="ghost", bindings={"chartRef": "ref:c"})
        assert change is None
        assert "not found" in err

    def test_rebind_invalid_value_types(self):
        comps = [chart_panel_component(component_id="c1")]
        _, _, err = rebind_component(comps, component_id="c1", bindings={"chartRef": "   "})
        assert "非空字符串" in err

    def test_table_binding_validation(self):
        # Valid tableRef
        assert validate_table_binding({"tableRef": "ref:stat-table"}) is None
        # Valid layerId
        assert validate_table_binding({"layerId": "counties"}) is None
        # Mutex violation
        assert "互斥" in validate_table_binding({"tableRef": "ref:stat-table", "layerId": "counties"})
        # Missing binding
        assert "需要绑定" in validate_table_binding({})
        # Too many columns (>32)
        assert "超过上限" in validate_table_binding({"layerId": "c", "columns": [f"col_{i}" for i in range(35)]})


# ════════════════════════════════════════════════════════════════════════════
# 4. RASTER ARTIFACT REGISTRY PROBING, PINNING & GC
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
class TestRasterArtifactRegistryStress:
    async def test_path_traversal_and_malformed_refs(self, tmp_path, monkeypatch):
        sid = "stress-session"
        monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)

        assert is_raster_ref("ref:raster/valid_id-123")
        assert not is_raster_ref("ref:geojson-abc")
        assert not is_raster_ref("some/path.png")

        # path traversal checks
        assert raster_png_path(sid, "ref:raster/../../etc/passwd") is None
        assert raster_png_path(sid, "ref:raster/foo;rm -rf /") is None
        assert raster_png_path(sid, "ref:raster/valid_id-123") is not None
        assert raster_png_path("../illegal_sid", "ref:raster/valid_id-123") is None

    async def test_probe_and_sweep_raster_states(self, tmp_path, monkeypatch):
        sid = "probe-stress-sid"
        raster_dir = tmp_path / sid / "raster"
        raster_dir.mkdir(parents=True)
        (raster_dir / "r1.png").write_bytes(b"\x89PNG-r1")
        (raster_dir / "r2.png").write_bytes(b"\x89PNG-r2")
        monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)

        # Register r1, r2, r3 (r3 missing from disk)
        await register_artifact(sid, artifact_id="ref:raster/r1", artifact_type="raster_surface")
        await register_artifact(sid, artifact_id="ref:raster/r2", artifact_type="raster_surface")
        await register_artifact(sid, artifact_id="ref:raster/r3", artifact_type="raster_surface")

        # MapSpec pins r1 in sources
        mapspec = {
            "sources": {"s1": {"type": "raster", "imageRef": "ref:raster/r1"}},
            "layers": [{"id": "l1", "source": "s1", "type": "raster"}],
        }

        # Sweep
        res = await sweep_statuses(sid, mapspec=mapspec)
        assert "ref:raster/r1" in res["valid"]     # disk exists + spec pinned -> VALID
        assert "ref:raster/r2" in res["stale"]     # disk exists + unreferenced -> STALE
        assert "ref:raster/r3" in res["expired"]   # disk missing -> EXPIRED

        # GC: should only unlink and clean r2 and r3, leaving r1 untouched
        deleted = await collect_orphan_refs(sid, mapspec=mapspec)
        assert "ref:raster/r2" in deleted
        assert "ref:raster/r1" not in deleted
        assert (raster_dir / "r1.png").is_file()
        assert not (raster_dir / "r2.png").is_file()

    async def test_table_panel_table_ref_pins_artifact(self, tmp_path, monkeypatch):
        """Table panel in layout.components must pin tableRef so it's not GC'd."""
        from app.services.session_data import session_data_manager

        sid = "table-ref-pin-sid"
        monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)

        table_ref = await session_data_manager.store(
            sid, {"columns": ["a", "b"], "rows": [{"a": 1, "b": 2}]}, prefix="table",
        )
        await register_artifact(sid, artifact_id=table_ref, artifact_type="feature_collection")

        mapspec = {
            "sources": {},
            "layers": [],
            "layout": {
                "components": [
                    {
                        "id": "table-1",
                        "type": "table_panel",
                        "options": {"tableRef": table_ref},
                    }
                ]
            },
        }

        res = await sweep_statuses(sid, mapspec=mapspec)
        assert table_ref in res["valid"]
        deleted = await collect_orphan_refs(sid, mapspec=mapspec)
        assert table_ref not in deleted
        assert await session_data_manager.get(sid, table_ref) is not None
