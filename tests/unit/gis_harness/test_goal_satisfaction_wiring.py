"""Goal Satisfaction 生产接线测试（ADR-0183 M3 / G5）。

锁定四条接线契约 + 零漂移红线：
- map_product_block 挂 additive ``goal_satisfaction`` 键（chapter 缺席时
  键省略 —— 旧调用零漂移）；
- ``_is_task_complete`` 折叠语义不变（goal partial 不改写既有完成布尔
  —— 决策 D-007：advisory，不抢产品裁决权威）；
- task_complete SSE / [GIS Plan] 投影行消费存储块（缺键 → 键/行省略）；
- 投影面只读存储块，不重算（块缺席 = 诚实缺席）。
"""
from __future__ import annotations

import json

from app.services.chat.pi_event_mapper import map_event_to_sse
from app.services.gis_harness.completion.contracts import MapCompletionResult
from app.services.gis_harness.completion.pipeline import (
    _is_task_complete,
    map_product_block,
)
from app.services.gis_harness.goal_satisfaction import (
    bounded_payload,
    goal_line_from_block,
)
from app.services.session_plan import SessionPlan, format_session_plan_projection


def _chapter():
    return {
        "query": "成都各区学校分布并比较",
        "intent": {
            "query": "成都各区学校分布并比较",
            "task": "distribution_overview",
            "scope": {"name": "成都", "level": "city"},
            "output_intents": ["map"],
            "export_intents": ["png"],
        },
        "analysis_steps": [
            {"capability": "poi.query", "purpose": "查询学校",
             "status": "complete"},
        ],
    }


def _result():
    return MapCompletionResult(
        status="complete", layer_status="valid",
        component_status="valid", render_status="verified",
        final_map_status="verified",
    )


def test_map_product_block_carries_goal_satisfaction():
    block = map_product_block(_result(), 7, chapter=_chapter())
    gs = block.get("goal_satisfaction")
    assert isinstance(gs, dict)
    assert gs["schema"] == "goal_satisfaction.v1"
    assert gs["verdict"] in ("satisfied", "partial", "blocked",
                             "failed", "not_evaluated")
    assert gs["signal"]
    # JSON-serializable（SSE 面）
    json.dumps(gs, ensure_ascii=False)


def test_map_product_block_without_chapter_omits_key():
    """零漂移：chapter=None（旧调用形状）→ 键整体省略。"""
    block = map_product_block(_result(), 7, chapter=None)
    assert "goal_satisfaction" not in block
    assert block["task_complete"] is True  # 既有折叠不受影响


def test_task_complete_semantics_unchanged_by_goal():
    """D-007：goal partial 不改写既有 task_complete 布尔（advisory）。"""
    chapter = _chapter()
    block = map_product_block(_result(), 7, chapter=chapter)
    # export:png 无回执 → goal 必为 partial/not_evaluated，但产品 READY。
    assert block["goal_satisfaction"]["verdict"] != "satisfied"
    assert _is_task_complete(block) is True


def test_bounded_payload_and_goal_line_from_block():
    gs = {"verdict": "partial", "signal": "continue",
          "counts": {"fulfilled": 2, "not_evaluated": 1},
          "missing_summary": ["export:png:export_receipt_absent"],
          "summary_line": "[GIS Goal] goal=partial signal=continue"}
    payload = bounded_payload(gs)
    assert payload["verdict"] == "partial"
    assert payload["signal"] == "continue"
    line = goal_line_from_block(gs)
    assert line.startswith("[GIS Goal]")
    assert "missing:export_receipt_absent" in line
    # 空/非法块 → 诚实缺席
    assert bounded_payload(None) is None
    assert bounded_payload({}) is None
    assert goal_line_from_block(None) == ""


def test_task_complete_sse_carries_goal_projection():
    event = {"type": "agent_settled"}
    stats = {
        "tool_step_count": 1, "final_text": "done",
        "map_product": {
            "status": "complete", "summary": "final",
            "goal_satisfaction": {
                "verdict": "partial", "signal": "continue",
                "counts": {"fulfilled": 1},
                "missing_summary": ["export:png:export_receipt_absent"],
                "summary_line": "[GIS Goal] goal=partial",
            },
        },
    }
    sse = map_event_to_sse(event, session_id="s1", turn_stats=lambda: stats)
    assert '"goal_satisfaction"' in sse
    assert '"verdict": "partial"' in sse
    assert '"signal": "continue"' in sse
    # 旧形状（无 goal_satisfaction 键）→ SSE 不携带，零漂移。
    sse_old = map_event_to_sse(
        event, session_id="s1",
        turn_stats=lambda: {"tool_step_count": 1, "final_text": "done",
                            "map_product": {"status": "complete",
                                            "summary": "final"}},
    )
    assert "goal_satisfaction" not in sse_old


def test_plan_projection_carries_goal_line_when_block_present():
    chapter = _chapter()
    chapter["map_product"] = {
        "status": "complete", "projection": "Map product: final",
        "goal_satisfaction": {
            "verdict": "partial", "signal": "continue",
            "counts": {"fulfilled": 1},
            "missing_summary": ["export:png:export_receipt_absent"],
            "summary_line": "[GIS Goal] goal=partial",
        },
    }
    plan = SessionPlan(envelope_id="e", session_id="s", user_goal="g",
                       gis_chapter=chapter)
    text = format_session_plan_projection(plan)
    assert "[GIS Goal] task=partial next=continue" in text
    assert "missing:export_receipt_absent" in text


def test_plan_projection_without_goal_block_zero_drift():
    chapter = _chapter()
    chapter["map_product"] = {"status": "complete",
                              "projection": "Map product: final"}
    plan = SessionPlan(envelope_id="e", session_id="s", user_goal="g",
                       gis_chapter=chapter)
    text = format_session_plan_projection(plan)
    assert "[GIS Goal]" not in text
    assert text.startswith("[SessionPlan]")  # 首行契约不变
