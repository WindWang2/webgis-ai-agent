"""决策辅助生产接线（goal-pi-native-decision-governance WP2）。

唯一入口 :func:`assist_map_request_intent`：在 async 工具上下文中对确定性
intent 做两类有界决策辅助——

1. **任务识别**（``task_identification``）：候选 ≥2 且 assist 平级采纳
   （``apply_task_choice`` 闸）→ 经既有 :func:`merge_intent_hints`
   （protected 护栏 + 派生重算 + ``hint_applied`` 审计）应用；采纳与否、
   responseId/置信度都记录进 meta（供 receipt 披露）。
2. **澄清默认项**（``clarification_needed`` 消费侧）：仅当确定性策略已
   产出 task 反问时，assist 采纳的任务若是反问选项之一 → 置为默认项并
   追加 reason code。不能凭空制造澄清、不能增删选项。

红线：feature gate 关闭 → 不出题、不触达传输层、原 intent 原样返回；
任何 rejected/unavailable 都回落确定性结果（meta 照实记录原因）。
模板候选域（``template_candidates``）的 tie 探测点在 recipe 选择之后
（``plan_from_intent`` 内部），本期不接线（SCOPE 已记录）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.chat import pi_decision as pd

logger = logging.getLogger(__name__)

#: 进程级单例（传输层可注入；生产默认 None → no_provider 确定性回落）
_ASSIST: Optional[pd.DecisionAssist] = None


def get_decision_assist() -> pd.DecisionAssist:
    global _ASSIST
    if _ASSIST is None:
        _ASSIST = pd.DecisionAssist(
            transport=None,
            enabled=pd.decision_assist_enabled(),
            model_ref=pd.default_model_ref(),
        )
    return _ASSIST


def set_decision_assist_transport(transport: pd.DecisionTransport) -> None:
    """注册真实传输层（扩展 classify 适配器）；仅影响之后的 assist 调用。"""
    global _ASSIST
    current = get_decision_assist()
    _ASSIST = pd.DecisionAssist(
        transport=transport,
        enabled=current.enabled,
        model_ref=pd.default_model_ref(),
    )


def reset_decision_assist() -> None:
    """测试/进程回收用：重置单例。"""
    global _ASSIST
    _ASSIST = None


def _task_state_fields(intent: Any) -> dict[str, str]:
    return {
        "task_type": getattr(intent, "task", ""),
        "subject_type": getattr(getattr(intent, "subject", None), "type", ""),
        "scope_name": getattr(getattr(intent, "scope", None), "name", ""),
        "confidence": "",  # 白名单外键不进 state；confidence 走 meta
    }


def _apply_clarification_default(intent: Any, assisted_task: str,
                                 outcome: pd.DecisionOutcome) -> bool:
    """把 assist 采纳的任务置为 task 反问默认项（仅当已是选项之一）。

    对 ``intent.clarification``（序列化 dict）做数据级修改：不改问题数、
    不增删选项、只移动 ``is_default`` 标记 + 追加 reason code。
    """
    clarification = getattr(intent, "clarification", None)
    if not isinstance(clarification, dict):
        return False
    questions = clarification.get("questions")
    if not isinstance(questions, list):
        return False
    applied = False
    for question in questions:
        if not isinstance(question, dict) or question.get("slot") != "task":
            continue
        options = question.get("options")
        if not isinstance(options, list):
            continue
        values = [opt.get("value") for opt in options if isinstance(opt, dict)]
        if assisted_task not in values:
            continue
        for opt in options:
            if isinstance(opt, dict):
                opt["is_default"] = opt.get("value") == assisted_task
        applied = True
    if applied:
        reasons = clarification.setdefault("reason_codes", [])
        if isinstance(reasons, list):
            tag = (f"decision_assist_default:{assisted_task}"
                   f":{outcome.response_id or outcome.request_id}")
            if tag not in reasons:
                reasons.append(tag)
    return applied


async def assist_map_request_intent(
    query: str,
    intent: Any,
    *,
    assist: Optional[pd.DecisionAssist] = None,
    scope: str = "",
) -> tuple[Any, dict[str, Any]]:
    """对确定性 intent 执行有界决策辅助；返回 ``(intent, decision_meta)``。

    ``scope`` 传入 session_id（dedupe 会话隔离）。任何路径都不抛出
    （传输层异常已在 DecisionAssist 内 typed 化）。
    """
    from app.services.gis_harness import intent_semantic as semantic

    meta: dict[str, Any] = {"enabled": False, "assists": []}
    helper = assist if assist is not None else get_decision_assist()
    if not helper.enabled:
        return intent, meta
    meta["enabled"] = True

    # 1. 任务识别（候选 ≥2 才出题；唯一胜者不出题 = not_applicable；
    #    decide_task 的 span 裁决语义由 apply_task_choice 全序平级闸尊重）
    decision = semantic.decide_task(query)
    request = pd.build_task_identification_request(
        query, task_candidates=decision.candidates, matched_task=intent.task,
        state_fields=_task_state_fields(intent))
    outcome = await helper.assist(request, scope=scope)
    assisted_task = pd.apply_task_choice(outcome, decision.candidates,
                                         intent.task)
    entry: dict[str, Any] = {
        "domain": pd.DOMAIN_TASK_IDENTIFICATION,
        "status": outcome.status,
        "reason_code": outcome.reason_code,
        "response_id": outcome.response_id,
        "confidence": round(outcome.confidence, 4),
        "applied": assisted_task != intent.task,
    }
    if entry["applied"]:
        intent = semantic_merge_task_hint(intent, assisted_task)
        # 澄清默认项联动（仅当策略已产出 task 反问且采纳任务在选项中）
        entry["clarification_default_applied"] = _apply_clarification_default(
            intent, assisted_task, outcome)
    meta["assists"].append(entry)
    logger.info("pi_decision_assist task_identification status=%s reason=%s "
                "applied=%s response_id=%s", outcome.status,
                outcome.reason_code, entry["applied"], outcome.response_id)
    return intent, meta


def semantic_merge_task_hint(intent: Any, hinted_task: str) -> Any:
    """经既有 merge_intent_hints 应用任务覆盖（protected 护栏 + 派生重算）。"""
    from app.services.gis_harness.intent import merge_intent_hints

    merged = merge_intent_hints(intent, {"task": hinted_task})
    if getattr(merged, "task", "") == hinted_task and hinted_task != getattr(intent, "task", ""):
        merged.hint_applied = list(merged.hint_applied) + [
            f"decision_assist:{hinted_task}"]
    return merged
