"""G41 Pi 工具注册 / 有界回执 / HTTP 路由旅程（静态测试引用 + 真实 dispatch）。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user_optional_with_version
from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from tests.unit.bivariate_reliability.helpers import (
    bivariate_plan,
    current_revision,
    make_fc,
    reliability_plan,
    seed_layer,
)

ROWS = [{"a": float(i), "b": float(i % 3), "est": float(i),
         "se": 0.0 if i == 0 else float(i % 4) * 0.5} for i in range(12)]
TOOL_NAMES = (
    "webgis_bivariate_reliability_inspect",
    "webgis_bivariate_reliability_preview",
    "webgis_bivariate_reliability_apply",
)


@pytest.fixture
async def seeded(session_id):
    await seed_layer(session_id, make_fc(ROWS))
    return True


@pytest.fixture
def app(session_id):
    from app.api.routes import bivariate_reliability_authoring as routes

    application = FastAPI()
    application.dependency_overrides[require_owned_session] = (
        lambda: Conversation(id=session_id))
    application.dependency_overrides[get_current_user_optional_with_version] = (
        lambda: {"user_id": "g41-test-user"})
    application.include_router(routes.router, prefix="/api/v1")
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        yield c


def _registry():
    from app.tools.registry import ToolRegistry
    from app.tools.bivariate_reliability_tools import (
        register_bivariate_reliability_tools,
    )
    reg = ToolRegistry()
    register_bivariate_reliability_tools(reg)
    return reg


def test_tools_registered_in_global_registry():
    reg = _registry()
    names = set(reg.list_tools()) if hasattr(reg, "list_tools") else set()
    if not names:
        names = set(getattr(reg, "_tools", {}).keys())
    for name in TOOL_NAMES:
        assert name in names, f"{name} 未注册"


def test_tool_module_registered_in_init():
    import app.tools as tools_pkg
    modules = [m for m, _ in getattr(tools_pkg, "_TOOL_MODULES", [])]
    assert "app.tools.bivariate_reliability_tools" in modules


def test_router_registered_in_main():
    """静态引用：main.py 必须挂载本域 router（否则 UI 404）。"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[3]
           / "app" / "main.py").read_text(encoding="utf-8")
    assert "bivariate_reliability_authoring as g41_routes" in src
    assert "g41_routes.router" in src


@pytest.mark.asyncio
async def test_inspect_tool_bounded_receipt(session_id, seeded):
    reg = _registry()
    fn = reg._tools[TOOL_NAMES[0]]
    out = await fn(layer_id="L1", session_id=session_id)
    assert out["success"] is True
    assert out["source"]["content_revision"] >= 1
    assert len(out["fields"]) <= 40
    assert "uncertainty_evidence" not in str(out)  # 不回传全量要素/证据体
    assert out["supported"]["bivariate_matrix"] is True


@pytest.mark.asyncio
async def test_inspect_tool_requires_session_context(session_id, seeded):
    reg = _registry()
    out = await reg._tools[TOOL_NAMES[0]](layer_id="L1", session_id="")
    assert out["success"] is False and out["code"] == "SOURCE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_preview_and_apply_tools_share_service_with_ui(session_id, seeded):
    from app.services.bivariate_reliability_authoring import (
        parse_plan,
        preview_expression,
    )
    reg = _registry()
    plan = bivariate_plan()
    prev = await reg._tools[TOOL_NAMES[1]](
        layer_id="L1", plan=plan, session_id=session_id)
    assert prev["success"] is True and prev["preview_only"] is True
    assert prev["candidate_digest"].startswith("g41c-")
    assert len(prev["legend"]["cells"]) <= 9
    assert "legend_spec" not in prev  # 有界回执
    ui = await preview_expression(session_id, "L1", parse_plan(plan),
                                  trusted_dispatch=True)
    assert ui["candidate_fingerprint"] == prev["candidate_fingerprint"]

    rev = await current_revision(session_id)
    out = await reg._tools[TOOL_NAMES[2]](
        layer_id="L1", plan=plan, expected_revision=rev,
        preview_digest=prev["candidate_digest"], session_id=session_id)
    assert out["success"] is True and out["committed"] is True
    assert out["revision"] == rev + 1
    assert out["reconcile_via"] == "mapspec_event"


@pytest.mark.asyncio
async def test_apply_tool_rejects_stale_preview(session_id, seeded):
    reg = _registry()
    prev = await reg._tools[TOOL_NAMES[1]](
        layer_id="L1", plan=bivariate_plan(), session_id=session_id)
    changed = bivariate_plan()
    changed["bivariate"]["n"] = 2
    out = await reg._tools[TOOL_NAMES[2]](
        layer_id="L1", plan=changed,
        expected_revision=await current_revision(session_id),
        preview_digest=prev["candidate_digest"], session_id=session_id)
    assert out["success"] is False and out["code"] == "STALE_PREVIEW"


@pytest.mark.asyncio
async def test_apply_tool_no_uncertainty_evidence_is_honest(session_id):
    rows = [{"a": float(i), "b": float(i), "est": float(i),
             "sample_count": 30} for i in range(8)]
    await seed_layer(session_id, make_fc(rows))
    reg = _registry()
    out = await reg._tools[TOOL_NAMES[1]](
        layer_id="L1", plan=reliability_plan(), session_id=session_id)
    assert out["success"] is False
    assert out["code"] == "NO_UNCERTAINTY_EVIDENCE"
    assert "标准误" in out.get("correction_hint", "")


@pytest.mark.asyncio
async def test_route_inspect_preview_commit_flow(session_id, seeded, client):
    base = f"/api/v1/bivariate-reliability/sessions/{session_id}/layers/L1"
    r = await client.get(f"{base}/inspect")
    assert r.status_code == 200, r.text
    ins = r.json()
    assert ins["source"]["feature_count"] == 12

    p = await client.post(f"{base}/preview", json={"plan": bivariate_plan()})
    assert p.status_code == 200, p.text
    prev = p.json()
    assert prev["committed"] is False

    rev = await current_revision(session_id)
    c = await client.post(f"{base}/commit", json={
        "plan": bivariate_plan(), "expected_revision": rev,
        "preview_digest": prev["candidate_digest"]})
    assert c.status_code == 200, c.text
    assert c.json()["committed"] is True
    assert c.json()["mutations"] == 1


@pytest.mark.asyncio
async def test_route_error_contract_locates_field(session_id, seeded, client):
    base = f"/api/v1/bivariate-reliability/sessions/{session_id}/layers/L1"
    body = bivariate_plan()
    body["bivariate"]["axis_a"] = {"field": "a", "method": "manual",
                                   "breaks": [30, 10]}
    r = await client.post(f"{base}/preview", json={"plan": body})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "BREAKS_NOT_STRICTLY_INCREASING"
    assert detail["index"] == 1
    assert "axis_a.breaks" in detail["field"]


@pytest.mark.asyncio
async def test_route_unknown_layer_404_and_missing_digest_422(session_id, seeded, client):
    base = f"/api/v1/bivariate-reliability/sessions/{session_id}/layers/missing"
    r = await client.get(f"{base}/inspect")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "UNKNOWN_LAYER"
    ok = f"/api/v1/bivariate-reliability/sessions/{session_id}/layers/L1"
    r = await client.post(f"{ok}/commit",
                          json={"plan": bivariate_plan(), "expected_revision": 0})
    assert r.status_code == 422  # preview_digest 必填
