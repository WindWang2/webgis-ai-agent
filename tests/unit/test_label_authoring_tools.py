"""G16 WP10 —— webgis_label_* 工具旅程（真实事务路径）。

旗舰场景：「保留这三所学校的名称」→ pin ×3 →「隐藏其中一个」→
inspect 的有来源 why-not-shown。全部经 apply_gis_mutation 真实落库
（同一 CAS/撤销栈），工具结果被主对话路径消费（mapspec 回执）。
"""
import pytest

from app.services.mapspec.intents import UpsertLayerIntent
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine
from app.tools.label_authoring_tools import register_label_authoring_tools
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


def _tool_fn(name: str):
    """经真实 ToolRegistry 取工具入口（与 Pi/legacy dispatch 同面）。"""
    reg = ToolRegistry()
    register_label_authoring_tools(reg)
    entry = reg._tools[name]
    return entry if callable(entry) else entry.func


async def webgis_label_author(*args, **kw):
    return await _tool_fn("webgis_label_author")(*args, **kw)


async def webgis_label_inspect(*args, **kw):
    return await _tool_fn("webgis_label_inspect")(*args, **kw)

_FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": "sch-1",
         "geometry": {"type": "Point", "coordinates": [116.44, 39.96]},
         "properties": {"name": "市第一小学", "score": 9}},
        {"type": "Feature", "id": "sch-2",
         "geometry": {"type": "Point", "coordinates": [116.46, 39.96]},
         "properties": {"name": "市第二小学", "score": 8}},
        {"type": "Feature", "id": "sch-3",
         "geometry": {"type": "Point", "coordinates": [116.45, 39.94]},
         "properties": {"name": "市第三小学", "score": 7}},
    ],
}


async def _seed(session_id: str) -> None:
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(session_id, UpsertLayerIntent(
        layer={"id": "schools", "type": "circle", "source": "schools",
               "label": {"field": "name"}, "paint": {"circle-radius": 4}},
        source_data=_FC))
    assert res.is_error is False, res.error_msg


class TestLabelAuthorJourney:
    async def test_pin_three_schools_then_hide_one(self):
        sid = "sess-g16-tools-a"
        await _seed(sid)

        # 「保留这三所学校的名称」——pin ×3（一次原子批量）。
        res = await webgis_label_author(
            sid, "schools",
            ops=[{"featureId": f"sch-{i}", "op": "set",
                  "set": {"pinned": True}} for i in (1, 2, 3)])
        assert res["success"] is True, res
        assert res["overrides_applied"] == 3
        assert res["mutation_revision"] >= 1

        # 刷新后仍在（真实落库）。
        engine = MapSpecLifecycleEngine()
        fresh = await engine.store.get_mapspec(sid)
        label = next(l for l in fresh["layers"] if l["id"] == "schools")["label"]
        ovs = label["authoring"]["overrides"]
        assert all(o.get("origin") == "agent" for o in ovs)

        # 「隐藏其中一个」。
        res2 = await webgis_label_author(
            sid, "schools",
            ops=[{"featureId": "sch-2", "op": "set", "set": {"hidden": True}}])
        assert res2["success"] is True, res2

        # inspect：有来源理由 —— 隐藏的（authoring_policy），其余无证
        # 不猜（not_evaluated，不冒充 live）。
        insp = await webgis_label_inspect(sid, "schools",
                                          feature_ids=["sch-1", "sch-2"])
        assert insp["success"] is True
        by = {t["featureId"]: t for t in insp["targets"]}
        assert by["sch-2"]["why_not_shown"] == [
            {"reason": "hidden_by_user", "source": "authoring_policy"}]
        assert by["sch-1"]["status"] == "active"
        assert by["sch-1"]["override"]["pinned"] is True
        assert insp["summary"] == {"overrides": 3, "pinned": 3, "hidden": 1}

    async def test_preview_only_commits_nothing(self):
        sid = "sess-g16-tools-b"
        await _seed(sid)
        res = await webgis_label_author(
            sid, "schools", preview_only=True,
            ops=[{"featureId": "sch-1", "op": "set", "set": {"hidden": True}}])
        assert res["success"] is True
        assert res["preview"]["reports"][0]["after"]["hidden"] is True
        engine = MapSpecLifecycleEngine()
        fresh = await engine.store.get_mapspec(sid)
        label = next(l for l in fresh["layers"] if l["id"] == "schools")["label"]
        assert "authoring" not in label

    async def test_atomic_rejection_surfaces_findings(self):
        sid = "sess-g16-tools-c"
        await _seed(sid)
        res = await webgis_label_author(
            sid, "schools",
            ops=[{"featureId": "sch-1", "op": "set", "set": {"hidden": True}},
                 {"featureId": "ghost", "op": "set", "set": {"hidden": True}}])
        assert res["success"] is False
        assert "feature_not_found" in (res.get("message") or "")

    async def test_labels_config_via_tool(self):
        sid = "sess-g16-tools-d"
        await _seed(sid)
        res = await webgis_label_author(
            sid, "schools", ops=[],
            labels_config={"obstaclePolicy": "geometry_v1"})
        assert res["success"] is True, res
        insp = await webgis_label_inspect(sid, "schools")
        assert insp["labels_config"]["obstaclePolicy"] == "geometry_v1"

    async def test_inspect_unknown_feature_without_override(self):
        sid = "sess-g16-tools-e"
        await _seed(sid)
        insp = await webgis_label_inspect(sid, "schools", feature_ids=["sch-3"])
        assert insp["targets"][0]["status"] == "active"
        assert "override" not in insp["targets"][0]

    async def test_inspect_stale_reason_with_source(self):
        sid = "sess-g16-tools-f"
        # 源带 revision=1 起步 → 覆写写入时 handler 盖 sourceRevision=1；
        # 源刷新到 revision=5（要素身份不变）→ 覆写照常应用 + stale 标记。
        engine = MapSpecLifecycleEngine()
        await engine.apply_mutation(sid, UpsertLayerIntent(
            layer={"id": "schools", "type": "circle", "source": "schools",
                   "label": {"field": "name"}, "paint": {"circle-radius": 4}},
            source_data=_FC))
        from app.services.mapspec.intents import (
            PatchLayerLabelAuthoringIntent, UpsertSourceIntent)
        await engine.apply_mutation(sid, UpsertSourceIntent(
            source_id="schools",
            source={"type": "geojson", "inlineData": _FC,
                    "content_revision": 1}))
        res = await engine.apply_mutation(sid, PatchLayerLabelAuthoringIntent(
            layer_id="schools",
            ops=[{"featureId": "sch-1", "op": "set", "set": {"hidden": True}}]))
        assert res.is_error is False
        ov = next(o for o in _ov_of(res) if o["featureId"] == "sch-1")
        assert ov.get("sourceRevision") == 1  # handler 盖章当前源 revision

        await engine.apply_mutation(sid, UpsertSourceIntent(
            source_id="schools",
            source={"type": "geojson", "inlineData": _FC,
                    "content_revision": 5}))
        insp = await webgis_label_inspect(sid, "schools", feature_ids=["sch-1"])
        t = insp["targets"][0]
        assert t["status"] == "stale"
        reasons = {r["reason"] for r in t["why_not_shown"]}
        assert "stale_override" in reasons


def _ov_of(res) -> list:
    label = next(l for l in res.mapspec["layers"] if l["id"] == "schools")["label"]
    return (label.get("authoring") or {}).get("overrides") or []
