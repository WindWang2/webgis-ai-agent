"""Track B（M14）：图层浏览/聚合工具契约测试。

- 注册面：三工具确实进 registry（init_tools 全量装载后可发现）；
- 分页/过滤/排序/total 诚实语义（与 REST 同源服务函数）；
- 聚合的窗口/非数值披露；
- 单要素链内/位置寻址两套命名空间不混用；
- 越权/缺失 ref 的错误语义走 session_data_manager 同一路径。
"""
from __future__ import annotations

import json

import pytest

from app.services.session_data import session_data_manager
from app.tools import init_tools
from app.tools.registry import ToolRegistry


class _FakeRefStore:
    """最小内存 ref store（monkeypatch 到 session_data_manager 的取数面）。"""

    def __init__(self, fc, revision=5):
        self.fc = fc
        self.descriptor = {
            "content_revision": revision,
            "feature_count": len(fc["features"]),
        }

    async def resolve_alias(self, session_id, ref):
        return ref

    async def get_ref_descriptor(self, session_id, ref):
        return dict(self.descriptor) if ref == "ref:schools" else None

    async def get_ref_data_shared(self, session_id, ref, owner_token=None):
        if ref != "ref:schools":
            class _Missing:
                success = False
                error = "数据不可用"
                error_type = "NotFound"
            return _Missing()

        class _Res:
            success = True
            error = None
            error_type = None
            data = {"geojson": self.fc}
        return _Res()


def _fc(n=300):
    return {"type": "FeatureCollection", "features": [
        {
            "type": "Feature",
            "id": i,
            "geometry": {"type": "Point", "coordinates": [104.0 + (i % 40) * 0.1, 30.6]},
            "properties": {
                "i": i,
                "类型": ["小学", "中学", "幼儿园"][i % 3],
                "学生数": (i * 37) % 900,
            },
        }
        for i in range(n)
    ]}


@pytest.fixture
def registry():
    reg = ToolRegistry()
    init_tools(reg)
    return reg


async def _run(registry, name, args, store):

    orig = (
        session_data_manager.resolve_alias,
        session_data_manager.get_ref_descriptor,
        session_data_manager.get_ref_data_shared,
    )
    session_data_manager.resolve_alias = store.resolve_alias
    session_data_manager.get_ref_descriptor = store.get_ref_descriptor
    session_data_manager.get_ref_data_shared = store.get_ref_data_shared
    try:
        handler = registry._tools.get(name)
        assert handler is not None, f"{name} 未注册"
        return await handler(session_id="session-tools-test-0001", **args)
    finally:
        (session_data_manager.resolve_alias,
         session_data_manager.get_ref_descriptor,
         session_data_manager.get_ref_data_shared) = orig
        import app.services.feature_pages as fp
        fp._derived_cache.clear()


def test_tools_registered(registry):
    for name in ("webgis_layer_features_page", "webgis_layer_aggregate", "webgis_layer_feature_detail"):
        assert name in registry._tools


@pytest.mark.asyncio
async def test_features_page_honest_metadata(registry):
    store = _FakeRefStore(_fc(300))
    out = await _run(registry, "webgis_layer_features_page",
                     {"ref_id": "ref:schools", "limit": 10}, store)
    assert len(out["features"]) == 10
    assert out["pagination"]["has_more"] is True
    assert out["pagination"]["is_complete"] is False
    assert out["revision"] == 5
    assert out["id_mode"] == "native"
    assert "scope_note" in out


@pytest.mark.asyncio
async def test_features_page_filter_total_and_order(registry):
    store = _FakeRefStore(_fc(300))
    where = json.dumps({"field": "类型", "op": "eq", "value": "小学"})
    out = await _run(registry, "webgis_layer_features_page",
                     {"ref_id": "ref:schools", "limit": 5, "where": where, "order": "-学生数"}, store)
    assert all(f["properties"]["类型"] == "小学" for f in out["features"])
    assert out["total_matching"] == 100
    assert "total_exact" in out["reason_codes"]
    students = [f["properties"]["学生数"] for f in out["features"]]
    assert students == sorted(students, reverse=True)


