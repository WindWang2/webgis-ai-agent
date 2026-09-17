"""SetSceneIntent transactional mutation tests (ADR-0201 M2).

Oracle anchors:
- 2D↔3D 切换 = presentation-only：layers/sources/legend_spec/thresholds 不变。
- 事务性：CAS superseded / mutation_id 幂等 / 失败回滚 / revision 单调。
- 旧 spec（无 scene）行为不变。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    SetSceneIntent,
    UpsertLayerIntent,
    UpsertSourceIntent,
)


@pytest.fixture
async def engine_with_spec():
    """全局 engine + 唯一 session（既有测试同款约定，session id 隔离）。"""
    engine = MapSpecLifecycleEngine()
    session = f"test-scene-{uuid.uuid4().hex[:12]}"

    from app.services.mapspec.lifecycle_engine import InitProjectIntent

    res = await engine.apply_mutation(
        session,
        InitProjectIntent(
            view={"center": [116.0, 39.9], "zoom": 10.5},
            thresholds={"maxFeatures": 5000},
        ),
    )
    assert not res.is_error, res.error_msg
    res = await engine.apply_mutation(
        session,
        UpsertSourceIntent(
            source_id="s1",
            source={
                "type": "geojson",
                "inlineData": {"type": "FeatureCollection", "features": []},
            },
        ),
    )
    assert not res.is_error, res.error_msg
    # ADR-0201：场景地形源（_scene_3d() 引用 "dem-src"）—— 必须真实存在，
    # 否则 SCENE_TERRAIN_SOURCE_REF 阻塞（review P1-1 后的正确语义）。
    res = await engine.apply_mutation(
        session,
        UpsertSourceIntent(
            source_id="dem-src",
            source={"type": "raster-dem", "url": "https://example.test/dem/{z}/{x}/{y}.png"},
        ),
    )
    assert not res.is_error, res.error_msg
    res = await engine.apply_mutation(
        session,
        UpsertLayerIntent(
            layer={
                "id": "l1",
                "source": "s1",
                "type": "fill",
                "legend_spec": {"kind": "graduated", "field": "pop", "breaks": [1, 2, 3]},
            }
        ),
    )
    assert not res.is_error, res.error_msg
    return engine, session


def _scene_3d() -> dict:
    return {
        "mode": "3d",
        "terrain": {"source": "dem-src", "exaggeration": 1.0, "vertical_unit": "m"},
        "camera": {"pitch": 50, "bearing": -15},
        "reason_code": "SCENE_MODE_3D_EVIDENCED",
    }


class TestSetSceneTransaction:
    async def test_set_scene_presentation_only(self, engine_with_spec):
        """Oracle：切换场景模式不触碰数据/图层/图例 —— 深比较不变。"""
        engine, session = engine_with_spec
        before = (await engine.store.get_mapspec(session))

        res = await engine.apply_mutation(session, SetSceneIntent(scene=_scene_3d()))
        assert not res.is_error, res.error_msg
        after = (await engine.store.get_mapspec(session))

        assert after["scene"]["mode"] == "3d"
        for key in ("sources", "layers", "thresholds"):
            assert after[key] == before[key], f"{key} drifted on scene switch"
        # legend_spec 深比较（分类/分级/图例不漂移）
        assert after["layers"][0].get("legend_spec") == before["layers"][0].get("legend_spec")
        assert after.get("view") == before.get("view")  # 相机态独立，不被 scene 隐改

    async def test_revision_monotonic(self, engine_with_spec):
        engine, session = engine_with_spec
        r1 = (await engine.apply_mutation(session, SetSceneIntent(scene=_scene_3d()))).mutation_revision
        r2 = (
            await engine.apply_mutation(session, SetSceneIntent(scene={"mode": "2d"}))
        ).mutation_revision
        assert r2 == r1 + 1

    async def test_cas_superseded(self, engine_with_spec):
        engine, session = engine_with_spec
        cur = (await engine.apply_mutation(session, SetSceneIntent(scene={"mode": "2.5d"})))
        res = await engine.apply_mutation(
            session,
            SetSceneIntent(scene=_scene_3d()),
            expected_revision=cur.mutation_revision - 1,
        )
        assert res.superseded is True

    async def test_mutation_id_idempotent(self, engine_with_spec):
        engine, session = engine_with_spec
        first = await engine.apply_mutation(
            session, SetSceneIntent(scene=_scene_3d()), mutation_id="scene-once"
        )
        replay = await engine.apply_mutation(
            session, SetSceneIntent(scene=_scene_3d()), mutation_id="scene-once"
        )
        assert replay.duplicate is True
        assert replay.mutation_revision == first.mutation_revision

    async def test_invalid_mode_rejected_and_rolled_back(self, engine_with_spec):
        engine, session = engine_with_spec
        await engine.apply_mutation(session, SetSceneIntent(scene={"mode": "2.5d"}))
        bad = await engine.apply_mutation(session, SetSceneIntent(scene={"mode": "4d"}))
        assert bad.is_error is True
        spec = (await engine.store.get_mapspec(session))
        assert spec["scene"]["mode"] == "2.5d"  # last-known-good 不变

    async def test_clear_scene_removes_key(self, engine_with_spec):
        engine, session = engine_with_spec
        await engine.apply_mutation(session, SetSceneIntent(scene=_scene_3d()))
        res = await engine.apply_mutation(session, SetSceneIntent(scene=None))
        assert not res.is_error
        assert "scene" not in ((await engine.store.get_mapspec(session)))

    async def test_exaggeration_bounds_enforced(self, engine_with_spec):
        engine, session = engine_with_spec
        scene = _scene_3d()
        scene["terrain"] = {"source": "dem-src", "exaggeration": 99.0}
        res = await engine.apply_mutation(session, SetSceneIntent(scene=scene))
        assert res.is_error is True

    async def test_terrain_requires_source_id(self, engine_with_spec):
        engine, session = engine_with_spec
        scene = _scene_3d()
        scene["terrain"] = {"exaggeration": 1.0}
        res = await engine.apply_mutation(session, SetSceneIntent(scene=scene))
        assert res.is_error is True

    async def test_dangling_terrain_source_rejected_at_commit(self, engine_with_spec):
        """Review P1-1：悬空 terrain 源在写路径被 SCENE_TERRAIN_SOURCE_REF 阻塞。"""
        engine, session = engine_with_spec
        scene = _scene_3d()
        scene["terrain"] = {"source": "never-registered", "exaggeration": 1.0}
        res = await engine.apply_mutation(session, SetSceneIntent(scene=scene))
        assert res.is_error is True
        spec = await engine.store.get_mapspec(session)
        assert (spec or {}).get("scene") is None or (spec["scene"].get("terrain") or {}).get("source") != "never-registered"

    async def test_non_dem_terrain_source_rejected_at_commit(self, engine_with_spec):
        """terrain 源指向非 raster-dem（geojson）→ SCENE_TERRAIN_SOURCE_TYPE 阻塞。"""
        engine, session = engine_with_spec
        scene = _scene_3d()
        scene["terrain"] = {"source": "s1", "exaggeration": 1.0}
        res = await engine.apply_mutation(session, SetSceneIntent(scene=scene))
        assert res.is_error is True

    async def test_camera_pitch_out_of_hard_range_rejected(self, engine_with_spec):
        """Review P3-4：相机 pitch/bearing 在写入口径钳制校验（>85 拒绝）。"""
        engine, session = engine_with_spec
        scene = _scene_3d()
        scene["camera"] = {"pitch": 200, "bearing": -15}
        res = await engine.apply_mutation(session, SetSceneIntent(scene=scene))
        assert res.is_error is True


class TestValidateScene:
    """coordinator.validate 的 SCENE_TERRAIN_SOURCE_REF 阻塞校验。"""

    def test_dangling_terrain_source_blocks(self):
        from app.services.mapspec.coordinator import validate

        spec = {
            "version": "1.4",
            "sources": {"s1": {"type": "geojson", "inlineData": {}}},
            "layers": [],
            "scene": {"mode": "3d", "terrain": {"source": "missing-dem"}},
        }
        report = validate(spec)
        assert not report["success"]
        assert any(e["code"] == "SCENE_TERRAIN_SOURCE_REF" for e in report["errors"])

    def test_bound_terrain_source_passes_scene_shape(self):
        from app.services.mapspec.coordinator import validate

        spec = {
            "version": "1.4",
            "sources": {
                "s1": {"type": "geojson", "inlineData": {}},
                "dem": {"type": "raster-dem", "url": "https://x.test/{z}/{x}/{y}.png"},
            },
            "layers": [],
            "scene": {"mode": "3d", "terrain": {"source": "dem"}},
        }
        report = validate(spec)
        assert report["success"]
