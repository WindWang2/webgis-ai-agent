"""G03 M8 —— 独立评审（子代理 B）发现修复的回归测试。

对应评审 F1（守卫预算绕过，实证复现）/F3（NaN bbox 伪 pass，实证复现）/
F5（mapspec task_scope 接线零覆盖）/F7（负数 limit 尾切片）/F12（共享
拆包无直接特征化）。每条测试复现原始攻击/误判形状，断言修复后行为。
"""
from __future__ import annotations


import pytest

from app.services.data_ingest.geojson_guard import (
    GeojsonGuardError,
    validate_feature_collection,
)
from app.services.data_ingest.pipeline import (
    get_ingest_pipeline,
    reset_ingest_pipeline,
)
from app.services.dataset_semantics import store as sem_store_mod
from app.services.dataset_semantics.store import DatasetSemanticStore


def _fc(features: list) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _point(coords, props=None) -> dict:
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": coords},
            "properties": props or {}}


@pytest.fixture(autouse=True)
def _reset():
    reset_ingest_pipeline()
    yield
    reset_ingest_pipeline()


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(sem_store_mod, "_store", store)
    yield store
    from app.services.dataset_semantics import reset_dataset_semantic_store

    reset_dataset_semantic_store()


# ═══════════════════ F1：守卫预算绕过（实证复现的反例） ═══════════════════


