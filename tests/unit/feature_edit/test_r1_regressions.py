"""R1 评审清偿回归钉（P1-1/P1-2/P2-4/P2-5/P2-10/P3-8/P3-10）。"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import feature_edits as fe_routes
from app.services.feature_edit import executor as fe_executor
from app.services.feature_edit import policy as fe_policy
from app.services.feature_edit import service as fe_service
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
ROADS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": "road-1",
            "geometry": {"type": "LineString", "coordinates": [[115.99, 39.0005], [116.03, 39.0005]]},
            "properties": {"kind": "main"},
        }
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
def _auth(app):
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()


@contextmanager
def _patched_store(store):
    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        for mod in (fe_routes, fe_executor, fe_policy, fe_service):
            mp.setattr(mod, "session_data_manager", store)
        yield


@pytest.fixture
def store():
    return MemorySessionStore()


def _url(session_id, suffix="") -> str:
    from urllib.parse import quote

    return f"/api/v1/chat/sessions/{quote(session_id)}/feature-edits{suffix}"


@pytest.mark.asyncio
async def test_p1_1_rest_snap_preview_uses_ref_id_field(client, store):
    """R1 P1-1：REST 面 snap 预览字段名 ref_id（曾用 target_ref 恒被拒）。"""
    with _patched_store(store):
        sid = "sess-r1a"
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        roads_ref = await store.store(sid, {"geojson": ROADS}, prefix="data")
        resp = await client.post(
            _url(sid, "/preview"),
            json={
                "target": {"mode": "explicit", "ref_id": ref, "feature_ids": ["sch-0"]},
                "ops": [{"op": "snap_to_ref", "ref_id": roads_ref, "tolerance_m": 100.0}],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["committable"] is True, body.get("validation")
        assert body["preview"] and body["preview"][0]["geometry"]


@pytest.mark.asyncio
async def test_p1_2_null_assignment_undo_roundtrip(store):
    """R1 P1-2：null 赋值（原值 null/缺失）→ 提交 → undo 指纹精确还原。"""
    with _patched_store(store):
        sid = "sess-r1b"
        payload = {
            "geojson": {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "id": "a", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                     "properties": {"v": None}},           # 显式 null
                    {"type": "Feature", "id": "b", "geometry": {"type": "Point", "coordinates": [116.1, 39.0]},
                     "properties": {}},                    # 缺失键
                ],
            }
        }
        ref = await store.store(sid, payload, prefix="data")
        plan, findings = await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=["a", "b"],
            ops=[{"op": "set_fields", "fields": {"v": None}}],
        )
        assert not findings, findings
        receipt = await fe_executor.commit_plan(sid, plan, actor="user", origin="user")
        assert receipt["changed_wfids"]

        # undo 不再 fingerprint_mismatch（presence 显式还原缺失/null 态）
        undo_receipt = await fe_executor.apply_undo(sid, ref, actor="user", origin="user")
        assert undo_receipt["kind"] == "undo"
        restored = await store.get(sid, ref)
        feats = {f["id"]: f for f in restored["geojson"]["features"]}
        assert feats["a"]["properties"]["v"] is None   # null 还原为 null
        assert "v" not in feats["b"]["properties"]     # 缺失还原为缺失


@pytest.mark.asyncio
async def test_p2_4_rename_never_safe(store):
    """R1 P2-4：rename_field 恒不 safe（数据集级 schema 变更需用户确认）。"""
    with _patched_store(store):
        sid = "sess-r1c"
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        plan, findings = await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=["sch-0"],
            ops=[{"op": "rename_field", "from": "type", "to": "类别"}],
        )
        assert not findings and plan["committable"]
        assert plan["safe"] is False
        # schema_ops 进 plan 摘要（披露面）
        assert plan["schema_ops"] == [{"op": "rename_field", "from": "type", "to": "类别"}]


@pytest.mark.asyncio
async def test_p2_5_rename_then_set_new_field_sequential(store):
    """R1 P2-5：rename 后按新字段名 set —— 顺序语义合法（profile 随视图重算）。"""
    with _patched_store(store):
        sid = "sess-r1d"
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        plan, findings = await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=["sch-0"],
            ops=[
                {"op": "rename_field", "from": "type", "to": "类别"},
                {"op": "set_fields", "fields": {"类别": "中学"}},
            ],
        )
        assert not findings, findings
        assert plan["committable"]
        # rename 后对旧名 set 会 fail-closed（unknown_field）——静默重建旧列不再可能
        plan2, findings2 = await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=["sch-0"],
            ops=[
                {"op": "rename_field", "from": "type", "to": "类别"},
                {"op": "set_fields", "fields": {"type": "中学"}},
            ],
        )
        assert any(f["code"] == "unknown_field" for f in findings2)
        assert not plan2["committable"]


@pytest.mark.asyncio
async def test_p2_10_custom_idempotency_key_namespaced_per_plan(store):
    """R1 P2-10：同自定义 key 跨 plan 不得重放他人 receipt。"""
    with _patched_store(store):
        sid = "sess-r1e"
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        plan_a = (await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=["sch-1"],
            ops=[{"op": "set_fields", "fields": {"type": "小学"}}],
        ))[0]
        receipt_a = await fe_executor.commit_plan(
            sid, plan_a, actor="user", origin="user", idempotency_key="shared-key"
        )
        plan_b = (await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=["sch-3"],
            ops=[{"op": "set_fields", "fields": {"capacity": 1}}],
        ))[0]
        # 先制造竞争：b 预览后他人改写 → b 的 approve 409；key 复用不得误吞 a 的账
        import copy

        other = copy.deepcopy(await store.get(sid, ref))
        other["geojson"]["features"][4]["properties"]["capacity"] = 0
        await store.overwrite(sid, ref, other)
        with pytest.raises(fe_executor.FeatureEditConflict):
            await fe_executor.commit_plan(
                sid, plan_b, actor="user", origin="user", idempotency_key="shared-key"
            )
        # 新 plan（同 key）在新鲜 revision 上正常执行自己的账
        plan_c = (await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=["sch-2"],
            ops=[{"op": "set_fields", "fields": {"capacity": 2}}],
        ))[0]
        receipt_c = await fe_executor.commit_plan(
            sid, plan_c, actor="user", origin="user", idempotency_key="shared-key"
        )
        assert receipt_c["entry_seq"] != receipt_a["entry_seq"]


@pytest.mark.asyncio
async def test_p3_8_noop_commit_short_circuits(store):
    """R1 P3-8：同值 set 不入账、不 bump revision、plan 销账 applied。"""
    with _patched_store(store):
        sid = "sess-r1f"
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        # 先物化（首笔编辑会写 _wfid —— 这是合法写入，不属 no-op）
        plan_m, findings_m = await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=["sch-1"],
            ops=[{"op": "set_fields", "fields": {"type": "小学"}}],
        )
        await fe_executor.commit_plan(sid, plan_m, actor="user", origin="user")
        rev_before = (await store.get_ref_descriptor(sid, ref))["content_revision"]
        journal_before = await fe_executor._load_journals(sid)

        # 同值 set（sch-0 原本就是小学，且已物化）→ no-op 短路
        plan, findings = await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=["sch-0"],
            ops=[{"op": "set_fields", "fields": {"type": "小学"}}],
        )
        assert not findings
        receipt = await fe_executor.commit_plan(sid, plan, actor="user", origin="user")
        assert receipt["stamp_status"] == "skipped_no_changes"
        assert receipt["content_revision_after"] == rev_before
        assert await fe_executor._load_journals(sid) == journal_before  # 无新账
        stored = await fe_executor.get_plan(sid, plan["plan_id"])
        assert stored["status"] == "applied"


@pytest.mark.asyncio
async def test_p3_10_map_state_journals_survive_mapspec_mutation(store):
    """R1 P3-10：engine mutation 不剥离未知 `_feature_edit_*` map_state 键
    （无整表 map_state 替换路径 —— set_map_state 系 per-key API）。"""
    with _patched_store(store):
        sid = "sess-r1g"
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        plan, findings = await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=["sch-1"],
            ops=[{"op": "set_fields", "fields": {"type": "小学"}}],
        )
        await fe_executor.commit_plan(sid, plan, actor="user", origin="user")
        journals_before = await fe_executor._load_journals(sid)
        assert ref in journals_before
        # 走一笔真实 engine mutation（盖章本身）后 journal 键仍在
        status, _ = await fe_executor.stamp_data_revision(
            sid, ref, 99, defer_if_turn_locked=False
        )
        assert status in ("stamped", "skipped")
        journals_after = await fe_executor._load_journals(sid)
        assert journals_after == journals_before


@pytest.mark.asyncio
async def test_geometry_target_budget(store):
    """R1 P2-2：几何 plan >10 目标 fail-closed（journal undo 保真预算）。"""
    with _patched_store(store):
        sid = "sess-r1h"
        big = {
            "geojson": {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "id": f"p-{i}", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                     "properties": {"v": i}} for i in range(15)
                ],
            }
        }
        ref = await store.store(sid, big, prefix="data")
        plan, findings = await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=[f"p-{i}" for i in range(15)],
            ops=[{"op": "move_by", "dx_m": 5.0, "dy_m": 0.0}],
        )
        assert any(f["code"] == "target_limit_exceeded" for f in findings)
        assert not plan.get("committable", False)
        # ≤10 目标放行
        plan2, findings2 = await fe_service.build_and_store_plan(
            sid, ref_id=ref, feature_ids=[f"p-{i}" for i in range(10)],
            ops=[{"op": "move_by", "dx_m": 5.0, "dy_m": 0.0}],
        )
        assert not findings2 and plan2["committable"]


def test_non_dict_op_does_not_crash_service():
    """R1 P2-6：垃圾 op（非 dict）不崩预取段（fail-closed 由形状校验出 finding）。"""
    import asyncio

    async def run():
        st = MemorySessionStore()

        async def _inner():
            with _patched_store(st):
                sid = "sess-r1i"
                ref = await st.store(sid, {"geojson": SCHOOLS}, prefix="data")
                plan, findings = await fe_service.build_and_store_plan(
                    sid, ref_id=ref, feature_ids=["sch-0"],
                    ops=["garbage-string"],
                )
                return plan, findings

        return await _inner()

    plan, findings = asyncio.run(run())
    assert findings and findings[0]["code"] == "invalid_op"
    assert plan.get("committable") is False
