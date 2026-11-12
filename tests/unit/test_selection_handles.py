"""selection_handles（G24 / P3）：服务端权威选择集句柄域内契约。

关键不变量：
- scope 封闭词表 + AM 拆分归一（A14）；
- 计数诚实：count_exact 只在可证时 True；
- ids 解析走 _wfid 链首的 FEATURE_ID_KEYS，缺失明示；
- handle 三重绑定（session/ref/handle）+ TTL + revision 一致性（A22）；
- 分页不漏不重；bbox 语义 = 粗滤相交（保守超集）并披露；
- 物化超预算 typed 拒绝，绝不静默截断。
"""
from __future__ import annotations

import asyncio

import pytest

import app.services.selection_handles as sh
from app.services.selection_handles import (
    MAX_MATERIALIZE_FEATURES,
    SelectionHandleError,
    create_selection,
    handle_store,
    parse_scope_payload,
    scope_matches,
    selection_features_page,
)


def _fc(n: int, lon0: float = 104.0, lat0: float = 30.0, with_wfid: bool = False):
    feats = []
    for i in range(n):
        props = {"i": i, "name": f"f{i}"}
        if with_wfid:
            props["_wfid"] = f"wf1-{i:04d}"
        feats.append({
            "type": "Feature",
            "id": i,
            "geometry": {"type": "Point", "coordinates": [lon0 + i * 0.01, lat0 + (i % 7) * 0.01]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": feats}


# ─── scope 解析 ─────────────────────────────────────────────────────────────

def test_scope_bbox_normalisation_and_am_split():
    scope = parse_scope_payload({"kind": "bbox", "bbox": [104, 30, 106, 32]})
    assert scope["boxes"] == [(104.0, 30.0, 106.0, 32.0)]
    # 跨反子午线：w > e → 两箱
    scope_am = parse_scope_payload({"kind": "bbox", "bbox": [170, -10, -170, 10]})
    assert scope_am["boxes"] == [(170.0, -10.0, 180.0, 10.0), (-180.0, -10.0, -170.0, 10.0)]
    # s/n 倒置自动归一
    assert parse_scope_payload({"kind": "bbox", "bbox": [0, 32, 1, 30]})["boxes"] == [(0.0, 30.0, 1.0, 32.0)]
    for bad in (
        {"kind": "bbox", "bbox": [1, 2, 3]},
        {"kind": "bbox", "bbox": ["a", 2, 3, 4]},
        {"kind": "bbox", "bbox": [181, 2, 3, 4]},
        {"kind": "unknown"},
        {"kind": "ids", "ids": []},
        {"kind": "ids", "ids": ["a"] * (sh.MAX_SCOPE_IDS + 1)},
        {"kind": "where", "where": {"op": "geotherma"}},
    ):
        with pytest.raises(SelectionHandleError):
            parse_scope_payload(bad)


def test_scope_where_uses_typed_ast_validation():
    scope = parse_scope_payload({"kind": "where", "where": {"field": "i", "op": "gte", "value": 5}})
    assert scope_matches(scope, {"properties": {"i": 6}}) is True
    assert scope_matches(scope, {"properties": {"i": 4}}) is False


def test_scope_matches_bbox_is_coarse_envelope_intersect():
    scope = parse_scope_payload({"kind": "bbox", "bbox": [104.0, 30.0, 104.5, 31.0]})
    inside = {"geometry": {"type": "Point", "coordinates": [104.2, 30.2]}}
    outside = {"geometry": {"type": "Point", "coordinates": [105.2, 30.2]}}
    # 大要素包络与窗口相交（几何本身可能在外）—— 粗滤是保守超集
    big = {"geometry": {"type": "LineString", "coordinates": [[100.0, 30.1], [110.0, 30.1]]}}
    assert scope_matches(scope, inside)
    assert not scope_matches(scope, outside)
    assert scope_matches(scope, big)


# ─── 创建与计数 ─────────────────────────────────────────────────────────────

def test_create_bbox_selection_exact_count():
    fc = _fc(50)
    entry = asyncio.run(create_selection(
        "sess-a", "ref:a", 3, {"kind": "bbox", "bbox": [104.0, 30.0, 104.2, 31.0]},
        fc["features"]))
    assert entry.count_exact is True
    assert entry.count == 21  # i*0.01 ≤ 0.2 → i ≤ 20（纬度窗口内）
    assert entry.revision == 3


def test_create_ids_selection_resolves_chain_and_discloses_missing():
    fc = _fc(10, with_wfid=True)
    entry = asyncio.run(create_selection(
        "sess-a", "ref:a", 1, {"kind": "ids", "ids": ["wf1-0002", "wf1-0005", "nope-404"]},
        fc["features"]))
    assert entry.count == 2
    assert entry.count_exact is True
    assert entry.missing_ids == ["nope-404"]


def test_create_where_selection():
    fc = _fc(20)
    entry = asyncio.run(create_selection(
        "sess-a", "ref:a", 1, {"kind": "where", "where": {"field": "i", "op": "lt", "value": 5}},
        fc["features"]))
    assert entry.count == 5 and entry.count_exact is True


def test_count_over_exact_limit_is_honest_unknown():
    fc = _fc(300_000)  # > TOTAL_EXACT_LIMIT(250k)
    entry = asyncio.run(create_selection(
        "sess-a", "ref:a", 1, {"kind": "where", "where": {"field": "i", "op": "lt", "value": 10}},
        fc["features"]))
    assert entry.count is None and entry.count_exact is False


# ─── handle 绑定 / TTL / 隔离 ───────────────────────────────────────────────

def test_handle_session_and_ref_binding():
    fc = _fc(5)
    entry = asyncio.run(create_selection(
        "sess-a", "ref:a", 1, {"kind": "ids", "ids": ["0", "1"]}, fc["features"]))
    # 正确绑定可取
    got = handle_store.get("sess-a", "ref:a", entry.handle)
    assert got.handle == entry.handle
    # 跨会话（A16）：不存在（不泄露）
    with pytest.raises(SelectionHandleError) as ei:
        handle_store.get("sess-b", "ref:a", entry.handle)
    assert ei.value.code == "not_found"
    # 跨 ref：不属于
    with pytest.raises(SelectionHandleError):
        handle_store.get("sess-a", "ref:b", entry.handle)


def test_handle_ttl_expiry():
    fc = _fc(5)
    entry = asyncio.run(create_selection(
        "sess-ttl", "ref:a", 1, {"kind": "ids", "ids": ["0"]}, fc["features"]))
    # 直接拨旧 last_used 模拟空闲超时
    with handle_store._lock:
        handle_store._by_session["sess-ttl"][entry.handle].last_used -= (
            sh.SELECTION_TTL_SECONDS + 1)
    with pytest.raises(SelectionHandleError) as ei:
        handle_store.get("sess-ttl", "ref:a", entry.handle)
    assert ei.value.code == "expired"


def test_handle_quota_lru_eviction():
    fc = _fc(2)
    handles = []
    for i in range(sh.MAX_HANDLES_PER_SESSION + 3):
        e = asyncio.run(create_selection(
            "sess-q", "ref:a", 1, {"kind": "ids", "ids": ["0"]}, fc["features"]))
        handles.append(e.handle)
    with handle_store._lock:
        live = list(handle_store._by_session["sess-q"].keys())
    assert len(live) == sh.MAX_HANDLES_PER_SESSION
    # 最旧的被 LRU 逐出
    with pytest.raises(SelectionHandleError):
        handle_store.get("sess-q", "ref:a", handles[0])
    handle_store.get("sess-q", "ref:a", handles[-1])  # 最新的仍在


# ─── 分页（不漏不重） ───────────────────────────────────────────────────────

def _paginate(entry, fc, limit=7):
    rows, cursor = [], None
    while True:
        page = asyncio.run(selection_features_page(
            entry, fc, limit=limit, cursor=cursor, fields=None, order=None))
        rows.extend(f["id"] for f in page["features"])
        if not page["has_more"]:
            return rows, page
        cursor = page["next_cursor"]


def test_features_paging_bbox_no_miss_no_dup():
    fc = _fc(60)
    entry = asyncio.run(create_selection(
        "sess-p", "ref:a", 1, {"kind": "bbox", "bbox": [104.0, 30.0, 104.4, 31.0]},
        fc["features"]))
    rows, last = _paginate(entry, fc)
    expected = [i for i in range(60) if 104.0 + i * 0.01 <= 104.4]
    assert rows == expected, "分页拼接 = 全集稳定序（不漏不重）"
    assert "bbox_coarse" in last["reason_codes"]


def test_features_paging_ids_scope_exact_enumeration():
    fc = _fc(30, with_wfid=True)
    ids = [f"wf1-{i:04d}" for i in range(0, 30, 3)]  # 0,3,6,...27 → 10 个
    entry = asyncio.run(create_selection(
        "sess-p", "ref:a", 1, {"kind": "ids", "ids": ids}, fc["features"]))
    rows, last = _paginate(entry, fc, limit=4)
    assert rows == list(range(0, 30, 3))
    assert entry.count == 10
    assert "ids_resolved" in last["reason_codes"]


def test_features_paging_am_split_scope():
    # 两批要素分别在 ±180 两侧
    feats = [
        {"type": "Feature", "id": 1, "geometry": {"type": "Point", "coordinates": [175.0, 30.0]}, "properties": {"i": 1}},
        {"type": "Feature", "id": 2, "geometry": {"type": "Point", "coordinates": [-175.0, 30.0]}, "properties": {"i": 2}},
        {"type": "Feature", "id": 3, "geometry": {"type": "Point", "coordinates": [100.0, 30.0]}, "properties": {"i": 3}},
    ]
    fc = {"type": "FeatureCollection", "features": feats}
    entry = asyncio.run(create_selection(
        "sess-am", "ref:a", 1, {"kind": "bbox", "bbox": [170.0, 20.0, -170.0, 40.0]},
        feats))
    rows, last = _paginate(entry, fc)
    assert rows == [1, 2], "跨日期变更线 bbox 命中两侧、排除中段"
    assert "am_split_natural_order" in last["reason_codes"]


def test_fields_projection_applies():
    fc = _fc(5)
    entry = asyncio.run(create_selection(
        "sess-f", "ref:a", 1, {"kind": "ids", "ids": ["2"]}, fc["features"]))
    page = asyncio.run(selection_features_page(
        entry, fc, limit=10, cursor=None, fields=["name"], order=None))
    assert page["features"][0]["properties"] == {"name": "f2"}


# ─── 物化 ───────────────────────────────────────────────────────────────────

def test_materialize_creates_ref_and_discloses_coarse():
    fc = _fc(10)
    entry = asyncio.run(create_selection(
        "sess-m", "ref:a", 7, {"kind": "bbox", "bbox": [104.0, 30.0, 104.05, 31.0]},
        fc["features"]))
    stored = {}

    async def store(out_fc):
        stored["fc"] = out_fc
        return "ref:selection-xyz"

    result = asyncio.run(sh.materialize_selection(entry, fc, store_call=store))
    assert result["ref_id"] == "ref:selection-xyz"
    assert result["feature_count"] == len(stored["fc"]["features"]) == 6
    assert result["coarse_bbox"] is True
    assert result["revision"] == 7


def test_materialize_over_budget_typed_reject_no_truncate():
    fc = _fc(MAX_MATERIALIZE_FEATURES + 10)
    entry = asyncio.run(create_selection(
        "sess-m2", "ref:a", 1, {"kind": "where", "where": {"field": "i", "op": "gte", "value": 0}},
        fc["features"]))

    async def store(out_fc):  # pragma: no cover - 不应被调用
        raise AssertionError("over_budget 必须在 store 前拒绝")

    with pytest.raises(SelectionHandleError) as ei:
        asyncio.run(sh.materialize_selection(entry, fc, store_call=store))
    assert ei.value.code == "over_budget"
    assert ei.value.detail["actual"] == MAX_MATERIALIZE_FEATURES + 10
