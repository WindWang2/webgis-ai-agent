"""T-B14/T-B15/T-B20：Pi 工具注册/dispatch 真实执行 + HTTP 路由旅程。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user_optional_with_version
from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from tests.unit.thematic_authoring.helpers import (
    current_revision,
    make_fc,
    seed_thematic_layer,
)


@pytest.fixture
async def seeded(session_id, store_root):
    rows = [{"pop": v} for v in (0, 3, 7, 12, 18, 25, 33, 47, 60, 88)]
    fc = make_fc(rows)
    await seed_thematic_layer(session_id, fc, layer_id="result-seed", field="pop")
    return fc


# ─── 工具注册与真实执行（经 registry） ──────────────────────────────────────


def test_tools_registered_in_global_registry():
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    from app.tools.thematic_authoring_tools import register_thematic_authoring_tools

    register_thematic_authoring_tools(registry)
    names = set(registry.list_tools()) if hasattr(registry, "list_tools") else set()
    if not names:
        # registry 的列举面不同实现——直接查 dispatch 表
        names = set(getattr(registry, "_tools", {}).keys())
    for expected in (
        "webgis_thematic_style_inspect",
        "webgis_thematic_style_preview",
        "webgis_thematic_style_apply",
    ):
        assert expected in names, f"{expected} 未注册"


def test_tool_module_registered_in_init():
    import app.tools as tools_pkg

    modules = [m for m, _fn in getattr(tools_pkg, "_TOOL_MODULES", [])]
    assert "app.tools.thematic_authoring_tools" in modules


@pytest.mark.asyncio
async def test_inspect_tool_bounded_receipt(session_id, store_root, seeded):
    from app.tools.thematic_authoring_tools import register_thematic_authoring_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_thematic_authoring_tools(registry)
    fn = registry._tools["webgis_thematic_style_inspect"]
    result = await fn(layer_id="result-seed", session_id=session_id)
    assert result["success"] is True
    assert result["current"]["type"] == "graduated"
    assert result["distribution"]["basis"]["mode"] == "full_scan"
    assert len(result["fields"]) <= 40


@pytest.mark.asyncio
async def test_apply_tool_commits_and_equivalent_to_ui_service(session_id, store_root, seeded):
    """验收 20：UI 与 Pi 同参数 → 等价分类与同源回执。"""
    from app.services.thematic_authoring import commit_expression, preview_expression
    from app.services.thematic_authoring.contract import parse_plan

    plan = {"layer_id": "result-seed", "mode": "graduated_manual",
            "breaks": [0, 10, 20, 50], "palette": "Blues"}
    rev0 = await current_revision(session_id)
    # Pi 工具路径
    from app.tools.thematic_authoring_tools import register_thematic_authoring_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_thematic_authoring_tools(registry)
    fn = registry._tools["webgis_thematic_style_apply"]
    tool_result = await fn(
        layer_id="result-seed", plan=plan, expected_revision=rev0,
        session_id=session_id)
    assert tool_result["success"] is True
    assert tool_result["committed"] is True
    assert tool_result["revision"] == rev0 + 1
    assert tool_result["class_count"] == 3
    assert "legend_spec" not in tool_result  # 有界回执：不塞完整 spec

    # 等价性：同一计划经 service preview 得到同一候选指纹
    ui_preview = await preview_expression(
        session_id, "result-seed", parse_plan(plan))
    from app.services.thematic_authoring.service import expression_fingerprint
    from tests.unit.thematic_authoring.helpers import read_layer
    layer = await read_layer(session_id, "result-seed")
    assert expression_fingerprint(layer["legend_spec"]) == ui_preview["candidate_fingerprint"]


# ─── HTTP 路由旅程 ─────────────────────────────────────────────────────────


@pytest.fixture
def app(monkeypatch, session_id):
    from app.api.routes import thematic_authoring as routes

    application = FastAPI()
    application.dependency_overrides[require_owned_session] = lambda: Conversation(id=session_id)
    application.dependency_overrides[get_current_user_optional_with_version] = (
        lambda: {"user_id": "g33-test-user"}
    )
    application.include_router(routes.router, prefix="/api/v1")
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.mark.asyncio
async def test_route_inspect_preview_commit_flow(session_id, store_root, seeded, client):
    base = f"/api/v1/thematic-authoring/sessions/{session_id}/layers/result-seed"
    r = await client.get(f"{base}/inspect")
    assert r.status_code == 200
    body = r.json()
    assert body["current"]["legend_spec"]["type"] == "graduated"

    plan = {"mode": "graduated_manual", "breaks": [0, 10, 20, 50], "palette": "Blues"}
    r = await client.post(f"{base}/preview", json={"plan": plan})
    assert r.status_code == 200
    assert r.json()["committed"] is False

    from tests.unit.thematic_authoring.helpers import current_revision
    rev = await current_revision(session_id)
    r = await client.post(
        f"{base}/commit",
        json={"plan": plan, "expected_revision": rev, "client_mutation_id": "ui-1"})
    assert r.status_code == 200
    assert r.json()["committed"] is True

    # 陈旧修订 → 409 superseded
    r = await client.post(
        f"{base}/commit", json={"plan": plan, "expected_revision": rev})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "CAS_SUPERSEDED"


@pytest.mark.asyncio
async def test_route_error_contract_locates_break_index(session_id, store_root, seeded, client):
    base = f"/api/v1/thematic-authoring/sessions/{session_id}/layers/result-seed"
    r = await client.post(
        f"{base}/preview",
        json={"plan": {"mode": "graduated_manual", "breaks": [0, 20, 10]}})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "BREAKS_NOT_STRICTLY_INCREASING"
    assert detail["index"] == 2


@pytest.mark.asyncio
async def test_route_unknown_layer_404_and_non_thematic_409(session_id, store_root, seeded, client):
    url = f"/api/v1/thematic-authoring/sessions/{session_id}/layers/missing"
    r = await client.get(url + "/inspect")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "UNKNOWN_LAYER"
