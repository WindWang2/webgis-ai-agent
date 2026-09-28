"""LabScenario v1 契约测试（E15）：fail-closed 校验 + 编译投影。"""
from __future__ import annotations

import pytest

from app.lib.harness.lab.spec import (
    LAB_SCHEMA_VERSION,
    Expectation,
    LabScenario,
    SpecError,
)


def _base_spec(**over) -> dict:
    data = {
        "spec_id": "spec-test",
        "title": "t",
        "kind": "replay",
        "turns": [{"user_input": "hi", "ops": []}],
    }
    data.update(over)
    return data


class TestValidateFailClosed:
    def test_unknown_kind_rejected(self):
        with pytest.raises(SpecError, match="unknown kind"):
            LabScenario.from_dict(_base_spec(kind="chaos")).validate()

    def test_unknown_fixture_builder_rejected(self):
        spec = LabScenario.from_dict(_base_spec(data=[
            {"alias": "x", "builder": "not_a_builder"}]))
        with pytest.raises(SpecError, match="unknown fixture builder"):
            spec.validate()

    def test_unknown_fault_type_rejected(self):
        spec = LabScenario.from_dict(_base_spec(
            fault_plan=[{"type": "meteor"}]))
        with pytest.raises(SpecError, match="unknown fault type"):
            spec.validate()

    def test_lab_fault_requires_settlement_kind(self):
        spec = LabScenario.from_dict(_base_spec(
            fault_plan=[{"type": "cancel"}]))
        with pytest.raises(SpecError, match="require kind='settlement'"):
            spec.validate()

    def test_export_expectation_only_lives_in_benchmark_case(self):
        """规格级导出字段已删除（S3 review P2-4）：replay 收据证明不了
        导出；导出期望唯一入口是 benchmark_case.expected_export_formats
        （由 plan tier 真实裁决）。"""
        spec = LabScenario.from_dict(_base_spec(
            expectations={"export_formats": ["png"]}))
        spec.validate()  # 未知字段被 from_dict 丢弃，不再有毒化声明面
        assert spec.expectations.declared_dimensions() == []
        benchmark_spec = LabScenario.from_dict(_base_spec(
            kind="benchmark",
            benchmark_case={"id": "T", "name": "n", "group": "poi",
                            "query": "q",
                            "expected_export_formats": ["png"]}))
        benchmark_spec.validate()
        case = benchmark_spec.compile_benchmark_case()
        assert case.expected_export_formats == ["png"]

    def test_components_expectation_requires_cartography_checks(self):
        spec = LabScenario.from_dict(_base_spec(
            expectations={"mapspec_components": ["title"]}))
        with pytest.raises(SpecError, match="cartography_checks"):
            spec.validate()

    def test_settlement_fault_plan_rejects_unconsumed_lab_faults(self):
        spec = LabScenario.from_dict(_base_spec(
            kind="settlement",
            settlement_checks=["settle_idempotent"],
            fault_plan=[{"type": "cancel"}]))
        with pytest.raises(SpecError, match="settlement_checks instead"):
            spec.validate()

    def test_settlement_requires_checks(self):
        with pytest.raises(SpecError, match="declares no checks"):
            LabScenario.from_dict(_base_spec(kind="settlement")).validate()

    def test_settlement_unknown_check_rejected(self):
        spec = LabScenario.from_dict(_base_spec(
            kind="settlement", settlement_checks=["nope"]))
        with pytest.raises(SpecError, match="unknown settlement checks"):
            spec.validate()

    def test_benchmark_requires_case(self):
        with pytest.raises(SpecError, match="benchmark_case"):
            LabScenario.from_dict(_base_spec(kind="benchmark")).validate()

    def test_replay_requires_turns(self):
        with pytest.raises(SpecError, match="requires turns"):
            LabScenario.from_dict(_base_spec(turns=[])).validate()

    def test_schema_version_pinned(self):
        spec = LabScenario.from_dict(
            _base_spec(schema_version=LAB_SCHEMA_VERSION + 3))
        with pytest.raises(SpecError, match="schema_version"):
            spec.validate()


class TestCompileProjections:
    def test_compile_replay_scenario_maps_turns_and_faults(self):
        spec = LabScenario.from_dict(_base_spec(
            fault_plan=[{"type": "timeout", "target_turn": 0}],
            turns=[{"user_input": "u",
                    "ops": [{"call_id": "c1", "tool": "t",
                             "arguments": {"a": 1},
                             "result": {"ok": True}}]}],
        ))
        spec.validate()
        scenario = spec.compile_replay_scenario()
        assert scenario.scenario_id == "spec-test"
        assert scenario.turns[0].ops[0].tool == "t"
        # 场景变换族故障原样投影；lab 执行面故障已在 validate 拒绝。
        assert [f["type"] for f in scenario.faults] == ["timeout"]

    def test_fixture_sentinel_resolved_from_data_plane(self):
        spec = LabScenario.from_dict(_base_spec(
            data=[{"alias": "schools", "builder": "chengdu_schools",
                   "params": {}}],
            turns=[{"user_input": "u", "ops": [],
                    "refs": {"ref:geojson-schools": {"fixture": "schools"}}}],
        ))
        spec.validate()
        scenario = spec.compile_replay_scenario()
        doc = scenario.turns[0].refs["ref:geojson-schools"]
        assert doc["type"] == "FeatureCollection"
        assert len(doc["features"]) == 60

    def test_fixture_sentinel_unknown_alias_rejected(self):
        spec = LabScenario.from_dict(_base_spec(
            turns=[{"user_input": "u", "ops": [],
                    "refs": {"ref:x": {"fixture": "ghost"}}}]))
        spec.validate()
        with pytest.raises(SpecError, match="undeclared data alias"):
            spec.compile_replay_scenario()

    def test_visual_judge_flag_sinks_into_turn_fixtures(self):
        spec = LabScenario.from_dict(_base_spec(
            visual_judge=True,
            turns=[{"user_input": "u", "ops": [],
                    "cartography": {"mapspec": {"layers": []},
                                    "map_state": {}}}]))
        scenario = spec.compile_replay_scenario()
        assert scenario.turns[0].cartography["visual_judge"] is True

    def test_compile_benchmark_case(self):
        spec = LabScenario.from_dict(_base_spec(
            kind="benchmark",
            benchmark_case={"id": "T", "name": "n", "group": "poi",
                            "query": "q", "plan_only": True}))
        spec.validate()
        case = spec.compile_benchmark_case()
        assert case.id == "T"
        assert case.plan_only is True


class TestExpectationDimensions:
    def test_declared_dimensions(self):
        exp = Expectation(goal_status="pass", evidence_min_count=2,
                          mapspec_components=["title"], user_wins=True)
        # 词表必须是 metrics.DIMENSIONS 全名（S3 review P0-1：短别名与
        # runner 的 declared 集合永不相交 → 防线死代码）。
        assert exp.declared_dimensions() == [
            "evidence_completeness", "goal_completion",
            "cartographic_compliance", "user_wins_compliance"]

    def test_empty_expectation_declares_nothing(self):
        assert Expectation().declared_dimensions() == []
