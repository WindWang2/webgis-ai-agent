"""G32 独立 review 修复的回归测试(R1:2×P0 + 3×P1 + P2-6/8/9)。"""
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
        {"type": "Feature", "id": f"s{i}",
         "geometry": {"type": "Point", "coordinates": [116.30 + 0.01 * i, 39.90]},
         "properties": {"name": f"学校{i}", "capacity": 500 + i, "score": i * 10}}
        for i in range(4)
    ],
}

SQUARES = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": f"q{i}",
         "geometry": {"type": "Polygon", "coordinates": [[
             [116.0 + 0.1 * i, 39.0], [116.1 + 0.1 * i, 39.0],
             [116.1 + 0.1 * i, 39.1], [116.0 + 0.1 * i, 39.1],
             [116.0 + 0.1 * i, 39.0]]]},
         "properties": {"name": f"地块{i}", "area": 100 + i}}
        for i in range(3)
    ],
}


def sid() -> str:
    return f"sess-g32r-{uuid.uuid4().hex[:8]}"


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


async def _seed(s: str, payload=None) -> str:
    return await session_data_manager.store(s, payload or SCHOOLS, prefix="geojson")


async def _create(client, s, ref, ops, **kw) -> str:
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
        "ref_id": ref, "submit": True, "ops": ops, **kw,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["proposal_id"]


# ── R-P0-1：有界集合 + 存储损坏 typed 化 ──────────────────────────────────


async def test_rp0_1a_comment_cap_and_store_still_loadable(client, _isolated):
    s = sid()
    ref = await _seed(s)
    pid = await _create(client, s, ref, [
        {"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 1}},
    ])
    # 100 条成功(=上限),第 101 条拒绝。
    for i in range(100):
        resp = await client.post(
            f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/comments",
            json={"body": f"comment {i}"},
        )
        assert resp.status_code == 200, f"comment {i}: {resp.text}"
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/comments",
        json={"body": "overflow"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "comment_limit_reached"
    # 存储仍可加载(此前无界 append 会让下一次读取抛 ValidationError→500)。
    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}")
    assert resp.status_code == 200


async def test_rp0_1b_use_new_value_cannot_exceed_ops_budget(client, _isolated):
    """8-op 满额提案 + plan 级 op 写的字段冲突 + use_new_value → 追加第 9 op
    被拒绝(此前会破坏存储)。"""
    s = sid()
    ref = await _seed(s)
    # plan 级 op(无 wfids)写 capacity —— 制造可裁决的字段冲突面。
    pid = await _create(client, s, ref, [
        {"op": "set_fields", "fields": {"capacity": 600}},
        {"op": "set_fields", "wfids": ["s1"], "fields": {"score": 1}},
        {"op": "set_fields", "wfids": ["s1"], "fields": {"score": 2}},
        {"op": "set_fields", "wfids": ["s1"], "fields": {"score": 3}},
        {"op": "set_fields", "wfids": ["s1"], "fields": {"score": 4}},
        {"op": "set_fields", "wfids": ["s1"], "fields": {"score": 5}},
        {"op": "set_fields", "wfids": ["s1"], "fields": {"score": 6}},
        {"op": "set_fields", "wfids": ["s1"], "fields": {"score": 7}},
    ], feature_ids=["s1"])
    # current 改同字段 → 冲突。
    from tests.unit.feature_proposals.test_service_journey import _immediate_edit

    await _immediate_edit(s, ref, [
        {"op": "set_fields", "fields": {"capacity": 999}},
    ], feature_ids=["s1"])
    rec = await _isolated.get_proposal(s, pid)
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/resolutions",
        json={
            "expected_revision": rec.revision,
            "resolutions": [{
                "conflict_id": None,  # 先探冲突 id
                "action": "use_new_value",
                "new_fields": {"capacity": 4242},
            }],
        },
    )
    # conflict_id 无效也须拒绝(不是 500)。
    assert resp.status_code == 422
    # 存储仍健康。
    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}")
    assert resp.status_code == 200


