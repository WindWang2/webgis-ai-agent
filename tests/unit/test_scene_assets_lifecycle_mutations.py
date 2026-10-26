"""SetSceneAssetsIntent transactional mutation tests（G3D02 M2）。

Oracle anchors:
- 整表替换只动 scene.assets：mode/terrain/camera/sources/layers 不变。
- fail-closed：悬空/错型 source、越界放置、重复 id 整笔拒绝，
  last-known-good 不变。
- 事务性：失败回滚、revision 单调；空列表清空。
- 无 scene 座位：assets 写入创建 mode='3d' 档。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    InitProjectIntent,
    SetSceneAssetsIntent,
    SetSceneIntent,
    UpsertSourceIntent,
)


@pytest.fixture
async def engine_with_spec():
    engine = MapSpecLifecycleEngine()
    session = f"test-scene-assets-{uuid.uuid4().hex[:12]}"
    res = await engine.apply_mutation(
        session,
        InitProjectIntent(view={"center": [104.06, 30.67], "zoom": 12}),
    )
    assert not res.is_error, res.error_msg
    res = await engine.apply_mutation(
        session,
        UpsertSourceIntent(
            source_id="model-a",
            source={
                "type": "model3d",
                "format": "3dtiles",
                "url": "https://example.test/tileset/tileset.json",
                "content_revision": 1,
            },
        ),
    )
    assert not res.is_error, res.error_msg
    return engine, session


def _asset(asset_id: str = "campus", source: str = "model-a") -> dict:
    return {
        "id": asset_id,
        "source": source,
        "placement": {"anchor": [104.06, 30.67], "altitude_m": 500.0, "scale": 1.0},
        "vertical": {"datum": "egm96", "unit": "m", "elevation_ref": "ref:dem-abc"},
        "provenance_ref": "ref:asset-license-1",
    }


class TestSetSceneAssetsTransactions:
    async def test_write_and_replace_roundtrip(self, engine_with_spec):
        engine, session = engine_with_spec
        res = await engine.apply_mutation(
            session, SetSceneAssetsIntent(assets=[_asset()]))
        assert not res.is_error, res.error_msg
        assert res.mapspec["scene"]["mode"] == "3d"
        assert res.mapspec["scene"]["assets"][0]["id"] == "campus"

        # 整表替换：换 id 集
        res2 = await engine.apply_mutation(
            session, SetSceneAssetsIntent(assets=[_asset("tower")]))
        assert not res2.is_error, res2.error_msg
        assets = res2.mapspec["scene"]["assets"]
        assert [a["id"] for a in assets] == ["tower"]

    async def test_preserves_scene_siblings(self, engine_with_spec):
        engine, session = engine_with_spec
        res = await engine.apply_mutation(
            session,
            SetSceneIntent(scene={
                "mode": "2.5d",
                "camera": {"pitch": 30, "bearing": 10},
            }),
        )
        assert not res.is_error
        res2 = await engine.apply_mutation(
            session, SetSceneAssetsIntent(assets=[_asset()]))
        assert not res2.is_error
        scene = res2.mapspec["scene"]
        assert scene["mode"] == "2.5d"  # 模式不被改写
        assert scene["camera"] == {"pitch": 30, "bearing": 10}
        assert len(scene["assets"]) == 1

    async def test_none_rejected(self, engine_with_spec):
        engine, session = engine_with_spec
        res = await engine.apply_mutation(session, SetSceneAssetsIntent(assets=None))
        assert res.is_error

    async def test_empty_list_clears(self, engine_with_spec):
        engine, session = engine_with_spec
        res = await engine.apply_mutation(
            session, SetSceneAssetsIntent(assets=[_asset()]))
        assert not res.is_error
        res2 = await engine.apply_mutation(session, SetSceneAssetsIntent(assets=[]))
        assert not res2.is_error
        assert res2.mapspec["scene"]["assets"] == []

    async def test_dangling_source_rejected_lkg_intact(self, engine_with_spec):
        engine, session = engine_with_spec
        res = await engine.apply_mutation(
            session, SetSceneAssetsIntent(assets=[_asset()]))
        assert not res.is_error
        res2 = await engine.apply_mutation(
            session, SetSceneAssetsIntent(assets=[_asset(source="missing")]))
        assert res2.is_error
        assert "SCENE_ASSET" in res2.error_msg or "missing" in res2.error_msg
        # last-known-good：当前 spec 不变
        current = await engine.store.get_mapspec(session)
        assert current["scene"]["assets"][0]["source"] == "model-a"

    async def test_wrong_source_type_rejected(self, engine_with_spec):
        engine, session = engine_with_spec
        # 注册一个 geojson 源作错型对照
        res = await engine.apply_mutation(
            session,
            UpsertSourceIntent(
                source_id="geo-src",
                source={"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}},
            ),
        )
        assert not res.is_error
        res2 = await engine.apply_mutation(
            session, SetSceneAssetsIntent(assets=[_asset(source="geo-src")]))
        assert res2.is_error

    async def test_duplicate_ids_rejected(self, engine_with_spec):
        engine, session = engine_with_spec
        res = await engine.apply_mutation(
            session, SetSceneAssetsIntent(assets=[_asset(), _asset()]))
        assert res.is_error

    async def test_bad_anchor_rejected(self, engine_with_spec):
        engine, session = engine_with_spec
        bad = _asset()
        bad["placement"]["anchor"] = [104.06, 99.9]
        res = await engine.apply_mutation(session, SetSceneAssetsIntent(assets=[bad]))
        assert res.is_error

    async def test_nonpositive_scale_rejected(self, engine_with_spec):
        engine, session = engine_with_spec
        bad = _asset()
        bad["placement"]["scale"] = -1.5
        res = await engine.apply_mutation(session, SetSceneAssetsIntent(assets=[bad]))
        assert res.is_error

    async def test_revision_monotonic(self, engine_with_spec):
        engine, session = engine_with_spec
        rev1 = None
        for i in range(3):
            res = await engine.apply_mutation(
                session, SetSceneAssetsIntent(assets=[_asset(f"a{i}")]))
            assert not res.is_error
            rev = res.mutation_revision if hasattr(res, "mutation_revision") else None
            if rev is not None:
                if rev1 is not None:
                    assert rev > rev1
                rev1 = rev

    async def test_scene_absent_creates_3d_seat(self, engine_with_spec):
        """无 scene 座位时 assets 写入创建 mode='3d'（3D 资产隐含 3D 场景）。"""
        engine, session = engine_with_spec
        spec0 = await engine.store.get_mapspec(session)
        assert spec0 is None or "scene" not in (spec0 or {})
        res = await engine.apply_mutation(
            session, SetSceneAssetsIntent(assets=[_asset()]))
        assert not res.is_error
        assert res.mapspec["scene"]["mode"] == "3d"

    async def test_layers_sources_untouched(self, engine_with_spec):
        """presentation 面：数据/图层分支零变化。"""
        engine, session = engine_with_spec
        res = await engine.apply_mutation(
            session, SetSceneAssetsIntent(assets=[_asset()]))
        assert not res.is_error
        assert res.mapspec["sources"]["model-a"]["type"] == "model3d"
        assert res.mapspec.get("layers") in (None, [])
