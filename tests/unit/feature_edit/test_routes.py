"""Feature-edit REST 集成测试（M3）：预览→决策→撤销/重做→历史→策略。

真实路由 + MemorySessionStore + 真实 mapspec engine 盖章链（数据面 store
打补丁替换；mapspec 面走全局内存 manager —— 与生产同一条 apply_gis_mutation
路径）。鉴权依赖直通（跨租户负例在专项 fixture 中用真实验证器覆盖）。
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import feature_edits as fe_routes
from app.services.feature_edit import executor as fe_executor
from app.services.feature_edit import policy as fe_policy
from app.services.session_data import MemorySessionStore

SCHOOLS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": f"sch-{i}",
            "geometry": {"type": "Point", "coordinates": [116.0 + 0.01 * i, 39.0]},
            "properties": {"name": f"学校{i}", "type": "小学" if i % 2 == 0 else "中学", "capacity": 500 + i},
        }
        for i in range(5)
    ],
}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(fe_routes.router, prefix="/api/v1")
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


@pytest.fixture
def store():
    return MemorySessionStore()


@contextmanager
def _patched_store(store):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fe_routes, "session_data_manager", store)
        mp.setattr(fe_executor, "session_data_manager", store)
        mp.setattr(fe_policy, "session_data_manager", store)
        yield


async def _seed_ref(store, session_id="sess-fe1", payload=None) -> str:
    return await store.store(session_id, {"geojson": payload or SCHOOLS}, prefix="data")


def _url(session_id, suffix="") -> str:
    return f"/api/v1/chat/sessions/{quote(session_id)}/feature-edits{suffix}"


async def _preview(client, session_id, body) -> dict:
    resp = await client.post(_url(session_id, "/preview"), json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _approve(client, session_id, plan_id, key=None) -> object:
    headers = {"Idempotency-Key": key} if key else {}
    return await client.post(
        _url(session_id, f"/plans/{plan_id}/decision"),
        json={"action": "approve"},
        headers=headers,
    )


# ─── happy path ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preview_approve_commit_and_isolation(client, store):
    with _patched_store(store):
        sid = "sess-fe1"
        ref = await _seed_ref(store, sid)
        plan = await _preview(
            client,
            sid,
            {
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-1", "sch-3"]},
                "ops": [{"op": "set_fields", "fields": {"type": "小学"}}],
            },
        )
        assert plan["committable"] and plan["safe"] and plan["n_targets"] == 2
        diffs = {p["wfid"]: p for p in plan["preview"]}
        assert all(p["fields"]["type"]["before"] == "中学" for p in diffs.values())

        resp = await _approve(client, sid, plan["plan_id"], key="idem-1")
        assert resp.status_code == 200, resp.text
        receipt = resp.json()["receipt"]
        assert receipt["kind"] == "apply" and len(receipt["changed_wfids"]) == 2
        assert receipt["stamp_status"] in ("stamped", "skipped")

        # 载荷已改：只有目标要素变化，其他要素指纹不变
        payload = await store.get(sid, ref)
        feats = payload["geojson"]["features"]
        changed = [f for f in feats if f["properties"]["type"] == "小学"]
        assert len(changed) == 5  # 原 3 小学（0/2/4）+ 新改 2（1/3）

        # 未选中要素除物化注入的 _wfid 外逐字不变
        def _strip(f):
            props = {k: v for k, v in f["properties"].items() if k != "_wfid"}
            return {**f, "properties": props}

        assert _strip(feats[0]) == SCHOOLS["features"][0]
        assert _strip(feats[2]) == SCHOOLS["features"][2]
        # revision 已 bump
        desc = await store.get_ref_descriptor(sid, ref)
        assert desc["content_revision"] == receipt["content_revision_after"] >= 2
        # journal 落账
        hist = await client.get(_url(sid, f"/{quote(ref)}/history"))
        assert hist.status_code == 200
        body = hist.json()
        assert body["head_seq"] == 1 and body["entries"][0]["kind"] == "apply"
        assert body["entries"][0]["actor"] == "user"

        # 同 Idempotency-Key 重放：不写第二版
        resp2 = await _approve(client, sid, plan["plan_id"], key="idem-1")
        assert resp2.status_code == 200
        assert resp2.json()["receipt"]["entry_seq"] == receipt["entry_seq"]
        desc2 = await store.get_ref_descriptor(sid, ref)
        assert desc2["content_revision"] == desc["content_revision"]


@pytest.mark.asyncio
async def test_materialization_on_first_commit(client, store):
    with _patched_store(store):
        sid = "sess-fe2"
        ref = await _seed_ref(store, sid)
        assert all("_wfid" not in f["properties"] for f in SCHOOLS["features"])
        plan = await _preview(
            client, sid,
            {
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-0"]},
                "ops": [{"op": "set_fields", "fields": {"capacity": 999}}],
            },
        )
        assert plan["committable"]
        resp = await _approve(client, sid, plan["plan_id"])
        assert resp.status_code == 200
        payload = await store.get(sid, ref)
        feats = payload["geojson"]["features"]
        # 物化：所有要素都有确定性 _wfid
        assert all(isinstance(f["properties"].get("_wfid"), str) for f in feats)
        assert feats[0]["properties"]["capacity"] == 999
        assert feats[1]["properties"]["capacity"] == 501  # 未动


# ─── 冲突 / 幂等 / 越权 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_revision_conflict(client, store):
    with _patched_store(store):
        sid = "sess-fe3"
        ref = await _seed_ref(store, sid)
        plan = await _preview(
            client, sid,
            {
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-0"]},
                "ops": [{"op": "set_fields", "fields": {"type": "中学"}}],
            },
        )
        # 他人并发改写（同会话另一路径）
        other = {"geojson": {**SCHOOLS, "features": [dict(f) for f in SCHOOLS["features"]]}}
        other["geojson"]["features"][0]["properties"]["capacity"] = 1
        await store.overwrite(sid, ref, other)
        resp = await _approve(client, sid, plan["plan_id"])
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "stale_revision"
        assert detail["current_content_revision"] >= plan["expected_content_revision"] + 1
        # plan 已标 conflicted；再 approve 仍 409（状态机拒绝）
        resp2 = await _approve(client, sid, plan["plan_id"])
        assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_cross_session_ref_not_found_no_leak(client, store):
    with _patched_store(store):
        # ref 只存在于 sess-A；sess-B 的请求不得得知 ref 是否存在
        ref = await _seed_ref(store, "sess-A")
        resp = await client.post(
            _url("sess-B", "/preview"),
            json={
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-0"]},
                "ops": [{"op": "set_fields", "fields": {"type": "中学"}}],
            },
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
        # undo 对不存在 ref 同样 404
        resp2 = await client.post(_url("sess-B", f"/{quote(ref)}/undo"))
        assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_enum_violation_rejected_at_preview(client, store):
    with _patched_store(store):
        sid = "sess-fe4"
        ref = await _seed_ref(store, sid)
        resp = await client.post(
            _url(sid, "/preview"),
            json={
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-0"]},
                "ops": [{"op": "set_fields", "fields": {"type": "幼儿园"}}],
            },
        )
        assert resp.status_code == 200  # 预览成功返回，但 committable=False
        body = resp.json()
        assert body["committable"] is False
        assert body["validation"][0]["code"] == "enum_violation"
        assert "plan_id" not in body or body.get("status") == "invalid"
        # 无 plan_id → 无可决策对象（恶意直发 decision 404）
        resp2 = await client.post(
            _url(sid, "/plans/fep-notexist/decision"), json={"action": "approve"}
        )
        assert resp2.status_code == 404


# ─── undo / redo ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_undo_redo_roundtrip_via_cas_path(client, store):
    with _patched_store(store):
        sid = "sess-fe5"
        ref = await _seed_ref(store, sid)
        plan = await _preview(
            client, sid,
            {
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-1"]},
                "ops": [{"op": "set_fields", "fields": {"type": "小学"}}],
            },
        )
        await _approve(client, sid, plan["plan_id"])
        rev_after_commit = (await store.get_ref_descriptor(sid, ref))["content_revision"]

        resp = await client.post(_url(sid, f"/{quote(ref)}/undo"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["receipt"]["kind"] == "undo"
        payload_undone = await store.get(sid, ref)
        assert (
            payload_undone["geojson"]["features"][1]["properties"]["type"] == "中学"
        )
        # undo 是新版本而非魔法回滚：revision 单调递增
        d1 = await store.get_ref_descriptor(sid, ref)
        assert d1["content_revision"] > rev_after_commit

        resp2 = await client.post(_url(sid, f"/{quote(ref)}/redo"))
        assert resp2.status_code == 200
        payload_redone = await store.get(sid, ref)
        assert payload_redone["geojson"]["features"][1]["properties"]["type"] == "小学"

        # 再 undo 一次 → 撤销 redo（head=redo 可撤销）
        resp3 = await client.post(_url(sid, f"/{quote(ref)}/undo"))
        assert resp3.status_code == 200
        payload2 = await store.get(sid, ref)
        assert payload2["geojson"]["features"][1]["properties"]["type"] == "中学"

        # 连续 undo 到链底 → undo_unavailable（409 面）
        while True:
            r = await client.post(_url(sid, f"/{quote(ref)}/undo"))
            if r.status_code != 200:
                assert r.status_code == 409
                assert r.json()["detail"]["code"] == "undo_unavailable"
                break


@pytest.mark.asyncio
async def test_geometry_commit_requires_user_card(client, store):
    with _patched_store(store):
        sid = "sess-fe6"
        ref = await _seed_ref(store, sid)
        plan = await _preview(
            client, sid,
            {
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-0"]},
                "ops": [{"op": "move_by", "dx_m": 50.0, "dy_m": 0.0}],
            },
        )
        assert plan["committable"] and plan["safe"] is False
        # 用户卡路径允许几何提交（显式 approve）
        resp = await _approve(client, sid, plan["plan_id"])
        assert resp.status_code == 200
        payload = await store.get(sid, ref)
        x = payload["geojson"]["features"][0]["geometry"]["coordinates"][0]
        assert x > 116.0  # 东移生效


@pytest.mark.asyncio
async def test_move_without_units_rejected(client, store):
    with _patched_store(store):
        sid = "sess-fe7"
        ref = await _seed_ref(store, sid)
        resp = await client.post(
            _url(sid, "/preview"),
            json={
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-0"]},
                "ops": [{"op": "move_by", "dx_m": "很近", "dy_m": None}],
            },
        )
        assert resp.status_code == 422  # pydantic 拒绝非数值
        resp2 = await client.post(
            _url(sid, "/preview"),
            json={
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-0"]},
                "ops": [{"op": "move_by", "dy_m": 10.0}],  # 缺 dx_m
            },
        )
        assert resp2.status_code == 200
        body = resp2.json()
        assert body["committable"] is False
        assert any(f["code"] == "unit_required" for f in body["validation"])


# ─── selection 模式（IR ledger）+ 策略 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_selection_mode_targets_from_ir_ledger(client, store):
    with _patched_store(store):
        sid = "sess-fe8"
        ref = await _seed_ref(store, sid)
        # 种入 IR ledger：选中 sch-2 + selected_ids [sch-2, sch-4]
        await store.set_map_state_fields(
            sid,
            {
                "_interaction_ir_ledger": [
                    {
                        "kind": "feature_selected",
                        "client_instance_id": "tab-1",
                        "client_seq": 1,
                        "server_seq": 1,
                        "payload": {
                            "layer_id": "schools",
                            "feature_id": "sch-2",
                            "field_names": ["type"],
                            "dataset_ref": ref,
                        },
                    }
                ],
                "_interaction_ir_meta": {"server_seq": 1},
            },
        )
        body = await _preview(
            client, sid,
            {
                "target": {"mode": "selection"},
                "ops": [{"op": "set_fields", "fields": {"type": "小学"}}],
            },
        )
        assert body["committable"] and body["n_targets"] == 1  # ledger 无 selected_ids → 单要素


@pytest.mark.asyncio
async def test_policy_get_put_and_default(client, store):
    with _patched_store(store):
        sid = "sess-fe9"
        resp = await client.get(_url(sid, "/policy"))
        assert resp.json() == {"mode": "confirm_all"}
        resp2 = await client.put(_url(sid, "/policy"), json={"mode": "allow_safe_attribute"})
        assert resp2.json() == {"mode": "allow_safe_attribute"}
        resp3 = await client.put(_url(sid, "/policy"), json={"mode": "auto_everything"})
        assert resp3.status_code == 422  # 词表外拒绝


# ─── MapSpec 盖章（真实 engine 路径，全局内存 manager） ─────────────────────

@pytest.mark.asyncio
async def test_mapspec_stamp_refreshes_source_revision(client, store):
    with _patched_store(store):
        sid = "sess-fe10"
        ref = await _seed_ref(store, sid)
        # 在全局 manager（engine 数据面）挂一个引用该 ref 的 mapspec 源
        from app.services.mapspec_store import mapspec_store

        await mapspec_store.layer_upsert(
            sid,
            layer={"id": "schools-layer", "type": "circle", "source": "src-schools"},
            source_data={"type": "geojson", "ref_id": ref, "profile": {}, "profile_fingerprint": "p", "data_fingerprint": "d"},
            origin="agent",
            actor="test",
        )
        plan = await _preview(
            client, sid,
            {
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-0"]},
                "ops": [{"op": "set_fields", "fields": {"capacity": 42}}],
            },
        )
        resp = await _approve(client, sid, plan["plan_id"])
        assert resp.status_code == 200
        receipt = resp.json()["receipt"]
        # 盖章真实发生（REST 路径无 turn 锁 → inline）
        assert receipt["stamp_status"] == "stamped", receipt
        spec = await mapspec_store.get_mapspec(sid)
        assert isinstance(spec, dict)
        sources = spec.get("sources", {})
        stamped = [s for s in sources.values() if s.get("ref_id") == ref]
        assert stamped and stamped[0]["content_revision"] == receipt["content_revision_after"]


@pytest.mark.asyncio
async def test_plan_ttl_expiry(client, store, monkeypatch):
    with _patched_store(store):
        sid = "sess-fe11"
        ref = await _seed_ref(store, sid)
        plan = await _preview(
            client, sid,
            {
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-0"]},
                "ops": [{"op": "set_fields", "fields": {"type": "中学"}}],
            },
        )
        import app.api.routes.feature_edits as fe_mod

        real_plan_expired = fe_mod.plan_expired
        monkeypatch.setattr(
            fe_mod, "plan_expired", lambda p, now=None: real_plan_expired(p, now=(p.get("created_at") or 0) + 10_000)
        )
        resp = await _approve(client, sid, plan["plan_id"])
        assert resp.status_code == 410