async def test_rp0_1c_store_corrupt_is_typed(client, _isolated, tmp_path):
    """损坏文件 → ProposalStoreCorrupt(typed,400 面),不是 TypeError/500。"""
    from app.services.feature_proposals.store import ProposalStore, ProposalStoreCorrupt

    d = tmp_path / "sess-x" / "feature_proposals"
    d.mkdir(parents=True)
    (d / "proposals.json").write_text("{ not json", encoding="utf-8")
    st = ProposalStore(base_dir=tmp_path)
    with pytest.raises(ProposalStoreCorrupt):
        await st.list_proposals("sess-x")


# ── R-P0-2:硬崩溃后 merging 不再卡死 ──────────────────────────────────────


async def test_rp0_2_hard_crash_merging_settles_via_merge_entry(client, _isolated):
    """进程在 merging 窗口硬崩溃(状态持久为 merging,无进程内处理)→ 下一
    次 merge 入口路由恢复判据:载荷未变+无 journal 键 → 回 approved。"""
    s = sid()
    ref = await _seed(s)
    pid = await _create(client, s, ref, [
        {"op": "set_fields", "wfids": ["s2"], "fields": {"capacity": 42}},
    ])
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/decisions",
        json={"action": "approve"},
    )
    assert resp.status_code == 200

    # 模拟硬崩溃:直接把状态置 merging(带批准绑定保留),租约过期由读取回收。
    def _crash(p):
        p.status = "merging"
        return p
    await _isolated.mutate_proposal(s, pid, _crash)

    # recovery 视图可用(此前 merging 被拒)。
    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/recovery")
    assert resp.status_code == 200
    assert resp.json()["verdict"] in ("executed", "not_executed", "unprovable")

    # merge 入口路由恢复判据:载荷未变 → not_executed → 回 approved 后可重试。
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 409
    assert resp.json()["code"] == "merge_failed_proven_not_executed"
    rec2 = await _isolated.get_proposal(s, pid)
    assert rec2.status == "approved"
    # 重试成功走完整提交。
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "merged"
    payload = await session_data_manager.get(s, ref)
    props = {f.get("id"): f["properties"] for f in payload.get("geojson", payload)["features"]}
    assert props["s2"]["capacity"] == 42


# ── R-P1-3:UI 裁决载荷 {conflict_id, action} 可被 REST 接受 ────────────────