class TestF1GuardBudgetBypass:
    def test_nan_first_with_padding_still_rejected(self):
        """评审 F1 反例：NaN 放遍历序最前 + 25k 垫点 → 必须拒绝。

        修复前：LIFO 预算耗尽后静默 return → NaN 入库。修复后：坐标扫描
        独立预算（100 万步）且耗尽即类型化拒绝——两种绕法都不成立。
        """
        padding = [[float(i % 100), float(i % 90)] for i in range(25_000)]
        coords = [[float("nan"), 39.0]] + padding  # LineString：NaN 首点
        fc = _fc([{"type": "Feature",
                   "geometry": {"type": "LineString", "coordinates": coords},
                   "properties": {}}])
        with pytest.raises(GeojsonGuardError) as ei:
            validate_feature_collection(fc)
        assert ei.value.code == "INVALID_COORDINATES_NONFINITE"

    def test_oversized_string_after_wide_siblings_rejected(self):
        """评审 F1 反例：300KB 字符串藏在 20k 兄弟键之后 → 必须拒绝。"""
        props: dict = {}
        for i in range(20_000):
            props[f"pad_{i}"] = i
        props["blob"] = "A" * (300 * 1024)
        fc = _fc([_point([116.0, 39.0], props)])
        with pytest.raises(GeojsonGuardError) as ei:
            validate_feature_collection(fc)
        assert ei.value.code in ("PROPERTY_STRING_OVERSIZED", "GUARD_BUDGET_EXCEEDED")

    def test_deep_subtree_after_wide_siblings_rejected(self):
        """评审 F1 反例：深子树藏于 20k 兄弟键后 → 必须拒绝或披露。"""
        props: dict = {f"pad_{i}": i for i in range(20_000)}
        node = props
        for _ in range(100):
            node["child"] = {}
            node = node["child"]
        fc = _fc([_point([116.0, 39.0], props)])
        with pytest.raises(GeojsonGuardError) as ei:
            validate_feature_collection(fc)
        assert ei.value.code in ("STRUCTURE_TOO_DEEP", "GUARD_BUDGET_EXCEEDED")

    def test_pathological_wide_props_typed_rejection_not_silent(self):
        """病态宽属性（>20k 键）→ 类型化 GUARD_BUDGET_EXCEEDED（不静默放行）。"""
        props = {f"k{i}": i for i in range(20_500)}
        fc = _fc([_point([116.0, 39.0], props)])
        with pytest.raises(GeojsonGuardError) as ei:
            validate_feature_collection(fc)
        assert ei.value.code == "GUARD_BUDGET_EXCEEDED"

    def test_heavy_realistic_geometry_not_falsely_rejected(self):
        """真实重几何（3 万顶点边界线）不得误拒（预算按载荷规模放）。"""
        coords = [[104.0 + (i % 300) * 0.0001, 30.6 + (i // 300) * 0.0001]
                  for i in range(30_000)]
        fc = _fc([{"type": "Feature",
                   "geometry": {"type": "LineString", "coordinates": coords},
                   "properties": {"name": "某市边界"}}])
        validate_feature_collection(fc)  # 不抛即通过

    def test_deep_geometry_coordinates_rejected(self):
        """评审 F4：坐标树深度帽（200 层嵌套数组 → STRUCTURE_TOO_DEEP）。"""
        node: list = [116.0, 39.0]
        for _ in range(200):
            node = [node]
        fc = _fc([{"type": "Feature",
                   "geometry": {"type": "Point", "coordinates": node},
                   "properties": {}}])
        with pytest.raises(GeojsonGuardError) as ei:
            validate_feature_collection(fc)
        assert ei.value.code == "STRUCTURE_TOO_DEEP"

    async def test_pipeline_rejects_nan_after_padding(self, sem_store):
        """管线级反例：垫料后 NaN 全链拒绝（无 ref 铸造）。"""
        padding = [[float(i % 100), float(i % 90)] for i in range(25_000)]
        coords = [[float("nan"), 39.0]] + padding
        result = await get_ingest_pipeline().ingest(
            "g03-f1", _fc([{"type": "Feature",
                            "geometry": {"type": "LineString",
                                         "coordinates": coords},
                            "properties": {}}]))
        assert not result.ok
        assert result.error_code == "INVALID_COORDINATES_NONFINITE"
        assert result.ref_id == ""


# ═══════════════════ F3：NaN/Inf bbox 伪 pass ════════════════════════════


class TestF3NanBbox:
    def test_scope_bbox_nan_rejected_not_pass(self):
        """评审 F3 实证反例：任务 scope bbox 含 NaN → 不可解析（不伪 pass）。"""
        from app.services.dataset_semantics.preflight import (
            preflight_task_scope_check,
        )

        result, gaps, check = preflight_task_scope_check(
            [("poi", _bbox_descriptor([116.3, 39.85, 116.5, 39.95]))],
            {"place": "北京", "bbox": [116.2, 39.8, 116.6, 40.1]},
        )
        assert result == "pass"  # 正常形状且重叠的基准

        bad = preflight_task_scope_check(
            [("poi", _bbox_descriptor([116.3, 39.85, 116.5, 39.95]))],
            {"place": "某地", "bbox": [float("nan"), 39.8, 116.6, 40.1]},
        )
        assert bad[2]["result"] == "unknown", "不可解析 bbox ≠ 未声明不检查"
        assert any(g.code == "PREFLIGHT_SCOPE_UNVERIFIABLE" for g in bad[1])

    def test_descriptor_bbox_nan_normalized_to_none(self):
        """descriptor 侧：NaN/Inf bbox 分量 → None（无证据，不持久化毒值）。"""
        from app.lib.gis.dataset_descriptor import GISDatasetDescriptor

        d = GISDatasetDescriptor(bbox=[float("nan"), 30.0, 104.0, 104.1])
        assert d.bbox is None
        d2 = GISDatasetDescriptor(bbox=[103.0, 30.0, float("inf"), 30.5])
        assert d2.bbox is None
        d3 = GISDatasetDescriptor(bbox=[103.0, 30.0, 104.0, 30.5])
        assert d3.bbox == [103.0, 30.0, 104.0, 30.5]

    def test_scope_fingerprint_nan_bbox_is_empty_scope(self):
        from app.services.dataset_semantics.task_scope import scope_fingerprint

        assert scope_fingerprint(place="x", bbox=[float("nan"), 1, 2, 3]) == (
            scope_fingerprint(place="x"))


def _bbox_descriptor(bbox):
    """带 bbox 的最小 descriptor 替身（preflight 只读 .bbox 属性）。"""
    from types import SimpleNamespace

    return SimpleNamespace(bbox=bbox, crs="EPSG:4326")


# ═══════════════════ F5：mapspec task_scope 接线 ═════════════════════════


class TestF5MapspecScopeWiring:
    async def test_city_switch_marks_binding_recompute(self, sem_store, monkeypatch):
        """同数据重绑定 + 城市切换 → source 带 TASK_SCOPE_CHANGED 裁决。"""
        from app.services import mapspec_store as ms_mod
        from app.services.mapspec_store import mapspec_store

        sid = "g03-f5"
        result = await get_ingest_pipeline().ingest(
            sid, _fc([_point([104.06, 30.6], {"v": 1})]), crs="EPSG:4326")
        assert result.ok
        ref = result.ref_id

        intents = []

        async def _fake_apply(session_id, intent, **kwargs):
            intents.append(intent)
            from app.services.mapspec import MapSpecResult

            return MapSpecResult(mapspec={"sources": {
                intent.source_id: intent.source}})

        async def _fake_get(session_id):
            return self._prev_spec

        monkeypatch.setattr(ms_mod.mapspec_store.engine, "apply_mutation",
                            _fake_apply)
        monkeypatch.setattr(ms_mod.mapspec_store, "get_mapspec", _fake_get)

        # 第一次绑定：成都范围（无旧绑定 → 无 scope 对账键）
        await mapspec_store.source_profile(
            sid, "schools", ref, task_scope={"place": "成都"})
        assert "task_scope_fingerprint" in intents[-1].source
        # 把第一次绑定的 source 作为 prev 持久化面
        self._prev_spec = {"sources": {"schools": dict(intents[-1].source)}}

        # 同数据同范围重绑 → 无 TASK_SCOPE_CHANGED（指纹一致 → 无键或 valid）
        await mapspec_store.source_profile(
            sid, "schools", ref, task_scope={"place": "成都"})
        reuse_same = intents[-1].source.get("descriptor_reuse") or {}
        assert "TASK_SCOPE_CHANGED" not in reuse_same.get("reason_codes", [])

        # 同数据切到北京 → TASK_SCOPE_CHANGED + recompute
        await mapspec_store.source_profile(
            sid, "schools", ref, task_scope={"place": "北京"})
        reuse_switch = intents[-1].source.get("descriptor_reuse") or {}
        assert reuse_switch.get("verdict") == "recompute"
        assert "TASK_SCOPE_CHANGED" in reuse_switch.get("reason_codes")

    _prev_spec: dict = {"sources": {}}


# ═══════════════════ F7/F12：adapter limit 钳制 + 共享拆包特征化 ═══════════


class TestF7NegativeLimit:
    def test_negative_limit_returns_empty_not_tail_slice(self, patched_safe_sessions):
        from tests.data.fabric_fixtures import FakeSourceServer

        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.geojson_url_adapter import (
            GeojsonUrlAdapter,
        )

        fc = _fc([_point([116.0 + i * 0.01, 39.0]) for i in range(10)])
        server = FakeSourceServer()

        def _handler(request):
            from tests.fixtures.data_fabric.fake_server import make_response

            server._record(request)
            return make_response(
                request.url, json_body=fc,
                headers={"Content-Type": "application/geo+json"})

        server._add("/neg.geojson", _handler)
        patched_safe_sessions(server)
        adapter = GeojsonUrlAdapter(ConnectionProfile(
            id="gj_neg", source_type="geojson_url",
            url=server.base_url + "/neg.geojson"))
        preview = adapter.preview("default", limit=-5)
        assert preview["features"] == [], "负数 limit 不得变尾部切片"
        assert preview["total_available"] == 10
        # truncated=True 是诚实披露（有数据未返回），不得谎称完整
        assert preview["truncated"] is True


class TestF12SharedUnwrapCharacterization:
    """评审 F12：两处共享拆包的直接特征化（此前仅 journey 间接覆盖）。"""

    def test_to_feature_collection_unwraps_storage_wrapper(self):
        from app.lib.geo_processor.core import to_feature_collection

        fc = {"type": "FeatureCollection", "features": [_point([116.0, 39.0])]}
        out = to_feature_collection({"geojson": fc})
        assert out.get("type") == "FeatureCollection"
        assert len(out.get("features", [])) == 1
        # 非 FC 内层不误拆（geojson 键下不是 dict → 原样走既有分支）
        weird = {"geojson": "not-a-dict", "features": [_point([1.0, 2.0])]}
        out2 = to_feature_collection(weird)
        assert len(out2.get("features", [])) == 1

    def test_inspect_semantics_sees_crs_through_wrapper(self):
        from app.services.gis_harness.spatial_semantics.inspect_semantics import (
            evidence_from_feature_collection,
        )

        fc = {"type": "FeatureCollection",
              "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
              "features": [_point([104.1, 30.65])]}
        direct = evidence_from_feature_collection(fc)
        wrapped = evidence_from_feature_collection({"geojson": fc})
        assert direct.crs == "EPSG:4326"
        assert wrapped.crs == "EPSG:4326", "包装层不得遮蔽 crs member"
