"""M6 工具面测试：真实 ToolRegistry dispatch + policy 闸 + situation 投影 + IR 扩展。"""
from __future__ import annotations

import pytest

from app.services.gis_harness.interaction_ir.contracts import normalize_payload
from app.services.gis_harness.interaction_ir.projection import project_interaction_snapshot
from app.services.session_data import MemorySessionStore
from app.tools.feature_edit_tools import register_feature_edit_tools
from app.tools.registry import ToolRegistry

SCHOOLS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": f"sch-{i}",
            "geometry": {"type": "Point", "coordinates": [116.0 + 0.01 * i, 39.0]},
            "properties": {"name": f"学校{i}", "type": "小学" if i % 2 == 0 else "中学", "capacity": 500 + i},
        }
        for i in range(5)
    ],
}


@pytest.fixture
def store():
    return MemorySessionStore()


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_feature_edit_tools(reg)
    return reg


@pytest.fixture(autouse=True)
def _patch_store(store, monkeypatch):
    from app.services.feature_edit import executor as fe_executor
    from app.services.feature_edit import policy as fe_policy
    from app.services.feature_edit import service as fe_service
    from app.tools import feature_edit_tools as fe_tools

    for mod in (fe_executor, fe_policy, fe_service, fe_tools):
        monkeypatch.setattr(mod, "session_data_manager", store)


class TestPreviewTool:
    @pytest.mark.asyncio
    async def test_preview_via_registry_dispatch(self, registry, store):
        sid = "sess-tool1"
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        result = await registry.dispatch(
            "webgis_feature_edit_preview",
            {
                "ref_id": ref,
                "feature_ids": ["sch-1", "sch-3"],
                "ops": [{"op": "set_fields", "fields": {"type": "小学"}}],
            },
            session_id=sid,
        )
        assert result["success"] is True
        assert result["committable"] and result["safe"] and result["n_targets"] == 2
        assert result["plan_id"].startswith("fep-")

    @pytest.mark.asyncio
    async def test_preview_validation_findings_relayed(self, registry, store):
        sid = "sess-tool2"
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        result = await registry.dispatch(
            "webgis_feature_edit_preview",
            {
                "ref_id": ref,
                "feature_ids": ["sch-1"],
                "ops": [{"op": "set_fields", "fields": {"type": "幼儿园"}}],
            },
            session_id=sid,
        )
        assert result["success"] is False
        assert result["code"] == "ENUM_VIOLATION"
        assert "allowed" in str(result["findings"])

    @pytest.mark.asyncio
    async def test_preview_from_selection_snapshot(self, registry, store):
        sid = "sess-tool3"
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        await store.set_map_state_fields(
            sid,
            {
                "_interaction_ir_ledger": [
                    {
                        "kind": "feature_selected",
                        "client_instance_id": "tab-1",
                        "client_seq": 1,
                        "server_seq": 1,
                        "payload": {
                            "layer_id": "schools",
                            "feature_id": "sch-2",
                            "dataset_ref": ref,
                            "selected_ids": ["sch-2", "sch-4", "sch-0"],
                        },
                    }
                ],
                "_interaction_ir_meta": {"server_seq": 1},
            },
        )
        result = await registry.dispatch(
            "webgis_feature_edit_preview",
            {"ops": [{"op": "set_fields", "fields": {"type": "中学"}}]},
            session_id=sid,
        )
        assert result["success"] is True and result["n_targets"] == 3  # selected_ids 扩展生效


