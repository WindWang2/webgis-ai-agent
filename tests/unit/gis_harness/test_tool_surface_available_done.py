"""R2-P1（qc-loop round 2）回归锁：_chapter_phase 的 done 集合必须含 "available"。

历史缺陷：_mark_progress 满足 data_requirement 时写入的状态是
``"available"``（session_plan.py），而 _chapter_phase 的 done_states
只认 {"complete","completed","skipped","done","ok"} —— 已满足行被永久
判 pending，阶段卡死 PHASE_DATA，pi 路径每轮据此编译工具面，导致
("core","statistics") 等分析域工具永不激活。
"""

from app.services.gis_harness.tool_surface import (
    PHASE_DATA,
    PHASE_PLANNING,
    _chapter_phase,
)


def test_available_data_rows_are_done_not_pending():
    chapter = {
        "data_requirements": [
            {"capability": "stat", "status": "available"},
            {"capability": "base", "status": "complete"},
        ],
        "analysis_steps": [],
    }
    phase, evidence = _chapter_phase(chapter)
    assert phase != PHASE_DATA
    assert phase == PHASE_PLANNING


def test_genuinely_pending_rows_still_hold_data_phase():
    chapter = {
        "data_requirements": [
            {"capability": "stat", "status": "available"},
            {"capability": "geo", "status": "pending"},
        ],
        "analysis_steps": [],
    }
    phase, evidence = _chapter_phase(chapter)
    assert phase == PHASE_DATA
    assert "1 pending data rows" in evidence[0]
