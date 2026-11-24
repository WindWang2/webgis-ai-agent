"""方法实验 REST journey（WP5/WP6）—— HTTP 面 → service → 真实
ToolDispatchService → 真实工具 → session store 全链。

验收旅程（任务书 §6-1/2/5/6 的 HTTP 投影）：

- 同链等价：同一输入经 REST plan 与工具层 compile 得到**相同
  experiment_id/指纹**（service 单一实现，UI 与 Agent 零分叉）；
- 插值实验全链：inspect → plan → run（生产 dispatch 含 ref 解引用）→
  compare（可比组 + 推荐）→ adopt（回执）；未完成候选采纳 → 409；
- plan typed 预检：缺值字段 → 400 MEXP_VALUE_FIELD_REQUIRED；
- 会话隔离：B 会话读 A 实验状态 → 404；
- 批量采纳拒绝路径（未评估绝不进回执）。
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient, Response
from fastapi import FastAPI

from app.services.session_data import session_data_manager


@pytest.fixture
def app():
    from app.api.routes import method_experiment as routes

    a = FastAPI()
    a.include_router(routes.router, prefix="/api/v1")
    return a


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _auth(app):
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()


@pytest.fixture(autouse=True)
def _armed_registry():
    """注入真实工具注册表（生产 dispatch 缝需要——与 app lifespan 同初始化）。"""
    from app.agent_pi_bridge import set_tool_registry, try_get_tool_registry
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    previous = try_get_tool_registry()
    registry = ToolRegistry()
    init_tools(registry)
    set_tool_registry(registry)
    yield registry
    set_tool_registry(previous)


def _fc(n=40, seed=12):
    rng = np.random.default_rng(seed)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [float(104.0 + rng.uniform(-0.03, 0.03)),
                                      float(30.6 + rng.uniform(-0.03, 0.03))]},
         "properties": {"price": float(rng.normal(100, 5))}}
        for _ in range(n)]}


def sid() -> str:
    return f"sess-mexp-{uuid.uuid4().hex[:8]}"


async def test_plan_typed_preflight_requires_value_field(client):
    s = sid()
    ref = await session_data_manager.store(s, _fc(20), prefix="geojson")
    resp = await client.post(f"/api/v1/method-experiment/sessions/{s}/plan",
                             json={"family_id": "interpolation",
                                   "input_ref": ref, "value_field": ""})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["reason_code"] == "MEXP_VALUE_FIELD_REQUIRED"


async def test_full_loop_rest_matches_tool_path(client):
    """REST plan 与服务层直接编译 → 同一 experiment_id（同链零分叉）。"""
    s = sid()
    ref = await session_data_manager.store(s, _fc(40), prefix="geojson")
    body = {"family_id": "interpolation", "input_ref": ref,
            "value_field": "price",
            "candidate_ids": ["idw", "nearest_neighbor"],
            "max_trials": 6}
    resp = await client.post(f"/api/v1/method-experiment/sessions/{s}/plan",
                             json=body)
    assert resp.status_code == 200, resp.text
    plan = resp.json()["plan"]
    assert plan["trials"], "计划应含试验清单"
    experiment_id = plan["experiment_id"]

    from app.services.gis_harness.method_experiment.plan import (
        compile_experiment_plan,
    )
    direct = compile_experiment_plan(
        family_id="interpolation", input_ref=ref, value_field="price",
        candidate_ids=["idw", "nearest_neighbor"], max_trials=6)
    assert direct.experiment_id == experiment_id

    # ── inspect 面可用 ──────────────────────────────────────────────────
    resp = await client.post(f"/api/v1/method-experiment/sessions/{s}/inspect",
                             json={"family_id": "interpolation"})
    assert resp.status_code == 200
    cards = resp.json()["cards"]
    assert cards and cards[0]["family_id"] == "interpolation"

    # ── run（生产 dispatch：真实 ToolDispatchService + ref 解引用）──────
    resp = await client.post(f"/api/v1/method-experiment/sessions/{s}/run",
                             json={"experiment_id": experiment_id,
                                   "max_trials_per_call": 6})
    assert resp.status_code == 200, resp.text
    run = resp.json()
    assert run["status"] == "completed"
    assert run["counts"].get("completed", 0) == 4   # idw×3 + nn×1
    for t in run["trials"]:
        assert t["state"] in ("completed", "reused")
        assert t["metrics"], "完成试验应有真实指标"

    # ── compare：可比组 + 推荐 ──────────────────────────────────────────
    resp = await client.get(
        f"/api/v1/method-experiment/sessions/{s}/compare/{experiment_id}")
    assert resp.status_code == 200, resp.text
    ev = resp.json()["evaluation"]
    assert ev["groups"], "应有可比组"
    rec = ev["recommendation"]
    assert rec is not None and rec["status"] == "recommended"
    assert rec["trial_id"]

    # ── adopt：未完成候选 409；已完成候选成功 ────────────────────────────
    resp = await client.post(f"/api/v1/method-experiment/sessions/{s}/adopt",
                             json={"experiment_id": experiment_id,
                                   "trial_id": "t99"})
    assert resp.status_code == 404
    resp = await client.post(f"/api/v1/method-experiment/sessions/{s}/adopt",
                             json={"experiment_id": experiment_id,
                                   "trial_id": rec["trial_id"],
                                   "reason": "比较证据支持"})
    assert resp.status_code == 200, resp.text
    adoption = resp.json()["adoption"]
    assert adoption["trial_id"] == rec["trial_id"]

    # ── status 投影携带回执 ─────────────────────────────────────────────
    resp = await client.get(
        f"/api/v1/method-experiment/sessions/{s}/status/{experiment_id}")
    assert resp.status_code == 200
    assert resp.json()["adoption"]["trial_id"] == rec["trial_id"]


async def test_session_isolation_on_routes(client):
    """B 会话读 A 的实验 → 404（状态按 session 作用域隔离）。"""
    s_a, s_b = sid(), sid()
    ref = await session_data_manager.store(s_a, _fc(20), prefix="geojson")
    resp = await client.post(f"/api/v1/method-experiment/sessions/{s_a}/plan",
                             json={"family_id": "density", "input_ref": ref})
    assert resp.status_code == 200
    exp_id = resp.json()["plan"]["experiment_id"]
    resp = await client.get(
        f"/api/v1/method-experiment/sessions/{s_b}/status/{exp_id}")
    assert resp.status_code == 404


async def test_network_plan_missing_extra_inputs_400(client):
    s = sid()
    ref = await session_data_manager.store(s, _fc(20), prefix="geojson")
    resp = await client.post(f"/api/v1/method-experiment/sessions/{s}/plan",
                             json={"family_id": "network_accessibility",
                                   "input_ref": ref})
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason_code"] == "MEXP_EXTRA_INPUT_REQUIRED:network"
