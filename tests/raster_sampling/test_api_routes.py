"""G39 API 路由面 — HTTP 合同 + 会话归属守卫 + G18 交付回读（S26/S29）。"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.services.raster_sampling import service as rs_service

SESS = "sess-g39-api"


@pytest.fixture()
def api_client(rs_env, raster_factory, seed_points_ref):
    """真实守卫签名（session_id 为必填 query，无默认值）—— 与生产
    ``require_owned_session(session_id: str, ...)`` 同形，不掩盖 422。"""
    from app.main import app
    from app.services.auth_history_bridge import require_owned_session

    registry = {}

    async def _fake_owned(session_id: str, conv=None):
        registry.setdefault("seen", []).append(session_id)
        from app.models.db_model import Conversation

        return Conversation(id="conv-1")

    app.dependency_overrides[require_owned_session] = _fake_owned
    with TestClient(app) as client:
        yield client, registry
    app.dependency_overrides.pop(require_owned_session, None)


async def _seed(raster_factory, seed_points_ref):
    values = np.full((1, 8, 8), 42.0, dtype="float32")
    ref, _path = await raster_factory(SESS, name="api_const", values=values)
    points_ref = await seed_points_ref(SESS, [
        {"id": "a", "x": 10.02, "y": 19.97},
        {"id": "b", "x": 10.04, "y": 19.95},
    ])
    return ref, points_ref


def _run(coro):
    return asyncio.run(coro)


def test_describe_and_full_http_flow(api_client, raster_factory,
                                     seed_points_ref):
    client, registry = api_client
    ref, points_ref = _run(_seed(raster_factory, seed_points_ref))

    r = client.get("/api/v1/raster-sampling/describe",
                   params={"session_id": SESS})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert any(x["ref_id"] == ref for x in body["raster_refs"])
    assert body["budgets"]["max_sources"] == 4

    # 创建计划
    r = client.post("/api/v1/raster-sampling/plan",
                    params={"session_id": SESS},
                    json={
                        "mode": "source_points",
                        "points_ref": points_ref, "method": "source_pixel",
                        "sources": [{"ref_id": ref, "bands": [1]}],
                    })
    assert r.status_code == 200
    plan = r.json()["plan"]
    assert plan["revision"] == 1

    # 预览
    r = client.post(f"/api/v1/raster-sampling/plan/{plan['plan_id']}/preview",
                    params={"session_id": SESS},
                    json={"expected_revision": 1, "limit": 8})
    assert r.status_code == 200
    assert r.json()["rows"][0]["raw_value"] == 42.0

    # 旧修订预览 → PREVIEW_STALE
    r = client.post(f"/api/v1/raster-sampling/plan/{plan['plan_id']}/preview",
                    params={"session_id": SESS},
                    json={"expected_revision": 99, "limit": 8})
    assert r.status_code == 409
    assert r.json()["data"]["code"] == "PREVIEW_STALE"

    # 执行（内联）
    r = client.post(f"/api/v1/raster-sampling/plan/{plan['plan_id']}/execute",
                    params={"session_id": SESS},
                    json={"expected_revision": 1, "background": False})
    assert r.status_code == 200
    out = r.json()
    assert "receipt" in out
    receipt = out["receipt"]

    # 状态
    r = client.get(f"/api/v1/raster-sampling/plan/{plan['plan_id']}/status",
                   params={"session_id": SESS})
    assert r.status_code == 200
    assert r.json()["latest_receipt"]["execution_id"] == receipt["execution_id"]

    # 成果（分页 + 曲线）
    r = client.get("/api/v1/raster-sampling/results",
                   params={"session_id": SESS, "plan_id": plan["plan_id"],
                           "page": 1, "per_page": 1, "include_curves": "true"})
    assert r.status_code == 200
    results = r.json()
    assert len(results["rows"]) == 1
    assert results["pagination"]["filtered_rows"] == 2
    assert results["station_layer_ref"].startswith("ref:")

    # 计划清单
    r = client.get("/api/v1/raster-sampling/plans",
                   params={"session_id": SESS})
    assert r.status_code == 200
    assert any(p["plan_id"] == plan["plan_id"] for p in r.json()["plans"])

    # 取消（无活跃执行 → typed 409）
    r = client.post(f"/api/v1/raster-sampling/plan/{plan['plan_id']}/cancel",
                    params={"session_id": SESS})
    assert r.status_code == 409
    assert r.json()["data"]["code"] == "EXECUTION_NOT_RUNNING"


def test_unknown_plan_typed_404(api_client):
    client, _ = api_client
    r = client.get("/api/v1/raster-sampling/plan/rsp_missing/status",
                   params={"session_id": SESS})
    assert r.status_code == 404
    assert r.json()["data"]["code"] == "PLAN_NOT_FOUND"


def test_band_out_of_range_typed(api_client, raster_factory,
                                 seed_points_ref):
    client, _ = api_client
    ref, points_ref = _run(_seed(raster_factory, seed_points_ref))
    r = client.post("/api/v1/raster-sampling/plan",
                    params={"session_id": SESS},
                    json={
                        "mode": "source_points",
                        "points_ref": points_ref,
                        "sources": [{"ref_id": ref, "bands": [9]}],
                    })
    assert r.status_code == 422
    assert r.json()["data"]["code"] == "BAND_OUT_OF_RANGE"


def test_results_require_identifier(api_client):
    client, _ = api_client
    r = client.get("/api/v1/raster-sampling/results",
                   params={"session_id": SESS})
    assert r.status_code == 422


# ── S29：G18 交付通道回读（长表 → CSV → 全行核对） ──────────────────

def test_s29_g18_delivery_readback(rs_env, raster_factory, seed_points_ref):
    """长表 ref 经 data_delivery resolve → CSV 交付 job → 回读全行一致。"""
    from app.services.data_delivery.source_resolver import (
        DeliveryActor,
        resolve_source,
    )
    from app.services.data_delivery.service import submit_delivery
    from app.schemas.data_delivery_schema import DeliverySourceRef
    from app.schemas.data_delivery_schema import DeliverySpec
    from app.core.database import SessionLocal

    # G18 会话归属（同 delivery conftest 范式：Conversation 行 + user）
    from app.models.db_model import Conversation

    with rs_env["sync_factory"]() as db:
        db.add(Conversation(id=SESS, user_id="user-g39", title="G39"))
        db.commit()

    async def main():
        values = np.arange(8 * 8, dtype="float32").reshape(1, 8, 8) + 1.0
        ref, _ = await raster_factory(SESS, name="del_table", values=values)
        points_ref = await seed_points_ref(SESS, [
            {"id": f"st-{i}", "x": 10.0 + (0.01 * (0.5 + i)),
             "y": 19.99 - 0.01 * i} for i in range(4)])
        out = await rs_service.create_plan(
            session_id=SESS, owner_token=None, name="交付回读",
            mode="source_points", points_ref=points_ref,
            method="source_pixel",
            sources=[{"ref_id": ref, "bands": [1]}],
            trusted_dispatch=True)
        exec_out = await rs_service.execute(
            session_id=SESS, plan_id=out["plan"]["plan_id"], owner_token=None,
            background=False, trusted_dispatch=True)
        table_ref = exec_out["receipt"].outputs["sample_table_ref"]

        # G18 解析（fabric_parquet 锚）
        actor = DeliveryActor(user_id="user-g39", org_id=None, owner_token=None)
        snapshot = await resolve_source(
            DeliverySourceRef(kind="fabric_parquet", session_id=SESS,
                              ref=table_ref),
            actor)
        assert snapshot.row_count == 4
        assert snapshot.kind == "fabric_parquet"

        # CSV 交付（真实 durable job + writer）—— 在同一事件循环内等待
        # 终态（runner 是 create_task，loop 必须存活）
        spec = DeliverySpec(
            source=DeliverySourceRef(kind="fabric_parquet",
                                     session_id=SESS, ref=table_ref),
            format="csv", filename="g39_samples")
        submitted = await submit_delivery(spec, actor)
        job_id = int(submitted["job_id"])
        from app.models.db_model import AnalysisTask
        import time as _time

        final_status = "unknown"
        deadline = _time.monotonic() + 60
        while _time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            with SessionLocal() as db:
                row = db.get(AnalysisTask, job_id)
                final_status = str(row.status) if row else "missing"
                db.commit()
            if final_status in ("completed", "failed", "cancelled", "stale"):
                break
        return {"final_status": final_status}

    out = asyncio.run(main())
    assert out["final_status"] == "completed", out
    # 交付包定位 + 回读全行核对
    from app.services.data_delivery.store import delivery_root

    root = delivery_root()
    candidates = [p for p in root.rglob("*.csv")
                  if ".staging" not in str(p)]
    assert candidates, f"no csv under {root}"
    import csv

    with open(sorted(candidates)[0], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4
    assert {r["parent_locator"] for r in rows} == {
        "st-0", "st-1", "st-2", "st-3"}
    assert all(float(r["raw_value"]) >= 1.0 for r in rows)
    # 无效原因/单位列在场（schema 完整回读）
    assert all("invalid_reason" in r and "unit" in r for r in rows)


def test_post_requires_query_session_id(api_client):
    """缺 query session_id → 422（真实守卫签名，非默认参数掩盖）。"""
    client, _ = api_client
    r = client.post("/api/v1/raster-sampling/plan", json={
        "mode": "source_points", "points_ref": "ref:x",
        "sources": [{"ref_id": "ref:y", "bands": [1]}],
    })
    assert r.status_code == 422


def test_post_session_identity_single_source_query(api_client, raster_factory,
                                                   seed_points_ref):
    """守卫看到的 session 唯一来源是 query 参数（body 不携带会话身份；
    真实 404 语义由 require_owned_session 本身与评审真实守卫探针覆盖）。"""
    client, registry = api_client
    ref, points_ref = _run(_seed(raster_factory, seed_points_ref))
    r = client.post("/api/v1/raster-sampling/plan",
                    params={"session_id": "sess-g39-other"},
                    json={
                        "mode": "source_points",
                        "points_ref": points_ref, "method": "source_pixel",
                        "sources": [{"ref_id": ref, "bands": [1]}],
                    })
    # 守卫对未知会话按真实 verify 语义（404）；本测试的 override 不做
    # 归属校验，因此只锁定「session 唯一来源是 query」：
    assert registry["seen"][-1] == "sess-g39-other"
    assert "session_id" not in (r.json() if r.status_code == 200 else {})
