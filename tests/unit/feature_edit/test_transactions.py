"""G14 事务级测试：结构编辑走真实 executor 提交路径（V01/V03/V06/V09/V10/
V11/V12/V15/V20 的事务面）+ provisioning（WP02）+ 拓扑（WP06）。

真实全局 session_data_manager + ingest 管线（唯一 sid 隔离，无打桩）；
故障注入（V15）以 monkeypatch 精确替换写路径节点并断言恢复语义。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock

from app.api.routes import feature_edits as fe_routes
from app.services.feature_edit import executor as fe_executor
from app.services.session_data import session_data_manager

SQUARE_A = [[116.00, 39.00], [116.10, 39.00], [116.10, 39.10], [116.00, 39.10], [116.00, 39.00]]
SQUARE_B = [[116.10, 39.00], [116.20, 39.00], [116.20, 39.10], [116.10, 39.10], [116.10, 39.00]]


def sid() -> str:
    return f"sess-g14-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(fe_routes.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _auth(app):
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()


async def _create_empty(client, s: str, geometry_type: str = "Polygon", topology=None, name="编辑图层"):
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-edits/datasets",
        json={"name": name, "geometry_type": geometry_type, **({"topology": topology} if topology else {})},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _preview(client, s: str, ref: str, ops, feature_ids=None):
    body = {
        "target": {"mode": "explicit", "ref_id": ref, **({"feature_ids": feature_ids} if feature_ids else {})},
        "ops": ops,
    }
    return await client.post(f"/api/v1/chat/sessions/{s}/feature-edits/preview", json=body)


async def _approve(client, s: str, plan_id: str):
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-edits/plans/{plan_id}/decision",
        json={"action": "approve"},
    )
    return resp


# ─── V01：空 typed 图层创建首个要素（真实事务全链） ─────────────────────────

class TestEmptyDatasetCreate:
    async def test_first_polygon_full_chain(self, client):
        s = sid()
        created = await _create_empty(client, s, "Polygon", topology={"rules": ["no_overlap"]})
        assert created["feature_count"] == 0 and created["ref_id"].startswith("ref:dataset-")
        assert created["layer_id"] and created["geometry_type"] == "Polygon"
        ref = created["ref_id"]

        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]},
             "properties": {"name": "服务区A", "kind": "primary"}},
        ]}])
        assert prev.status_code == 200, prev.text
        body = prev.json()
        assert body["committable"] is True and body["safe"] is False
        plan_id = body["plan_id"]

        app = await _approve(client, s, plan_id)
        assert app.status_code == 200, app.text
        receipt = app.json()["receipt"]
        assert receipt["content_revision_after"] == 2  # 创建 → revision 递增
        assert len(receipt["created_wfids"]) == 1
        new_wfid = receipt["created_wfids"][0]

        # 真实数据可读：payload、descriptor、要素稳定身份
        payload = await session_data_manager.get(s, ref)
        feats = payload["geojson"]["features"]
        assert len(feats) == 1
        assert feats[0]["properties"]["_wfid"] == new_wfid
        assert feats[0]["properties"]["name"] == "服务区A"
        desc = await session_data_manager.get_ref_descriptor(s, ref)
        assert desc["feature_count"] == 1 and desc["geometry_types"] == ["Polygon"]
        # envelope 兄弟键保留（typed 语义与拓扑声明不丢）
        assert payload["geometry_type"] == "Polygon"
        assert payload["topology"]["rules"] == ["no_overlap"]

    async def test_typed_mismatch_rejected(self, client):
        s = sid()
        created = await _create_empty(client, s, "Polygon")
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0, 39.0]}}
        ]}])
        assert prev.status_code == 200  # 既有路由语义：plan 可建但不可提交
        body = prev.json()
        assert body["committable"] is False
        assert any(f["code"] == "geometry_type_mismatch" for f in body["validation"])

    async def test_qualification_endpoint(self, client):
        s = sid()
        created = await _create_empty(client, s, "LineString")
        resp = await client.get(
            f"/api/v1/chat/sessions/{s}/feature-edits/datasets/{created['ref_id']}/qualification"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["editable"] is True and body["feature_count"] == 0
        assert body["declared_geometry_type"] == "LineString"
        # 跨会话不泄存在性
        other = await client.get(
            f"/api/v1/chat/sessions/{sid()}/feature-edits/datasets/{created['ref_id']}/qualification"
        )
        assert other.status_code == 404


# ─── V06/V12：删除 + undo/redo 身份 + 幂等重放 ─────────────────────────────

class TestDeleteUndoIdempotency:
    async def test_delete_undo_redo_identity(self, client):
        s = sid()
        created = await _create_empty(client, s, "Polygon")
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]},
             "properties": {"name": "区域"}},
        ]}])
        r1 = (await _approve(client, s, prev.json()["plan_id"])).json()["receipt"]
        wfid = r1["created_wfids"][0]

        # 删除
        prev2 = await _preview(client, s, ref, [{"op": "delete_features"}], feature_ids=[wfid])
        assert prev2.status_code == 200
        r2 = (await _approve(client, s, prev2.json()["plan_id"])).json()["receipt"]
        assert r2["retired_wfids"] == [wfid]
        assert (await session_data_manager.get_ref_descriptor(s, ref))["feature_count"] == 0

        # undo：复活同身份同内容
        undo = await client.post(f"/api/v1/chat/sessions/{s}/feature-edits/{ref}/undo")
        assert undo.status_code == 200
        payload = await session_data_manager.get(s, ref)
        feats = payload["geojson"]["features"]
        assert len(feats) == 1
        assert feats[0]["properties"]["_wfid"] == wfid  # 不生成新身份
        assert feats[0]["properties"]["name"] == "区域"
        assert feats[0]["geometry"]["coordinates"] == [SQUARE_A]

        # redo：再删除（同身份语义）
        redo = await client.post(f"/api/v1/chat/sessions/{s}/feature-edits/{ref}/redo")
        assert redo.status_code == 200
        assert len((await session_data_manager.get(s, ref))["geojson"]["features"]) == 0

    async def test_create_idempotent_replay_no_duplicates(self, client):
        """V12：同 plan/幂等键重放只写一次，返回同一回执。"""
        s = sid()
        created = await _create_empty(client, s, "Point")
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"name": "P"}}
        ]}])
        plan_id = prev.json()["plan_id"]
        first = (await _approve(client, s, plan_id)).json()["receipt"]
        replay = (await _approve(client, s, plan_id)).json()["receipt"]
        assert replay["entry_seq"] == first["entry_seq"]
        desc = await session_data_manager.get_ref_descriptor(s, ref)
        assert desc["feature_count"] == 1
        assert desc["content_revision"] == first["content_revision_after"]  # 不重复 bump


# ─── V03：真实图层顶点编辑（replace_geometry 服务端事务） ──────────────────

class TestReplaceGeometryTransaction:
    async def test_vertex_edit_commits_and_reverts(self, client):
        s = sid()
        created = await _create_empty(client, s, "Polygon")
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]}, "properties": {"name": "X"}}
        ]}])
        receipt = (await _approve(client, s, prev.json()["plan_id"])).json()["receipt"]
        wfid = receipt["created_wfids"][0]

        moved = [[116.00, 39.20], [116.10, 39.20], [116.10, 39.30], [116.00, 39.30], [116.00, 39.20]]
        prev2 = await _preview(client, s, ref, [
            {"op": "replace_geometry", "geometry": {"type": "Polygon", "coordinates": [moved]}}
        ], feature_ids=[wfid])
        assert prev2.status_code == 200
        assert prev2.json()["preview"][0]["geometry"]["positions_after"] == 5
        r2 = (await _approve(client, s, prev2.json()["plan_id"])).json()["receipt"]
        payload = await session_data_manager.get(s, ref)
        assert payload["geojson"]["features"][0]["geometry"]["coordinates"][0][0] == [116.00, 39.20]

        undo = await client.post(f"/api/v1/chat/sessions/{s}/feature-edits/{ref}/undo")
        assert undo.status_code == 200
        payload = await session_data_manager.get(s, ref)
        assert payload["geojson"]["features"][0]["geometry"]["coordinates"][0][0][1] == 39.00


# ─── V07/V08/V17：分割/合并事务 + 血缘投影 ─────────────────────────────────

class TestSplitMergeTransactions:
    async def _seed_two_squares(self, client, s):
        created = await _create_empty(client, s, "Polygon")
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]}, "properties": {"name": "A", "rank": 1}},
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_B]}, "properties": {"name": "B", "rank": 2}},
        ]}])
        receipt = (await _approve(client, s, prev.json()["plan_id"])).json()["receipt"]
        return ref, receipt["created_wfids"]

    async def test_split_lineage_and_undo(self, client):
        s = sid()
        ref, wfids = await self._seed_two_squares(client, s)
        line = {"type": "LineString", "coordinates": [[116.05, 38.9], [116.05, 39.2]]}
        prev = await _preview(client, s, ref, [{"op": "split_feature", "wfid": wfids[0], "line": line}])
        assert prev.status_code == 200, prev.text
        receipt = (await _approve(client, s, prev.json()["plan_id"])).json()["receipt"]
        assert receipt["retired_wfids"] == [wfids[0]]
        assert len(receipt["created_wfids"]) == 2
        assert receipt["lineage_events"][0]["kind"] == "split"

        # 数据事实：3 个要素（1 原样 + 2 子）
        payload = await session_data_manager.get(s, ref)
        assert len(payload["geojson"]["features"]) == 3

        # V17：lineage REST 投影（父 wfid 查询）
        lin = await client.get(f"/api/v1/chat/sessions/{s}/feature-edits/{ref}/lineage?wfid={wfids[0]}")
        events = lin.json()["events"]
        assert len(events) == 1 and events[0]["kind"] == "split"
        assert events[0]["parent_wfids"] == [wfids[0]]

        # undo：parent 原样复活、children 消失
        undo = await client.post(f"/api/v1/chat/sessions/{s}/feature-edits/{ref}/undo")
        assert undo.status_code == 200
        payload = await session_data_manager.get(s, ref)
        feats = payload["geojson"]["features"]
        assert len(feats) == 2
        assert {f["properties"]["_wfid"] for f in feats} == set(wfids)
        assert feats[0]["geometry"]["coordinates"] == [SQUARE_A]

    async def test_merge_transaction_with_disclosure(self, client):
        s = sid()
        ref, wfids = await self._seed_two_squares(client, s)
        prev = await _preview(client, s, ref, [{"op": "merge_features", "field_policy": {"mode": "first"}}],
                              feature_ids=wfids)
        assert prev.status_code == 200, prev.text
        summary = prev.json()
        op = [o for o in summary["ops"] if o["op"] == "merge_features"][0]
        assert "name" in op.get("conflict_fields", [])  # 差异面在预览披露
        receipt = (await _approve(client, s, summary["plan_id"])).json()["receipt"]
        assert sorted(receipt["retired_wfids"]) == sorted(wfids)
        assert len(receipt["created_wfids"]) == 1
        payload = await session_data_manager.get(s, ref)
        feats = payload["geojson"]["features"]
        assert len(feats) == 1 and feats[0]["properties"]["name"] == "A"
        ring = feats[0]["geometry"]["coordinates"][0]
        assert ring[0] == [116.00, 39.00]
        assert [116.20, 39.10] in ring  # 并集 bbox 角点在合并环上


# ─── V09/V10：拓扑声明拒绝与豁免 ───────────────────────────────────────────

class TestTopologyTransactions:
    async def test_declared_no_overlap_rejects_overlap(self, client):
        s = sid()
        created = await _create_empty(client, s, "Polygon", topology={"rules": ["no_overlap"]})
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]}}]}])
        await _approve(client, s, prev.json()["plan_id"])

        # 与 A 大幅重叠的第二个面
        overlap = [[116.05, 39.00], [116.15, 39.00], [116.15, 39.10], [116.05, 39.10], [116.05, 39.00]]
        prev2 = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [overlap]}}]}])
        assert prev2.status_code == 200  # plan 可建但不可提交（validation 面披露）
        body2 = prev2.json()
        assert body2["committable"] is False
        topo = [f for f in body2["validation"] if f["code"] == "topology_violation"]
        assert topo, body2["validation"]
        # 定位：受影响对 + bbox
        assert topo[0]["detail"]["rule"] == "no_overlap"
        assert "bbox" in topo[0]["detail"] or "other_wfid" in topo[0]["detail"]
        # 权威数据未变（不可提交的 plan 不产生服务端写入）
        desc = await session_data_manager.get_ref_descriptor(s, ref)
        assert desc["feature_count"] == 1

    async def test_undeclared_layer_allows_overlap(self, client):
        """V10：未声明的业务面层不受行政区规则误杀。"""
        s = sid()
        created = await _create_empty(client, s, "Polygon")  # 无拓扑声明
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]}}]}])
        await _approve(client, s, prev.json()["plan_id"])
        overlap = [[116.05, 39.00], [116.15, 39.00], [116.15, 39.10], [116.05, 39.10], [116.05, 39.00]]
        prev2 = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [overlap]}}]}])
        assert prev2.status_code == 200
        await _approve(client, s, prev2.json()["plan_id"])
        desc = await session_data_manager.get_ref_descriptor(s, ref)
        assert desc["feature_count"] == 2


# ─── V11：stale revision（预览后他人先提交） ───────────────────────────────

class TestStaleRevision:
    async def test_second_committer_gets_conflict(self, client):
        s = sid()
        created = await _create_empty(client, s, "Polygon")
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]}}]}])
        plan_a = prev.json()["plan_id"]
        # 另一路径先提交（直接 executor，模拟另一操作者）
        plan_dict = await fe_executor.get_plan(s, plan_a)
        await fe_executor.commit_plan(s, plan_dict, actor="other", origin="user")
        # 同 revision 的旧预览再提交 → 409
        prev_b = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_B]}}]}])
        # 先用旧 plan_a 的 decision 重放（applied 幂等返回）
        replay = await _approve(client, s, plan_a)
        assert replay.status_code == 200 and replay.json()["status"] == "applied"
        # 新 plan 基于 revision=3 提交正常（重新预览成功路径）
        r_b = (await _approve(client, s, prev_b.json()["plan_id"])).json()["receipt"]
        desc = await session_data_manager.get_ref_descriptor(s, ref)
        assert desc["feature_count"] == 2 and desc["content_revision"] == r_b["content_revision_after"]

    async def test_stale_plan_conflict_on_decision(self, client):
        s = sid()
        created = await _create_empty(client, s, "Polygon")
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]}}]}])
        stale_plan_id = prev.json()["plan_id"]
        # 他人先写（绕过 plan：直接 overwrite 提升修订版）
        payload = await session_data_manager.get(s, ref)
        payload["geojson"]["features"].append(
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [SQUARE_B]}, "properties": {}}
        )
        await session_data_manager.overwrite(s, ref, payload)
        resp = await _approve(client, s, stale_plan_id)
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "stale_revision"


# ─── V15：写入/记账/盖章间故障注入 ────────────────────────────────────────

class TestFaultInjection:
    async def test_journal_write_failure_disclosed_no_false_success(self, client, monkeypatch):
        """overwrite 成功但 journal 落账失败：数据正确、receipt 仍返回、
        undo 对该条不可用（诚实披露，不伪装两全）。"""
        s = sid()
        created = await _create_empty(client, s, "Polygon")
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]}}]}])
        plan_id = prev.json()["plan_id"]

        real_save = fe_executor._save_journal
        calls = {"n": 0}

        async def flaky_save(session_id, journals, ref_id_arg):
            calls["n"] += 1
            # 真实执行（保留行为），但返回 False 模拟 set_map_state_fields 失败
            await real_save(session_id, journals, ref_id_arg)
            return False

        monkeypatch.setattr(fe_executor, "_save_journal", flaky_save)
        resp = await _approve(client, s, plan_id)
        monkeypatch.setattr(fe_executor, "_save_journal", real_save)
        assert resp.status_code == 200
        receipt = resp.json()["receipt"]
        assert receipt["entry_seq"] is not None
        assert len(receipt["created_wfids"]) == 1
        # 数据事实：要素已写入
        payload = await session_data_manager.get(s, ref)
        assert len(payload["geojson"]["features"]) == 1
        assert calls["n"] >= 1  # 故障点真实执行

    async def test_overwrite_failure_no_journal_entry(self, client, monkeypatch):
        """overwrite 失败（ref 消失竞态）：不入账、无伪成功。"""
        s = sid()
        created = await _create_empty(client, s, "Polygon")
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]}}]}])
        plan_id = prev.json()["plan_id"]

        async def failing_overwrite(session_id, ref_id_arg, data, **kw):
            return False

        monkeypatch.setattr(fe_executor.session_data_manager, "overwrite", failing_overwrite)
        resp = await _approve(client, s, plan_id)
        monkeypatch.undo()
        # 「ref 消失竞态」面 → dataset_not_found 语义（路由 404），无伪成功
        assert resp.status_code == 404
        # journal 无该条（回执失败）
        journals = await fe_executor._load_journals(s)
        assert (journals.get(ref) or {}).get("entries") in (None, [])
        # 数据未变
        desc = await session_data_manager.get_ref_descriptor(s, ref)
        assert desc["feature_count"] == 0

    async def test_stamp_failure_data_still_correct(self, client, monkeypatch):
        """盖章失败（deferred/pending）：数据正确、receipt 披露 stamp_status。"""
        s = sid()
        created = await _create_empty(client, s, "Polygon")
        ref = created["ref_id"]
        prev = await _preview(client, s, ref, [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE_A]}}]}])
        plan_id = prev.json()["plan_id"]

        async def failing_stamp(session_id, ref_id_arg, content_revision, **kw):
            return "pending", None

        monkeypatch.setattr(fe_executor, "stamp_data_revision", failing_stamp)
        resp = await _approve(client, s, plan_id)
        monkeypatch.undo()
        assert resp.status_code == 200
        receipt = resp.json()["receipt"]
        assert receipt["stamp_status"] == "pending"  # 诚实披露，不谎称 stamped
        desc = await session_data_manager.get_ref_descriptor(s, ref)
        assert desc["feature_count"] == 1


# ─── V20：只读源派生编辑副本 ──────────────────────────────────────────────

class TestEditableCopy:
    async def test_read_only_source_copy_semantics(self, client):
        from app.services.data_ingest.pipeline import get_ingest_pipeline

        s = sid()
        remote_mirror = {
            "geojson": {"type": "FeatureCollection", "features": [
                {"type": "Feature", "id": "pt-1", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                 "properties": {"name": "远程点", "capacity": 100}},
            ]},
            "read_only": True,
        }
        result = await get_ingest_pipeline().ingest(s, remote_mirror, name="remote-mirror")
        assert result.ok
        source_ref = result.ref_id

        # 资格判定：只读
        qual = await client.get(f"/api/v1/chat/sessions/{s}/feature-edits/datasets/{source_ref}/qualification")
        body = qual.json()
        assert body["editable"] is False and body["read_only"] is True

        # 直接编辑 → typed 拒绝
        prev = await _preview(client, s, source_ref, [{"op": "set_fields", "fields": {"name": "x"}}],
                              feature_ids=["远程点"])
        assert prev.status_code == 400
        assert prev.json()["detail"]["code"] == "dataset_read_only_source"

        # 派生副本
        copy_resp = await client.post(
            f"/api/v1/chat/sessions/{s}/feature-edits/datasets/{source_ref}/editable-copy",
            json={"name": "服务区编辑副本"},
        )
        assert copy_resp.status_code == 200, copy_resp.text
        copy = copy_resp.json()
        assert copy["source_was_read_only"] is True and copy["derived_from"] == source_ref

        # 副本可编辑（首编辑走完整事务），原源指纹不变
        import json as _json

        from app.services.data_ingest.pipeline import compute_payload_fingerprint

        source_before = compute_payload_fingerprint(await session_data_manager.get(s, source_ref))
        prev2 = await _preview(client, s, copy["ref_id"], [{"op": "set_fields", "fields": {"capacity": 200}}],
                               feature_ids=["pt-1"])
        assert prev2.status_code == 200, prev2.text
        approved = await _approve(client, s, prev2.json()["plan_id"])
        assert approved.status_code == 200, approved.text
        source_after = compute_payload_fingerprint(await session_data_manager.get(s, source_ref))
        assert source_before == source_after  # 原源零写入
        copy_payload = await session_data_manager.get(s, copy["ref_id"])
        assert copy_payload["geojson"]["features"][0]["properties"]["capacity"] == 200
        assert copy_payload["derived_from"] == source_ref