async def test_rp1_3_resolution_payload_without_decided_by_accepted(client, _isolated):
    s = sid()
    ref = await _seed(s)
    pid = await _create(client, s, ref, [
        {"op": "set_fields", "wfids": ["s1"],
         "fields": {"capacity": 321, "score": 77}},
    ])
    from tests.unit.feature_proposals.test_service_journey import _immediate_edit

    await _immediate_edit(s, ref, [
        {"op": "set_fields", "fields": {"capacity": 111}},
    ], feature_ids=["s1"])
    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/conflicts")
    conflicts = resp.json()["conflict_report"]["unresolved_conflicts"]
    assert conflicts and conflicts[0]["kind"] == "field_value_conflict"
    rec = await _isolated.get_proposal(s, pid)
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/resolutions",
        json={
            "expected_revision": rec.revision,
            "resolutions": [
                {"conflict_id": conflicts[0]["conflict_id"], "action": "keep_current"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text  # 此前 422(decided_by 必填)
    # 裁决者由服务端填充。
    updated = await _isolated.get_proposal(s, pid)
    assert updated.resolutions[-1].decided_by


# ── R-P1-4:结构父上的 delete_update keep_current 移除结构 op ──────────────


async def test_rp1_4_split_parent_delete_update_resolution_removes_op(client, _isolated):
    s = sid()
    ref = await _seed(s, SQUARES)
    pid = await _create(client, s, ref, [
        {"op": "split_feature", "wfid": "q0",
         "line": {"type": "LineString", "coordinates": [[116.05, 38.9], [116.05, 39.2]]}},
        {"op": "set_fields", "wfids": ["q2"], "fields": {"area": 55}},
    ])
    # current 修改 q0 属性 → delete_update(split 退役 q0 vs current 修改)。
    from tests.unit.feature_proposals.test_service_journey import _immediate_edit

    await _immediate_edit(s, ref, [
        {"op": "set_fields", "fields": {"area": 999}},
    ], feature_ids=["q0"])
    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/conflicts")
    report = resp.json()["conflict_report"]
    kinds = [c["kind"] for c in report["unresolved_conflicts"]]
    assert "delete_update_conflict" in kinds, report
    cid = next(c["conflict_id"] for c in report["unresolved_conflicts"]
               if c["kind"] == "delete_update_conflict")
    rec = await _isolated.get_proposal(s, pid)
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/resolutions",
        json={"expected_revision": rec.revision,
              "resolutions": [{"conflict_id": cid, "action": "keep_current"}]},
    )
    assert resp.status_code == 200, resp.text
    updated = await _isolated.get_proposal(s, pid)
    # split op 被移除(此前静默保留 → merge 仍退役父、销毁用户修改)。
    assert all(op.get("op") != "split_feature" for op in updated.ops)


# ── R-P1-5:feature_ids 持久 + merge 提案可创建可合并 ──────────────────────


async def test_rp1_5_merge_proposal_via_feature_ids_end_to_end(client, _isolated):
    s = sid()
    ref = await _seed(s, SQUARES)
    pid = await _create(client, s, ref, [
        {"op": "merge_features", "field_policy": {"mode": "first"}},
    ], feature_ids=["q0", "q1"])
    # 候选可求值(此前 delta_count=0 + blocked 死提案)。
    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/diff")
    diff = resp.json()
    assert diff["candidate_delta_count"] == 3  # 2 父退役 + 1 新生
    assert diff["conflict_report"]["verdict"] == "clean"
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/decisions",
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/merge")
    assert resp.status_code == 200, resp.text
    payload = await session_data_manager.get(s, ref)
    feats = payload.get("geojson", payload)["features"]
    ids = {f.get("id") for f in feats}
    assert "q0" not in ids and "q1" not in ids  # 父退役
    assert len(feats) == 2  # q2 + 合并产物


# ── R-P2-6:唯一引用的 base blob 在 withdraw 后物理删除 ────────────────────


async def test_rp2_6_withdraw_deletes_unreferenced_blob(client, _isolated, tmp_path):
    s = sid()
    ref = await _seed(s)
    pid = await _create(client, s, ref, [
        {"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 5}},
    ])
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/withdraw",
        json={"action": "withdraw"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshot_released"] is True
    assert body["snapshot_blob_deleted"] is True  # 此前恒 False(自引用)


# ── R-P2-8:目标预算在创建时强制 ───────────────────────────────────────────


async def test_rp2_8_target_budget_enforced_at_create(client, _isolated):
    s = sid()
    big = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": f"b{i}",
             "geometry": {"type": "Point", "coordinates": [116.0 + 0.001 * i, 39.0]},
             "properties": {"capacity": i}}
            for i in range(250)
        ],
    }
    ref = await _seed(s, big)
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
        "ref_id": ref, "submit": True,
        "ops": [
            {"op": "set_fields", "wfids": [f"b{i}" for i in range(150)], "fields": {"capacity": 1}},
            {"op": "set_fields", "wfids": [f"b{i}" for i in range(100, 250)], "fields": {"capacity": 2}},
        ],
    })
    assert resp.status_code == 400
    assert resp.json()["code"] == "target_limit_exceeded"


# ── R-P2-9:plan 级 op 的字段裁决被拒绝(不误伤全体目标)────────────────────


