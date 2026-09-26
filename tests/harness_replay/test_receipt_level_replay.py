"""T4 receipt 级 ToolDispatchService 重放契约（ADR-0214 D6，WP1）。

钉死的行为：
- 沙箱内跑**真实** ToolDispatchService：ok 收据 → status ok + ref 铸出；
  error 收据 → 错误折叠（status error + code）；同参重发 → repeated；
- 全部外部副作用替身化：内存 store 不落 Redis、MapSpec 存储换沙箱目录、
  广播 None、横切闸全关（真实单例零调用）；
- expect.receipt / receipt_repeat 可钉住合同面，篡改翻红；
- declared-but-unrun（receipt_backed 无 ops）→ t4_receipt not_run 翻红。
"""
from __future__ import annotations

import copy

import pytest

from app.lib.harness.replay.receipt import (
    _dispatch_sandbox,
    replay_receipt_level,
)
from app.lib.harness.replay.replayer import OfflineReplayer, Scenario, ScenarioOp, TurnSpec

pytestmark = pytest.mark.cartography


def _turn(*ops) -> TurnSpec:
    return TurnSpec(user_input="probe", ops=list(ops))


def _ok_op(call_id: str = "c1", tool: str = "spatial_aggregate") -> ScenarioOp:
    return ScenarioOp(
        call_id=call_id, tool=tool,
        arguments={"layer_ref": "ref:geojson:seed"},
        result={"geojson_ref": "ref:geojson:recorded"},
    )


def _err_op(call_id: str = "c2", tool: str = "webgis_query") -> ScenarioOp:
    return ScenarioOp(
        call_id=call_id, tool=tool,
        arguments={"keyword": "parks"},
        result={"status": "error"},
        is_error=True, error_msg="provider timeout",
    )


class TestReceiptLevelReplay:
    @pytest.mark.asyncio
    async def test_ok_receipt_mints_ref_via_real_dispatch(self):
        result = await replay_receipt_level(
            _turn(_ok_op()), session_id="rsess-t4a", tool_registry={})
        assert result.skipped_reason == ""
        entry = result.entries["c1"]
        assert entry["status"] == "ok"
        assert entry["ref_minted"] is True
        # dedup 合同：同参重发 → repeated。
        assert result.repeat["c1"] == "repeated"

    @pytest.mark.asyncio
    async def test_error_receipt_folds_to_error_contract(self):
        result = await replay_receipt_level(
            _turn(_err_op()), session_id="rsess-t4b", tool_registry={})
        entry = result.entries["c2"]
        assert entry["status"] == "error"
        assert entry["ref_minted"] is False
        assert entry["error_code"] == "TOOL_ERROR"

    @pytest.mark.asyncio
    async def test_sandbox_restores_production_state(self):
        """沙箱退出后 env / 模块单例 / MapSpec 目录全部还原。"""
        import os

        import app.services.session_data as sd_module
        from app.services.mapspec import store as store_module

        singleton_before = sd_module.session_data_manager
        base_dir_before = store_module.BASE_STORAGE_DIR
        with _dispatch_sandbox():
            assert os.environ.get("GIS_ANALYSIS_REUSE") == "0"
            assert sd_module.session_data_manager is not singleton_before
        assert os.environ.get("GIS_ANALYSIS_REUSE") != "0" \
            or os.environ.get("GIS_ANALYSIS_REUSE") is None
        assert sd_module.session_data_manager is singleton_before
        assert store_module.BASE_STORAGE_DIR == base_dir_before

    @pytest.mark.asyncio
    async def test_memory_store_bounded_and_deterministic(self):
        with _dispatch_sandbox() as sandbox:
            store = sandbox.memory_store
            ref = await store.store("s", {"a": 1}, prefix="geojson")
            assert ref.startswith("ref:geojson:t4-")
            assert await store.get("s", ref) == {"a": 1}
            assert await store.get_ref_descriptor("s", ref) is not None
            await store.append_event("s", "tool_executed", {"x": 1})
            log = await store.get_event_log("s")
            assert log and log[0]["event"] == "tool_executed"


class TestReceiptBackedScenario:
    @pytest.mark.asyncio
    async def test_scenario_runs_t4_and_pins_contract(self):
        scenario = Scenario(
            scenario_id="scn-t4", category="recorded",
            turns=[_turn(_ok_op(), _err_op())],
            receipt_backed=True,
        )
        scenario.turns[0].expect = {
            "receipt": {
                "c1": {"status": "ok", "ref_minted": True},
                "c2": {"status": "error"},
            },
            "receipt_repeat": {"c1": "repeated"},
        }
        result = await OfflineReplayer(seed=5).replay_scenario(scenario)
        assert "t4_receipt" in result.levels_run
        assert result.not_run == []
        assert result.ok is True
        # digest 覆盖 T4 合同结论（重跑确定性）。
        again = await OfflineReplayer(seed=5).replay_scenario(scenario)
        assert again.replay_digest == result.replay_digest

    @pytest.mark.asyncio
    async def test_receipt_expect_tamper_turns_red(self):
        scenario = Scenario(
            scenario_id="scn-t4-red", category="recorded",
            turns=[_turn(_ok_op())],
            receipt_backed=True,
        )
        scenario.turns[0].expect = {
            "receipt": {"c1": {"status": "ok", "ref_minted": True}},
        }
        baseline = await OfflineReplayer(seed=6).replay_scenario(scenario)
        assert baseline.ok is True
        tampered = copy.deepcopy(scenario)
        tampered.turns[0].expect["receipt"]["c1"]["ref_minted"] = False
        red = await OfflineReplayer(seed=6).replay_scenario(tampered)
        assert red.ok is False
        assert any("receipt.c1" in d["path"]
                   for d in red.turns[0].exact_diffs)

    @pytest.mark.asyncio
    async def test_declared_but_unrun_is_not_run_red(self):
        """receipt_backed 但 turn 无 ops → t4_receipt not_run，翻红不静默。"""
        scenario = Scenario(
            scenario_id="scn-t4-empty", category="recorded",
            turns=[TurnSpec(user_input="no ops")],
            receipt_backed=True,
        )
        result = await OfflineReplayer(seed=7).replay_scenario(scenario)
        assert "t4_receipt" in result.not_run
        assert result.ok is False
