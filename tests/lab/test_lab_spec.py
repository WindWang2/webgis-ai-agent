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

    def test_export_expectation_requires_benchmark_kind(self):
        spec = LabScenario.from_dict(_base_spec(
            expectations={"export_formats": ["png"]}))
        with pytest.raises(SpecError, match="export_formats"):
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
                          user_wins=True)
        assert exp.declared_dimensions() == [
            "evidence", "goal", "user_wins"]

    def test_empty_expectation_declares_nothing(self):
        assert Expectation().declared_dimensions() == []
