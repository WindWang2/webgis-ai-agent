"""共享拓扑编辑 —— 真实事务链路与工具面测试（goal-gis-topology-authoring）。

覆盖任务书验收面：
- WP4：preview→approve→commit→undo/redo 全链（真实 executor CAS/journal）、
  幂等重放、stale 409、reject 零写入、MapSpec 盖章联动、原子性披露
- WP5：inspect_edit_constraints / get_impact / revalidate 工具 + REST 端点
  （用户与 Pi 同一链路）
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services.session_data import MemorySessionStore
from app.tools.feature_edit_tools import register_feature_edit_tools
from app.tools.registry import ToolRegistry

STEP = 0.0001  # ≈8.5m（lon@39.9N）


def lon(i: float) -> float:
    return 116.4 + i * STEP


def lat(j: float) -> float:
    return 39.9 + j * STEP


def ring(x0: float, y0: float, x1: float, y1: float) -> list:
    return [
        [lon(x0), lat(y0)],
        [lon(x1), lat(y0)],
        [lon(x1), lat(y1)],
        [lon(x0), lat(y1)],
        [lon(x0), lat(y0)],
    ]


PARCEL_A = {
    "type": "Feature",
    "id": "parcel-A",
    "geometry": {"type": "Polygon", "coordinates": [ring(0, 0, 2, 1)]},
    "properties": {"name": "A"},
}
PARCEL_B = {
    "type": "Feature",
    "id": "parcel-B",
    "geometry": {"type": "Polygon", "coordinates": [ring(2, 0, 3, 1)]},
    "properties": {"name": "B"},
}
PARCELS = {"type": "FeatureCollection", "features": [PARCEL_A, PARCEL_B]}

MOVE_SHARED_OP = {
    "op": "move_shared_vertex",
    "at": [lon(2), lat(0)],
    "dx_m": 5.0,
    "dy_m": 0.0,
}


def sid() -> str:
    return f"sess-shared-{uuid.uuid4().hex[:8]}"


# ── REST 事务链路夹具（与 test_routes 同款隔离模式） ────────────────────────

@pytest.fixture
def app():
    from app.api.routes import feature_edits as fe_routes

    application = FastAPI()
    application.include_router(fe_routes.router, prefix="/api/v1")
    return application


@pytest.fixture
async def client(app):
    from unittest.mock import MagicMock

    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def store():
    return MemorySessionStore()


@contextmanager
def _patched_store(store):
    from app.api.routes import feature_edits as fe_routes
    from app.services.feature_edit import executor as fe_executor
    from app.services.feature_edit import policy as fe_policy
    from app.services.feature_edit import service as fe_service

    with pytest.MonkeyPatch.context() as mp:
        for mod in (fe_routes, fe_executor, fe_policy, fe_service):
            mp.setattr(mod, "session_data_manager", store)
        yield


async def _seed(store, s: str, payload=None) -> str:
    """payload 为裸 FC 时包 envelope；已带 geojson 键的 envelope 原样入库。"""
    if isinstance(payload, dict) and "geojson" in payload:
        return await store.store(s, payload, prefix="data")
    return await store.store(s, {"geojson": payload or PARCELS}, prefix="data")


async def _preview(client, s: str, ref: str, ops, feature_ids=None) -> dict:
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-edits/preview",
        json={
            "target": {"mode": "explicit", "ref_id": ref, **({"feature_ids": feature_ids} if feature_ids else {})},
            "ops": ops,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestTransactionJourney:
    """WP4：真实 preview→approve→commit→undo/redo 原子事务。"""

    @pytest.mark.asyncio
    async def test_full_journey_atomic_commit_and_undo(self, app, client, store) -> None:
        with _patched_store(store):
            s = sid()
            ref = await _seed(store, s)
            plan = await _preview(client, s, ref, [MOVE_SHARED_OP], feature_ids=["parcel-A"])
            assert plan["committable"] is True and plan["safe"] is False
            # impact 投影经 preview summary 可见（用户卡片证据）
            assert plan["impact_summary"]["touched_features"] == 2
            assert plan["impact_summary"]["atomicity"]["cross_dataset"] == "blocked"
            assert plan["impact_summary"]["affected_neighbors"] >= 1

            resp = await client.post(
                f"/api/v1/chat/sessions/{s}/feature-edits/plans/{plan['plan_id']}/decision",
                json={"action": "approve"},
            )
            assert resp.status_code == 200, resp.text
            receipt = resp.json()["receipt"]
            assert receipt["kind"] == "apply" and len(receipt["changed_wfids"]) == 2

            payload = await store.get(s, ref)
            feats = payload["geojson"]["features"]
            # 原子性：一次提交两面同步（共享节点坐标一致；A 的共享顶点在环 1 位、B 在 0 位）
            orig = {"parcel-A": PARCEL_A["geometry"]["coordinates"], "parcel-B": PARCEL_B["geometry"]["coordinates"]}
            moved = [f for f in feats if f["geometry"]["coordinates"] != orig[f["id"]]]
            assert len(moved) == 2, f"两面均应同步更新，实际 {len(moved)}"
            a_ring = next(f["geometry"]["coordinates"][0] for f in moved if f["id"] == "parcel-A")
            b_ring = next(f["geometry"]["coordinates"][0] for f in moved if f["id"] == "parcel-B")
            assert a_ring[1] == b_ring[0], "两侧共享节点坐标精确一致"

            # undo（owner REST）：两面恢复原字节（_wfid 是一次性身份升级，
            # 不属于可撤销的编辑效果 —— ADR-0277 D2）
            resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-edits/{ref}/undo")
            assert resp.status_code == 200, resp.text
            payload_after_undo = await store.get(s, ref)

            def strip_wfid(features):
                return [
                    {**f, "properties": {k: v for k, v in f["properties"].items() if k != "_wfid"}}
                    for f in features
                ]

            assert strip_wfid(payload_after_undo["geojson"]["features"]) == PARCELS["features"], "undo 恢复真实字节"

            # redo 走同一事务语义
            resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-edits/{ref}/redo")
            assert resp.status_code == 200, resp.text
            payload_after_redo = await store.get(s, ref)
            assert payload_after_redo["geojson"]["features"] == payload["geojson"]["features"]

    @pytest.mark.asyncio
    async def test_double_approve_idempotent_replay(self, app, client, store) -> None:
        with _patched_store(store):
            s = sid()
            ref = await _seed(store, s)
            plan = await _preview(client, s, ref, [MOVE_SHARED_OP], feature_ids=["parcel-A"])
            key = f"idem-{plan['plan_id']}"
            first = await client.post(
                f"/api/v1/chat/sessions/{s}/feature-edits/plans/{plan['plan_id']}/decision",
                json={"action": "approve"}, headers={"Idempotency-Key": key},
            )
            assert first.status_code == 200
            payload_once = await store.get(s, ref)
            second = await client.post(
                f"/api/v1/chat/sessions/{s}/feature-edits/plans/{plan['plan_id']}/decision",
                json={"action": "approve"}, headers={"Idempotency-Key": key},
            )
            assert second.status_code == 200
            payload_twice = await store.get(s, ref)
            assert payload_once["geojson"]["features"] == payload_twice["geojson"]["features"], "重复 approve 零二次写入"

    @pytest.mark.asyncio
    async def test_stale_cas_conflict_on_concurrent_write(self, app, client, store) -> None:
        with _patched_store(store):
            s = sid()
            ref = await _seed(store, s)
            plan = await _preview(client, s, ref, [MOVE_SHARED_OP], feature_ids=["parcel-A"])
            # 预览后另一写方推进 revision（CAS expected_fingerprint 失配）
            stale = {"geojson": {"type": "FeatureCollection", "features": [PARCEL_A, PARCEL_B, {
                "type": "Feature", "id": "parcel-C",
                "geometry": {"type": "Polygon", "coordinates": [ring(3, 0, 4, 1)]},
                "properties": {"name": "C"},
            }]}}
            await store.overwrite(s, ref, stale)
            resp = await client.post(
                f"/api/v1/chat/sessions/{s}/feature-edits/plans/{plan['plan_id']}/decision",
                json={"action": "approve"},
            )
            assert resp.status_code == 409, resp.text
            body = resp.json()
            assert body["detail"]["code"] == "stale_revision"

    @pytest.mark.asyncio
    async def test_reject_writes_nothing(self, app, client, store) -> None:
        with _patched_store(store):
            s = sid()
            ref = await _seed(store, s)
            plan = await _preview(client, s, ref, [MOVE_SHARED_OP], feature_ids=["parcel-A"])
            resp = await client.post(
                f"/api/v1/chat/sessions/{s}/feature-edits/plans/{plan['plan_id']}/decision",
                json={"action": "reject"},
            )
            assert resp.status_code == 200
            payload = await store.get(s, ref)
            assert payload["geojson"]["features"] == PARCELS["features"], "拒绝后零写入"

    @pytest.mark.asyncio
    async def test_new_topology_violation_blocks_commit(self, app, client, store) -> None:
        with _patched_store(store):
            s = sid()
            # 线网场景验证基线/新增分离阻断（自触经声明规则而非面合法性拦截）：
            l1 = {"type": "Feature", "id": "road-1", "geometry": {"type": "LineString", "coordinates": [
                [lon(2), lat(5)], [lon(0), lat(5)], [lon(0), lat(7)], [lon(4), lat(7)], [lon(4), lat(5)], [lon(2.5), lat(5)],
            ]}, "properties": {}}
            l2 = {"type": "Feature", "id": "road-2", "geometry": {"type": "LineString", "coordinates": [
                [lon(2.5), lat(5)], [lon(2.5), lat(7)],
            ]}, "properties": {}}
            net = {"geojson": {"type": "FeatureCollection", "features": [l1, l2]},
                   "topology": {"rules": ["no_self_intersection"], "tolerance_m": 1.0}}
            ref2 = await _seed(store, s, net)
            plan = await _preview(client, s, ref2, [
                {"op": "move_shared_vertex", "at": [lon(2.5), lat(5)], "to": [lon(0), lat(5)]},
            ], feature_ids=["road-1"])
            assert plan["committable"] is False
            assert any(f["code"] == "topology_violation" for f in plan["validation"])
            ctx = plan.get("topology_context") or {}
            assert ctx.get("new_violations", 0) >= 1
            # 阻断 plan 不可提交：决策端点必须拒绝
            resp = await client.post(
                f"/api/v1/chat/sessions/{s}/feature-edits/plans/{plan['plan_id']}/decision",
                json={"action": "approve"},
            )
            assert resp.status_code in (400, 404, 409, 410), "违规 plan 不可被 approve 提交"


# ── WP5：工具面（Pi 同链） ──────────────────────────────────────────────────

@pytest.fixture
def reg():
    reg = ToolRegistry()
    register_feature_edit_tools(reg)
    return reg


class TestConstraintTools:
    @pytest.mark.asyncio
    async def test_inspect_edit_constraints_via_tool(self, reg) -> None:
        s = sid()
        ref = await session_store_seed(s)
        out = await reg.dispatch(
            "inspect_edit_constraints", {"ref_id": ref, "wfids": ["parcel-A"]}, session_id=s
        )
        assert out["success"] is True, out
        assert out["match_mode"] == "exact"
        assert out["shared_edges"] >= 1, "相邻地块共享边"
        assert out["indexed_features"] == 2
        neighbors = out.get("neighbors") or []
        assert any(n["wfid"] and n["relation"] in ("shares_edge", "incident_node") for n in neighbors)

    @pytest.mark.asyncio
    async def test_get_impact_via_tool(self, reg) -> None:
        s = sid()
        payload = {"geojson": PARCELS, "topology": {"rules": ["no_overlap"], "tolerance_m": 1.0}}
        ref = await session_store_seed(s, payload=payload)
        out = await reg.dispatch(
            "get_impact",
            {"ref_id": ref, "ops": [MOVE_SHARED_OP], "feature_ids": ["parcel-A"]},
            session_id=s,
        )
        assert out["success"] is True, out
        assert out["committable"] is True
        assert out["touched_features"] == 2
        assert out["atomicity"]["cross_dataset"] == "blocked"
        assert out["new_violations"] == 0
        assert out["existing_violations"] == 0
        measure = out.get("measure_delta") or {}
        assert measure.get("touched"), "逐要素测地量变化"

    @pytest.mark.asyncio
    async def test_revalidate_via_tool(self, reg) -> None:
        s = sid()
        ref = await session_store_seed(s)
        out = await reg.dispatch("revalidate", {"ref_id": ref}, session_id=s)
        assert out["success"] is True
        # 未声明规则 → 明确说明（declared-rules-only 纪律）
        assert out["rules"] == []
        assert "declared" in (out.get("note") or "")

        # 声明规则的数据集：revalidate 报基线
        ref2 = await session_store_seed(s, suffix="b", topology=True)
        out2 = await reg.dispatch("revalidate", {"ref_id": ref2}, session_id=f"{s}b")
        assert out2["success"] is True, out2
        assert out2["rules"] == ["no_overlap"]
        assert "baseline_total" in out2

    @pytest.mark.asyncio
    async def test_missing_session_typed_rejection(self, reg) -> None:
        out = await reg.dispatch("inspect_edit_constraints", {"ref_id": "ref:x"})
        assert out["success"] is False and out["code"] == "MISSING_SESSION"


async def session_store_seed(s: str, suffix: str = "", topology: bool = False, payload: dict | None = None):
    """工具路径用真实 session_data_manager（非 patch store）——独立命名空间会话。"""
    from app.services.session_data import session_data_manager

    if payload is None:
        payload = {"geojson": PARCELS}
        if topology:
            payload["topology"] = {"rules": ["no_overlap"], "tolerance_m": 1.0}
    return await session_data_manager.store(f"{s}{suffix}", payload, prefix="data")


# ── WP5：REST 只读端点 ──────────────────────────────────────────────────────

class TestConstraintRoutes:
    @pytest.mark.asyncio
    async def test_constraints_inspect_route(self, app, client, store) -> None:
        with _patched_store(store):
            s = sid()
            ref = await _seed(store, s)
            resp = await client.post(
                f"/api/v1/chat/sessions/{s}/feature-edits/{ref}/constraints/inspect",
                json={"wfids": ["parcel-A"]},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["success"] is True
            assert body["shared_edges"] >= 1
            assert any(n["wfid"] for n in (body.get("neighbors") or []))

    @pytest.mark.asyncio
    async def test_impact_route_does_not_store_plan(self, app, client, store) -> None:
        with _patched_store(store):
            s = sid()
            ref = await _seed(store, s)
            resp = await client.post(
                f"/api/v1/chat/sessions/{s}/feature-edits/{ref}/impact",
                json={"ops": [MOVE_SHARED_OP], "feature_ids": ["parcel-A"]},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["committable"] is True
            assert "plan_id" not in body, "纯读投影不落 plan store"
            assert body["touched_features"] == 2

    @pytest.mark.asyncio
    async def test_revalidate_route(self, app, client, store) -> None:
        with _patched_store(store):
            s = sid()
            ref = await _seed(store, s, {"geojson": PARCELS, "topology": {"rules": ["no_overlap"], "tolerance_m": 1.0}})
            resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-edits/{ref}/revalidate", json={})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["success"] is True
            assert body["rules"] == ["no_overlap"]
            assert "baseline_total" in body

    @pytest.mark.asyncio
    async def test_unknown_dataset_404(self, app, client, store) -> None:
        with _patched_store(store):
            s = sid()
            resp = await client.post(
                f"/api/v1/chat/sessions/{s}/feature-edits/ref:missing/impact",
                json={"ops": [MOVE_SHARED_OP]},
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_schema_rejects_unknown_op(self, app, client, store) -> None:
        with _patched_store(store):
            s = sid()
            ref = await _seed(store, s)
            resp = await client.post(
                f"/api/v1/chat/sessions/{s}/feature-edits/preview",
                json={"target": {"mode": "explicit", "ref_id": ref}, "ops": [{"op": "teleport_feature"}]},
            )
            assert resp.status_code == 422, "词表外 op schema 层拒绝"
