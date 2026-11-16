"""G44 执行链测试 —— 真实 session store + sqlite 合同 DB + 真实 G14 写链。

覆盖：F01/F02/F03（行为分层）、F11（并发唯一键）、F12（合同变化使 plan 过期）、
F13（评估后数据变化阻断激活）、F15（只读源资格）、F17（违规分页）、
F18（create 必填缺失无残留）、F19（继承重复业务键整案拒绝）、F21（rename 阻断）、
F22（G32 批准后合同收紧 → merge 阻断）、F23（undo 恢复态门禁）、F31（取消收据不可激活）。
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from app.services.feature_edit import executor as fe_executor
from app.services.feature_edit import service as fe_service
from app.services.session_data import MemorySessionStore

FACILITIES = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": f"fac-{i}",
            "geometry": {"type": "Point", "coordinates": [116.0 + 0.01 * i, 39.0]},
            "properties": {
                "asset_no": f"A-{i:03d}",
                "status": "planned" if i % 2 == 0 else "active",
                "capacity": 10.0 + i,
            },
        }
        for i in range(4)
    ],
}

CONTRACT_DOC = {
    "version": 1,
    "title": "设施资产合同",
    "fields": [
        {"field": "asset_no", "type": "string", "required": True,
         "min_length": 3, "max_length": 16},
        {"field": "status", "type": "string",
         "enum": ["planned", "active", "retired"]},   # retired：观测词表外的声明值
        {"field": "capacity", "type": "number", "min": 0},
        {"field": "retired_reason", "type": "string", "nullable": True},
    ],
    "cross_rules": [
        {"rule_id": "rr", "kind": "conditional_required",
         "when": {"field": "status", "op": "eq", "value": "retired"},
         "require": ["retired_reason"]},
        {"rule_id": "uniq_asset", "kind": "unique", "fields": ["asset_no"]},
    ],
}


def sid() -> str:
    return f"sess-g44-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _storage(tmp_path: Path, monkeypatch):
    store = MemorySessionStore()
    import app.services.session_data as sd

    monkeypatch.setattr(sd, "session_data_manager", store)
    for mod in (fe_service, fe_executor):
        monkeypatch.setattr(mod, "session_data_manager", store)
    from app.core.config import settings

    try:
        monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path), raising=False)
    except Exception:
        pass
    from app.services.field_contracts import service as fc_service

    monkeypatch.setattr(fc_service, "_findings_dir",
                        lambda session_id: tmp_path / "fc" / session_id)
    yield store


async def _seed(store, session_id: str, payload=None) -> str:
    return await store.store(session_id, {"geojson": payload or FACILITIES}, prefix="data")


async def _enforced(store, session_id: str, ref_id: str, doc=None) -> dict:
    """草稿 → 完整评估（内联）→ 激活：返回 contract 视图。"""
    from app.services.field_contracts import service as fc_service

    created = await fc_service.create_contract(
        session_id, ref_id=ref_id, document=doc or CONTRACT_DOC, actor="owner"
    )
    started = await fc_service.start_assessment(
        session_id, created["contract_id"], actor="owner", background=False
    )
    assessment = started["assessment"]
    assert assessment["status"] == "completed", assessment
    activated = await fc_service.activate_contract(
        session_id, created["contract_id"], actor="owner"
    )
    assert activated["activated"]
    return activated["contract"]


async def _preview(session_id: str, ref_id: str, ops, feature_ids=None):
    return await fe_service.build_and_store_plan(
        session_id, ref_id=ref_id, feature_ids=feature_ids, ops=ops
    )


async def _commit_user(session_id: str, plan: dict):
    return await fe_executor.commit_plan(
        session_id, plan, actor="user", origin="user"
    )


def _codes(findings):
    return [f.get("code") for f in (findings or [])]


# ─── F01：无合同 → 既有行为完全兼容 ──────────────────────────────────────


class TestNoContractBehavior:
    async def test_f01_observed_vocabulary_still_enforced(self, _storage):
        s = sid()
        ref = await _seed(_storage, s)
        # observed enum = {planned, active}；retired 未观测 → 旧 enum_violation
        plan, findings = await _preview(s, ref, [{
            "op": "set_fields", "fields": {"status": "retired"},
            "wfids": ["fac-0"],
        }])
        assert "enum_violation" in _codes(findings)
        assert plan.get("committable") is False


# ─── F02/F03：声明域与观测域分层 ─────────────────────────────────────────


class TestDeclaredDomain:
    async def test_f02_declared_enum_allows_unobserved_value(self, _storage):
        s = sid()
        ref = await _seed(_storage, s)
        await _enforced(_storage, s, ref)
        plan, findings = await _preview(s, ref, [{
            "op": "set_fields", "fields": {"status": "retired", "retired_reason": "EOF"},
            "wfids": ["fac-0"],
        }])
        assert findings == [], findings
        assert plan.get("committable") is True
        receipt = await _commit_user(s, plan)
        assert receipt["content_revision_after"] >= 1
        # 回读：值真实写入权威数据
        payload = await _storage.get(s, ref)
        props = payload["geojson"]["features"][0]["properties"]
        assert props["status"] == "retired" and props["retired_reason"] == "EOF"

    async def test_f03_dirty_observed_value_still_violates(self, _storage):
        s = sid()
        dirty = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": "d1", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                 "properties": {"asset_no": "A-001", "status": "unknown", "capacity": 5}},
            ],
        }
        ref = await _seed(_storage, s, dirty)
        from app.services.field_contracts import service as fc_service

        created = await fc_service.create_contract(s, ref_id=ref, document=CONTRACT_DOC)
        started = await fc_service.start_assessment(
            s, created["contract_id"], background=False
        )
        assessment = started["assessment"]
        assert assessment["status"] == "completed"
        assert assessment["counts"]["violations_total"] == 1
        assert assessment["counts"]["counts_by_code"].get("enum_violation") == 1
        # 出现过 ≠ 合法：激活被拒
        with pytest.raises(Exception) as ei:
            await fc_service.activate_contract(s, created["contract_id"])
        assert "no_activatable_assessment" in str(ei.value) or ei.value.code == "no_activatable_assessment"

    async def test_f17_findings_pagination_and_totals(self, _storage):
        s = sid()
        n = 30
        dirty = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": f"d{i}", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"asset_no": "SAME", "status": "planned", "capacity": 1}}
            for i in range(n)
        ]}
        ref = await _seed(_storage, s, dirty)
        from app.services.field_contracts import service as fc_service

        created = await fc_service.create_contract(s, ref_id=ref, document=CONTRACT_DOC)
        started = await fc_service.start_assessment(s, created["contract_id"], background=False)
        aid = started["assessment"]["id"]
        page1 = await fc_service.assessment_findings_page(s, aid, limit=20)
        assert page1["total_violations"] == n  # 30 行同 asset_no → 30 条冲突
        assert len(page1["findings"]) == 20
        assert page1["next_cursor"] is not None
        page2 = await fc_service.assessment_findings_page(s, aid, cursor=page1["next_cursor"], limit=20)
        assert len(page2["findings"]) == 10
        assert page2["next_cursor"] is None
        # 激活仍被阻断（总数非零）
        with pytest.raises(Exception):
            await fc_service.activate_contract(s, created["contract_id"])


# ─── F11/F12：并发与合同 read-set ────────────────────────────────────────


class TestConcurrencyAndContractCAS:
    async def test_f11_two_writers_same_unique_key_at_most_one(self, _storage):
        s = sid()
        ref = await _seed(_storage, s)
        await _enforced(_storage, s, ref)
        # 两个 create（不同 plan）都带同一 asset_no —— 预览各自通过（数据未变）
        results = []
        for tag in ("w1", "w2"):
            plan, findings = await _preview(s, ref, [{
                "op": "create_features",
                "features": [{
                    "geometry": {"type": "Point", "coordinates": [116.5, 39.5]},
                    "properties": {"asset_no": "DUP-1", "status": "planned", "capacity": 1},
                }],
            }])
            assert findings == [], findings
            results.append((tag, plan))
        # 并发提交：CAS + 锁内唯一性 → 至多一个成功
        receipts = await asyncio.gather(
            _commit_user(s, results[0][1]),
            _commit_user(s, results[1][1]),
            return_exceptions=True,
        )
        ok = [r for r in receipts if not isinstance(r, Exception)]
        bad = [r for r in receipts if isinstance(r, Exception)]
        assert len(ok) == 1 and len(bad) == 1
        # 权威数据：DUP-1 至多一条
        payload = await _storage.get(s, ref)
        hits = [f for f in payload["geojson"]["features"]
                if f["properties"].get("asset_no") == "DUP-1"]
        assert len(hits) == 1

    async def test_f12_contract_change_expires_old_plan(self, _storage):
        s = sid()
        ref = await _seed(_storage, s)
        contract = await _enforced(_storage, s, ref)
        plan, findings = await _preview(s, ref, [{
            "op": "set_fields", "fields": {"capacity": 99},
            "wfids": ["fac-1"],
        }])
        assert findings == []
        # 数据未变，但合同退役（revision 变化）→ 旧 plan 409 contract_changed
        from app.services.field_contracts import service as fc_service

        await fc_service.retire_contract(s, contract["contract_id"])
        with pytest.raises(fe_executor.FeatureEditConflict) as ei:
            await _commit_user(s, plan)
        assert ei.value.code == "contract_changed"
        # 反向：无合同期预览 → 激活合同后旧 plan 同样过期
        plan2, _ = await _preview(s, ref, [{
            "op": "set_fields", "fields": {"capacity": 98}, "wfids": ["fac-1"],
        }])
        await _enforced(_storage, s, ref)
        with pytest.raises(fe_executor.FeatureEditConflict) as ei2:
            await _commit_user(s, plan2)
        assert ei2.value.code == "contract_changed"


# ─── F13/F15/F31：激活资格 ───────────────────────────────────────────────


class TestActivationGates:
    async def test_f13_data_changed_after_assessment_blocks_activation(self, _storage):
        s = sid()
        ref = await _seed(_storage, s)
        from app.services.field_contracts import service as fc_service

        created = await fc_service.create_contract(s, ref_id=ref, document=CONTRACT_DOC)
        started = await fc_service.start_assessment(s, created["contract_id"], background=False)
        assert started["assessment"]["status"] == "completed"
        # 评估后数据被编辑（无合同期，允许）→ 激活失败，要求重新评估
        plan, findings = await _preview(s, ref, [{
            "op": "set_fields", "fields": {"capacity": 77}, "wfids": ["fac-0"],
        }])
        assert findings == []
        await _commit_user(s, plan)
        with pytest.raises(Exception) as ei:
            await fc_service.activate_contract(s, created["contract_id"])
        assert getattr(ei.value, "code", "") == "assessment_stale"

    async def test_f15_readonly_source_cannot_bind(self, _storage):
        s = sid()
        ref = await _storage.store(s, {"geojson": FACILITIES, "read_only": True}, prefix="data")
        from app.services.field_contracts import service as fc_service

        with pytest.raises(Exception) as ei:
            await fc_service.create_contract(s, ref_id=ref, document=CONTRACT_DOC)
        assert getattr(ei.value, "code", "") == "dataset_not_eligible"

    async def test_f31_cancelled_assessment_never_activatable(self, _storage):
        from app.services.field_contracts import service as fc_service

        s = sid()
        ref = await _seed(_storage, s)
        created = await fc_service.create_contract(s, ref_id=ref, document=CONTRACT_DOC)
        assessment = await fc_service.start_assessment(
            s, created["contract_id"], background=False
        )
        aid = assessment["assessment"]["id"]
        # 直接把收据置为 cancelled（模拟取消终态）→ 激活拒绝
        from app.services.field_contracts.store import field_contract_store

        await asyncio.to_thread(field_contract_store.update_assessment, aid, status="cancelled")
        with pytest.raises(Exception) as ei:
            await fc_service.activate_contract(s, created["contract_id"])
        assert getattr(ei.value, "code", "") == "no_activatable_assessment"


# ─── F18/F19/F21：结构性写入与 rename ───────────────────────────────────


class TestStructuralWrites:
    async def test_f18_create_missing_required_blocked_no_residue(self, _storage):
        s = sid()
        ref = await _seed(_storage, s)
        await _enforced(_storage, s, ref)
        plan, findings = await _preview(s, ref, [{
            "op": "create_features",
            "features": [{
                "geometry": {"type": "Point", "coordinates": [116.9, 39.9]},
                "properties": {"status": "planned"},   # 缺 asset_no（required）
            }],
        }])
        assert "contract_violation" in _codes(findings)
        assert plan.get("committable") is False
        payload = await _storage.get(s, ref)
        assert len(payload["geojson"]["features"]) == 4  # 无残留

    async def test_f19_inherited_duplicate_business_key_rejected(self, _storage):
        # split 的两个子要素继承同一 asset_no → 唯一键重复 → 整案候选被拒
        s = sid()
        poly_fc = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "id": "pz-1",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [116.00, 39.00], [116.10, 39.00], [116.10, 39.10],
                    [116.00, 39.10], [116.00, 39.00],
                ]]},
                "properties": {"asset_no": "P-001", "status": "active", "capacity": 3},
            }],
        }
        ref = await _seed(_storage, s, poly_fc)
        await _enforced(_storage, s, ref)
        # split 的 wfid 走自然 id 解析链（build_plan 内部物化后归一到稳定 wfid）
        plan, findings = await _preview(s, ref, [{
            "op": "split_feature",
            "wfid": "pz-1",
            "line": {"type": "LineString", "coordinates": [[116.05, 38.99], [116.05, 39.11]]},
        }])
        assert "contract_violation" in _codes(findings), findings
        assert plan.get("committable") is False
        # 数据零残留
        payload = await _storage.get(s, ref)
        assert len(payload["geojson"]["features"]) == 1

    async def test_f21_rename_declared_field_blocked(self, _storage):
        s = sid()
        ref = await _seed(_storage, s)
        await _enforced(_storage, s, ref)
        plan, findings = await _preview(s, ref, [{
            "op": "rename_field", "from": "asset_no", "to": "asset_id",
        }], feature_ids=["fac-0"])
        assert "contract_rename_blocked" in _codes(findings)
        assert plan.get("committable") is False
        # 未声明字段的 rename 不受影响
        extra = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": "fac-0", "geometry": FACILITIES["features"][0]["geometry"],
             "properties": {"asset_no": "A-000", "status": "planned", "capacity": 10, "memo": "m"}}
        ]}
        ref2 = await _seed(_storage, s + "x", extra)
        await _enforced(_storage, s + "x", ref2)
        plan2, findings2 = await _preview(s + "x", ref2, [{
            "op": "rename_field", "from": "memo", "to": "note",
        }], feature_ids=["fac-0"])
        assert findings2 == []
        assert plan2.get("committable") is True


# ─── F22：G32 批准后合同收紧 ─────────────────────────────────────────────


class TestG32Bridge:
    async def test_f22_approved_proposal_blocked_after_tightening(self, _storage, tmp_path, monkeypatch):
        # 真实提案服务链（快照/批准/桥），仅隔离 proposal 文件存储根
        monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "proposals"))
        from app.services.feature_proposals import bridge as fp_bridge
        from app.services.feature_proposals import service as fp_service
        from app.services.feature_proposals.contracts import (
            FeatureProposalConflict,
        )
        from app.services.feature_proposals.store import ProposalStore

        fp_service.proposal_store = ProposalStore(base_dir=tmp_path / "proposals")

        s = sid()
        ref = await _seed(_storage, s)
        # 无合同期：真实创建并批准提案（capacity=-5 —— 合同收紧后非法）
        ops = [{"op": "set_fields", "fields": {"capacity": -5}, "wfids": ["fac-0"]}]
        record = await fp_service.create_proposal(
            s, ref_id=ref, ops=ops, feature_ids=["fac-0"],
            title="将变为非法的提案", author_kind="user", author_id="owner",
            submit=True,
        )
        await fp_service.decide(
            s, record.proposal_id, action="approve", note=None,
            actor_kind="user", actor_id="owner",
        )
        detail = await fp_service.get_proposal_detail(s, record.proposal_id)
        assert detail["status"] == "approved"
        # 批准后合同激活（capacity ≥ 0 收紧）
        await _enforced(_storage, s, ref)
        approved_record = await fp_service.proposal_store.get_proposal(s, record.proposal_id)
        with pytest.raises(FeatureProposalConflict) as ei:
            await fp_bridge.merge_proposal(
                approved_record, session_id=s, actor_id="owner"
            )
        # 合同收紧 → 候选不再可提交（旧 approval 不授予新豁免）
        assert ei.value.code == "candidate_not_committable"
        detail_findings = ei.value.detail.get("findings") or []
        assert any("contract_violation" in str(f.get("code", "")) for f in detail_findings)
        # 数据未变
        payload = await _storage.get(s, ref)
        assert payload["geojson"]["features"][0]["properties"]["capacity"] == 10.0


# ─── F23：undo/redo 恢复态门禁 ───────────────────────────────────────────


class TestUndoRedoGate:
    async def test_f23_undo_blocked_when_restoring_violating_state(self, _storage):
        s = sid()
        ref = await _seed(_storage, s)
        # ① 无合同期编辑：fac-0.status = retired（旧观测词表恰好允许？planned→retired
        #    未观测会被拒 —— 用无枚举字段 capacity 走负值路径）
        plan, findings = await _preview(s, ref, [{
            "op": "set_fields", "fields": {"capacity": 42}, "wfids": ["fac-0"],
        }])
        assert findings == []
        await _commit_user(s, plan)
        # ② 激活合同（min=0；当前数据全部合法）
        await _enforced(_storage, s, ref)
        # ③ 新编辑把 capacity=50（合法）→ 可 undo
        plan2, findings2 = await _preview(s, ref, [{
            "op": "set_fields", "fields": {"capacity": 50}, "wfids": ["fac-0"],
        }])
        assert findings2 == []
        await _commit_user(s, plan2)
        # undo 恢复 42（合法）→ 允许
        receipt = await fe_executor.apply_undo(s, ref, actor="user", origin="user")
        assert receipt["kind"] == "undo"
        # redo 恢复 50（合法）→ 允许
        receipt2 = await fe_executor.apply_redo(s, ref, actor="user", origin="user")
        assert receipt2["kind"] == "redo"
        # ④ 现构造「undo 会恢复违反态」：先退役合同 → 改 status=retired 无 reason
        #    （旧观测词表会拒 retired…… 用直接数据写入模拟历史编辑不可行 —— 走
        #    journal 语义：在无合同期提交 status 未知值不行。改用可比较路径：
        #    退役合同 → capacity=-1 提交（无合同允许）→ 重新激活同内容合同
        #    会因数据脏被拒。因此用第二会话数据集直接验证：脏历史 + 合同 →
        #    undo 阻断）
        s2 = sid()
        ref2 = await _seed(_storage, s2)
        planA, fA = await _preview(s2, ref2, [{
            "op": "set_fields", "fields": {"capacity": 7}, "wfids": ["fac-0"],
        }])
        assert fA == []
        await _commit_user(s2, planA)
        planB, fB = await _preview(s2, ref2, [{
            "op": "set_fields", "fields": {"capacity": -7}, "wfids": ["fac-0"],
        }])
        assert fB == []  # 无合同期：观测类型面无负值限制
        await _commit_user(s2, planB)
        # 此时 journal head = capacity:-7；激活前需数据干净 —— 把数据修回，
        # 但保留一个「undo 会回到 -7」的 journal：
        planC, fC = await _preview(s2, ref2, [{
            "op": "set_fields", "fields": {"capacity": 8}, "wfids": ["fac-0"],
        }])
        assert fC == []
        await _commit_user(s2, planC)
        # 数据干净（8 ≥ 0）→ 激活
        await _enforced(_storage, s2, ref2)
        # undo 将恢复 -7（违反 min=0）→ typed 阻断，journal 不改写
        with pytest.raises(fe_executor.FeatureEditBlocked) as ei:
            await fe_executor.apply_undo(s2, ref2, actor="user", origin="user")
        assert ei.value.code == "contract_violation"
        detail = ei.value.detail
        assert detail.get("kind") == "undo"
        payload2 = await _storage.get(s2, ref2)
        assert payload2["geojson"]["features"][0]["properties"]["capacity"] == 8


# ─── F29：跨会话 contract_id 不可当访问令牌 ──────────────────────────────


class TestAuthzBoundary:
    async def test_f29_cross_session_contract_access_denied(self, _storage):
        from app.services.field_contracts import service as fc_service

        s1, s2 = sid(), sid()
        ref = await _seed(_storage, s1)
        created = await fc_service.create_contract(s1, ref_id=ref, document=CONTRACT_DOC)
        with pytest.raises(Exception) as ei:
            await fc_service.get_contract(s2, created["contract_id"])
        assert getattr(ei.value, "code", "") == "contract_not_found"
        with pytest.raises(Exception):
            await fc_service.activate_contract(s2, created["contract_id"])
        with pytest.raises(Exception):
            await fc_service.start_assessment(s2, created["contract_id"])