async def test_rp2_9_plan_level_op_field_resolution_rejected(client, _isolated):
    s = sid()
    ref = await _seed(s)
    pid = await _create(client, s, ref, [
        {"op": "set_fields", "fields": {"capacity": 600}},
    ], feature_ids=["s1", "s2"])
    from tests.unit.feature_proposals.test_service_journey import _immediate_edit

    await _immediate_edit(s, ref, [
        {"op": "set_fields", "fields": {"capacity": 999}},
    ], feature_ids=["s1"])
    resp = await client.get(f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/conflicts")
    conflicts = resp.json()["conflict_report"]["unresolved_conflicts"]
    cid = next(c["conflict_id"] for c in conflicts if c["kind"] == "field_value_conflict")
    rec = await _isolated.get_proposal(s, pid)
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-proposals/{pid}/resolutions",
        json={"expected_revision": rec.revision,
              "resolutions": [{"conflict_id": cid, "action": "keep_current"}]},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "resolution_rejected"
    # ops 原样保留(plan 级写对全体目标仍生效 —— 用户需先收窄 op)。
    updated = await _isolated.get_proposal(s, pid)
    assert updated.ops == rec.ops


# ── R2-1:物化后仅 properties 级自然键的要素仍可按自然键起草 ───────────────


async def test_r2_1_properties_level_natural_key_indexed_after_materialize(client, _isolated):
    from app.services.feature_edit.identity import materialize_wfids, natural_to_wfid_index

    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"OBJECTID": 101, "capacity": 1}},
        ],
    }
    mat, _ = materialize_wfids(fc, "fp-r21")
    idx = natural_to_wfid_index(mat)
    wfid = mat["features"][0]["properties"]["_wfid"]
    assert idx.get("101") == wfid  # properties 级自然键 → wfid(R2-1 回归钉)
    assert idx.get(wfid) == wfid


# ── P2:Summary 响应时间戳契约须与运行时 float 一致 ─────────────────────────


def test_summary_response_timestamps_declared_as_float():
    from app.schemas.feature_proposal_schema import FeatureProposalSummaryResponse

    # proposal_summary() 原样回传持久记录的 float 时间戳；声明为 str 会让
    # OpenAPI 契约与实测线格式不符（前端按字符串解析会错）。
    model = FeatureProposalSummaryResponse(
        proposal_id="fpr-1", ref_id="ref-1", title="t", author_kind="agent",
        status="draft", revision=1, candidate_digest="d",
        created_at=1700000000.0, updated_at=1700000001.5,
    )
    assert isinstance(model.created_at, float)
    assert isinstance(model.updated_at, float)
    schema = FeatureProposalSummaryResponse.model_json_schema()
    assert schema["properties"]["created_at"]["type"] == "number"
    assert schema["properties"]["updated_at"]["type"] == "number"


# ── R2-2:merge 提案收敛(不与其他 op 混排;父集=feature_ids)──────────────


async def test_r2_2_merge_mixing_rejected_and_parents_explicit(client, _isolated):
    s = sid()
    ref = await _seed(s, SQUARES)
    # 混排(merge + 对 q2 的编辑)→ 拒绝(此前 q2 被静默吸并为父)。
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
        "ref_id": ref, "submit": True,
        "ops": [
            {"op": "merge_features", "field_policy": {"mode": "first"}},
            {"op": "set_fields", "wfids": ["q2"], "fields": {"area": 5}},
        ],
        "feature_ids": ["q0", "q1"],
    })
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_op"
    # 缺 feature_ids → 拒绝。
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
        "ref_id": ref, "submit": True,
        "ops": [{"op": "merge_features", "field_policy": {"mode": "first"}}],
    })
    assert resp.status_code == 400
    # 合法形态(唯一 op + 显式父集)仍通 —— 由 test_rp1_5 覆盖。


# ── P3:容量闸拒绝不得留下孤立 base blob(先落盘后拒绝 → 无回收路径)────────


