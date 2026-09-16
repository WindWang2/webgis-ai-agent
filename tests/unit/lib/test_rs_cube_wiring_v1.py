"""M8 接线面集成测试：artifact type / capabilities / algorithms / tools /
skill YAML / recipe pack / kill-switch —— 六面一次验证。

边界红线：``app/tools/__init__.py``、``capability_graph.py``、
``app/lib/modelops/**`` 不许碰（#1336 热区）——本文件同时钉住
``_TOOL_MODULES`` 未被修改（diff 域校验在 CI；这里验证接线经由
``register_rs_tools`` 尾部生效）。
"""
from __future__ import annotations

import importlib



class TestRegistrySurfaces:
    def test_artifact_type_registered(self):
        from app.lib.gis.artifacts import artifact_type

        desc = artifact_type("rs_cube_descriptor")
        assert desc is not None
        assert desc.geometry_kind == "raster"

    def test_capabilities_in_packs(self):
        from app.lib.gis.capabilities import iter_capability_packs

        ids = {c.id for pack in iter_capability_packs() for c in pack}
        need = {"rs_cube_describe", "rs_cube_alignment",
                "rs_temporal_feature_pack", "rs_joint_fusion",
                "rs_sample_split"}
        assert need <= ids

    def test_algorithms_in_registry(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        reg = get_algorithm_registry()
        need = {"remote.cube.describe", "remote.cube.align",
                "remote.cube.features", "remote.cube.fusion",
                "remote.cube.samples"}
        assert need <= set(reg.all_ids)


class TestToolSurface:
    def test_cube_tools_registered_via_register_rs_tools(self):
        from app.tools.registry import ToolRegistry
        from app.tools.remote_sensing import register_rs_tools

        registry = ToolRegistry()
        register_rs_tools(registry)
        names = set(registry.tool_names())
        assert {"rs_cube_describe", "rs_cube_align",
                "rs_temporal_feature_pack", "rs_joint_fusion_stack",
                "rs_cube_sample_split"} <= names

    def test_tool_modules_frozen_list_untouched(self):
        # app/tools/__init__.py 是 #1336 热区：本方向不往 _TOOL_MODULES 加行
        import app.tools as t

        src = open(t.__file__, encoding="utf-8").read()
        assert "rs_cube_tools" not in src

    def test_describe_tool_end_to_end(self):
        import asyncio

        from app.tools.registry import ToolRegistry
        from app.tools.remote_sensing import register_rs_tools

        registry = ToolRegistry()
        register_rs_tools(registry)
        descriptor = {
            "cube_id": "cube-wiring",
            "grid": {"crs": "EPSG:32650", "width": 4, "height": 4},
            "assets": [
                {"ref": "ref:raster/a", "time_iso": "2024-03-01",
                 "role": "optical", "band": "nir"},
                {"ref": "ref:raster/b", "time_iso": "2024-03-03",
                 "role": "sar", "polarization": "vv"},
            ],
        }
        out = asyncio.get_event_loop().run_until_complete(
            registry.dispatch("rs_cube_describe",
                              {"descriptor": descriptor}))
        assert out["summary"]["cube_id"] == "cube-wiring"
        assert out["summary"]["n_assets"] == 2


class TestSkillAndRecipe:
    def test_skill_yaml_loads_clean(self):
        from app.services.gis_harness.skills.loader import load_skill_library

        skills, _comps, violations = load_skill_library()
        assert violations == []
        assert any(s.id == "rs_temporal_cube_workflow" for s in skills)

    def test_recipes_registered(self):
        from app.services.gis_harness.recipes import get_recipe_registry

        reg = get_recipe_registry()
        assert reg.get("rs_temporal_cube_product")
        assert reg.get("rs_cube_coverage_audit")

    def test_recipe_pack_kill_switch(self, monkeypatch):
        monkeypatch.setenv("RS_TEMPORAL_CUBE", "0")
        mod = importlib.reload(
            importlib.import_module(
                "app.services.gis_harness.recipe_packs.rs_temporal_cube"))
        assert mod.RECIPES == []
        monkeypatch.setenv("RS_TEMPORAL_CUBE", "1")
        mod = importlib.reload(mod)
        assert len(mod.RECIPES) == 2
        monkeypatch.delenv("RS_TEMPORAL_CUBE")
        importlib.reload(mod)

    def test_skill_policy_sees_new_capability_ids(self):
        from app.lib.gis.capability_registry import get_capability_registry

        reg = get_capability_registry()
        assert reg.has("rs_cube_alignment")
        assert reg.has("rs_joint_fusion")


class TestPrecondition:
    def test_grid_identity_match_registered_and_evaluates(self):
        from app.lib.gis.scientific_preconditions import (
            evaluate_precondition,
            precondition_exists,
        )

        assert precondition_exists("grid_identity_match")
        ok = evaluate_precondition("grid_identity_match", {
            "grid": {"crs": "EPSG:32650", "width": 4, "height": 4}})
        assert ok.verdict == "PASS"
        incomplete = evaluate_precondition("grid_identity_match", {
            "grid": {"crs": "EPSG:32650"}})
        assert incomplete.verdict == "REQUIRES_TRANSFORM"
