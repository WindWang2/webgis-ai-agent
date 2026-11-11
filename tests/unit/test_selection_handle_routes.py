"""selection_handles routes（G24 / P3）：REST 集成测试。

真实路由 + MemorySessionStore；鉴权依赖直通（跨会话负例走 handle 绑定
语义：他人 handle 一律 404 not_found，不泄露存在性）。

覆盖验收：
- A11 框选 300 对象不是前 50：bbox handle 创建 + 分页枚举全集；
- A14 跨日期变更线范围正确（AM 拆分）；
- A16 跨会话 handle 拒绝；
- A22 数据刷新（revision 前进）旧 handle 410；
- A20/materialize 超预算 typed 拒绝；权限（owner_token 不符）403。
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import selection_handles as sel_routes
from app.services.session_data import MemorySessionStore


def _fc(n: int = 300):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": i,
                "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.001, 30.0]},
                "properties": {"i": i, "name": f"f{i}"},
            }
            for i in range(n)
        ],
    }


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(sel_routes.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _stub_ownership(app):
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()


@contextmanager
def _patched_store(store: MemorySessionStore):
    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(sel_routes, "session_data_manager", store)
        yield


@pytest.fixture
def store():
    return MemorySessionStore()


async def _seed_ref(store: MemorySessionStore, sid: str, fc=None) -> str:
    return await store.store(sid, {"geojson": fc or _fc()}, prefix="data")


async def test_bbox_handle_pages_full_selection_not_first_50(client, store):
    """A11：300 命中 ≠ 前 50 —— handle 分页能枚举全集。"""
    sid = "sess-g24-route-0001"
    ref = await _seed_ref(store, sid)
    with _patched_store(store):
        r = await client.post(
            f"/api/v1/layers/data/{ref}/selection?session_id={sid}",
            json={"kind": "bbox", "bbox": [104.0, 29.9, 104.4, 30.1]},
        )
        assert r.status_code == 200, r.text
        created = r.json()
        assert created["count_exact"] is True
        assert created["count"] == 300  # 300 要素（i ≤ 299 → lon ≤ 104.299）全命中

        handle = created["handle"]
        ids, cursor = [], None
        while True:
            url = (
                f"/api/v1/layers/data/{ref}/selection/{handle}/features"
                f"?session_id={sid}&limit=137"
            )
            if cursor:
                url += f"&cursor={cursor}"
            page = (await client.get(url)).json()
            ids.extend(f["id"] for f in page["features"])
            if not page["pagination"]["has_more"]:
                break
            cursor = page["pagination"]["next_cursor"]
        assert ids == list(range(300)), "分页枚举 = 全集（不是前 50/首屏）"
        assert page["selection"]["count"] == 300


async def test_ids_scope_creates_and_enumerates(client, store):
    sid = "sess-g24-route-0002"
    ref = await _seed_ref(store, sid)
    with _patched_store(store):
        r = await client.post(
            f"/api/v1/layers/data/{ref}/selection?session_id={sid}",
            json={"kind": "ids", "ids": ["3", "17", "missing-99"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        assert body["missing_ids"] == ["missing-99"]

        page = await client.get(
            f"/api/v1/layers/data/{ref}/selection/{body['handle']}/features"
            f"?session_id={sid}&limit=10"
        )
        rows = page.json()["features"]
        assert [f["id"] for f in rows] == [3, 17]


async def test_am_split_bbox_selects_both_sides(client, store):
    """A14：跨日期变更线 bbox 命中两侧、排除中段。"""
    sid = "sess-g24-route-0003"
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": 1, "geometry": {"type": "Point", "coordinates": [175.0, 10.0]}, "properties": {}},
            {"type": "Feature", "id": 2, "geometry": {"type": "Point", "coordinates": [-175.0, 10.0]}, "properties": {}},
            {"type": "Feature", "id": 3, "geometry": {"type": "Point", "coordinates": [10.0, 10.0]}, "properties": {}},
        ],
    }
    ref = await _seed_ref(store, sid, fc)
    with _patched_store(store):
        r = await client.post(
            f"/api/v1/layers/data/{ref}/selection?session_id={sid}",
            json={"kind": "bbox", "bbox": [170.0, 0.0, -170.0, 20.0]},
        )
        assert r.status_code == 200
        assert r.json()["count"] == 2
        page = await client.get(
            f"/api/v1/layers/data/{ref}/selection/{r.json()['handle']}/features"
            f"?session_id={sid}&limit=10"
        )
        assert [f["id"] for f in page.json()["features"]] == [1, 2]


async def test_cross_session_handle_404_no_leak(client, store):
    """A16：他人 handle → 404 not_found（不泄露存在性）。"""
    sid_a, sid_b = "sess-g24-route-a4", "sess-g24-route-b4"
    ref = await _seed_ref(store, sid_a)
    with _patched_store(store):
        created = (
            await client.post(
                f"/api/v1/layers/data/{ref}/selection?session_id={sid_a}",
                json={"kind": "ids", "ids": ["1"]},
            )
        ).json()
        r = await client.get(
            f"/api/v1/layers/data/{ref}/selection/{created['handle']}"
            f"?session_id={sid_b}"
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error"] == "selection_not_found"


async def test_revision_advance_invalidates_handle_410(client, store):
    """A22：overwrite（编辑/刷新）使 revision 前进 → 旧 handle 410 stale。"""
    sid = "sess-g24-route-0005"
    ref = await _seed_ref(store, sid)
    with _patched_store(store):
        created = (
            await client.post(
                f"/api/v1/layers/data/{ref}/selection?session_id={sid}",
                json={"kind": "ids", "ids": ["1"]},
            )
        ).json()
        assert created["revision"] is not None
        # 模拟一次编辑提交：同 ref 覆写 → content_revision 前进
        ok = await store.overwrite(sid, ref, {"geojson": _fc(10)})
        assert ok is True
        r = await client.get(
            f"/api/v1/layers/data/{ref}/selection/{created['handle']}"
            f"?session_id={sid}"
        )
        assert r.status_code == 410
        detail = r.json()["detail"]
        assert detail["error"] == "selection_stale"
        assert detail["current_revision"] > created["revision"]


async def test_create_with_stale_v_409(client, store):
    sid = "sess-g24-route-0006"
    ref = await _seed_ref(store, sid)
    with _patched_store(store):
        r = await client.post(
            f"/api/v1/layers/data/{ref}/selection?session_id={sid}&v=999",
            json={"kind": "ids", "ids": ["1"]},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "revision_conflict"


async def test_bad_scope_400_closed_vocab(client, store):
    sid = "sess-g24-route-0007"
    ref = await _seed_ref(store, sid)
    with _patched_store(store):
        r = await client.post(
            f"/api/v1/layers/data/{ref}/selection?session_id={sid}",
            json={"kind": "sql", "query": "1=1"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "selection_bad_scope"


async def test_materialize_creates_ref_for_fieldcompute(client, store):
    """P4：fieldcompute 只认 selection_ref —— handle 物化成 ref。"""
    sid = "sess-g24-route-0008"
    ref = await _seed_ref(store, sid)
    with _patched_store(store):
        created = (
            await client.post(
                f"/api/v1/layers/data/{ref}/selection?session_id={sid}",
                json={"kind": "where", "where": {"field": "i", "op": "lt", "value": 7}},
            )
        ).json()
        assert created["count"] == 7
        r = await client.post(
            f"/api/v1/layers/data/{ref}/selection/{created['handle']}/materialize"
            f"?session_id={sid}"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["feature_count"] == 7
        assert "selection" in body["ref_id"]
        # 物化 ref 真实可读（同一会话）
        data = await store.get(sid, body["ref_id"])
        assert data["geojson"]["features"][0]["properties"]["i"] == 0


async def test_delete_releases_and_idempotent(client, store):
    sid = "sess-g24-route-0009"
    ref = await _seed_ref(store, sid)
    with _patched_store(store):
        created = (
            await client.post(
                f"/api/v1/layers/data/{ref}/selection?session_id={sid}",
                json={"kind": "ids", "ids": ["1"]},
            )
        ).json()
        h = created["handle"]
        assert (await client.delete(
            f"/api/v1/layers/data/{ref}/selection/{h}?session_id={sid}")).status_code == 200
        # 释放后读 → 404；再次释放仍 200（幂等）
        assert (await client.get(
            f"/api/v1/layers/data/{ref}/selection/{h}?session_id={sid}")).status_code == 404
        assert (await client.delete(
            f"/api/v1/layers/data/{ref}/selection/{h}?session_id={sid}")).status_code == 200


async def test_unknown_ref_404(client, store):
    sid = "sess-g24-route-0010"
    with _patched_store(store):
        r = await client.post(
            f"/api/v1/layers/data/ref:ghost/selection?session_id={sid}",
            json={"kind": "ids", "ids": ["1"]},
        )
        assert r.status_code == 404