@pytest.mark.asyncio
async def test_features_page_revision_conflict(registry):
    store = _FakeRefStore(_fc(30))
    out = await _run(registry, "webgis_layer_features_page",
                     {"ref_id": "ref:schools", "limit": 5, "v": 4}, store)
    assert out["error"] == "revision_conflict"
    assert out["current_revision"] == 5


@pytest.mark.asyncio
async def test_features_page_bad_where(registry):
    store = _FakeRefStore(_fc(10))
    out = await _run(registry, "webgis_layer_features_page",
                     {"ref_id": "ref:schools", "where": "not json"}, store)
    assert "参数非法" in out["error"]


@pytest.mark.asyncio
async def test_aggregate_window_stats(registry):
    store = _FakeRefStore(_fc(300))
    out = await _run(registry, "webgis_layer_aggregate",
                     {"ref_id": "ref:schools", "group_by": "类型"}, store)
    by_key = {row["key"]: row["count"] for row in out["rows"]}
    assert by_key == {"小学": 100, "中学": 100, "幼儿园": 100}
    assert out["is_complete"] is True
    assert out["total_matching"] == 300

    agg = await _run(registry, "webgis_layer_aggregate",
                     {"ref_id": "ref:schools", "group_by": "类型", "agg": "mean", "agg_field": "学生数"}, store)
    assert all("mean" in row for row in agg["rows"])


@pytest.mark.asyncio
async def test_feature_detail_native_and_positional_namespaces(registry):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104, 30]},
             "properties": {"id": "native-1", "n": "a"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.1, 30]},
             "properties": {"n": "no-id"}},
        ],
    }
    store = _FakeRefStore(fc)
    native = await _run(registry, "webgis_layer_feature_detail",
                        {"ref_id": "ref:schools", "feature_id": "native-1"}, store)
    assert native["id_mode"] == "native"
    assert native["feature"]["properties"]["n"] == "a"

    positional = await _run(registry, "webgis_layer_feature_detail",
                            {"ref_id": "ref:schools", "feature_id": "2", "id_mode": "positional"}, store)
    assert positional["id_mode"] == "positional"
    assert positional["feature"]["id"] == 2
    assert "仅当前 revision 内稳定" in positional["scope_note"]

    # 有链内 id 的要素拒绝位置寻址（命名空间不混用）
    mixed = await _run(registry, "webgis_layer_feature_detail",
                       {"ref_id": "ref:schools", "feature_id": "1", "id_mode": "positional"}, store)
    assert "链内稳定 id" in mixed["error"]


@pytest.mark.asyncio
async def test_missing_ref_not_found(registry):
    store = _FakeRefStore(_fc(5))
    out = await _run(registry, "webgis_layer_features_page",
                     {"ref_id": "ref:missing", "limit": 5}, store)
    assert out["error"] == "数据不可用"


@pytest.mark.asyncio
async def test_features_page_post_fetch_revision_recheck(registry):
    """R1-P2-2：计算期间覆写 → 取数后复查必须报 revision_conflict。"""
    store = _FakeRefStore(_fc(30))
    orig = (
        session_data_manager.resolve_alias,
        session_data_manager.get_ref_descriptor,
        session_data_manager.get_ref_data_shared,
    )
    orig_descriptor = store.get_ref_descriptor

    calls = {"n": 0}

    async def _advancing_descriptor(session_id, ref):
        d = await orig_descriptor(session_id, ref)
        calls["n"] += 1
        if d is not None and calls["n"] > 1:
            d["content_revision"] = 6  # 取数后 revision 前进（首次读仍是 5）
        return d

    orig_shared = store.get_ref_data_shared
    session_data_manager.resolve_alias = store.resolve_alias
    session_data_manager.get_ref_descriptor = _advancing_descriptor
    session_data_manager.get_ref_data_shared = orig_shared
    try:
        handler = registry._tools["webgis_layer_features_page"]
        out = await handler(ref_id="ref:schools", limit=5, session_id="s1")
        assert out["error"] == "revision_conflict"
        assert out["current_revision"] == 6
    finally:
        session_data_manager.resolve_alias = orig[0]
        session_data_manager.get_ref_descriptor = orig[1]
        session_data_manager.get_ref_data_shared = orig[2]
