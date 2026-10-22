"""G05 ScenarioContract DSL 契约测试（schema 负例 / roundtrip / pin 一致性）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.cartography

from pydantic import ValidationError

from .contracts import (
    SCHEMA_VERSION,
    AgentStep,
    CheckVerdict,
    Expectation,
    Face,
    ScenarioContract,
    StepKind,
    TurnSpec,
    scenario_digest_payload,
)
from .corpus import all_scenarios

PIN_PATH = (Path(__file__).parent / "fixtures" / "task_scenarios"
            / "scenarios.json")


def _minimal_scenario(**overrides) -> ScenarioContract:
    base = dict(
        id="contract-probe", family="F00", title="probe", user_goal="q",
        turns=[TurnSpec(turn_id="t1", message="q",
                        steps=[AgentStep(call_id="i", kind=StepKind.INTENT)])],
        expectations=[Expectation(id="e1", face=Face.INTENT, kind="probe",
                                  path="chapter_summary.recipe_id")],
    )
    base.update(overrides)
    return ScenarioContract(**base)


class TestSchemaContracts:
    def test_schema_version_pinned(self):
        assert SCHEMA_VERSION == "task_scenario.v1"

    def test_minimal_scenario_valid(self):
        s = _minimal_scenario()
        assert s.not_evaluated == []
        assert s.timeout_s == 60

    def test_expectation_missing_path_rejected(self):
        with pytest.raises(ValidationError):
            Expectation(id="e", face=Face.DATA, kind="k", path="")

    def test_unknown_face_rejected(self):
        with pytest.raises(ValidationError):
            Expectation(id="e", face="not_a_face", kind="k", path="a.b")

    def test_unknown_step_kind_rejected(self):
        with pytest.raises(ValidationError):
            AgentStep(call_id="x", kind="teleport")

    def test_expected_verdict_vocabulary_closed(self):
        with pytest.raises(ValidationError):
            Expectation(id="e", face=Face.DATA, kind="k", path="a",
                        expected_verdict="maybe")

    def test_turn_requires_message(self):
        with pytest.raises(ValidationError):
            TurnSpec(turn_id="t", message="", steps=[])


class TestRoundtripDeterminism:
    def test_roundtrip_stable_and_sorted(self):
        s = _minimal_scenario()
        once = json.dumps(scenario_digest_payload(s), sort_keys=True,
                          ensure_ascii=False)
        again = json.dumps(
            scenario_digest_payload(_minimal_scenario()), sort_keys=True,
            ensure_ascii=False)
        assert once == again, "同输入两次序列化必须逐字节一致（无时间戳）"

    def test_corpus_scenario_roundtrip_preserves_fields(self):
        s = all_scenarios()[0]
        dump = scenario_digest_payload(s)
        restored = ScenarioContract.model_validate(dump)
        assert scenario_digest_payload(restored) == dump


class TestPinConsistency:
    def test_pin_exists_and_matches_corpus(self):
        assert PIN_PATH.exists(), "pin 缺失——运行 " \
            "`python scripts/g05_task_acceptance.py pin` 生成"
        payload = json.loads(PIN_PATH.read_text(encoding="utf-8"))
        assert payload["schema_version"] == SCHEMA_VERSION
        scenarios = all_scenarios()
        assert payload["count"] == len(scenarios)
        expected = {
            s.id: json.dumps(scenario_digest_payload(s), sort_keys=True,
                             ensure_ascii=False)
            for s in scenarios
        }
        pinned = {
            s["id"]: json.dumps(s, sort_keys=True, ensure_ascii=False)
            for s in payload["scenarios"]
        }
        assert pinned == expected, (
            "corpus 与 pin 漂移——重跑 `python scripts/g05_task_acceptance.py pin`"
            " 并确认期望变化是有意的")


class TestVerdictSemantics:
    def test_missing_path_is_not_evaluated_not_pass(self):
        from .reporting import _verdict_of, MISSING
        exp = Expectation(id="e", face=Face.DATA, kind="k", path="a.b",
                          value=True)
        verdict, detail = _verdict_of(MISSING, exp)
        assert verdict is CheckVerdict.NOT_EVALUATED
        assert "absent" in detail

    def test_ops_covered(self):
        from .reporting import _verdict_of
        exp = lambda op, value: Expectation(  # noqa: E731
            id="e", face=Face.DATA, kind="k", path="a", op=op, value=value)
        assert _verdict_of(3, exp("ge", 2))[0] is CheckVerdict.PASS
        assert _verdict_of(1, exp("ge", 2))[0] is CheckVerdict.FAIL
        assert _verdict_of("ab", exp("contains", "a"))[0] is CheckVerdict.PASS
        assert _verdict_of("x", exp("regex", "^x$"))[0] is CheckVerdict.PASS
        assert _verdict_of(None, exp("absent", None))[0] is CheckVerdict.PASS
        assert _verdict_of([1], exp("exists", None))[0] is CheckVerdict.PASS


class TestEvaluatorOpBoundaries:
    """P3-15：op 边界语义钉死（空容器对称 / None 泄漏）。"""

    def test_exists_absent_symmetric_on_empty_containers(self):
        from .reporting import _verdict_of
        from .contracts import CheckVerdict
        exp_exists = Expectation(id="e", face=Face.DATA, kind="k",
                                 path="a", op="exists")
        exp_absent = Expectation(id="e", face=Face.DATA, kind="k",
                                 path="a", op="absent")
        for empty in ({}, [], "", None):
            assert _verdict_of(empty, exp_exists)[0] is CheckVerdict.FAIL, \
                f"exists({empty!r}) 必须判 FAIL"
            assert _verdict_of(empty, exp_absent)[0] is CheckVerdict.PASS, \
                f"absent({empty!r}) 必须判 PASS"

    def test_not_in_semantics_documented(self):
        from .reporting import _verdict_of, MISSING
        from .contracts import CheckVerdict
        exp = Expectation(id="e", face=Face.DATA, kind="k", path="a",
                          op="not_in", value=["x"])
        # None（路径存在但值为空）：不含被排除值 → PASS（排除语义）
        assert _verdict_of(None, exp)[0] is CheckVerdict.PASS
        # 路径缺席：NOT_EVALUATED（缺席不冒充排除成立）
        verdict, _ = _verdict_of(MISSING, exp)
        assert verdict is CheckVerdict.NOT_EVALUATED

    def test_not_in_list_semantics(self):
        from .reporting import _verdict_of
        exp = Expectation(id="e", face=Face.DATA, kind="k", path="a",
                          op="not_in", value=["heatmap", "fill"])
        from .contracts import CheckVerdict
        assert _verdict_of(["circle"], exp)[0] is CheckVerdict.PASS
        assert _verdict_of(["circle", "heatmap"], exp)[0] is CheckVerdict.FAIL
        assert _verdict_of("heatmap", exp)[0] is CheckVerdict.FAIL
