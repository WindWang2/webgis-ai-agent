"""G32 核心旅程测试 —— 真实持久层 + 真实 G14 提交链（P01/P02/P04/P28 的服务面）。

真实全局 session_data_manager / BlobStore / proposal_store（唯一 sid 隔离，
无打桩）；覆盖「保存提案 → 关闭重开（新 store 实例读同一文件）→ 差异/冲突
→ 用户批准 → merge → 真实数据更新 → 幂等重放」。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import feature_proposals as fp_routes
from app.services.session_data import session_data_manager

SCHOOLS = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": "s1", "geometry": {"type": "Point", "coordinates": [116.30, 39.90]},
         "properties": {"name": "第一小学", "type": "primary", "capacity": 600}},
        {"type": "Feature", "id": "s2", "geometry": {"type": "Point", "coordinates": [116.31, 39.91]},
         "properties": {"name": "第二小学", "type": "primary", "capacity": 450}},
        {"type": "Feature", "id": "s3", "geometry": {"type": "Point", "coordinates": [116.32, 39.92]},
         "properties": {"name": "实验中学", "type": "secondary", "capacity": 1200}},
    ],
}


def sid() -> str:
    return f"sess-g32-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(fp_routes.router, prefix="/api/v1")
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
def _isolated_store(tmp_path: Path, monkeypatch):
    """每个测试独立持久根（文件真相）+ 独立 BlobStore 根。"""
    from app.services.feature_proposals import store as fp_store
    from app.services.durable_blob_store import reset_filesystem_blob_store

    s = fp_store.ProposalStore(base_dir=tmp_path)
    monkeypatch.setattr(fp_routes.fp_service, "proposal_store", s)
    monkeypatch.setattr("app.services.feature_proposals.service.proposal_store", s)
    from app.services import project_artifact_promotion as pap

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path / "blobs")
    reset_filesystem_blob_store()
    yield s
    reset_filesystem_blob_store()


async def _seed_ref(s: str, payload=None) -> str:
    ref = await session_data_manager.store(s, payload or SCHOOLS, prefix="geojson")
    return ref


async def _immediate_edit(s: str, ref: str, ops: list, feature_ids: list | None = None) -> dict:
    """用 G14 即时编辑改变当前数据（用户另一编辑路径）。"""
    from app.services.feature_edit import service as fe_service

    plan, findings = await fe_service.build_and_store_plan(
        s, ref_id=ref, feature_ids=feature_ids, ops=ops,
    )
    assert plan.get("committable"), findings
    from app.services.feature_edit import executor as fe_executor

    return await fe_executor.commit_plan(
        s, plan, actor="user", origin="user", idempotency_key=f"test:{uuid.uuid4().hex[:8]}"
    )


# ── P01：保存 → 重启恢复 ─────────────────────────────────────────────────────


async def test_p01_create_reload_restore(client, _isolated_store, tmp_path):
    s = sid()
    ref = await _seed_ref(s)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
        "ref_id": ref,
        "title": "分类修正提案",
        "submit": True,
        "ops": [
            {"op": "set_fields", "wfids": ["s1"], "fields": {"type": "secondary"}},
            {"op": "set_fields", "wfids": ["s2"], "fields": {"capacity": 500}},
        ],
    })
    assert resp.status_code == 201, resp.text
    proposal = resp.json()
    pid = proposal["proposal_id"]
    assert proposal["status"] == "submitted"
    assert proposal["base"]["content_revision"] >= 0

    # 当前 ref 未被修改（提案不落数据）。
    payload_now = await session_data_manager.get(s, ref)
    feats = payload_now["geojson"]["features"] if "geojson" in payload_now else payload_now["features"]
    assert all(f["properties"].get("type") != "middle" for f in feats)
    # （type=secondary 是词表内既有值，不算未落标记 —— 用 capacity 验证未写）
    assert all(f["properties"].get("capacity") != 500 for f in feats)

    # 「重启服务」：新 store 实例读同一持久目录。
    from app.services.feature_proposals.store import ProposalStore

    fresh = ProposalStore(base_dir=tmp_path)
    restored = await fresh.get_proposal(s, pid)
    assert restored is not None
    assert restored.status == "submitted"
    assert restored.ops[0]["fields"] == {"type": "secondary"}
    assert restored.base.snapshot_key  # base 证据完整
    # 差异仍可重算。
    resp2 = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/diff")
    assert resp2.status_code == 200, resp2.text
    diff = resp2.json()
    assert diff["candidate_delta_count"] == 2
    assert diff["conflict_report"]["verdict"] == "clean"


# ── P28 主链：批准 → merge → 真实数据更新 + 回执 + 幂等 ────────────────────


async def test_p28_approve_merge_updates_real_data(client, _isolated_store):
    s = sid()
    ref = await _seed_ref(s)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
        "ref_id": ref, "submit": True,
        "ops": [
            {"op": "set_fields", "wfids": ["s1"], "fields": {"type": "secondary"}},
            {"op": "set_fields", "wfids": ["s2"], "fields": {"capacity": 500}},
        ],
    })
    assert resp.status_code == 201, resp.text
    pid = resp.json()["proposal_id"]

    # 用户批准（decision 端点，actor 服务端裁决为 user）。
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/decisions",
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["proposal"]["status"] == "approved"

    # merge → 真实提交。
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 200, resp.text
    merged = resp.json()
    assert merged["status"] == "merged"
    receipt = merged["receipt"]
    assert receipt["changed_wfids"], receipt
    assert receipt["origin"] == "feature_proposal"

    # 数据真实更新。
    payload_now = await session_data_manager.get(s, ref)
    feats = payload_now.get("geojson", payload_now)["features"]
    by_id = {f.get("id"): f["properties"] for f in feats}
    assert by_id["s1"]["type"] == "secondary"
    assert by_id["s2"]["capacity"] == 500
    # 未触及要素保持不变。
    assert by_id["s3"]["capacity"] == 1200

    # 重复 merge → 幂等重放持久回执（不重复改数据）。
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 200
    replay = resp.json()
    assert replay.get("already_merged") is True
    assert replay["receipt"]["entry_seq"] == receipt.get("entry_seq")

    # journal/undo 仍可用（G14 一致性）。
    from app.services.feature_edit import executor as fe_executor

    undo_receipt = await fe_executor.apply_undo(s, ref, actor="user", origin="user")
    assert undo_receipt["kind"] == "undo"
    payload_after_undo = await session_data_manager.get(s, ref)
    feats2 = payload_after_undo.get("geojson", payload_after_undo)["features"]
    by_id2 = {f.get("id"): f["properties"] for f in feats2}
    assert by_id2["s1"]["type"] == "primary"  # 撤销恢复


# ── P02/P04：冲突分类（独立 vs 冲突）────────────────────────────────────────


async def test_p02_independent_change_preserved(client, _isolated_store):
    s = sid()
    ref = await _seed_ref(s)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
        "ref_id": ref, "submit": True,
        "ops": [{"op": "set_fields", "wfids": ["s1"], "fields": {"type": "secondary"}}],
    })
    pid = resp.json()["proposal_id"]

    # 用户另改**另一个要素**（s3 的数值字段）→ 独立修改。
    await _immediate_edit(s, ref, [
        {"op": "set_fields", "fields": {"capacity": 1300}},
    ], feature_ids=["s3"])

    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/conflicts")
    report = resp.json()["conflict_report"]
    assert report["verdict"] == "clean"
    assert report["current_changed"] is True

    # rebase 保留双方后 approve+merge。
    current = await _isolated_store.get_proposal(s, pid)
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/rebase",
        json={"expected_revision": current.revision, "apply": True},
    )
    assert resp.status_code == 200, resp.text
    rebased_rev = resp.json()["proposal"]["revision"]
    assert rebased_rev == current.revision + 1
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/decisions",
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 200, resp.text

    payload_now = await session_data_manager.get(s, ref)
    props = {f.get("id"): f["properties"]
             for f in payload_now.get("geojson", payload_now)["features"]}
    assert props["s1"]["type"] == "secondary"     # 提案保留
    assert props["s3"]["capacity"] == 1300           # 用户修改保留


async def test_p04_same_field_conflict_blocks_merge(client, _isolated_store):
    s = sid()
    ref = await _seed_ref(s)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
        "ref_id": ref, "submit": True,
        "ops": [{"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 999}}],
    })
    pid = resp.json()["proposal_id"]

    # 用户把同一字段改成不同值。
    await _immediate_edit(s, ref, [
        {"op": "set_fields", "fields": {"capacity": 777}},
    ], feature_ids=["s1"])

    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/conflicts")
    report = resp.json()["conflict_report"]
    assert report["verdict"] == "conflicted"
    kinds = [c["kind"] for c in report["unresolved_conflicts"]]
    assert "field_value_conflict" in kinds
    conflict = next(c for c in report["unresolved_conflicts"] if c["kind"] == "field_value_conflict")
    assert conflict["current"] == {"presence": "present", "value": 777}
    assert conflict["proposed"] == {"presence": "present", "value": 999}

    # 未裁决不可批准。
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/decisions",
        json={"action": "approve"},
    )
    assert resp.status_code == 409
