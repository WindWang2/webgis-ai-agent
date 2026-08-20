"""Bounded LLM rendering of the session cartography harness verdict.

Agent ↔ harness 双向闭环的两个消费面共用：
- turn 开始注入（chat 路由组装，bridge 附着在用户消息尾部）；
- ``webgis_cartography_status`` 只读工具的 summary。

渲染是证据投影，不是数据通道：字段逐项封顶，整体再硬截断，防止把
verdict 变相当成 MapSpec/观测数据的搬运口。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# 注入针对当前 MapSpec 代的一切 verdict：pass / fail / not_evaluated 都注入
# 微型三态 token——沉默不再是 pass（#657）。superseded 属于被新意图取代的
# 旧代（用户已向前走），无制图活动的会话不注入。
_SKIP_STATUSES = {"superseded"}
_SKIP_REASONS = {"no_session_harness", "no_mapspec_mutation"}
_PASS_STATUSES = {"passed", "passed_with_warnings"}

_VERDICT_MARKER = "CARTOGRAPHY_VERDICT"
_MAX_FAILED_CHECKS = 3
_MAX_MESSAGE_CHARS = 200
_MAX_TOTAL_CHARS = 1500
_STATUS_TOOL_NAME = "webgis_cartography_status"


def _clip(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def should_inject_verdict(
    review: Optional[Dict[str, Any]],
    current_fingerprint: Optional[str] = None,
) -> bool:
    """Return whether the stored verdict should enter the next Pi prompt.

    指纹守卫：verdict 的 ``mapspec_fingerprint`` 必须与当前 MapSpec 一致。
    跨代 verdict（指纹不匹配或缺失）描述的是旧地图状态，注入只会误导
    本 turn 的修正决策。当前指纹不可得（None）时同样不注入——无法验证
    就不当证据用，与 harness 的诚实语义一致。
    """
    if not isinstance(review, dict):
        return False
    cartography = review.get("cartography")
    if not isinstance(cartography, dict):
        return False
    if cartography.get("status") in _SKIP_STATUSES:
        return False
    if cartography.get("termination_reason") in _SKIP_REASONS:
        return False
    fingerprint = cartography.get("mapspec_fingerprint")
    if not fingerprint or not current_fingerprint:
        return False
    return str(fingerprint) == str(current_fingerprint)


def _verdict_token(status: str) -> str:
    """归一化为 #657 的三态注入 token：pass | fail | not_evaluated。"""
    if status in _PASS_STATUSES:
        return "pass"
    if status == "not_evaluated":
        return "not_evaluated"
    return "fail"


def _project_failed_checks(checks: Any) -> List[Dict[str, Any]]:
    """最多 3 条 fail/not_evaluated check 的 LLM 投影。"""
    projected: List[Dict[str, Any]] = []
    if not isinstance(checks, list):
        return projected
    for check in checks:
        if not isinstance(check, dict):
            continue
        if check.get("status") not in ("fail", "not_evaluated"):
            continue
        entry: Dict[str, Any] = {
            "rule": _clip(check.get("rule"), 80),
            "status": str(check.get("status") or ""),
            "message": _clip(check.get("message"), _MAX_MESSAGE_CHARS),
        }
        suggested_fix = check.get("suggested_fix")
        if isinstance(suggested_fix, dict) and suggested_fix:
            entry["suggested_fix"] = suggested_fix
        elif suggested_fix:
            entry["suggested_fix"] = _clip(suggested_fix, _MAX_MESSAGE_CHARS)
        projected.append(entry)
        if len(projected) >= _MAX_FAILED_CHECKS:
            break
    return projected


def render_verdict_for_llm(review: Dict[str, Any]) -> str:
    """Render one bounded ``[CARTOGRAPHY_VERDICT]`` block for the model.

    输入是 ``_cartographic_review`` 存储形态（``session_id`` / ``cartography``
    / ``gate`` / ``overall_passed``）。调用方负责先过 :func:`should_inject_verdict`
    （prompt 注入路径）；工具路径直接渲染，把完整判定交还给 agent。

    #657：body 一律携带三态 ``verdict`` token（pass | fail | not_evaluated），
    省略 ``overall_passed``；pass 只渲染微型 token，原始 status、
    termination_reason、失败检查项与修复进度只出现在非 pass 上。
    """
    cartography = review.get("cartography") if isinstance(review.get("cartography"), dict) else {}
    token = _verdict_token(str(cartography.get("status") or "not_evaluated"))
    body: Dict[str, Any] = {
        "verdict": token,
        "mapspec_fingerprint": cartography.get("mapspec_fingerprint"),
    }
    if token != "pass":
        body.update({
            "status": str(cartography.get("status") or "not_evaluated"),
            "termination_reason": str(cartography.get("termination_reason") or ""),
            "desired_status": str(cartography.get("desired_status") or "not_evaluated"),
            "runtime_status": str(cartography.get("runtime_status") or "not_evaluated"),
        })
        failed_checks = _project_failed_checks(cartography.get("checks"))
        if failed_checks:
            body["failed_checks"] = failed_checks
        repair_attempts = cartography.get("repair_attempts")
        if isinstance(repair_attempts, list) and repair_attempts:
            body["repair_attempts"] = [
                {
                    "iteration": attempt.get("iteration"),
                    "status": str(attempt.get("status") or ""),
                    "repairability": str(attempt.get("repairability") or ""),
                }
                for attempt in repair_attempts[-2:]
                if isinstance(attempt, dict)
            ]
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True)
    if len(serialized) > _MAX_TOTAL_CHARS:
        # 兜底截断：逐字段封顶后正常到不了这里，防御未来字段膨胀。
        serialized = serialized[:_MAX_TOTAL_CHARS]
    if token == "pass":
        hint = "No corrective action needed."
    else:
        hint = "Plan a corrective webgis_* action."
    return (
        f"[{_VERDICT_MARKER}]\n"
        f"{serialized}\n"
        "Server-verified cartography harness verdict for the CURRENT map state. "
        f"{hint} "
        f"Query `{_STATUS_TOOL_NAME}` for full details."
    )
