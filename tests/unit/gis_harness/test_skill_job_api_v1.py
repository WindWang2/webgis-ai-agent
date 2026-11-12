"""G30 — 技能作业 API 路由测试（工作台契约 + 归属隔离 + 预设重用）。

场景映射：#17（预设换数据重跑）、#18（他人预设不泄露数据——重新绑定）、
#24（prepare→execute 间权限/会话归属复核）、#26（busy 会话 409 语义——
活跃 turn 检测在无活跃 turn 的测试环境走空闲路径，busy 分支以
SkillJobErrorCode 映射单测覆盖）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import skill_jobs as sj_routes
from app.core.database import Base, get_engine


def _app(user: dict) -> TestClient:
    application = FastAPI()
    application.include_router(sj_routes.router, prefix="/api/v1")
    application.dependency_overrides[sj_routes.get_current_user] = lambda: user
    return TestClient(application)


@pytest.fixture(autouse=True)
def _tables():
    Base.metadata.create_all(bind=get_engine())
    yield


@pytest.fixture()
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.agent_pi_bridge import set_tool_registry

    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)
    return reg


_USER = {"id": "user-a", "org_id": 1}
_USER_B = {"id": "user-b", "org_id": 2}


def _points(n: int = 12):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0 + i * 0.01, 30.6 + i * 0.005]},
             "properties": {"name": f"p{i}", "score": i * 1.5}}
            for i in range(n)
        ],
    }


class _FakeConversation:
    pass


async def _fake_verify(db, session_id, user_id=None, owner_token=None):
    return _FakeConversation()


@pytest.fixture()
def owned_session_id(registry):
    """替换会话归属验证（归属链由 verify_session_owner 既有单测覆盖；
    此处聚焦技能作业契约本身），返回测试会话 id。"""
    # 路由模块在 import 期直接绑定了 verify_session_owner —— patch 该引用
    original = sj_routes.verify_session_owner
    sj_routes.verify_session_owner = _fake_verify
    yield "sess-api-1"
    sj_routes.verify_session_owner = original


def test_catalog_endpoint(registry):
    client = _app(_USER)
    res = client.get("/api/v1/skill-jobs/catalog")
    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["gis_skills"] >= 3
    ids = {e["id"] for e in body["skills"]}
    assert {"validate_spatial_data", "point_distribution_analysis",
            "choropleth_map_design"} <= ids


def test_skill_detail_endpoint(registry):
    client = _app(_USER)
    res = client.get("/api/v1/skill-jobs/skills/choropleth_map_design")
    assert res.status_code == 200
    entry = res.json()
    assert entry["version"] == "1.1.0"
    names = {p["name"] for p in entry["parameters"]}
    assert {"field", "measure_semantics", "denominator_field"} <= names
    res404 = client.get("/api/v1/skill-jobs/skills/nope")
    assert res404.status_code == 404
    assert res404.json()["detail"]["code"] == "SKILL_NOT_FOUND"


def test_prepare_typed_rejection_shape(registry, owned_session_id):
    client = _app(_USER)
    res = client.post("/api/v1/skill-jobs/prepare", json={
        "session_id": owned_session_id,
        "skill_id": "choropleth_map_design",
        "params": {"field": "pop", "measure_semantics": "rate"},
        "bindings": [],
    })
    # rate 缺绑定数据（先撞 BINDING_MISSING）——typed code 形状可判
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["code"] in ("BINDING_MISSING", "PARAM_CONFLICT")


def test_prepare_start_run_and_idempotent_reuse(registry, owned_session_id):
    import asyncio

    from app.services.session_data import session_data_manager

    ref = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        session_data_manager.store(owned_session_id, _points(), prefix="geojson"))
    client = _app(_USER)
    # prepare
    r1 = client.post("/api/v1/skill-jobs/prepare", json={
        "session_id": owned_session_id,
        "skill_id": "validate_spatial_data",
        "params": {},
        "bindings": [{"role": "subject", "ref": ref}],
    })
    assert r1.status_code == 200, r1.text
    prepared = r1.json()
    assert prepared["prepared_id"].startswith("skp-")
    assert prepared["preview"]["evidence_covered"]
    # start（UI：wait=0 后台运行）
    r2 = client.post("/api/v1/skill-jobs/start", json={
        "prepared_id": prepared["prepared_id"],
        "session_id": owned_session_id,
        "wait_timeout_s": 60,
    })
    assert r2.status_code == 200, r2.text
    run = r2.json()
    job_id = run["job_id"]
    # 轮询到终态
    import time

    for _ in range(100):
        got = client.get(f"/api/v1/skill-jobs/runs/{job_id}").json()
        if got.get("status") in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.2)
    assert got["status"] == "completed", got
    assert got["result_ref"].startswith("ref:skillreport-")
    # prepared 一次性消费：重复 start → 410
    r3 = client.post("/api/v1/skill-jobs/start", json={
        "prepared_id": prepared["prepared_id"],
        "session_id": owned_session_id,
    })
    assert r3.status_code == 410
    assert r3.json()["detail"]["code"] == "PREPARED_EXPIRED"
    # runs 列表（刷新回看）
    r4 = client.get(
        f"/api/v1/skill-jobs/runs?session_id={owned_session_id}")
    assert r4.status_code == 200
    assert any(x["job_id"] == job_id for x in r4.json()["runs"])


def test_preset_save_and_reuse_with_new_data(registry, owned_session_id):
    """场景 #17/#18：预设重用 = 显式新绑定重新 prepare（参数来源=preset）。"""
    import asyncio
    import time

    from app.services.session_data import session_data_manager

    loop = asyncio.new_event_loop()
    ref_a = loop.run_until_complete(
        session_data_manager.store(owned_session_id, _points(14), prefix="geojson"))
    client = _app(_USER)
    # 保存预设
    r1 = client.post("/api/v1/skill-jobs/presets", json={
        "name": "标准点分布配置",
        "skill_id": "point_distribution_analysis",
        "skill_version": "1.1.0",
        "params": {"layer_title": "默认标题"},
        "bindings_template": {"subject": {"last_ref": ref_a, "mode": "ref"}},
    })
    assert r1.status_code == 200, r1.text
    preset = r1.json()
    assert preset["skill_id"] == "point_distribution_analysis"
    # 绑定模板只含 ref 提示，无数据内容
    assert "features" not in str(preset["bindings_template"])
    # 第二份数据上重用（显式新绑定）
    ref_b = loop.run_until_complete(
        session_data_manager.store(owned_session_id, _points(18), prefix="geojson"))
    loop.close()
    r2 = client.post(
        f"/api/v1/skill-jobs/presets/{preset['id']}/prepare", json={
            "session_id": owned_session_id,
            "bindings": [{"role": "subject", "ref": ref_b}],
        })
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["prepared_id"].startswith("skp-")
    sources = {p["name"]: p["source"] for p in body["param_sources"]}
    assert sources["layer_title"] == "preset"  # 继承参数来源=预设
    # 覆盖参数 → user
    r3 = client.post(
        f"/api/v1/skill-jobs/presets/{preset['id']}/prepare", json={
            "session_id": owned_session_id,
            "bindings": [{"role": "subject", "ref": ref_b}],
            "params_override": {"layer_title": "新标题"},
        })
    sources3 = {p["name"]: p["source"] for p in r3.json()["param_sources"]}
    assert sources3["layer_title"] == "user"
    # 他人 org 看不到该预设（404 不泄露存在性）
    client_b = _app(_USER_B)
    r4 = client_b.get(f"/api/v1/skill-jobs/presets/{preset['id']}")
    assert r4.status_code == 404
    # 预设里的过期 ref 不可见 → 重新绑定要求（typed 拒绝）
    r5 = client.post(
        f"/api/v1/skill-jobs/presets/{preset['id']}/prepare", json={
            "session_id": owned_session_id,
            "bindings": [{"role": "subject", "ref": "ref:geojson-gone"}],
        })
    assert r5.status_code == 404
    assert r5.json()["detail"]["code"] == "REF_NOT_FOUND"
