"""WP2 有界决策辅助（pi_decision）单元测试。

覆盖：出题构建边界、ClassifierResult 严格验证矩阵、编排降级路径、
三类应用闸（宿主权威、永不越权）。传输层在单测中用确定性 stub dict
（编排逻辑本身是被测物）；真实 vendor classify 消费由集成车道证明。
"""
from __future__ import annotations

import pytest

from app.services.chat import pi_decision as pd


# ── 测试工具 ────────────────────────────────────────────────────────

def _task_candidates(*specs):
    """(rule_id, task, specificity, span) 候选构造。"""
    return [("r1", task, spec, span) for task, spec, span in specs]


def _choice_result(choice, confidence, *, response_id="resp-1", total=42):
    return {
        "stopReason": "stop",
        "answers": {"task_choice": {"type": "choice", "choice": choice,
                                    "probabilities": {choice: confidence},
                                    "confidence": confidence}},
        "responseId": response_id,
        "usage": {"totalTokens": total},
    }


def _request(domain=pd.DOMAIN_TASK_IDENTIFICATION, **kw):
    is_bool = domain == pd.DOMAIN_CLARIFICATION_NEEDED
    return pd.DecisionRequest(
        domain=domain,
        question_key="task_choice" if domain == pd.DOMAIN_TASK_IDENTIFICATION
        else ("template_choice" if domain == pd.DOMAIN_TEMPLATE_CANDIDATES
              else "needs_clarification"),
        question=({"type": "bool", "instructions": "x",
                   "criteria": {"true": "是", "false": "否"}}
                 if is_bool else
                 {"type": "choice", "instructions": "x",
                  "criteria": {"a": "A", "b": "B"}}),
        candidates=("a", "b"),
        request_id="dec-test-0001",
        **kw)


# ── feature gate ────────────────────────────────────────────────────

def test_disabled_by_default_and_env_gate():
    assert pd.decision_assist_enabled({}) is False
    assert pd.decision_assist_enabled({pd.DECISION_ASSIST_ENV: "0"}) is False
    assert pd.decision_assist_enabled({pd.DECISION_ASSIST_ENV: "1"}) is True


def test_assist_disabled_short_circuits_without_transport_call():
    class Boom:
        async def classify(self, model_ref, context):  # pragma: no cover
            raise AssertionError("disabled 时不得触达传输层")

    assist = pd.DecisionAssist(Boom(), enabled=False, model_ref="typesafe/jev-latest")
    outcome = _run(assist.assist(_request()))
    assert outcome.status == pd.STATUS_REJECTED
    assert outcome.reason_code == pd.REASON_DISABLED


# ── 出题构建 ────────────────────────────────────────────────────────

def test_task_request_none_when_unique_winner():
    req = pd.build_task_identification_request(
        "成都小学分布", task_candidates=_task_candidates(("a", 5, 4)),
        matched_task="a")
    assert req is None


def test_task_request_bounded_candidates_and_state():
    cands = _task_candidates(*[(f"t{i}", 3, 2) for i in range(10)])
    req = pd.build_task_identification_request(
        "q" * 300, task_candidates=cands, matched_task="t0",
        state_fields={"task_type": "t0", "scope_name": "成都" * 200,
                      "junk": "必须被丢弃"})
    assert req is not None
    assert len(req.candidates) == pd.MAX_CANDIDATES
    assert "junk" not in req.state
    assert len(req.state["scope_name"]) <= pd.MAX_FIELD_CHARS
    assert req.to_classifier_context()["questions"]["task_choice"]["type"] == "choice"


def test_state_byte_budget_enforced():
    big = {"scope_name": "x" * 9000, "subject_type": "poi"}
    state = pd._bounded_state(big)
    assert json_bytes(state) <= pd.MAX_STATE_BYTES


def json_bytes(state) -> int:
    import json
    return len(json.dumps(state, ensure_ascii=False).encode("utf-8"))


def test_template_request_none_without_tie():
    assert pd.build_template_candidates_request("q", tied_template_ids=["only"]) is None
    assert pd.build_template_candidates_request("q", tied_template_ids=[]) is None


def test_clarification_request_requires_policy_reasons():
    assert pd.build_clarification_request("q", policy_reasons=[],
                                          task_candidates=["a", "b"]) is None
    req = pd.build_clarification_request("q", policy_reasons=["low_confidence"],
                                         task_candidates=["a", "b"])
    assert req is not None
    assert req.question["type"] == "bool"
    assert req.confidence_floor == pd.BOOL_YES_FLOOR


# ── 验证矩阵 ────────────────────────────────────────────────────────