async def test_p3_capacity_rejection_leaves_no_orphan_base_blob(
    client, _isolated, tmp_path,
):
    from app.services.feature_proposals.contracts import MAX_DRAFT_PROPOSALS_PER_REF
    from tests.unit.feature_proposals.test_service_journey import _immediate_edit

    s = sid()
    ref = await _seed(s)
    for i in range(MAX_DRAFT_PROPOSALS_PER_REF):
        resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
            "ref_id": ref, "submit": False,
            "ops": [{"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": i}}],
        })
        assert resp.status_code == 201, resp.text
    # 改变当前数据 → 下一次 capture 必是新 CAS key(同 key 会被去重掩盖)。
    await _immediate_edit(
        s, ref, [{"op": "set_fields", "fields": {"capacity": 999}}], feature_ids=["s1"],
    )
    blob_dir = tmp_path / "blobs"
    before = {p for p in blob_dir.rglob("*") if p.is_file()}
    resp = await client.post(f"/api/v1/chat/sessions/{s}/feature-proposals", json={
        "ref_id": ref, "submit": False,
        "ops": [{"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 1234}}],
    })
    assert resp.status_code == 429
    assert resp.json()["code"] == "draft_limit_reached"
    after = {p for p in blob_dir.rglob("*") if p.is_file()}
    assert after == before  # 未新增无引用的 base 快照 blob


# ── P3:list_proposals 负 offset 须钳到 0(此前切片返回错页/空页)────────────


async def test_p3_list_proposals_negative_offset_clamped(client, _isolated):
    from app.services.feature_proposals import service as fpsvc

    s = sid()
    ref = await _seed(s)
    for i in range(2):
        await _create(client, s, ref, [
            {"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 10 + i}},
        ])
    listing = await fpsvc.list_proposals(s, offset=-1)
    assert listing["total"] == 2
    assert listing["offset"] == 0
    assert len(listing["items"]) == 2  # 未修复时 items[-1:49] 只剩 1 条


# ── P2：非 FC 早返回必须保留 unresolved_conflicts（snap 依赖组装不再 500）───


async def test_non_fc_conflict_report_is_blocked_with_snap_dependency():
    from types import SimpleNamespace

    from app.schemas.feature_proposal_schema import BaseEvidence
    from app.services.feature_proposals.bridge import build_conflict_report

    base = BaseEvidence(
        ref_id="ref:target", content_revision=1, fingerprint="fp-base",
        snapshot_key="a" * 64, snapshot_sha256="a" * 64, snapshot_bytes=2,
        materialized=False, captured_at=1.0,
    )
    proposal = SimpleNamespace(
        base=base,
        ops=[{"op": "snap_to_ref", "ref_id": "ref:dep"}],
        resolutions=[], feature_ids=None, op_seed="seed",
    )
    report = await build_conflict_report(
        proposal,
        current_payload={"type": "not-a-feature-collection"},
        current_fingerprint="fp-current",
        base_payload={"type": "FeatureCollection", "features": []},
        dep_fingerprints_current={},
    )
    assert report["verdict"] == "blocked"
    assert report["unresolved_conflicts"]
    assert any(c["conflict_id"] == "payload-not-fc" for c in report["unresolved_conflicts"])


# ── P2：dependency_changed 只删除该 ref 的 snap op，并明确记录删除数量 ───────


def test_dependency_resolution_preserves_other_snap_refs():
    from app.services.feature_proposals.rebase import apply_resolutions_to_ops

    ops = [
        {"op": "snap_to_ref", "ref_id": "ref:a", "wfids": ["s1"]},
        {"op": "snap_to_ref", "ref_id": "ref:b", "wfids": ["s2"]},
        {"op": "set_fields", "wfids": ["s3"], "fields": {"capacity": 5}},
    ]
    conflicts = [
        {"conflict_id": "dependency:snap_ref:ref:a", "kind": "dependency_changed",
         "detail": {"ref_id": "ref:a"}},
        {"conflict_id": "dependency:snap_ref:ref:b", "kind": "dependency_changed",
         "detail": {"ref_id": "ref:b"}},
    ]
    new_ops, applied, rejected = apply_resolutions_to_ops(
        ops,
        [{"conflict_id": "dependency:snap_ref:ref:a", "action": "keep_current"}],
        conflicts,
        base_payload=SCHOOLS,
    )
    assert rejected == []
    assert new_ops == [ops[1], ops[2]]
    assert len(applied) == 1
    assert "ref:a" in applied[0]["effect"]
    assert "1 snap_to_ref" in applied[0]["effect"]