class TestCommitToolPolicyGate:
    @pytest.mark.asyncio
    async def _preview(self, registry, store, sid, ops):
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        result = await registry.dispatch(
            "webgis_feature_edit_preview",
            {"ref_id": ref, "feature_ids": ["sch-1"], "ops": ops},
            session_id=sid,
        )
        return result["plan_id"]

    @pytest.mark.asyncio
    async def test_confirm_all_policy_blocks_agent_commit(self, registry, store):
        sid = "sess-tool4"
        plan_id = await self._preview(registry, store, sid, [{"op": "set_fields", "fields": {"type": "小学"}}])
        result = await registry.dispatch(
            "webgis_feature_edit_commit", {"plan_id": plan_id}, session_id=sid
        )
        assert result["success"] is False
        assert result["code"] == "CONFIRMATION_REQUIRED"
        # 数据未动：journal 无 apply 入账
        from app.services.feature_edit import executor as fe_executor

        journals = await fe_executor._load_journals(sid)
        assert journals == {}

    @pytest.mark.asyncio
    async def test_allow_safe_attribute_enables_agent_commit(self, registry, store):
        from app.services.feature_edit import policy as fe_policy

        sid = "sess-tool5"
        await fe_policy.set_policy(sid, "allow_safe_attribute")
        plan_id = await self._preview(registry, store, sid, [{"op": "set_fields", "fields": {"type": "小学"}}])
        result = await registry.dispatch(
            "webgis_feature_edit_commit", {"plan_id": plan_id}, session_id=sid
        )
        assert result["success"] is True, result
        assert result["content_revision_after"] >= 2
        # 幂等：同 plan_id 再提交不写第二版
        result2 = await registry.dispatch(
            "webgis_feature_edit_commit", {"plan_id": plan_id}, session_id=sid
        )
        assert result2["success"] is True
        assert result2.get("entry_seq") == result.get("entry_seq")

    @pytest.mark.asyncio
    async def test_geometry_never_auto_even_with_policy(self, registry, store):
        from app.services.feature_edit import policy as fe_policy

        sid = "sess-tool6"
        await fe_policy.set_policy(sid, "allow_safe_attribute")
        plan_id = await self._preview(registry, store, sid, [{"op": "move_by", "dx_m": 30.0, "dy_m": 0.0}])
        result = await registry.dispatch(
            "webgis_feature_edit_commit", {"plan_id": plan_id}, session_id=sid
        )
        assert result["success"] is False and result["code"] == "CONFIRMATION_REQUIRED"

    @pytest.mark.asyncio
    async def test_history_tool_reads_journal(self, registry, store):
        from app.services.feature_edit import policy as fe_policy

        sid = "sess-tool7"
        await fe_policy.set_policy(sid, "allow_safe_attribute")
        ref = await store.store(sid, {"geojson": SCHOOLS}, prefix="data")
        plan_id = (await registry.dispatch(
            "webgis_feature_edit_preview",
            {"ref_id": ref, "feature_ids": ["sch-1"], "ops": [{"op": "set_fields", "fields": {"type": "小学"}}]},
            session_id=sid,
        ))["plan_id"]
        await registry.dispatch("webgis_feature_edit_commit", {"plan_id": plan_id}, session_id=sid)
        hist = await registry.dispatch(
            "webgis_feature_edit_history", {"ref_id": ref}, session_id=sid
        )
        assert hist["success"] is True
        assert hist["head_seq"] == 1
        assert hist["entries"][0]["actor"] == "agent" and hist["entries"][0]["origin"] == "agent"


class TestIRSelectedIds:
    def test_normalize_accepts_bounded_selected_ids(self):
        payload = normalize_payload(
            "feature_selected",
            {
                "layer_id": "schools",
                "feature_id": "sch-2",
                "dataset_ref": "ref:data-abc",
                "selected_ids": ["sch-0", "sch-2", "sch-4"],
            },
        )
        assert payload is not None
        assert payload["selected_ids"] == ["sch-0", "sch-2", "sch-4"]

    def test_normalize_caps_list_at_eight_and_rejects_oversized(self):
        many = [f"id-{i}" for i in range(12)]
        payload = normalize_payload("feature_selected", {"layer_id": "l", "feature_id": "id-0", "selected_ids": many})
        assert payload is not None and len(payload["selected_ids"]) == 8  # list 面 ≤8
        # 超预算整事件拒收（512B 纪律 —— 诚实拒绝而非静默截断语义集）
        huge = ["x" * 120] * 8
        assert normalize_payload("feature_selected", {"layer_id": "l", "selected_ids": huge}) is None

    def test_projection_carries_selected_ids(self):
        snapshot = project_interaction_snapshot(
            [
                {
                    "kind": "feature_selected",
                    "client_instance_id": "tab-1",
                    "client_seq": 1,
                    "server_seq": 1,
                    "payload": {"layer_id": "l", "feature_id": "f1", "selected_ids": ["f1", "f2"]},
                }
            ]
        )
        assert snapshot["selection"]["selected_ids"] == ["f1", "f2"]


class TestSituationProjection:
    def test_compile_interaction_includes_feature_edits(self):
        from app.services.gis_situation.compiler import compile_interaction

        map_state = {
            "_feature_edit_journals": {
                "ref:data-1": {
                    "head_seq": 2,
                    "dropped": 0,
                    "entries": [
                        {
                            "seq": 1,
                            "kind": "apply",
                            "actor": "user",
                            "origin": "user",
                            "deltas": [{"wfid": "w1"}, {"wfid": "w2"}],
                            "schema_ops": [],
                            "content_revision_after": 2,
                        },
                        {
                            "seq": 2,
                            "kind": "undo",
                            "actor": "user",
                            "origin": "user",
                            "deltas": [],
                            "schema_ops": [],
                            "content_revision_after": 3,
                        },
                    ],
                }
            }
        }
        ctx = compile_interaction(map_state, [])
        fact = ctx.feature_edits
        assert fact.status == "known"
        receipts = fact.value
        assert len(receipts) == 2
        assert receipts[0]["seq"] == 2  # 最新在前（undo head）
        assert receipts[1]["changed_features"] == 2

    def test_compile_interaction_feature_edits_unknown_when_no_journal(self):
        from app.services.gis_situation.compiler import compile_interaction

        ctx = compile_interaction({}, [])
        assert ctx.feature_edits.status == "unknown"
