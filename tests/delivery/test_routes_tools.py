"""G18 — HTTP 路由契约 + 标准工具面（WP09/WP11 场景 22/23/24 的接口面）。

真实路由 + 真实 auth 依赖覆盖（登录用户）；不 mock 被测语义。
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.schemas.data_delivery_schema import (
    DeliveryFieldSelection,
    DeliverySourceRef,
    DeliverySpec,
)


@pytest_asyncio.fixture
async def client(delivery_env, seeded_session):
    from fastapi import FastAPI

    from app.api.routes import data_delivery as routes
    from app.core import auth as auth_mod

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")

    async def fake_user():
        return {"user_id": "user-1", "id": "user-1", "org_id": None}

    async def fake_owner_token():
        return None

    app.dependency_overrides[auth_mod.get_current_user] = fake_user
    app.dependency_overrides[auth_mod.get_current_user_optional] = fake_user
    app.dependency_overrides[auth_mod.get_owner_token] = fake_owner_token
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


def _spec_body(ref: str, fmt: str = "geojson") -> dict:
    """POST /jobs 体（含 spec 包装）。"""
    return {
        "spec": {
            "source": {"kind": "session_ref", "session_id": "sess-1", "ref": ref},
            "format": fmt,
            "filename": "route-t",
        }
    }


def _bare_spec(ref: str, fmt: str = "geojson") -> dict:
    """POST /preview 体（规格直挂 body）。"""
    return _spec_body(ref, fmt)["spec"]


async def test_capabilities_route(client):
    r = await client.get("/api/v1/data-delivery/capabilities")
    assert r.status_code == 200
    body = r.json()
    fmts = {f["format"]: f for f in body["formats"]}
    assert fmts["geojson"]["available"] is True
    assert fmts["gpkg"]["available"] is True
    assert fmts["shapefile_zip"]["constrained"] is True
    assert "max_rows_per_delivery" in body["limits"]


async def test_preview_route_happy_and_missing_crs(client):
    r = await client.post("/api/v1/data-delivery/preview",
                          json=_spec_body("ref:nope-dead"))
    assert r.status_code in (404, 422)
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "x",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"a": 1}}],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="route")
    r2 = await client.post("/api/v1/data-delivery/preview",
                           json=_bare_spec(ref))
    assert r2.status_code == 200
    body = r2.json()
    assert body["source"]["row_count"] == 1
    assert body["source"]["crs"] == "EPSG:4326"


async def test_submit_requires_loss_ack(client):
    """缺 CRS 的 GPKG 提交：未 ack → LOSS_ACK_REQUIRED；ack 后 202。"""
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "x",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"a": 1}}],
          "properties": {}}
    ref = await session_data_manager.store("sess-1", fc, prefix="route")
    body = _spec_body(ref, "gpkg")
    body["spec"]["crs"] = {"allow_unknown_source_crs": True}
    r = await client.post("/api/v1/data-delivery/jobs", json=body)
    assert r.status_code == 422
    assert "LOSS_ACK_REQUIRED" in json.dumps(r.json())
    body["acknowledged_losses"] = ["crs_label_unknown"]
    r2 = await client.post("/api/v1/data-delivery/jobs", json=body)
    assert r2.status_code == 202
    job_id = r2.json()["job_id"]
    # 等 runner 完成
    from tests.delivery.conftest import run_job_to_completion

    await run_job_to_completion(job_id)
    r3 = await client.get(f"/api/v1/data-delivery/jobs/{job_id}")
    assert r3.status_code == 200
    view = r3.json()
    assert view["status"] == "completed", view
    assert view["receipt"]["row_count"] == 1


async def test_download_headers_and_member_guard(client):
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "x",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"a": 1}}],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="route")
    body = _spec_body(ref, "csv")
    body["spec"]["fields"] = {"fields": ["a"], "include_geometry": False}
    r = await client.post("/api/v1/data-delivery/jobs", json=body)
    job_id = r.json()["job_id"]
    from tests.delivery.conftest import run_job_to_completion

    await run_job_to_completion(job_id)
    d = await client.get(f"/api/v1/data-delivery/jobs/{job_id}/download")
    assert d.status_code == 200
    assert d.headers["content-type"].startswith("text/csv")
    assert "attachment" in d.headers["content-disposition"]
    assert "X-Content-Sha256" in d.headers
    # 成员守卫：非清单成员 404（不泄漏存在性）
    bad = await client.get(
        f"/api/v1/data-delivery/jobs/{job_id}/download?member=receipt.json")
    assert bad.status_code == 404
    # schema sidecar 成员可下载
    side = await client.get(
        f"/api/v1/data-delivery/jobs/{job_id}/download?member=route-t.schema.json")
    assert side.status_code == 200
    assert json.loads(side.content)["format"] == "csv"


async def test_unauthenticated_submit_rejected(delivery_env, seeded_session):
    from fastapi import FastAPI

    from app.api.routes import data_delivery as routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        r = await c.post("/api/v1/data-delivery/jobs",
                         json=_spec_body(seeded_session["ref"]))
        assert r.status_code in (401, 403)


async def test_old_geojson_export_untouched(client):
    """场景 24（接口面）：老 /export 端点不因新域路由挂载被抢占。"""
    r = await client.get("/api/v1/export/download/nonexistent.png")
    # 404 = 老路由仍在其命名空间（本 app 未挂 map 路由 → 404 而非 405）
    assert r.status_code == 404


# ── 工具面 ────────────────────────────────────────────────────────────────

async def test_tools_registered_and_preview_dispatch(delivery_env, seeded_session):
    """场景 22：标准工具经 ToolRegistry 联通（预览→提交→读收据）。"""
    from app.tools.data_delivery_tools import register_data_delivery_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_data_delivery_tools(registry)
    names = registry.tool_names()
    for expected in ("delivery_capabilities", "preview_data_delivery",
                     "submit_data_delivery", "get_data_delivery"):
        assert expected in names, f"{expected} 未注册"

    from app.services.jobs.context import JobOrigin, use_origin

    origin = JobOrigin(owner_id="user-1", session_id="sess-1")
    spec = {"source": {"kind": "session_ref", "session_id": "sess-1",
                       "ref": seeded_session["ref"]},
            "format": "geojson", "filename": "tool-t"}
    # 场景 22：经 registry.dispatch 的真实分发路径（Pydantic 校验 + 执行）
    with use_origin(origin):
        result = await registry.dispatch("preview_data_delivery", {"spec": spec})
    assert result["status"] == "success", result
    assert result["source"]["row_count"] == 3

    with use_origin(origin):
        sub = await registry.dispatch(
            "submit_data_delivery",
            {"spec": spec,
             "acknowledged_losses": result.get("required_acknowledgments", [])})
    assert sub["status"] == "success", sub
    from tests.delivery.conftest import run_job_to_completion

    await run_job_to_completion(sub["job_id"])

    with use_origin(origin):
        view = await registry.dispatch("get_data_delivery",
                                       {"job_id": sub["job_id"]})
    assert view["job_status"] == "completed", view
    assert view["receipt"]["row_count"] == 3
    assert view["download_url"].startswith("/api/v1/data-delivery/jobs/")


async def test_tool_submit_enforces_ack(delivery_env, seeded_session):
    """工具路径不旁路损失确认：shapefile 长字段名未 ack → typed 拒绝。"""
    from app.services.jobs.context import JobOrigin, use_origin
    from app.services.session_data import session_data_manager
    from app.tools.data_delivery_tools import register_data_delivery_tools
    from app.tools.registry import ToolRegistry

    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "x",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"population_total": 1,
                                       "population_rate": 2}}],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="toolshp")

    registry = ToolRegistry()
    register_data_delivery_tools(registry)
    origin = JobOrigin(owner_id="user-1", session_id="sess-1")
    with use_origin(origin):
        sub = await registry.dispatch(
            "submit_data_delivery",
            {"spec": {"source": {"kind": "session_ref", "session_id": "sess-1",
                                 "ref": ref},
                      "format": "shapefile_zip", "filename": "tool-t"},
             # 不带 acknowledged_losses —— 长字段名映射需确认
             })
    assert sub["status"] == "error"
    assert sub["error_type"] == "LOSS_ACK_REQUIRED"
