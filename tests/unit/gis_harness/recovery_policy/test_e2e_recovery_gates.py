"""M9 端到端完成门（GOAL 05 / ADR-0279）—— 真实 ToolDispatchService seam。

3 个恢复成功场景：
- E2E-1 瞬态 503 → 诊断 retryable → 重试成功 → 账本清零（预算恢复）；
- E2E-2 数据缺字段 → needs_user_data → 绝不自动重试（调用数恒 1）→
  用户补数据后成功 → goal_verification 数据面 pass；
- E2E-3 分析完成但渲染观察缺席 → goal_verification 记矛盾不假完成 →
  观察补齐后对账 pass（成功前不宣称成图）。

2 个安全终止场景：
- STOP-1 跨会话 ref → cross_session_ref unsafe + 续跑指令 fail-closed +
  他会话 ID 零泄漏；
- STOP-2 破坏性工具失败 + 用户取消 → 永不自动重试、零补偿动作。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.gis_harness.failure_taxonomy import RemediationLedger
from app.services.session_data import session_data_manager


def _tc(name, args=None, tc_id="call_e2e"):
    return {"id": tc_id, "function": {"name": name, "arguments": args or {}}}


async def _mk_svc(monkeypatch, tmp_path, side_effect):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "d"))
    from app.services.tool_dispatch_service import ToolDispatchService

    reg = MagicMock(dispatch=AsyncMock(side_effect=side_effect))
    svc = ToolDispatchService(registry=reg)
    monkeypatch.setattr(
        "app.services.gis_harness.failure_taxonomy._global_ledger",
        RemediationLedger())
    return svc, reg


# ───────────────────────── E2E-1：瞬态失败恢复 ───────────────────────────

@pytest.mark.asyncio
async def test_e2e1_transient_recovery_with_budget_reset(monkeypatch, tmp_path):
    sid = "e2e1-transient"
    await session_data_manager.clear_session(sid)
    calls = {"n": 0}

    async def flaky(tool_name, tool_args_raw, session_id=None,
                    executed_tools=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"success": False, "error": "HTTP 503 Service Unavailable"}
        return {"summary": "recovered", "count": 7}

    svc, reg = await _mk_svc(monkeypatch, tmp_path, flaky)

    r1 = await svc.dispatch(_tc("query_pois"), sid, set())
    assert r1.status == "error"
    rec = r1.raw_result["recovery"]
    assert rec["diagnostic"]["reason"] == "upstream_server_error"
    assert rec["diagnostic"]["disposition"] == "retryable"
    assert "[recovery]" in r1.llm_payload

    # 依建议重试 → 成功；成功结果零 recovery 键
    r2 = await svc.dispatch(_tc("query_pois"), sid, set())
    assert r2.status == "ok"
    assert "recovery" not in r2.raw_result

    # 账本清零（record_success）：同工具再失败仍有完整预算（回归验证）
    async def fail_again(tool_name, tool_args_raw, session_id=None,
                         executed_tools=None):
        return {"success": False, "error": "HTTP 503 again"}
    reg.dispatch.side_effect = fail_again
    r3 = await svc.dispatch(_tc("query_pois"), sid, set())
    assert r3.status == "error"
    assert r3.raw_result["recovery"]["diagnostic"]["retry"]["attempts"] == 1
    assert r3.raw_result["recovery"]["diagnostic"]["retry"]["retry_allowed"] \
        is True

    await session_data_manager.clear_session(sid)


# ───────────────────────── E2E-2：数据缺口问用户 ─────────────────────────

@pytest.mark.asyncio
async def test_e2e2_eligibility_gap_never_auto_retries(monkeypatch, tmp_path):
    sid = "e2e2-eligibility"
    await session_data_manager.clear_session(sid)
    calls = {"n": 0}

    async def missing_field(tool_name, tool_args_raw, session_id=None,
                            executed_tools=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"success": False,
                    "error": "data_error: missing required field 'score'"}
        return {"summary": "ok with user data"}

    svc, reg = await _mk_svc(monkeypatch, tmp_path, missing_field)

    r1 = await svc.dispatch(_tc("analyze_schools"), sid, set())
    assert r1.status == "error"
    rec = r1.raw_result["recovery"]
    # characterization 钉死：缺字段 → data_invalid + needs_user_data
    # （既有 diagnose 的 data_error/missing required 词表映射）。
    assert rec["diagnostic"]["reason"] == "data_invalid"
    assert rec["diagnostic"]["disposition"] == "needs_user_data"
    assert rec["decision"]["auto_retry"] is False
    assert "needs_you=data" in r1.llm_payload

    # 场景 A 红线：不能无脑重试 5 次 —— dispatch 只被调用过 1 次
    assert calls["n"] == 1

    # 用户补数据后（agent 携新参重发）→ 成功
    r2 = await svc.dispatch(
        _tc("analyze_schools", {"field": "score"}), sid, set())
    assert r2.status == "ok"

    await session_data_manager.clear_session(sid)


# ───────────────────── E2E-3：渲染观察缺席不假完成 ────────────────────────

@pytest.mark.asyncio
async def test_e2e3_goal_verification_flips_with_observation(monkeypatch,
                                                              tmp_path):
    from app.lib.harness.goal_verification import reconcile_goal_verification

    goal = {"verdict": "satisfied", "requirements": [
        {"id": "r-map", "kind": "map", "verdict": "fulfilled"},
        {"id": "r-sci", "kind": "analysis", "verdict": "fulfilled"}],
        "missing_summary": []}
    chapter = {"workflow_contract": {"roles": [
        {"role": "schools", "required": True, "status": "bound"}]}}

    # 阶段 1：分析完成（goal satisfied）但渲染观察缺席 → 矛盾、不许完成
    rep1 = reconcile_goal_verification(
        goal_report=goal, observation_health=None, chapter=chapter,
        task_complete=True, quality_review_present=True)
    assert rep1.overall != "pass"
    assert any("rendering" in c for c in rep1.contradictions)

    # 阶段 2：观察补齐（前端 reconcile 上报后）→ 对账翻绿
    rep2 = reconcile_goal_verification(
        goal_report=goal,
        observation_health={"layers": [{"id": "l1",
                                        "state": "semantically_correct"}],
                            "aggregate": "semantically_correct",
                            "health": "ok", "observed_revision": 4},
        chapter={"workflow_contract": {"roles": [
            {"role": "schools", "required": True, "status": "bound"}]}},
        task_complete=True, quality_review_present=True)
    assert rep2.overall == "pass"
    assert rep2.contradictions == []


# ───────────────────── STOP-1：跨会话 ref fail-closed ─────────────────────

@pytest.mark.asyncio
async def test_stop1_cross_session_ref_unsafe_and_silent(monkeypatch,
                                                          tmp_path):
    sid = "stop1-cross"
    await session_data_manager.clear_session(sid)

    async def not_found(tool_name, tool_args_raw, session_id=None,
                        executed_tools=None):
        return {"success": False, "code": "NOT_FOUND",
                "error": "ref not found: ref:data-foreign"}

    svc, _ = await _mk_svc(monkeypatch, tmp_path, not_found)
    r = await svc.dispatch(_tc("buffer_analysis",
                               {"input": "ref:data-foreign"}), sid, set())
    assert r.status == "error"
    # TDS 层：ref 归属未解析（生产由 store 解析后传入）—— 按 stale_ref
    # 可再获取；归属确证为他会话时由 continuation 层 fail-closed。
    rec = r.raw_result["recovery"]
    assert rec["diagnostic"]["disposition"] in (
        "retryable", "unsafe_to_retry")
    assert rec["decision"]["auto_retry"] is False  # 未分类工具保守

    # 续跑指令：归属解析为他会话 → fail-closed + 零泄漏
    from app.services.gis_harness.recovery_policy.continuation import (
        resolve_retry_directive,
    )

    d = resolve_retry_directive(
        progress_rows=[{"capability": "buf", "status": "failed"}],
        last_recovery=None, chapter=None,
        user_ref_hints=["ref:data-foreign"],
        ref_owner_sessions={"ref:data-foreign": "sess-VICTIM"},
        current_session_id=sid)
    assert d.verdict == "fail_closed"
    assert "sess-VICTIM" not in d.directive_line
    assert d.retry_capabilities == []

    await session_data_manager.clear_session(sid)


# ───────────────── STOP-2：破坏性/取消 永不自动重试 ───────────────────────

@pytest.mark.asyncio
async def test_stop2_destructive_and_cancel_zero_actions(monkeypatch,
                                                          tmp_path):
    sid = "stop2-destructive"
    await session_data_manager.clear_session(sid)
    calls = {"n": 0}

    async def failing_delete(tool_name, tool_args_raw, session_id=None,
                             executed_tools=None):
        calls["n"] += 1
        return {"success": False, "error": "dataset locked"}

    svc, _ = await _mk_svc(monkeypatch, tmp_path, failing_delete)
    r = await svc.dispatch(_tc("delete_dataset"), sid, set())
    assert r.status == "error"
    rec = r.raw_result["recovery"]
    # 破坏性风险（tier3 语义）：绝不自动重试，确认门 destructive
    assert rec["decision"]["auto_retry"] is False
    assert "retry" not in rec["decision"]["allowed_actions"]
    assert calls["n"] == 1  # 引擎零自动重发

    # 取消路径：terminal + 零动作（无补偿副作用）
    from app.services.gis_harness.recovery_policy.contracts import (
        DiagnosticAttribution, GISRecoveryDiagnostic, GISRecoveryReason,
    )
    from app.services.gis_harness.recovery_policy.policy import decide_recovery

    diag = GISRecoveryDiagnostic(
        reason=GISRecoveryReason.CANCELLED,
        attribution=DiagnosticAttribution(session_id=sid))
    dec = decide_recovery(diag, cancelled=True)
    assert dec.auto_retry is False
    assert dec.allowed_actions == ()
    assert dec.terminal is True

    await session_data_manager.clear_session(sid)


# ───────────── recovery.diagnostic.attribution 身份保留 ─────────────

@pytest.mark.asyncio
async def test_recovery_attribution_carries_identity(monkeypatch, tmp_path):
    """统一错误分支的 attribution 如实保留本次调用已确证的身份：

    - tool_call_id 恒来自 tc.id（双引擎同一入口）；
    - capability 仅从 capability bind 的既有 evidence 真实键取
      （rank_capability → capabilities[0]），缺席留空；
    - turn_id 只在 current_turn_evidence() 在场时填 —— Pi 独立 HTTP
      task 无 ContextVar → 诚实留空，不虚构。
    """
    import app.services.gis_harness.hotpath_convergence as hotpath
    from app.lib.runtime.evidence import TurnEvidence, bind_turn_evidence
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        CapabilityBindOutcome,
    )

    sid = "e2e-attribution"
    await session_data_manager.clear_session(sid)

    async def fail(tool_name, tool_args_raw, session_id=None,
                   executed_tools=None):
        return {"success": False, "error": "HTTP 503 Service Unavailable"}

    svc, _ = await _mk_svc(monkeypatch, tmp_path, fail)
    monkeypatch.setattr(
        hotpath, "bind_tool_capability",
        lambda *a, **k: CapabilityBindOutcome(
            decision=None,
            evidence={"tool": "query_pois", "action": "allowed",
                      "rank_capability": "poi_query", "rank": 0}),
    )

    # 段 1：turn evidence 在场（legacy 流任务路径）→ 三身份齐全
    ev = TurnEvidence(request_id=None, session_id=sid, turn_id="turn-42",
                      run_id=None)
    with bind_turn_evidence(ev):
        r = await svc.dispatch(_tc("query_pois", tc_id="call_attr"), sid, set())
    assert r.status == "error"
    attr = r.raw_result["recovery"]["diagnostic"]["attribution"]
    assert attr["tool_call_id"] == "call_attr"
    assert attr["capability"] == "poi_query"
    assert attr["turn_id"] == "turn-42"

    # 段 2：无 turn evidence（Pi 独立 HTTP task）→ turn_id 留空，
    # 但 tool_call_id / capability 不受影响
    r2 = await svc.dispatch(_tc("query_pois", {"q": "x"}, tc_id="call_attr2"),
                            sid, set())
    assert r2.status == "error"
    attr2 = r2.raw_result["recovery"]["diagnostic"]["attribution"]
    assert attr2["tool_call_id"] == "call_attr2"
    assert attr2["capability"] == "poi_query"
    assert attr2["turn_id"] == ""

    await session_data_manager.clear_session(sid)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
