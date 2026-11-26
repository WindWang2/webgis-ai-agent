"""WP2 决策辅助接线（pi_decision_wiring）单元测试。

真实 resolve/decide_task/merge_intent_hints 管线 + 注入式 stub 传输
（编排被测物是 wiring 本身）；覆盖：disabled 零行为差、唯一胜者不出题、
平级 tie 采纳（含派生重算与审计）、特异性降级拒绝（评价任务不可降为
视觉任务）、不可用回落、澄清默认项联动（不凭空制造）。
"""
from __future__ import annotations

import pytest

from app.services.chat import pi_decision as pd
from app.services.chat.pi_decision_wiring import (
    assist_map_request_intent,
    reset_decision_assist,
)
from app.services.gis_harness.intent import resolve_map_request_intent

#: 真实查询（decide_task 实证）：
#:  - 平级 tie：两项 specificity=30，胜者 accessibility_analysis
TIE_QUERY = "某区域学校可达性与服务范围变化分析"
#:  - 降级对抗：胜者 risk_exposure(40)，assist 试图降级 analytical_density(38)
DOWNGRADE_QUERY = "成都各区小学密度每平方公里，有哪些风险"
#:  - 唯一胜者（候选 <2，不出题）
UNIQUE_QUERY = "成都小学分布"


class ScriptedTransport:
    """确定性 stub：按模型名回固定 choice（confidence 0.9）。"""

    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.contexts: list[dict] = []

    async def classify(self, model_ref, context):
        self.contexts.append(context)
        questions = context["questions"]
        (key, question), = questions.items()
        if question["type"] == "choice":
            answer = {"type": "choice", "choice": self.choice,
                      "probabilities": {self.choice: 0.9}, "confidence": 0.9}
        else:
            answer = {"type": "bool",
                      "probability": 0.9 if self.choice == "yes" else 0.2}
        return {"stopReason": "stop", "answers": {key: answer},
                "responseId": "resp-wiring-1", "usage": {"totalTokens": 7}}


def _assist(choice: str, *, enabled: bool = True) -> pd.DecisionAssist:
    return pd.DecisionAssist(ScriptedTransport(choice), enabled=enabled,
                             model_ref="stub/classifier")


def _resolve(query: str):
    return resolve_map_request_intent(query, record_metrics=False)


def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_decision_assist()
    yield
    reset_decision_assist()


def test_disabled_zero_behavior_change():
    intent = _resolve(TIE_QUERY)
    new_intent, meta = _run(assist_map_request_intent(
        TIE_QUERY, intent, assist=_assist("change_detection", enabled=False)))
    assert meta["enabled"] is False
    assert meta["assists"] == []
    assert new_intent.task == intent.task


def test_unique_winner_not_asked():
    intent = _resolve(UNIQUE_QUERY)
    transport = ScriptedTransport("anything")
    assist = pd.DecisionAssist(transport, enabled=True, model_ref="stub/x")
    new_intent, meta = _run(assist_map_request_intent(
        UNIQUE_QUERY, intent, assist=assist))
    assert transport.contexts == []  # 候选唯一 → 不出题、不触达传输
    assert meta["assists"] == [{
        "domain": pd.DOMAIN_TASK_IDENTIFICATION,
        "status": pd.STATUS_REJECTED,
        "reason_code": pd.REASON_NOT_APPLICABLE,
        "response_id": "", "confidence": 0.0, "applied": False,
    }]
    assert new_intent.task == intent.task


def test_tie_choice_applied_with_derived_recompute_and_audit():
    intent = _resolve(TIE_QUERY)
    assert intent.task == "accessibility_analysis"
    new_intent, meta = _run(assist_map_request_intent(
        TIE_QUERY, intent, assist=_assist("change_detection")))
    entry = meta["assists"][0]
    assert entry["status"] == pd.STATUS_ACCEPTED
    assert entry["applied"] is True
    assert entry["response_id"] == "resp-wiring-1"
    assert new_intent.task == "change_detection"
    # 派生重算 + 审计（merge_intent_hints 语义 + decision_assist 标记）
    assert any(a.startswith("task:accessibility_analysis->change_detection")
               for a in new_intent.hint_applied)
    assert "decision_assist:change_detection" in new_intent.hint_applied


def test_downgrade_attempt_rejected_by_specificity_gate():
    intent = _resolve(DOWNGRADE_QUERY)
    assert intent.task == "risk_exposure"  # SPEC_DECISION(40) 胜出
    new_intent, meta = _run(assist_map_request_intent(
        DOWNGRADE_QUERY, intent, assist=_assist("analytical_density")))
    entry = meta["assists"][0]
    # assist 采纳了（provider 答案合法），但 38 < 40 特异性闸拒绝应用
    assert entry["status"] == pd.STATUS_ACCEPTED
    assert entry["applied"] is False
    assert new_intent.task == "risk_exposure"


def test_unavailable_transport_falls_back_deterministically():
    class Down:
        async def classify(self, model_ref, context):
            raise pd.DecisionTransportUnavailable(model_ref)

    intent = _resolve(TIE_QUERY)
    assist = pd.DecisionAssist(Down(), enabled=True, model_ref="stub/x")
    new_intent, meta = _run(assist_map_request_intent(TIE_QUERY, intent,
                                                      assist=assist))
    assert meta["assists"][0]["status"] == pd.STATUS_UNAVAILABLE
    assert new_intent.task == "accessibility_analysis"


def test_clarification_default_flipped_only_within_existing_options():
    intent = _resolve(TIE_QUERY)
    # 预置策略已产出的 task 反问（模拟 ClarificationPolicy 结果序列化）
    intent.clarification = {
        "questions": [{
            "slot": "task",
            "prompt_zh": "需要哪种任务？",
            "prompt_en": "Which task?",
            "options": [
                {"value": "accessibility_analysis", "label_zh": "可达性",
                 "label_en": "", "is_default": True},
                {"value": "change_detection", "label_zh": "变化检测",
                 "label_en": "", "is_default": False},
            ],
            "reason_code": "task_ambiguous",
        }],
        "confidence": 0.4,
        "reason_codes": ["task_ambiguous"],
    }
    new_intent, meta = _run(assist_map_request_intent(
        TIE_QUERY, intent, assist=_assist("change_detection")))
    q = new_intent.clarification["questions"][0]
    defaults = [o["value"] for o in q["options"] if o["is_default"]]
    assert defaults == ["change_detection"]
    assert any(r.startswith("decision_assist_default:change_detection")
               for r in new_intent.clarification["reason_codes"])
    assert meta["assists"][0]["clarification_default_applied"] is True


def test_clarification_default_not_created_without_existing_question():
    """策略未触发（无 clarification）→ assist 不凭空制造反问。"""
    intent = _resolve(TIE_QUERY)
    new_intent, meta = _run(assist_map_request_intent(
        TIE_QUERY, intent, assist=_assist("change_detection")))
    assert getattr(new_intent, "clarification", None) in (None, {})
    assert meta["assists"][0].get("clarification_default_applied") is False


def test_out_of_set_choice_never_applied():
    """rogue provider 答案（不在候选闭集）→ 拒绝应用（unknown_choice）。"""
    intent = _resolve(TIE_QUERY)
    new_intent, meta = _run(assist_map_request_intent(
        TIE_QUERY, intent, assist=_assist("rogue_task")))
    entry = meta["assists"][0]
    assert entry["status"] == pd.STATUS_REJECTED
    assert entry["reason_code"] == pd.REASON_UNKNOWN_CHOICE
    assert new_intent.task == "accessibility_analysis"