def test_validate_accepts_wellformed_choice():
    outcome = pd.validate_classifier_result(_request(), _choice_result("a", 0.9))
    assert outcome.ok and outcome.choice == "a"
    assert outcome.confidence == pytest.approx(0.9)
    assert outcome.response_id == "resp-1"
    assert outcome.usage_total_tokens == 42


@pytest.mark.parametrize("bad", [
    None, "x", 123, [],
    {"stopReason": "error", "answers": {}},
    {"stopReason": "aborted", "answers": {}},
    {"stopReason": "stop"},
    {"stopReason": "stop", "answers": {}},
    {"stopReason": "stop", "answers": {"other": {"type": "choice", "choice": "a",
                                                 "confidence": 0.9}}},
    {"stopReason": "stop",
     "answers": {"task_choice": {"type": "choice", "choice": "a",
                                 "confidence": 0.9}, "extra": {}},
     },
    {"stopReason": "stop", "answers": {"task_choice": "yes"}},
    {"stopReason": "stop", "answers": {"task_choice": {"type": "bool",
                                                       "probability": 0.9}}},
    {"stopReason": "stop", "answers": {"task_choice": {"type": "choice",
                                                       "choice": 42,
                                                       "confidence": 0.9}}},
])
def test_validate_rejects_malformed_or_wrong_type(bad):
    outcome = pd.validate_classifier_result(_request(), bad)
    assert not outcome.ok
    assert outcome.reason_code in pd.REJECT_REASONS


def test_validate_rejects_unknown_choice_case_sensitive():
    outcome = pd.validate_classifier_result(_request(), _choice_result("A", 0.99))
    assert outcome.reason_code == pd.REASON_UNKNOWN_CHOICE


def test_validate_rejects_low_confidence_choice():
    outcome = pd.validate_classifier_result(_request(), _choice_result("a", 0.55))
    assert outcome.reason_code == pd.REASON_LOW_CONFIDENCE


@pytest.mark.parametrize("conf", [float("nan"), 1.2, -0.1, "0.9", True])
def test_validate_rejects_invalid_confidence(conf):
    outcome = pd.validate_classifier_result(_request(), _choice_result("a", conf))
    assert outcome.reason_code == pd.REASON_MALFORMED


def test_validate_missing_answer_code():
    result = {"stopReason": "stop", "answers": {"wrong_key": {"type": "choice"}}}
    outcome = pd.validate_classifier_result(_request(), result)
    assert outcome.reason_code == pd.REASON_MISSING_ANSWER


def test_validate_duplicate_request_id():
    seen: set[str] = set()
    first = pd.validate_classifier_result(_request(), _choice_result("a", 0.9),
                                          seen_request_ids=seen)
    assert first.ok
    second = pd.validate_classifier_result(_request(), _choice_result("a", 0.9),
                                           seen_request_ids=seen)
    assert second.reason_code == pd.REASON_DUPLICATE


# ── bool 域阈值 ─────────────────────────────────────────────────────

def _bool_result(p):
    return {"stopReason": "stop",
            "answers": {"needs_clarification": {"type": "bool", "probability": p}}}


def _clar_request():
    return _request(domain=pd.DOMAIN_CLARIFICATION_NEEDED)


def test_bool_yes_above_floor():
    outcome = pd.validate_classifier_result(_clar_request(), _bool_result(0.93))
    assert outcome.ok and outcome.choice == "yes"


def test_bool_no_below_half():
    outcome = pd.validate_classifier_result(_clar_request(), _bool_result(0.2))
    assert outcome.ok and outcome.choice == "no"


@pytest.mark.parametrize("p", [0.5, 0.55, 0.69])
def test_bool_ambiguous_band_rejected(p):
    outcome = pd.validate_classifier_result(_clar_request(), _bool_result(p))
    assert outcome.reason_code == pd.REASON_LOW_CONFIDENCE


def test_bool_invalid_probability():
    outcome = pd.validate_classifier_result(_clar_request(),
                                            _bool_result(float("nan")))
    assert outcome.reason_code == pd.REASON_MALFORMED


# ── 编排降级 ────────────────────────────────────────────────────────

def _run(coro):
    import asyncio
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class StubTransport:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = 0

    async def classify(self, model_ref, context):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


def test_assist_no_provider_without_transport_or_model_ref():
    assist = pd.DecisionAssist(None, enabled=True, model_ref="")
    outcome = _run(assist.assist(_request()))
    assert outcome.status == pd.STATUS_REJECTED
    assert outcome.reason_code == pd.REASON_NO_PROVIDER


def test_assist_unavailable_transport_maps_to_unavailable_status():
    transport = StubTransport(exc=pd.DecisionTransportUnavailable("typesafe/jev-latest"))
    assist = pd.DecisionAssist(transport, enabled=True, model_ref="typesafe/jev-latest")
    outcome = _run(assist.assist(_request()))
    assert outcome.status == pd.STATUS_UNAVAILABLE
    assert outcome.reason_code == pd.REASON_NO_PROVIDER


