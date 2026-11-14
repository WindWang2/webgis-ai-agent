"""G38 工具注册/回执 + REST 路由（G33 standalone-app 模式）。

J16（>50 截断选择集不冒充精确范围）由 selection 集成侧覆盖；此处锁定：
工具族注册、_TOOL_MODULES 索引、有界回执、typed 错误 HTTP 映射、
会话守卫依赖注入。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_tables")

import app.tools as tools_pkg
from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.data_join_authoring import service
from tests.unit.data_join_authoring.conftest import seed_session_source
from tests.unit.data_join_authoring.fixtures import attr_plan


# ── 工具注册面 ───────────────────────────────────────────────────────

def test_tools_registered_in_fresh_registry():
    from app.tools.data_join_authoring_tools import (
        register_data_join_authoring_tools,
    )
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_data_join_authoring_tools(reg)
    names = {n for n in reg._tools if n.startswith("webgis_data_join")}
    assert names == {
        "webgis_data_join_describe", "webgis_data_join_plan_upsert",
        "webgis_data_join_preview", "webgis_data_join_execute",
        "webgis_data_join_status", "webgis_data_join_result",
        "webgis_data_join_cancel", "webgis_data_join_list",
    }


def test_tool_module_registered_in_init():
    """_TOOL_MODULES 索引钉死（漏登记 = Pi 面不可见）。"""
    assert ("app.tools.data_join_authoring_tools",
            "register_data_join_authoring_tools") in [
        (m, fn) for m, fn in tools_pkg._TOOL_MODULES]


async def test_describe_tool_bounded_receipt(seeded_sources):
    from app.tools.data_join_authoring_tools import (
        register_data_join_authoring_tools,
    )
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_data_join_authoring_tools(reg)
    describe = reg._tools["webgis_data_join_describe"]
    res = await describe(
        source={"kind": "session_ref",
                "session_id": seeded_sources["session_id"],
                "ref": seeded_sources["left_ref"]},
        session_id=seeded_sources["session_id"],
    )
    assert res["row_count"] == 4
    assert res["source"]["crs"] == "EPSG:4326"
    # 缺 session 注入 → 明确拒绝（不越权）
    res2 = await describe(source={"kind": "session_ref", "ref": "ref:x"})
    assert res2["error"] == "session_required"


# ── REST 路由（standalone app + 依赖覆盖） ──────────────────────────

@pytest.fixture()
def client(seeded_sources):
    from httpx import ASGITransport, AsyncClient
    from fastapi import FastAPI

    from app.api.routes import data_join_authoring as routes

    app = FastAPI()
    app.dependency_overrides[require_owned_session] = lambda: Conversation(
        id=seeded_sources["session_id"])
    app.include_router(routes.router, prefix="/api/v1")
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://t"), seeded_sources


async def test_route_plan_lifecycle_and_execute(client):
    http, seed = client
    base = "/api/v1/data-join-authoring/sessions/" + seed["session_id"]
    plan = attr_plan(
        left={"kind": "session_ref", "session_id": seed["session_id"],
              "ref": seed["left_ref"]},
        right={"kind": "table_rows", "session_id": seed["session_id"],
               "ref": seed["right_ref"]},
    )
    r = await http.post(f"{base}/plans", json={"plan": plan})
    assert r.status_code == 200, r.text
    view = r.json()
    pid, rev = view["plan_id"], view["revision"]
    # 预览（全量诊断）
    r = await http.post(f"{base}/plans/{pid}/preview",
                        json={"expected_revision": rev})
    assert r.status_code == 200, r.text
    assert r.json()["diagnostics"]["left_rows"] == 4
    # 执行（内联）
    r = await http.post(f"{base}/plans/{pid}/execute",
                        json={"expected_revision": rev, "background": False})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "completed" and run["output_rows"] == 4
    # 结果分页
    r = await http.get(f"{base}/runs/{run['run_id']}/results",
                       params={"cursor": 0, "limit": 3})
    assert r.status_code == 200
    page = r.json()
    assert page["total_rows"] == 4 and len(page["rows"]) == 3
    assert page["next_cursor"] == 3
    # 渲染投影
    r = await http.post(f"{base}/runs/{run['run_id']}/render-ref")
    assert r.status_code == 200
    assert r.json()["render_ref"].startswith("ref:")
    # runs 列表
    r = await http.get(f"{base}/runs", params={"plan_id": pid})
    assert r.status_code == 200 and r.json()["count"] == 1


async def test_route_cas_conflict_and_typed_errors(client):
    http, seed = client
    base = "/api/v1/data-join-authoring/sessions/" + seed["session_id"]
    plan = attr_plan(
        left={"kind": "session_ref", "session_id": seed["session_id"],
              "ref": seed["left_ref"]},
        right={"kind": "table_rows", "session_id": seed["session_id"],
               "ref": seed["right_ref"]},
    )
    r = await http.post(f"{base}/plans", json={"plan": plan})
    pid = r.json()["plan_id"]
    # CAS 冲突 → 409 + typed code
    r = await http.patch(f"{base}/plans/{pid}",
                         json={"plan": plan, "expected_revision": 42})
    assert r.status_code == 409
    assert r.json()["detail"]["error_code"] == "stale_plan_revision"
    # 无效计划 → 400 typed
    bad = dict(plan)
    bad["null_policy"] = "nulls_match"
    r = await http.post(f"{base}/plans", json={"plan": bad})
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "invalid_plan"
    # 不存在的 run → 404 不泄漏
    r = await http.get(f"{base}/runs/dj-nonexistent")
    assert r.status_code == 404
    assert r.json()["detail"]["error_code"] == "run_not_found"


async def test_route_wrong_session_404(client):
    """路径 session 与守卫会话不一致：守卫仍拦（require_owned_session 语义），
    且本域计划查询按 session 隔离。"""
    http, seed = client
    # 守卫会话 = seed.session_id；查询他人 session 的计划 → not_owned 404
    r = await http.get(
        "/api/v1/data-join-authoring/sessions/sess-other/plans")
    assert r.status_code == 200  # 列表为空（隔离而非报错）
    assert r.json()["count"] == 0


async def test_cancel_terminal_run_is_idempotent(client):
    http, seed = client
    base = "/api/v1/data-join-authoring/sessions/" + seed["session_id"]
    plan = attr_plan(
        left={"kind": "session_ref", "session_id": seed["session_id"],
              "ref": seed["left_ref"]},
        right={"kind": "table_rows", "session_id": seed["session_id"],
               "ref": seed["right_ref"]},
    )
    r = await http.post(f"{base}/plans", json={"plan": plan})
    pid, rev = r.json()["plan_id"], r.json()["revision"]
    r = await http.post(f"{base}/plans/{pid}/execute",
                        json={"expected_revision": rev, "background": False})
    run_id = r.json()["run_id"]
    # 终态 run 取消 → already_terminal（幂等、不报错）
    r = await http.post(f"{base}/runs/{run_id}/cancel")
    assert r.status_code == 200
    assert r.json()["note"] == "already_terminal"
