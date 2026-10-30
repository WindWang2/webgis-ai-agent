"""Track B vNext：features 分页（order/where/total/reason_codes）与
aggregate 端点的 HTTP 契约。复用 tests/test_layer_features_page.py 的
mock-store 模式（内存 store + dependency override）。"""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.api.routes import layer as _mod
from app.services.auth_history_bridge import require_owned_session
from app.models.db_model import Conversation

_VALID_SID = "session-aaaaaaaaaaaaaaaa"


def _fc(n, with_ids=True):
    features = []
    for i in range(n):
        f = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [104.0 + (i % 40) * 0.1, 30.0 + (i % 7) * 0.1]},
            "properties": {
                "i": i,
                "type": ["小学", "中学", "幼儿园"][i % 3],
                "students": (i * 37) % 900,
                "名称": f"第{i}小学",
            },
        }
        if with_ids:
            f["id"] = i
        features.append(f)
    return {"type": "FeatureCollection", "features": features}


_FC = _fc(300)
_FC_NOID = _fc(120, with_ids=False)


@pytest.fixture(autouse=True)
def _clear_derived_cache():
    """feature_pages 的 (ref,revision,spec) 派生缓存在生产里以「revision 内
    载荷不可变」为前提 —— 测试 mock 会给同 ref+revision 塞不同数据，必须
    逐用例清缓存，防跨用例污染。"""
    import app.services.feature_pages as fp

    fp._derived_cache.clear()
    yield
    fp._derived_cache.clear()


@pytest.fixture
def app(monkeypatch):
    async def _noop_verify(session_id, user_id, owner_token=None):
        return None

    monkeypatch.setattr(_mod, "_verify_session_owner", _noop_verify)
    app = FastAPI()
    app.dependency_overrides[require_owned_session] = lambda: Conversation(id=_VALID_SID)
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _install_store(monkeypatch, data=_FC, revision=5):
    descriptor = {"content_revision": revision, "feature_count": len(data["features"])}

    class _Res:
        success = True
        error = None
        error_type = None

        def __init__(self, payload):
            self.data = payload

    async def _resolve_alias(session_id, ref_or_alias):
        return ref_or_alias

    async def _get_ref_descriptor(session_id, ref_id):
        return dict(descriptor) if ref_id == "ref-page" else None

    async def _get_ref_data_shared(session_id, ref_id, owner_token=None):
        if ref_id != "ref-page":
            class _Missing:
                success = False
                error = "数据不可用"
                error_type = "NotFound"

            return _Missing()
        return _Res(data)

    async def _get_ref_data(session_id, ref_id, owner_token=None):
        return await _get_ref_data_shared(session_id, ref_id, owner_token=owner_token)

    monkeypatch.setattr(_mod.session_data_manager, "resolve_alias", _resolve_alias)
    monkeypatch.setattr(_mod.session_data_manager, "get_ref_descriptor", _get_ref_descriptor)
    monkeypatch.setattr(_mod.session_data_manager, "get_ref_data_shared", _get_ref_data_shared)
    monkeypatch.setattr(_mod.session_data_manager, "get_ref_data", _get_ref_data)