def test_assist_generic_transport_error_rejected():
    transport = StubTransport(exc=RuntimeError("boom"))
    assist = pd.DecisionAssist(transport, enabled=True, model_ref="x/y")
    outcome = _run(assist.assist(_request()))
    assert outcome.status == pd.STATUS_REJECTED
    assert outcome.reason_code == pd.REASON_PROVIDER_ERROR


def test_assist_accepts_and_dedupes_same_request():
    transport = StubTransport(_choice_result("b", 0.88))
    assist = pd.DecisionAssist(transport, enabled=True, model_ref="x/y")
    first = _run(assist.assist(_request()))
    assert first.ok and first.choice == "b"
    second = _run(assist.assist(_request()))
    assert second.reason_code == pd.REASON_DUPLICATE
    assert transport.calls == 1


def test_assist_not_applicable_for_none_request():
    assist = pd.DecisionAssist(StubTransport(), enabled=True, model_ref="x/y")
    outcome = _run(assist.assist(None))
    assert outcome.reason_code == pd.REASON_NOT_APPLICABLE


# ── 应用闸（宿主权威） ──────────────────────────────────────────────

def test_task_gate_applies_only_on_specificity_tie():
    tied = _task_candidates(("a", 3, 4), ("b", 3, 6))
    outcome = pd.DecisionOutcome(status=pd.STATUS_ACCEPTED,
                                 domain=pd.DOMAIN_TASK_IDENTIFICATION,
                                 request_id="x", choice="b", confidence=0.9)
    assert pd.apply_task_choice(outcome, tied, "a") == "b"
    strict = _task_candidates(("a", 5, 4), ("b", 3, 6))
    assert pd.apply_task_choice(outcome, strict, "a") == "a"


def test_task_gate_rejects_out_of_set_and_non_accepted():
    tied = _task_candidates(("a", 3, 4), ("b", 3, 6))
    rogue = pd.DecisionOutcome(status=pd.STATUS_ACCEPTED,
                               domain=pd.DOMAIN_TASK_IDENTIFICATION,
                               request_id="x", choice="zzz", confidence=0.99)
    assert pd.apply_task_choice(rogue, tied, "a") == "a"
    rejected = pd.DecisionOutcome(status=pd.STATUS_REJECTED,
                                  domain=pd.DOMAIN_TASK_IDENTIFICATION,
                                  request_id="x", choice="b",
                                  reason_code=pd.REASON_LOW_CONFIDENCE)
    assert pd.apply_task_choice(rejected, tied, "a") == "a"


def test_template_gate_only_within_tied_top():
    outcome = pd.DecisionOutcome(status=pd.STATUS_ACCEPTED,
                                 domain=pd.DOMAIN_TEMPLATE_CANDIDATES,
                                 request_id="x", choice="tpl-2", confidence=0.9)
    assert pd.apply_template_tie_break(outcome, ("tpl-1", "tpl-2"),
                                       "tpl-1") == "tpl-2"
    assert pd.apply_template_tie_break(outcome, ("tpl-1", "tpl-3"),
                                       "tpl-1") == "tpl-1"


def test_clarification_gate_cannot_create_from_nothing():
    yes = pd.DecisionOutcome(status=pd.STATUS_ACCEPTED,
                             domain=pd.DOMAIN_CLARIFICATION_NEEDED,
                             request_id="x", choice="yes", confidence=0.9)
    assert pd.apply_clarification_signal(yes, []) == []
    enriched = pd.apply_clarification_signal(yes, ["low_confidence"])
    assert pd.CLARIFICATION_PROMOTE_REASON in enriched
    assert "low_confidence" in enriched
    no = pd.DecisionOutcome(status=pd.STATUS_ACCEPTED,
                            domain=pd.DOMAIN_CLARIFICATION_NEEDED,
                            request_id="x", choice="no", confidence=0.9)
    assert pd.apply_clarification_signal(no, ["low_confidence"]) == ["low_confidence"]


def test_no_payload_leakage_in_projection():
    """出题投影不得携带长文/地理数据——state 只有白名单键。"""
    req = pd.build_task_identification_request(
        "机密长文" * 500,
        task_candidates=_task_candidates(("a", 3, 4), ("b", 3, 6)),
        matched_task="a",
        state_fields={"scope_name": "成都", "measure_hint": "数量",
                      "raw_query": "机密长文" * 500})
    assert req is not None
    assert "raw_query" not in req.state
    assert set(req.state) <= set(pd._TASK_KEYS)
