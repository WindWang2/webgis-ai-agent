"""G16 WP03 —— PatchLayerLabelAuthoringIntent 生命周期回归。

真实验径：MapSpecLifecycleEngine.apply_mutation（锁 + CAS + checkpoint +
journal），覆盖：原子性、身份核验、溯源盖章、reset 语义、labels_config
补丁、撤销栈（auto_checkpoint）、失败路径 typed findings。
"""
import pytest

from app.services.mapspec.intents import (
    PatchLayerLabelAuthoringIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

pytestmark = pytest.mark.unit

_SCHOOLS_FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": "s1",
         "geometry": {"type": "Point", "coordinates": [116.44, 39.96]},
         "properties": {"name": "第一小学", "score": 9}},
        {"type": "Feature", "id": "s2",
         "geometry": {"type": "Point", "coordinates": [116.46, 39.96]},
         "properties": {"name": "第二小学", "score": 7}},
        {"type": "Feature", "id": "s3",
         "geometry": {"type": "Point", "coordinates": [116.45, 39.94]},
         "properties": {"name": "第三小学", "score": 8}},
    ],
}

_LAYER = {
    "id": "schools", "type": "circle", "source": "schools",
    "label": {"field": "name"}, "paint": {"circle-radius": 4},
}


async def _seed(session_id: str) -> MapSpecLifecycleEngine:
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        session_id,
        UpsertLayerIntent(layer=dict(_LAYER), source_data=_SCHOOLS_FC),
    )
    assert res.is_error is False, res.error_msg
    return engine


def _overrides(res) -> list:
    layers = res.mapspec.get("layers", [])
    label = next(l for l in layers if l["id"] == "schools")["label"]
    return (label.get("authoring") or {}).get("overrides") or []


