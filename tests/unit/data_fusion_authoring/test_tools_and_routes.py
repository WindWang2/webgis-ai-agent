"""融合工具面 + REST 路由测试（G38 同款模式）。

锁定：10 工具注册齐、_TOOL_MODULES 索引、session 注入拒绝、typed
错误 HTTP 映射、REST 端到端（资格卡 → 计划 → 执行 → 审阅 → commit
→ 结果分页）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_tables")

import app.tools as tools_pkg  # noqa: E402
from app.models.db_model import Conversation  # noqa: E402
from app.services.auth_history_bridge import require_owned_session  # noqa: E402

_EXPECTED_TOOLS = {
    "webgis_data_fusion_describe_sources",
    "webgis_data_fusion_plan_upsert",
    "webgis_data_fusion_preview",
    "webgis_data_fusion_execute",
    "webgis_data_fusion_status",
    "webgis_data_fusion_review_queue",
    "webgis_data_fusion_decide",
    "webgis_data_fusion_commit",
    "webgis_data_fusion_result",
    "webgis_data_fusion_refresh",
}


def test_tools_registered_in_fresh_registry():
    from app.tools.data_fusion_authoring_tools import (
        register_data_fusion_authoring_tools,
    )
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_data_fusion_authoring_tools(reg)
    names = {n for n in reg._tools if n.startswith("webgis_data_fusion")}
    assert names == _EXPECTED_TOOLS


def test_tool_module_registered_in_init():
    """_TOOL_MODULES 索引钉死（漏登记 = Pi 面不可见）。"""
    assert ("app.tools.data_fusion_authoring_tools",
            "register_data_fusion_authoring_tools") in [
        (m, fn) for m, fn in tools_pkg._TOOL_MODULES]


async def test_describe_sources_tool_session_guard(seeded_fusion_sources):
    """缺 session 注入 → 明确拒绝（不越权）；注入后返回资格卡。"""
    from app.tools.data_fusion_authoring_tools import (
        register_data_fusion_authoring_tools,
    )
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_data_fusion_authoring_tools(reg)
    describe = reg._tools["webgis_data_fusion_describe_sources"]
    res = await describe(sources=[{"side": "a", "kind": "session_ref",
                                   "ref": "ref:x"}])
    assert res["error"] == "session_required"
    s = seeded_fusion_sources
    res2 = await describe(
        sources=[
            {"side": "a", "kind": "session_ref",
             "session_id": s["session_id"], "ref": s["a_ref"],
             "name_field": "name"},
            {"side": "b", "kind": "session_ref",
             "session_id": s["session_id"], "ref": s["b_ref"],
             "name_field": "name"},
        ],
        session_id=s["session_id"])
    assert res2["sources"][0]["state"] == "ok"
    assert res2["sources"][1]["row_count"] == 3


async def test_decide_tool_cas_and_review_queue_tool(seeded_fusion_sources):
    """工具面走同一 service：审阅队列分页 + 决定 CAS 语义。"""
    from app.tools.data_fusion_authoring_tools import (
        register_data_fusion_authoring_tools,
    )
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_data_fusion_authoring_tools(reg)
    s = seeded_fusion_sources
    plan_upsert = reg._tools["webgis_data_fusion_plan_upsert"]
    execute = reg._tools["webgis_data_fusion_execute"]
    queue_tool = reg._tools["webgis_data_fusion_review_queue"]
    decide = reg._tools["webgis_data_fusion_decide"]

    up = await plan_upsert(plan=s["plan"], session_id=s["session_id"])
    assert up["revision"] == 1
    run = await execute(plan_id=up["plan_id"], expected_revision=1,
                        wait_max_s=120, session_id=s["session_id"])
    assert run["status"] == "completed"
    q = await queue_tool(run_id=run["run_id"], session_id=s["session_id"])
    assert q["total"] >= 1
    item = q["items"][0]
    dec = await decide(plan_id=up["plan_id"], pair_key=item["pair_key"],
                       action="accept", expected_revision=0,
                       field_values={"name": "裁决名"},
                       session_id=s["session_id"])
    assert dec["action"] == "accept" and dec["locked"] is True
    # 同修订重提 → stale（typed，不覆盖）
    res = await decide(plan_id=up["plan_id"], pair_key=item["pair_key"],
                       action="accept", expected_revision=0,
                       session_id=s["session_id"])
    assert res["error"] == "stale_review_revision"


# ── REST 路由（standalone app + 依赖覆盖） ──────────────────────────

@pytest.fixture()
def client(seeded_fusion_sources):
    from httpx import ASGITransport, AsyncClient
    from fastapi import FastAPI

    from app.api.routes import data_fusion_authoring as routes

    app = FastAPI()
    app.dependency_overrides[require_owned_session] = lambda: Conversation(
        id=seeded_fusion_sources["session_id"])
    app.include_router(routes.router, prefix="/api/v1")
    transport = ASGITransport(app=app)

    async def _make():
        return AsyncClient(transport=transport, base_url="http://t")

    return _make


async def test_rest_full_journey(seeded_fusion_sources, client):
    """REST 端到端：计划 → 执行 → 队列 → 决定 → commit → 结果分页。"""
    s = seeded_fusion_sources
    async with await client() as c:
        r = await c.post(f"/api/v1/data-fusion-authoring/sessions/{s['session_id']}/plans",
                         json={"plan": s["plan"]})
        assert r.status_code == 200, r.text
        plan_view = r.json()
        pid = plan_view["plan_id"]

        r = await c.post(
            f"/api/v1/data-fusion-authoring/sessions/{s['session_id']}/plans/{pid}/execute",
            json={"expected_revision": 1, "background": False})
        assert r.status_code == 200, r.text
        run = r.json()
        rid = run["run_id"]

        r = await c.get(
            f"/api/v1/data-fusion-authoring/sessions/{s['session_id']}/runs/{rid}/review-queue")
        assert r.status_code == 200
        queue = r.json()
        assert queue["total"] >= 1
        item = queue["items"][0]

        r = await c.post(
            f"/api/v1/data-fusion-authoring/sessions/{s['session_id']}/plans/{pid}/decisions",
            json={"pair_key": item["pair_key"], "action": "accept",
                  "expected_revision": 0,
                  "field_values": {"name": "REST 裁决名"},
                  "reviewer": "rest-user"})
        assert r.status_code == 200, r.text

        r = await c.post(
            f"/api/v1/data-fusion-authoring/sessions/{s['session_id']}/runs/{rid}/commit",
            json={})
        assert r.status_code == 200, r.text
        done = r.json()
        assert done["output_ref"] == f"ref:data-fusion/{rid}"

        r = await c.get(
            f"/api/v1/data-fusion-authoring/sessions/{s['session_id']}/runs/{rid}/results"
            "?limit=50")
        assert r.status_code == 200
        page = r.json()
        assert page["total"] == 4
        merged = [f for f in page["features"]
                  if f["properties"]["_fusion"]["origin"] == "merged"]
        assert any(f["properties"].get("name") == "REST 裁决名"
                   for f in merged)


async def test_rest_typed_error_envelope(seeded_fusion_sources, client):
    """typed 错误 → 稳定 machine code 信封（error_code + correction_hint）。"""
    s = seeded_fusion_sources
    async with await client() as c:
        r = await c.post(
            f"/api/v1/data-fusion-authoring/sessions/{s['session_id']}/plans/nope/preview",
            json={"expected_revision": 1})
        assert r.status_code == 404
        detail = r.json().get("detail") or {}
        assert detail.get("error_code") in ("not_owned", "run_not_found")
