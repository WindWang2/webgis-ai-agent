"""M3 Runtime 接线测试 —— TDS 单 seam 产出 recovery，双引擎同源消费。

验收锚点（GOAL 05 / ADR-0279 M3）：
- TDS 错误结果 additive ``recovery`` 键（diagnostic+decision bounded）；
- llm_payload 携带单行 [recovery] note（Pi HTTP 回包与 legacy tool_result
  同一文案源）；
- step_error SSE 契约透传 recovery（execution_engine 消费路径）；
- SessionPlan 失败分支落账 last_recovery，投影出现 [GIS Recovery] 行；
- 成功路径零新键（additive 纪律）；重复失败不重写 last_recovery。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.gis_harness.failure_taxonomy import RemediationLedger
from app.services.session_data import session_data_manager


def _tc(name, args=None, tc_id="call_rec"):
    return {"id": tc_id, "function": {"name": name, "arguments": args or {}}}


@pytest.mark.asyncio
async def test_error_result_carries_recovery_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "d"))
    sid = "m3-wiring-429"
    await session_data_manager.clear_session(sid)

    from app.services.tool_dispatch_service import ToolDispatchService

    reg = MagicMock(dispatch=AsyncMock(return_value={
        "success": False,
        "error": "upstream returned HTTP 429 Too Many Requests",
    }))
    svc = ToolDispatchService(registry=reg)
    monkeypatch.setattr(
        "app.services.gis_harness.failure_taxonomy._global_ledger",
        RemediationLedger())

    r = await svc.dispatch(_tc("poi_query"), sid, set())
    assert r.status == "error"
    rec = r.raw_result["recovery"]
    assert rec["diagnostic"]["reason"] == "rate_limited"
    assert rec["diagnostic"]["disposition"] == "retryable"
    # 决策在场：读工具（poi_query 前缀无 tier 信息 → 保守无自动重试，
    # 但诊断与建议动作完整）
    assert rec["decision"]["disposition"] == "retryable"
    assert isinstance(rec["decision"]["allowed_actions"], list)
    # LLM 单行 note 双引擎同源
    assert "[recovery]" in r.llm_payload
    assert "rate_limited" in r.llm_payload

    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_success_result_has_no_recovery_key(monkeypatch, tmp_path):
    """成功路径零新键（additive 纪律）。"""
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "d"))
    sid = "m3-wiring-ok"
    await session_data_manager.clear_session(sid)

    from app.services.tool_dispatch_service import ToolDispatchService

    reg = MagicMock(dispatch=AsyncMock(return_value={
        "summary": "ok", "count": 3,
    }))
    svc = ToolDispatchService(registry=reg)
    r = await svc.dispatch(_tc("buffer_analysis"), sid, set())
    assert r.status == "ok"
    assert "recovery" not in (r.raw_result or {})

    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_permission_failure_note_says_needs_user(monkeypatch, tmp_path):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "d"))
    sid = "m3-wiring-403"
    await session_data_manager.clear_session(sid)

    from app.services.tool_dispatch_service import ToolDispatchService

    reg = MagicMock(dispatch=AsyncMock(return_value={
        "success": False, "error": "403 permission denied for dataset",
    }))
    svc = ToolDispatchService(registry=reg)
    monkeypatch.setattr(
        "app.services.gis_harness.failure_taxonomy._global_ledger",
        RemediationLedger())
    r = await svc.dispatch(_tc("query_pois"), sid, set())
    assert r.raw_result["recovery"]["diagnostic"]["reason"] == \
        "permission_denied"
    assert "needs_you=approval" in r.llm_payload

    await session_data_manager.clear_session(sid)


def test_step_error_sse_carries_recovery():
    from app.services.chat.sse_contracts import TurnEventEmitter

    emitter = TurnEventEmitter(session_id="s1", turn_id="t1")
    payload = emitter.step_error(
        "task1", "step1", "tool_x", "boom",
        failure_class="tool_error",
        recovery={"diagnostic": {"reason": "rate_limited"},
                  "decision": {"auto_retry": False}})
    assert '"recovery"' in payload
    assert "rate_limited" in payload


def test_step_error_sse_without_recovery_unchanged():
    from app.services.chat.sse_contracts import TurnEventEmitter

    emitter = TurnEventEmitter(session_id="s1", turn_id="t1")
    payload = emitter.step_error(
        "task1", "step1", "tool_x", "boom",
        failure_class="tool_error")
    assert "recovery" not in payload


def test_recovery_from_outcome_extracts_payload():
    from app.services.chat.turn_recovery import recovery_from_outcome
    from app.services.tool_dispatch_service import ToolDispatchResult

    outcome = ToolDispatchResult(
        status="error", llm_payload="", slim_event=None, geojson_ref=None,
        raw_result={"recovery": {"diagnostic": {"reason": "stale_ref"},
                                  "decision": {}}}, error_msg="x")
    assert recovery_from_outcome(outcome)["diagnostic"]["reason"] == "stale_ref"
    assert recovery_from_outcome(None) is None
    ok = ToolDispatchResult(
        status="ok", llm_payload="", slim_event=None, geojson_ref=None,
        raw_result={"summary": "fine"}, error_msg="")
    assert recovery_from_outcome(ok) is None


@pytest.mark.asyncio
async def test_session_plan_persists_last_recovery(monkeypatch, tmp_path):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "d"))
    sid = "m3-plan-recovery"
    await session_data_manager.clear_session(sid)

    from app.services.session_plan import (
        apply_tool_result,
        format_session_plan_projection,
        load_session_plan,
    )

    # 先经意图工具建带能力行的章节
    await apply_tool_result(
        sid, "webgis_map_intent",
        {"plan": {"query": "成都小学", "capabilities": [
            {"capability": "buffer", "tools": ["buffer_analysis"]}],
            "data_requirements": []}},
        success=True)
    plan = await load_session_plan(sid, store=session_data_manager)
    assert plan is not None and plan.gis_chapter is not None

    # 失败分支：带 recovery payload → last_recovery 落账
    await apply_tool_result(
        sid, "buffer_analysis",
        {"success": False, "error": "HTTP 503 service unavailable",
         "recovery": {
             "diagnostic": {"reason": "upstream_server_error",
                            "disposition": "retryable",
                            "retry": {"attempts": 1, "max_attempts": 2,
                                      "retry_allowed": True}},
             "decision": {"auto_retry": False}}},
        success=False)
    plan = await load_session_plan(sid, store=session_data_manager)
    assert plan.last_recovery is not None
    assert plan.last_recovery["diagnostic"]["reason"] == \
        "upstream_server_error"
    assert plan.last_recovery["tool"] == "buffer_analysis"

    # 投影出现 [GIS Recovery] 行（agent 下一 turn 可见）
    projection = format_session_plan_projection(plan)
    assert "[GIS Recovery]" in projection
    assert "upstream_server_error" in projection

    # 重复同形状失败：不重写（幂等落账）
    await apply_tool_result(
        sid, "buffer_analysis",
        {"success": False, "error": "HTTP 503 service unavailable",
         "recovery": {
             "diagnostic": {"reason": "upstream_server_error",
                            "disposition": "retryable",
                            "retry": {"attempts": 1, "max_attempts": 2,
                                      "retry_allowed": True}},
             "decision": {"auto_retry": False}}},
        success=False)
    plan2 = await load_session_plan(sid, store=session_data_manager)
    assert plan2.last_recovery == plan.last_recovery

    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_old_failure_shape_no_recovery_zero_drift(monkeypatch, tmp_path):
    """旧形状失败（无 recovery 键）→ last_recovery 不写、投影零新行。"""
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "d"))
    sid = "m3-plan-legacy"
    await session_data_manager.clear_session(sid)

    from app.services.session_plan import (
        apply_tool_result,
        format_session_plan_projection,
        load_session_plan,
    )

    await apply_tool_result(
        sid, "webgis_map_intent",
        {"plan": {"query": "q", "capabilities": [
            {"capability": "buffer", "tools": ["buffer_analysis"]}],
            "data_requirements": []}},
        success=True)
    await apply_tool_result(
        sid, "buffer_analysis",
        {"success": False, "error": "boom"},
        success=False)
    plan = await load_session_plan(sid, store=session_data_manager)
    assert plan.last_recovery is None
    assert "[GIS Recovery]" not in format_session_plan_projection(plan)

    await session_data_manager.clear_session(sid)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
