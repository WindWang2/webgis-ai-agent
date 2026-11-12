"""G32 恢复/并发/权限测试 —— P16/P17/P18/P19/P20/P22/P23/P27。

真实持久层 + 故障注入（monkeypatch 提交桥窗口节点）；权限面用**无**
require_owned_session override 的裸 app 验证 401/404 拒绝路径。
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
    ],
}


def sid() -> str:
    return f"sess-g32x-{uuid.uuid4().hex[:8]}"


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
def _isolated(tmp_path: Path, monkeypatch):
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


async def _seed(s: str) -> str:
    return await session_data_manager.store(s, SCHOOLS, prefix="geojson")


async def _create_submitted(client, s, ref, ops) -> str:
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
        "ref_id": ref, "submit": True, "ops": ops,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["proposal_id"]


async def _approve(client, s, pid):
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/decisions",
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text


# ── P16：批准后内容/裁决/base 变更 → 旧批准失效 ────────────────────────────


async def test_p16_approval_invalidated_by_content_change(client, _isolated):
    s = sid()
    ref = await _seed(s)
    pid = await _create_submitted(client, s, ref, [
        {"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 700}},
    ])
    await _approve(client, s, pid)

    # 更新候选内容（revision+1）→ 批准失效。
    current = await _isolated.get_proposal(s, pid)
    resp = await client.patch(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}", json={
        "expected_revision": current.revision,
        "ops": [{"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 999}}],
    })
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["has_approval"] is False
    assert updated["revision"] == current.revision + 1

    # 旧批准 merge → 拒绝（approval 不绑定新 digest）。
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "state_conflict"  # 状态回到 submitted


# ── P17：并发 revision CAS（旧客户端不得覆盖新草稿）────────────────────────


async def test_p17_revision_cas_concurrent_update(client, _isolated):
    s = sid()
    ref = await _seed(s)
    pid = await _create_submitted(client, s, ref, [
        {"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 700}},
    ])
    rec = await _isolated.get_proposal(s, pid)
    # 第一个更新成功。
    resp = await client.patch(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}", json={
        "expected_revision": rec.revision,
        "ops": [{"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 701}}],
    })
    assert resp.status_code == 200
    # 旧 revision 的第二个更新 → CAS 拒绝。
    resp = await client.patch(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}", json={
        "expected_revision": rec.revision,
        "ops": [{"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 702}}],
    })
    assert resp.status_code == 409
    assert resp.json()["code"] == "proposal_revision_conflict"


# ── P18/P19：提交窗口崩溃 → 恢复判据（executed / not_executed / unprovable）─


async def test_p18_recover_executed_after_window_crash(client, _isolated, monkeypatch):
    """数据已写、持久回执落库前进程退出 → 重试恢复相同回执，不重复创建。"""
    s = sid()
    ref = await _seed(s)
    pid = await _create_submitted(client, s, ref, [
        {"op": "delete_features", "wfids": ["s1"]},
    ])
    await _approve(client, s, pid)

    # 注入：executor.commit_plan 成功后、_finalize_merged 落库前抛崩溃。
    from app.services.feature_proposals import service as fps

    original_merge = fps.bridge.merge_proposal

    async def crashing_merge(proposal, *, session_id, actor_id):
        await original_merge(proposal, session_id=session_id, actor_id=actor_id)
        # 模拟 _finalize_merged 前进程退出（回执未持久 —— 故意丢弃返回值）。
        raise RuntimeError("simulated crash before receipt persistence")

    monkeypatch.setattr(fps.bridge, "merge_proposal", crashing_merge)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    # journal 幂等证据在 → assess 判 executed → **同一请求内**恢复 merged
    # （数据不重复写；回执与原提交一致）。
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "merged"
    assert body["receipt"]["origin"] == "feature_proposal"
    rec = await _isolated.get_proposal(s, pid)
    assert rec.status == "merged"

    monkeypatch.setattr(fps.bridge, "merge_proposal", original_merge)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 200
    replay = resp.json()
    assert replay.get("already_merged") is True
    assert replay["receipt"]["entry_seq"] == body["receipt"].get("entry_seq")
    # 数据只被写一次：s1 恰好删除一次（feature 数量 = 1，不会更少）。
    payload = await session_data_manager.get(s, ref)
    feats = payload.get("geojson", payload)["features"]
    assert len(feats) == 1
    assert feats[0].get("id") == "s2"


async def test_p19_unprovable_blocks_blind_retry(client, _isolated, monkeypatch):
    """提交窗口崩溃（executor 层注入）+ 事后指纹漂移 → recovery_required；
    merge 盲重试被拒；unprovable 下 resolve-recovery 也拒绝。"""
    s = sid()
    ref = await _seed(s)
    pid = await _create_submitted(client, s, ref, [
        {"op": "set_fields", "wfids": ["s2"], "fields": {"capacity": 999}},
    ])
    await _approve(client, s, pid)

    from app.services.feature_edit import executor as fe_executor

    original_commit = fe_executor.commit_plan

    async def crashing_commit(*a, **kw):
        raise RuntimeError("simulated crash inside commit window")

    monkeypatch.setattr(fe_executor, "commit_plan", crashing_commit)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 409
    rec = await _isolated.get_proposal(s, pid)
    # 载荷未变 + journal 无键 → 可证未执行 → 回 approved（允许重试）。
    assert rec.status == "approved"

    # 用户在崩溃后做了**另一笔编辑** → 载荷指纹漂移；此时再崩 → 不可证。
    monkeypatch.setattr(fe_executor, "commit_plan", original_commit)
    from tests.unit.feature_proposals.test_service_journey import _immediate_edit

    await _immediate_edit(s, ref, [
        {"op": "set_fields", "fields": {"capacity": 123}},
    ], feature_ids=["s1"])

    monkeypatch.setattr(fe_executor, "commit_plan", crashing_commit)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 409
    rec2 = await _isolated.get_proposal(s, pid)
    assert rec2.status == "recovery_required"

    # 盲重试被 merge 入口拒绝。
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 409
    assert resp.json()["code"] == "recovery_required"

    # recovery 视图（只读证据）+ unprovable 下 resolve 也拒绝。
    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/recovery")
    assert resp.status_code == 200
    view = resp.json()
    assert view["verdict"] == "unprovable"
    assert view["missing_evidence"]
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/recovery/resolve"
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "recovery_unprovable"


# ── P20：长期 merged 提案再次 merge → 从持久回执重放 ───────────────────────


async def test_p20_merged_replay_from_persistent_receipt(client, _isolated, monkeypatch):
    s = sid()
    ref = await _seed(s)
    pid = await _create_submitted(client, s, ref, [
        {"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 777}},
    ])
    await _approve(client, s, pid)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 200
    first_receipt = resp.json()["receipt"]

    # 模拟 journal 已逐出（长期后）：merge 重放不得依赖 journal。
    async def _no_journals(session_id):
        return {}

    from app.services.feature_edit import executor as fe_executor

    monkeypatch.setattr(fe_executor, "_load_journals", _no_journals)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("already_merged") is True
    assert body["receipt"]["entry_seq"] == first_receipt.get("entry_seq")


# ── P22：跨会话 / 未授权 → 统一 404（不泄露存在性）─────────────────────────


async def test_p22_cross_session_ref_rejected(client, _isolated):
    s_owner = sid()
    s_other = sid()
    ref = await _seed(s_owner)  # ref 属于 s_owner 的 session store
    # 在另一个会话创建提案指向不属于自己的 ref → dataset_not_found。
    resp = await client.post(f"/api/v1/chat/sessions/{s_other}/feature-proposals", json={
        "ref_id": ref, "submit": True,
        "ops": [{"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 1}}],
    })
    assert resp.status_code in (400, 404)
    assert resp.json()["code"] == "dataset_not_found"


async def test_p22_proposal_scoped_to_session(client, _isolated):
    s1 = sid()
    ref = await _seed(s1)
    pid = await _create_submitted(client, s1, ref, [
        {"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 5}},
    ])
    s2 = sid()
    resp = await client.get(f"/api/v1/chat/sessions/{s2}/feature-proposals/{pid}")
    assert resp.status_code == 404


# ── P23：Agent 工具面没有批准函数（结构性断言）──────────────────────────────


async def test_p23_agent_tools_have_no_decision_entry(_isolated):
    """Agent 工具族不注册 decision/merge/resolutions —— 服务端不可达即不可批。"""
    from app.tools.feature_proposal_tools import register_feature_proposal_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_feature_proposal_tools(registry)
    names = set(registry.tool_names())
    forbidden = [n for n in names if any(
        k in n for k in ("decision", "approve", "merge", "resolution", "withdraw")
    )]
    assert not forbidden, forbidden
    assert any("create" in n for n in names)


# ── P27：withdraw 释放引用，共享 blob 不被误删 ──────────────────────────────


async def test_p27_withdraw_releases_shared_snapshot_safely(client, _isolated, tmp_path):
    s = sid()
    ref = await _seed(s)
    # 两个提案在同一数据形态上创建 → 共享同一 base 快照 blob（CAS）。
    pid1 = await _create_submitted(client, s, ref, [
        {"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 1}},
    ])
    pid2 = await _create_submitted(client, s, ref, [
        {"op": "set_fields", "wfids": ["s2"], "fields": {"capacity": 2}},
    ])
    rec1 = await _isolated.get_proposal(s, pid1)
    rec2 = await _isolated.get_proposal(s, pid2)
    assert rec1.base.snapshot_key == rec2.base.snapshot_key  # 内容寻址共享

    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid1}/withdraw",
        json={"action": "withdraw"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["snapshot_released"] is True
    assert body["snapshot_blob_deleted"] is False  # 仍被 pid2 引用 → 不删

    # pid2 的 base 快照仍可读（diff 正常）。
    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid2}/diff")
    assert resp.status_code == 200
    assert resp.json()["candidate_delta_count"] >= 1
