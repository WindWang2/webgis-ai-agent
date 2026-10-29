"""E2E journey（/goal 03 验收场景）：真实摄入管线 → IR 选中 → 工具预览 →
用户卡批准 → 数据/地图版本更新 → 隔离不变量 → undo → 历史。

模拟标注：会话/鉴权面用依赖直通（跨租户负例已有专项）；地图渲染消费以
mapspec 盖章 + content_revision 管线断言代替真实浏览器（Playwright 环境注
记见 EVIDENCE.md —— 本机无浏览器依赖时的诚实降级）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock

from app.api.routes import feature_edits as fe_routes
from app.services.data_ingest.pipeline import get_ingest_pipeline
from app.services.feature_edit import executor as fe_executor
from app.services.session_data import session_data_manager
from app.tools.feature_edit_tools import register_feature_edit_tools
from app.tools.registry import ToolRegistry

# 用户场景数据：5 所学校（3 小学 / 2 中学），几何在道路 ref 附近
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
            "geometry": {"type": "LineString", "coordinates": [[115.99, 39.0005], [116.03, 39.0005]],},
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


@pytest.fixture
def store():
    """真实全局 manager（摄入管线/executor/mutation engine 同一真相面；
    以唯一 sid 隔离 —— 与生产路径完全一致，无打桩）。"""
    return session_data_manager


@pytest.mark.asyncio
async def test_school_type_edit_journey(client, store):
    sid = "sess-e2e"

    # ① 真实摄入管线：学校 + 道路 → 会话 ref（画像/质量/登记全跑）
    schools_result = await get_ingest_pipeline().ingest(sid, SCHOOLS, name="schools")
    assert schools_result.ok, schools_result.error
    ref = schools_result.ref_id
    roads_result = await get_ingest_pipeline().ingest(sid, ROADS, name="roads")
    assert roads_result.ok
    roads_ref = roads_result.ref_id

    # 地图挂层（mapspec 源引用 ref —— 盖章对象）
    from app.services.mapspec_store import mapspec_store

    await mapspec_store.layer_upsert(
        sid,
        layer={"id": "schools", "type": "circle", "source": "src-schools"},
        source_data={"type": "geojson", "ref_id": ref, "profile": {}, "profile_fingerprint": "p", "data_fingerprint": "d"},
        origin="agent", actor="e2e",
    )

    # ② 用户框选 3 所学校（IR ledger：brush 上行 → selected_ids）
    await store.set_map_state_fields(
        sid,
        {
            "_interaction_ir_ledger": [
                {
                    "kind": "feature_selected",
                    "client_instance_id": "tab-e2e",
                    "client_seq": 1,
                    "server_seq": 1,
                    "payload": {
                        "layer_id": "schools",
                        "feature_id": "sch-1",
                        "field_names": ["type"],
                        "dataset_ref": ref,
                        "selected_ids": ["sch-0", "sch-1", "sch-2"],
                    },
                }
            ],
            "_interaction_ir_meta": {"server_seq": 1},
        },
    )

    # ③ Agent：NL → 工具预览（selection 模式；类型改小学 + 坐标吸附到路）
    reg = ToolRegistry()
    register_feature_edit_tools(reg)
    preview = await reg.dispatch(
        "webgis_feature_edit_preview",
        {
            "ops": [
                {"op": "set_fields", "fields": {"type": "小学"}},
                {"op": "snap_to_ref", "ref_id": roads_ref, "tolerance_m": 100.0},
            ],
        },
        session_id=sid,
    )
    assert preview["success"] is True, preview
    assert preview["n_targets"] == 3
    assert preview["safe"] is False  # 含坐标变更 → 必须用户确认
    plan_id = preview["plan_id"]

    # ④ Agent 尝试直接提交（confirm_all 默认策略）→ typed 拒绝
    blocked = await reg.dispatch("webgis_feature_edit_commit", {"plan_id": plan_id}, session_id=sid)
    assert blocked["success"] is False and blocked["code"] == "CONFIRMATION_REQUIRED"
    journals = await fe_executor._load_journals(sid)
    assert journals == {}  # 数据未动

    # ⑤ 用户在提案卡 approve（REST owner 通道）
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/feature-edits/plans/{plan_id}/decision",
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    receipt = resp.json()["receipt"]
    assert receipt["stamp_status"] == "stamped"
    changed = set(receipt["changed_wfids"])
    assert len(changed) == 3

    # ⑥ 不变量：未选中要素（sch-3/sch-4）除 _wfid 外逐字不变
    payload = await store.get(sid, ref)
    feats = payload["geojson"]["features"]

    def _strip(f):
        props = {k: v for k, v in f["properties"].items() if k != "_wfid"}
        return {**f, "properties": props}

    by_id = {f["id"]: f for f in feats}
    assert _strip(by_id["sch-3"]) == SCHOOLS["features"][3]
    assert _strip(by_id["sch-4"]) == SCHOOLS["features"][4]
    # 选中要素：类型已改（原 中学 sch-1 → 小学）+ 坐标吸附到路边
    for i in (0, 1, 2):
        assert by_id[f"sch-{i}"]["properties"]["type"] == "小学"
    assert abs(by_id["sch-0"]["geometry"]["coordinates"][1] - 39.0005) < 1e-6

    # ⑦ 地图与业务数据版本联动：mapspec 源 content_revision 盖章 = 数据新版本
    spec = await mapspec_store.get_mapspec(sid)
    stamped = [s for s in spec["sources"].values() if s.get("ref_id") == ref]
    assert stamped[0]["content_revision"] == receipt["content_revision_after"]
    desc = await store.get_ref_descriptor(sid, ref)
    assert desc["content_revision"] == receipt["content_revision_after"]

    # ⑧ Agent 下一 turn 可观察（situation feature_edits 投影）
    from app.services.gis_situation.compiler import compile_interaction

    map_state = await store.get_map_state(sid)
    ctx = compile_interaction(map_state, [])
    assert ctx.feature_edits.status == "known"
    assert ctx.feature_edits.value[0]["changed_features"] == 3
    assert ctx.feature_edits.value[0]["origin"] == "user"

    # ⑨ 撤销：反向走同一 CAS 写路径 → 数据回到编辑前（类型/坐标）
    undo = await client.post(f"/api/v1/chat/sessions/{sid}/feature-edits/{ref}/undo")
    assert undo.status_code == 200
    payload2 = await store.get(sid, ref)
    feats2 = payload2["geojson"]["features"]
    by_id2 = {f["id"]: f for f in feats2}
    assert by_id2["sch-1"]["properties"]["type"] == "中学"  # 类型还原
    assert abs(by_id2["sch-0"]["geometry"]["coordinates"][1] - 39.0) < 1e-9  # 坐标还原

    # ⑩ 历史可见（刷新重进 = journal 持久在 session store）
    hist = await client.get(f"/api/v1/chat/sessions/{sid}/feature-edits/{ref}/history")
    body = hist.json()
    assert body["head_seq"] == 2
    kinds = [e["kind"] for e in body["entries"]]
    assert kinds[0] == "undo" and kinds[1] == "apply"
    assert all(e["actor"] == "user" for e in body["entries"])

    # ⑪ 重复决策幂等（重放旧 plan）→ 不写第三版
    replay = await client.post(
        f"/api/v1/chat/sessions/{sid}/feature-edits/plans/{plan_id}/decision",
        json={"action": "approve"},
    )
    assert replay.status_code == 200
    assert replay.json()["receipt"]["entry_seq"] == receipt["entry_seq"]
    desc3 = await store.get_ref_descriptor(sid, ref)
    hist3 = (await client.get(f"/api/v1/chat/sessions/{sid}/feature-edits/{ref}/history")).json()
    assert hist3["head_seq"] == 2 and desc3["content_revision"] == 3