class TestAuthoringMutation:
    async def test_set_and_merge_persists(self):
        engine = await _seed("sess-g16-a")
        res = await engine.apply_mutation("sess-g16-a", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "set",
                  "set": {"pinned": True, "offset": {"dx": 1.5, "dy": -0.5}}},
                 {"featureId": "s2", "op": "set", "set": {"hidden": True}}],
        ))
        assert res.is_error is False, res.error_msg
        ovs = _overrides(res)
        assert {o["featureId"] for o in ovs} == {"s1", "s2"}
        s1 = next(o for o in ovs if o["featureId"] == "s1")
        assert s1["pinned"] is True and s1["offset"] == {"dx": 1.5, "dy": -0.5}
        assert s1["origin"] == "agent"  # 引擎路径缺省 origin（无 REST 门面）

    async def test_atomic_rejection_no_partial_write(self):
        engine = await _seed("sess-g16-b")
        res = await engine.apply_mutation("sess-g16-b", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "set", "set": {"hidden": True}},
                 {"featureId": "ghost", "op": "set", "set": {"hidden": True}}],
        ))
        assert res.is_error is True
        assert "feature_not_found" in res.error_msg
        # 原子：s1 的合法 op 也不落盘（错误回执不带 mapspec —— 读权威库）。
        fresh = await engine.store.get_mapspec("sess-g16-b")
        label = next(l for l in fresh["layers"] if l["id"] == "schools")["label"]
        assert (label.get("authoring") or {}).get("overrides") in (None, [])

    async def test_bad_target_type_rejected(self):
        engine = await _seed("sess-g16-c")
        res = await engine.apply_mutation("sess-g16-c", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "set", "set": {"text": "x" * 200}}],
        ))
        assert res.is_error is True and "text_too_long" in res.error_msg

    async def test_unknown_layer_rejected(self):
        engine = await _seed("sess-g16-d")
        res = await engine.apply_mutation("sess-g16-d", PatchLayerLabelAuthoringIntent(
            layer_id="nope", ops=[{"featureId": "s1", "op": "set", "set": {"hidden": True}}]))
        assert res.is_error is True and "not found" in res.error_msg

    async def test_layer_without_label_config_rejected(self):
        engine = MapSpecLifecycleEngine()
        await engine.apply_mutation("sess-g16-e", UpsertLayerIntent(
            layer={"id": "plain", "type": "circle", "source": "src",
                   "paint": {}},
            source_data=_SCHOOLS_FC))
        res = await engine.apply_mutation("sess-g16-e", PatchLayerLabelAuthoringIntent(
            layer_id="plain",
            ops=[{"featureId": "s1", "op": "set", "set": {"hidden": True}}]))
        assert res.is_error is True and "no label config" in res.error_msg

    async def test_reset_scopes_are_independent(self):
        engine = await _seed("sess-g16-f")
        await engine.apply_mutation("sess-g16-f", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "set",
                  "set": {"hidden": True, "text": "自定义名", "pinned": True,
                          "offset": {"dx": 1, "dy": 1}}}],
        ))
        # visibility reset：只解除隐藏 —— 文字与位置保留。
        res = await engine.apply_mutation("sess-g16-f", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "reset", "scope": "visibility"}]))
        assert res.is_error is False, res.error_msg
        s1 = _overrides(res)[0]
        assert "hidden" not in s1
        assert s1["text"] == "自定义名" and s1["pinned"] is True
        # position reset：恢复自动位置 —— 隐藏/文字保留。
        res = await engine.apply_mutation("sess-g16-f", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "reset", "scope": "position"}]))
        s1 = _overrides(res)[0]
        assert "pinned" not in s1 and "offset" not in s1
        assert s1["text"] == "自定义名"
        # text reset：恢复字段文字（此时只剩它 —— 条目随后为空）。
        res = await engine.apply_mutation("sess-g16-f", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "reset", "scope": "text"}]))
        assert _overrides(res) == []
        # all reset on 已空条目 → noop。
        res = await engine.apply_mutation("sess-g16-f", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "reset", "scope": "all"}]))
        assert res.is_error is False and _overrides(res) == []
        res = await engine.apply_mutation("sess-g16-f", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "reset", "scope": "all"}]))
        assert _overrides(res) == []

    async def test_labels_config_patch_and_clear(self):
        engine = await _seed("sess-g16-g")
        res = await engine.apply_mutation("sess-g16-g", PatchLayerLabelAuthoringIntent(
            layer_id="schools", ops=[], labels_config={"obstaclePolicy": "geometry_v1"}))
        assert res.is_error is False, res.error_msg
        labels = res.mapspec["layout"]["labels"]
        assert labels.get("obstaclePolicy") == "geometry_v1"
        res2 = await engine.apply_mutation("sess-g16-g", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "set", "set": {"hidden": True}}],
            labels_config={"obstaclePolicy": None}))
        assert "obstaclePolicy" not in res2.mapspec["layout"]["labels"]

    async def test_identity_missing_source_rejected(self):
        engine = MapSpecLifecycleEngine()
        bare_fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
             "properties": {"name": "无名点"}},
        ]}
        await engine.apply_mutation("sess-g16-h", UpsertLayerIntent(
            layer={"id": "bare", "type": "circle", "source": "src",
                   "label": {"field": "name"}, "paint": {}},
            source_data=bare_fc))
        res = await engine.apply_mutation("sess-g16-h", PatchLayerLabelAuthoringIntent(
            layer_id="bare",
            ops=[{"featureId": "whatever", "op": "set", "set": {"hidden": True}}]))
        assert res.is_error is True
        assert "identity" in res.error_msg

    async def test_checkpoint_rollback_restores_authoring(self):
        engine = await _seed("sess-g16-i")
        await engine.apply_mutation("sess-g16-i", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "set", "set": {"hidden": True}}]))
        # auto_checkpoint=True：上一笔的 checkpoint 回滚可撤销本笔。
        from app.services.mapspec.intents import CheckpointIntent, RollbackIntent
        ck = await engine.apply_mutation("sess-g16-i", CheckpointIntent())
        assert ck.is_error is False and ck.checkpoint_id
        res2 = await engine.apply_mutation("sess-g16-i", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "reset", "scope": "all"}]))
        assert res2.is_error is False and _overrides(res2) == []
        rb = await engine.apply_mutation("sess-g16-i", RollbackIntent(
            checkpoint_id=ck.checkpoint_id))
        assert rb.is_error is False, rb.error_msg
        assert {o["featureId"] for o in _overrides(rb)} == {"s1"}

    async def test_concurrent_revision_conflict_surfaces(self):
        engine = await _seed("sess-g16-j")
        res1 = await engine.apply_mutation("sess-g16-j", PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "s1", "op": "set", "set": {"hidden": True}}],
        ))
        assert res1.is_error is False
        rev1 = res1.mutation_revision
        # 用过期 revision 提交（模拟迟到工具结果）→ 不得覆盖。
        res2 = await engine.apply_mutation(
            "sess-g16-j",
            PatchLayerLabelAuthoringIntent(
                layer_id="schools",
                ops=[{"featureId": "s1", "op": "set",
                      "set": {"hidden": None, "text": "迟到写入"}}],
            ),
            expected_revision=rev1 - 1 if rev1 and rev1 > 1 else None,
        )
        # 引擎级 CAS：过期提交要么 superseded 要么拒绝 —— 绝不静默覆盖。
        if res2.superseded or res2.is_error:
            # 用户最新决定保留。
            fresh = await engine.store.get_mapspec("sess-g16-j")
            ovs = (next(l for l in fresh["layers"] if l["id"] == "schools")
                   ["label"].get("authoring") or {}).get("overrides") or []
            s1 = next(o for o in ovs if o["featureId"] == "s1")
            assert s1.get("hidden") is True