# ─── features: order / where / total ────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_desc_http(client, monkeypatch):
    _install_store(monkeypatch)
    r = await client.get("/api/v1/layers/data/ref-page/features", params={
        "session_id": _VALID_SID, "limit": 5, "order": "-students",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    students = [f["properties"]["students"] for f in body["features"]]
    assert students == sorted(students, reverse=True)
    assert body["id_mode"] == "native"
    assert "sorted:students" in body["reason_codes"]
    assert body["pagination"]["is_complete"] is False
    # 无过滤 → total_matching 缺席（不假装算过）
    assert body["total_matching"] is None
    assert "total_exact" not in body["reason_codes"]


@pytest.mark.asyncio
async def test_where_total_and_reason_codes_http(client, monkeypatch):
    _install_store(monkeypatch)
    where = json.dumps({"field": "type", "op": "eq", "value": "小学"})
    r = await client.get("/api/v1/layers/data/ref-page/features", params={
        "session_id": _VALID_SID, "limit": 10, "where": where,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert all(f["properties"]["type"] == "小学" for f in body["features"])
    assert body["total_matching"] == 100  # 300 / 3
    assert "filter_applied" in body["reason_codes"]
    assert "total_exact" in body["reason_codes"]


@pytest.mark.asyncio
async def test_where_plus_bbox_total_intersection(client, monkeypatch):
    _install_store(monkeypatch)
    where = json.dumps({"field": "type", "op": "in", "values": ["小学", "幼儿园"]})
    r = await client.get("/api/v1/layers/data/ref-page/features", params={
        "session_id": _VALID_SID, "limit": 1000, "where": where,
        "bbox": "104.0,30.0,107.5,31.0",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_matching"] == sum(
        1 for f in _FC["features"]
        if f["properties"]["type"] in ("小学", "幼儿园")
        and 104.0 <= f["geometry"]["coordinates"][0] <= 107.5
    )
    assert "bbox_coarse" in body["reason_codes"]


@pytest.mark.asyncio
async def test_bad_where_and_order_400(client, monkeypatch):
    _install_store(monkeypatch)
    for params in (
        {"where": '{"field":"t","op":"DROP TABLE","value":1}'},
        {"where": "not-json"},
        {"order": "-"},
        {"order": "a" * 65},
        {"where": json.dumps({"field": "t", "op": "between", "gte": 9, "lte": 1})},
    ):
        r = await client.get("/api/v1/layers/data/ref-page/features", params={
            "session_id": _VALID_SID, **params,
        })
        assert r.status_code == 400, (params, r.text)


@pytest.mark.asyncio
async def test_v2_cursor_mismatch_400_http(client, monkeypatch):
    _install_store(monkeypatch)
    r1 = await client.get("/api/v1/layers/data/ref-page/features", params={
        "session_id": _VALID_SID, "limit": 5, "order": "-students",
    })
    cursor = r1.json()["pagination"]["next_cursor"]
    # 同序续页 OK
    r2 = await client.get("/api/v1/layers/data/ref-page/features", params={
        "session_id": _VALID_SID, "limit": 5, "order": "-students", "cursor": cursor,
    })
    assert r2.status_code == 200
    # 换向续页 → 400
    r3 = await client.get("/api/v1/layers/data/ref-page/features", params={
        "session_id": _VALID_SID, "limit": 5, "order": "students", "cursor": cursor,
    })
    assert r3.status_code == 400


@pytest.mark.asyncio
async def test_revision_guard_with_order_and_where(client, monkeypatch):
    _install_store(monkeypatch, revision=5)
    where = json.dumps({"field": "i", "op": "gte", "value": 0})
    r = await client.get("/api/v1/layers/data/ref-page/features", params={
        "session_id": _VALID_SID, "limit": 5, "where": where, "v": 4,
    })
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "revision_conflict"


@pytest.mark.asyncio
async def test_positional_id_mode_http(client, monkeypatch):
    _install_store(monkeypatch, data=_FC_NOID)
    r = await client.get("/api/v1/layers/data/ref-page/features", params={
        "session_id": _VALID_SID, "limit": 3,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id_mode"] == "positional"
    assert "positional_id_projected" in body["reason_codes"]
    assert [f["id"] for f in body["features"]] == [1, 2, 3]

    # 位置寻址单要素：命中 + 越界 + 有链内 id 的位置拒绝
    ok = await client.get("/api/v1/layers/data/ref-page/feature/2", params={
        "session_id": _VALID_SID, "id_mode": "positional",
    })
    assert ok.status_code == 200 and ok.json()["id"] == 2
    oob = await client.get("/api/v1/layers/data/ref-page/feature/9999", params={
        "session_id": _VALID_SID, "id_mode": "positional",
    })
    assert oob.status_code == 404
    bad = await client.get("/api/v1/layers/data/ref-page/feature/xyz", params={
        "session_id": _VALID_SID, "id_mode": "positional",
    })
    assert bad.status_code == 400
    weird_mode = await client.get("/api/v1/layers/data/ref-page/feature/2", params={
        "session_id": _VALID_SID, "id_mode": "native",
    })
    assert weird_mode.status_code == 400


# ─── aggregate ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aggregate_count_group(client, monkeypatch):
    _install_store(monkeypatch)
    r = await client.get("/api/v1/layers/data/ref-page/aggregate", params={
        "session_id": _VALID_SID, "group_by": "type",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    by_key = {row["key"]: row["count"] for row in body["rows"]}
    assert by_key == {"小学": 100, "中学": 100, "幼儿园": 100}
    assert body["is_complete"] is True and body["truncated"] is False
    assert body["total_matching"] == 300
    assert body["reason_codes"] == ["aggregate_full_scan"]


@pytest.mark.asyncio
async def test_aggregate_sum_mean_with_non_numeric_disclosure(client, monkeypatch):
    features = []
    for i in range(10):
        props = {"type": "a" if i < 6 else "b", "students": i if i != 5 else "N/A"}
        features.append({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
            "properties": props,
        })
    _install_store(monkeypatch, data={"type": "FeatureCollection", "features": features})
    r = await client.get("/api/v1/layers/data/ref-page/aggregate", params={
        "session_id": _VALID_SID, "group_by": "type", "agg": "mean", "agg_field": "students",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {row["key"]: row for row in body["rows"]}
    # a 组：0,1,2,3,4 数值 + 1 个 "N/A"（非数值，不计入均值分母）
    assert rows["a"]["count"] == 6
    assert rows["a"]["mean"] == pytest.approx(10 / 5)
    assert body["non_numeric_count"] == 1


@pytest.mark.asyncio
async def test_aggregate_null_keys_and_other_group(client, monkeypatch):
    features = []
    for i in range(30):
        props = {"district": None if i < 4 else f"区{i % 25}"}
        features.append({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
            "properties": props,
        })
    _install_store(monkeypatch, data={"type": "FeatureCollection", "features": features})
    r = await client.get("/api/v1/layers/data/ref-page/aggregate", params={
        "session_id": _VALID_SID, "group_by": "district", "max_groups": 5,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["null_key_count"] == 4
    assert body["truncated"] is True and body["is_complete"] is False
    keys = [row["key"] for row in body["rows"]]
    assert "__other__" in keys and None in keys
    assert sum(row["count"] for row in body["rows"]) == 30
    assert "groups_truncated" in body["reason_codes"]


@pytest.mark.asyncio
async def test_aggregate_where_and_bbox_parity_with_features(client, monkeypatch):
    _install_store(monkeypatch)
    where = json.dumps({"field": "type", "op": "eq", "value": "小学"})
    bbox = "104.0,30.0,106.0,31.0"
    agg = await client.get("/api/v1/layers/data/ref-page/aggregate", params={
        "session_id": _VALID_SID, "group_by": "type", "where": where, "bbox": bbox,
    })
    assert agg.status_code == 200
    total_via_agg = agg.json()["total_matching"]
    # 分页通道同 (where,bbox) 全量翻页 → 两条通道匹配集恒等
    matched = 0
    cursor = None
    while True:
        params = {"session_id": _VALID_SID, "limit": 1000, "where": where, "bbox": bbox}
        if cursor:
            params["cursor"] = cursor
        page = await client.get("/api/v1/layers/data/ref-page/features", params=params)
        assert page.status_code == 200, page.text
        body = page.json()
        matched += len(body["features"])
        cursor = body["pagination"]["next_cursor"]
        if cursor is None:
            break
    assert matched == total_via_agg
    assert page.json()["total_matching"] == total_via_agg


@pytest.mark.asyncio
async def test_aggregate_validation_and_not_found(client, monkeypatch):
    _install_store(monkeypatch)
    base = {"session_id": _VALID_SID}
    r = await client.get("/api/v1/layers/data/ref-page/aggregate",
                         params={**base, "group_by": "type", "agg": "sum"})
    assert r.status_code == 400  # sum 需要 agg_field
    r = await client.get("/api/v1/layers/data/ref-page/aggregate",
                         params={**base, "group_by": "type", "agg": "median"})
    assert r.status_code == 400
    r = await client.get("/api/v1/layers/data/ref-page/aggregate",
                         params={**base, "group_by": "type", "max_groups": 999})
    assert r.status_code == 422  # Query 校验（le=256）
    r = await client.get("/api/v1/layers/data/ref-other/aggregate",
                         params={**base, "group_by": "type"})
    assert r.status_code == 404
    r = await client.get("/api/v1/layers/data/ref-page/aggregate",
                         params={**base, "group_by": "type", "v": 1})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_aggregate_over_limit_422(client, monkeypatch):
    import app.services.feature_aggregate as fa

    big = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
         "properties": {"t": "x"}} for _ in range(5)
    ]}
    _install_store(monkeypatch, data=big)
    orig = fa.AGG_SCAN_LIMIT
    fa.AGG_SCAN_LIMIT = 4
    try:
        r = await client.get("/api/v1/layers/data/ref-page/aggregate", params={
            "session_id": _VALID_SID, "group_by": "t",
        })
        assert r.status_code == 422
        assert r.json()["detail"]["error"] == "aggregate_over_limit"
    finally:
        fa.AGG_SCAN_LIMIT = orig


# ─── R1/R2 评审清偿：聚合撞名/null 组/位置 revision guard ──────────────────


@pytest.mark.asyncio
async def test_aggregate_overflow_key_disambiguation(client, monkeypatch):
    features = []
    for i in range(12):
        props = {"g": "__other__" if i < 6 else f"k{i}"}
        features.append({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
            "properties": props,
        })
    _install_store(monkeypatch, data={"type": "FeatureCollection", "features": features})
    r = await client.get("/api/v1/layers/data/ref-page/aggregate", params={
        "session_id": _VALID_SID, "group_by": "g", "max_groups": 3,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    keys = [row["key"] for row in body["rows"]]
    # 真实 __other__ 组保留原名；溢出行改用递进哨兵且 is_overflow=true
    assert keys.count("__other__") == 1
    overflow_rows = [row for row in body["rows"] if row.get("is_overflow")]
    assert len(overflow_rows) == 1
    assert overflow_rows[0]["key"] != "__other__"
    assert sum(row["count"] for row in body["rows"]) == 12


@pytest.mark.asyncio
async def test_aggregate_null_group_always_disclosed(client, monkeypatch):
    features = []
    for i in range(4):
        features.append({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
            "properties": {"g": f"k{i}"},
        })
    for i in range(3):  # null 键组（count=3 < 名额竞争线）
        features.append({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
            "properties": {"g": None},
        })
    _install_store(monkeypatch, data={"type": "FeatureCollection", "features": features})
    r = await client.get("/api/v1/layers/data/ref-page/aggregate", params={
        "session_id": _VALID_SID, "group_by": "g", "max_groups": 2,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    keys = [row["key"] for row in body["rows"]]
    assert None in keys, "null 键组必须恒单独披露（不并入 __other__）"
    assert body["null_key_count"] == 3
    assert sum(row["count"] for row in body["rows"]) == 7


@pytest.mark.asyncio
async def test_positional_feature_revision_guard(client, monkeypatch):
    _install_store(monkeypatch, data=_FC_NOID)
    ok = await client.get("/api/v1/layers/data/ref-page/feature/2", params={
        "session_id": _VALID_SID, "id_mode": "positional", "v": 5,
    })
    assert ok.status_code == 200, ok.text
    assert ok.headers.get("x-content-revision") == "5"
    stale = await client.get("/api/v1/layers/data/ref-page/feature/2", params={
        "session_id": _VALID_SID, "id_mode": "positional", "v": 4,
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"] == "revision_conflict"
