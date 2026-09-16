"""Scene agent tools tests (ADR-0199 M8).

覆盖：plan 只读无副作用；set 走 facade 事务（成功/非法值/清除）；
词表越界输入被保守钳制。
"""
from __future__ import annotations

import uuid

import pytest

from app.tools.registry import ToolRegistry
from app.tools.scene_tools import register_scene_tools


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_scene_tools(reg)
    return reg


@pytest.fixture
def session_id():
    return f"test-scene-tools-{uuid.uuid4().hex[:12]}"


class TestPlanMapScene:
    @pytest.mark.asyncio
    async def test_plan_read_only_deterministic(self, registry):
        res = await registry.dispatch("plan_map_scene", {
            "has_height_attribute_evidence": True,
            "vertical_extrusion_intent": True,
            "geometry_kinds": ["polygon"],
        })
        assert res["success"] is True
        assert res["decision"]["mode"] == "3d"
        assert res["suggested_scene"]["mode"] == "3d"

    @pytest.mark.asyncio
    async def test_plan_without_evidence_never_suggests_3d(self, registry):
        res = await registry.dispatch("plan_map_scene", {
            "vertical_extrusion_intent": True,
            "terrain_intent": True,
            "geometry_kinds": ["polygon"],
        })
        assert res["success"] is True
        assert res["decision"]["mode"] == "2d"
        codes = [r["code"] for r in res["decision"]["reasons"]]
        assert "SCENE_EXTRUSION_NO_HEIGHT_EVIDENCE" in codes
        assert "SCENE_TERRAIN_NO_ELEVATION_EVIDENCE" in codes

    @pytest.mark.asyncio
    async def test_plan_unknown_vocab_clamped(self, registry):
        res = await registry.dispatch("plan_map_scene", {
            "geometry_kinds": ["alien"],
            "medium": "hologram",
            "purpose": "vibes",
        })
        assert res["success"] is True
        assert res["decision"]["mode"] == "2d"  # 保守档

    @pytest.mark.asyncio
    async def test_plan_terrain_without_source_disclosed_and_omitted(self, registry):
        """Review P1-3：决策含地形但未绑定源 → terrain 省略 + 披露，绝不产
        出会被 set_map_scene 拒绝的半成品建议。"""
        res = await registry.dispatch("plan_map_scene", {
            "terrain_intent": True,
            "has_elevation_evidence": True,
        })
        assert res["success"] is True
        assert res["decision"]["terrain"] is True
        assert "terrain" not in res["suggested_scene"]
        assert "terrain_source_note" in res

    @pytest.mark.asyncio
    async def test_plan_to_set_composed_flow_with_terrain(self, registry, session_id):
        """Review P1-3：plan（绑定源）→ set 组合流程必须真实可用。"""
        await mapspec_seed(session_id)
        plan = await registry.dispatch("plan_map_scene", {
            "terrain_intent": True,
            "has_elevation_evidence": True,
            "terrain_source": "dem",
        })
        assert plan["success"] is True
        assert plan["suggested_scene"]["terrain"]["source"] == "dem"
        assert "terrain_source_note" not in plan
        res = await registry.dispatch("set_map_scene", {
            "session_id": session_id,
            "scene": plan["suggested_scene"],
        })
        assert res["success"] is True, res


class TestSetMapScene:
    @pytest.mark.asyncio
    async def test_set_requires_session(self, registry):
        res = await registry.dispatch("set_map_scene", {})
        assert res["success"] is False

    @pytest.mark.asyncio
    async def test_set_applies_scene_transactionally(self, registry, session_id):
        await mapspec_seed(session_id)
        scene = {
            "mode": "2.5d",
            "terrain": {"source": "dem", "exaggeration": 1.0, "vertical_unit": "m"},
        }
        res = await registry.dispatch("set_map_scene", {"session_id": session_id, "scene": scene})
        assert res["success"] is True, res
        assert res["scene_applied"]["mode"] == "2.5d"
        assert isinstance(res.get("mutation_revision"), int)

    @pytest.mark.asyncio
    async def test_set_rejects_bad_mode(self, registry, session_id):
        await mapspec_seed(session_id)
        res = await registry.dispatch(
            "set_map_scene", {"session_id": session_id, "scene": {"mode": "4d"}}
        )
        assert res["success"] is False

    @pytest.mark.asyncio
    async def test_clear_scene(self, registry, session_id):
        await mapspec_seed(session_id)
        await registry.dispatch(
            "set_map_scene",
            {"session_id": session_id, "scene": {"mode": "2.5d", "terrain": {"source": "dem"}}},
        )
        res = await registry.dispatch("set_map_scene", {"session_id": session_id, "scene": None})
        assert res["success"] is True
        assert res.get("scene_applied") is None


async def mapspec_seed(session_id: str) -> None:
    """最小 spec 骨架（init + dem 源），供 set_scene 校验 terrain.source。"""
    from app.services.mapspec import UpsertSourceIntent
    from app.services.mapspec_store import mapspec_store

    res = await mapspec_store.init_project(session_id)
    assert res["success"] is True
    res = await mapspec_store.engine.apply_mutation(
        session_id,
        UpsertSourceIntent(
            source_id="dem",
            source={"type": "raster-dem", "url": "https://example.test/dem/{z}/{x}/{y}.png"},
        ),
    )
    assert res.is_error is False, res.error_msg
