"""ADR-0204 D3/D4：capability dispatch bind 单点接入 + dispatch 证据。

覆盖：
- ``bind_tool_capability`` 双面求值（allowed 带 rank/备选证据 / refused
  带 typed decision + 证据 / skipped 早退 / kill switch）；
- ``ToolDispatchService.dispatch`` 单点接入：legacy 路径拒绝 + dedup 槽
  释放 + ``capability_evidence`` additive 字段；
- ``TurnEvidence.add_capability_dispatch`` 有界证据累加。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.tool_dispatch_service import ToolDispatchService

# 承袭 test_audit_1395 的 patch 模式：bind 在调用点延迟导入
# plan_candidates_v8 —— patch 模块属性即全链路生效。


@pytest.fixture(autouse=True)
def _clean_bind_env(monkeypatch):
    monkeypatch.delenv("GIS_CAPABILITY_DISPATCH_BIND", raising=False)
    yield
    monkeypatch.delenv("GIS_CAPABILITY_DISPATCH_BIND", raising=False)


def _patch_plan(monkeypatch, plan):
    import app.services.gis_harness.candidate_planner_v8 as cp

    monkeypatch.setattr(cp, "plan_candidates_v8", lambda cap, ctx, **kw: plan)


def _eligible_plan(cap, tool_id):
    from app.services.gis_harness.candidate_planner_v8 import (
        Candidate,
        CandidatePlan,
    )
    from app.services.gis_harness.qualification_v8 import (
        QualificationResult,
        QualificationStatus,
    )

    return CandidatePlan(
        capability_id=cap,
        candidates=[
            Candidate(kind="tool", id="alt_tool",
                      qualification=QualificationResult(
                          status=QualificationStatus.ELIGIBLE),
                      latency_class="fast", reliability_penalty=0.0,
                      score=0.0),
            Candidate(kind="tool", id=tool_id,
                      qualification=QualificationResult(
                          status=QualificationStatus.ELIGIBLE),
                      latency_class="medium", reliability_penalty=0.0,
                      score=1.0),
        ],
        excluded=[],
    )


def _ineligible_plan(cap, tool_id):
    from app.services.gis_harness.candidate_planner_v8 import (
        Candidate,
        CandidatePlan,
    )
    from app.services.gis_harness.qualification_v8 import (
        QualificationReason,
        QualificationResult,
        QualificationStatus,
    )

    return CandidatePlan(
        capability_id=cap,
        candidates=[
            Candidate(kind="tool", id="alt_tool",
                      qualification=QualificationResult(
                          status=QualificationStatus.ELIGIBLE),
                      latency_class="fast", reliability_penalty=0.0,
                      score=0.0),
        ],
        excluded=[{
            "kind": "tool",
            "id": tool_id,
            "qualification": QualificationResult(
                status=QualificationStatus.INELIGIBLE,
                reasons=[QualificationReason(
                    check="offline", observed="online_only",
                    expected="local", hint="run with network or pick local")],
            ).to_dict(),
        }],
    )


def _tc(name: str, args: dict | None = None) -> dict:
    return {"id": "call_1", "function": {"name": name, "arguments": args or {}}}


# ── bind_tool_capability ─────────────────────────────────────────────


def test_bind_skipped_when_no_capabilities(monkeypatch):
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        bind_tool_capability,
    )

    reg = MagicMock()
    reg.metadata.return_value = {"tier": 1}
    assert bind_tool_capability("plain_tool", registry=reg) is None


def test_bind_kill_switch(monkeypatch):
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        bind_tool_capability,
    )

    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "0")
    reg = MagicMock()
    reg.metadata.return_value = {"capabilities": ["cap_x"]}
    assert bind_tool_capability("t", registry=reg) is None


def test_bind_allowed_carries_rank_and_alternatives(monkeypatch):
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        bind_tool_capability,
    )

    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")
    _patch_plan(monkeypatch, _eligible_plan("cap_x", "my_tool"))
    reg = MagicMock()
    reg.metadata.return_value = {"capabilities": ["cap_x"]}

    outcome = bind_tool_capability("my_tool", registry=reg)
    assert outcome is not None and not outcome.refused
    ev = outcome.evidence
    assert ev["action"] == "allowed"
    assert ev["capabilities"] == ["cap_x"]
    assert ev["rank"] == 1
    assert ev["rank_capability"] == "cap_x"
    alts = ev["alternatives"]
    assert alts and alts[0]["id"] == "alt_tool"
    # 兼容包装：decision 面仍为 None
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        check_tool_capability_at_dispatch,
    )

    assert check_tool_capability_at_dispatch("my_tool", registry=reg) is None


def test_bind_no_refusal_when_only_models_eligible(monkeypatch):
    """review RB-P2：alternatives 只认 tool-kind —— 仅 model 合格时不可
    拒绝（model 不可 dispatch，拒绝会把 LLM 引向不可执行替代）。"""
    from app.services.gis_harness.candidate_planner_v8 import (
        Candidate,
        CandidatePlan,
    )
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        bind_tool_capability,
    )
    from app.services.gis_harness.qualification_v8 import (
        QualificationReason,
        QualificationResult,
        QualificationStatus,
    )

    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")
    plan = CandidatePlan(
        capability_id="cap_x",
        candidates=[Candidate(
            kind="model", id="seg_model@1",
            qualification=QualificationResult(
                status=QualificationStatus.ELIGIBLE),
            latency_class="slow", reliability_penalty=0.0, score=0.0)],
        excluded=[{
            "kind": "tool",
            "id": "bad_tool",
            "qualification": QualificationResult(
                status=QualificationStatus.INELIGIBLE,
                reasons=[QualificationReason(
                    check="offline", observed="online_only",
                    expected="local")]),
        }.copy()],
    )
    _patch_plan(monkeypatch, plan)
    reg = MagicMock()
    reg.metadata.return_value = {"capabilities": ["cap_x"]}
    assert bind_tool_capability("bad_tool", registry=reg) is not None
    outcome = bind_tool_capability("bad_tool", registry=reg)
    assert outcome is not None and not outcome.refused
    assert outcome.evidence["action"] == "allowed"


def test_bind_refused_carries_decision_and_evidence(monkeypatch):
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        CAPABILITY_INELIGIBLE_CODE,
        bind_tool_capability,
    )

    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")
    _patch_plan(monkeypatch, _ineligible_plan("cap_x", "bad_tool"))
    reg = MagicMock()
    reg.metadata.return_value = {"capabilities": ["cap_x"]}

    outcome = bind_tool_capability("bad_tool", registry=reg)
    assert outcome is not None and outcome.refused
    assert outcome.decision.code == CAPABILITY_INELIGIBLE_CODE
    assert outcome.evidence["action"] == "refused"
    assert outcome.evidence["code"] == CAPABILITY_INELIGIBLE_CODE
    assert outcome.evidence["alternatives"][0]["id"] == "alt_tool"
    # 兼容包装：#1395 语义逐位保留（二次求值，等值决策）
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        check_tool_capability_at_dispatch,
    )

    decision_again = check_tool_capability_at_dispatch("bad_tool", registry=reg)
    assert decision_again is not None
    assert decision_again.code == outcome.decision.code
    assert decision_again.capability_id == outcome.decision.capability_id
    assert decision_again.alternatives == outcome.decision.alternatives


def test_bind_passes_situation_through(monkeypatch):
    """dispatch 的 situation 形参必须抵达资格判定（ADR-0204 D3）。"""
    import app.services.gis_harness.candidate_planner_v8 as cp
    from app.services.gis_harness.hotpath_convergence.capability_bind import (
        bind_tool_capability,
    )
    from app.services.gis_harness.qualification_v8 import QualificationContext

    seen: dict = {}

    def _spy(cap, ctx, **kw):
        seen["cap"] = cap
        seen["ctx"] = ctx
        return _eligible_plan(cap, "t")

    monkeypatch.setattr(cp, "plan_candidates_v8", _spy)
    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")
    reg = MagicMock()
    reg.metadata.return_value = {"capabilities": ["cap_x"]}
    ctx = QualificationContext(task_hint="earthquake", offline=True)
    bind_tool_capability("t", registry=reg, situation=ctx, session_id="s1")
    assert seen["ctx"] is ctx
    assert seen["cap"] == "cap_x"


# ── ToolDispatchService 单点接入 ──────────────────────────────────────


def _service_with_caps(monkeypatch, caps):
    reg = MagicMock(dispatch=AsyncMock(return_value={"success": True}))
    reg.list_tools.return_value = ["bad_tool", "plain_tool"]
    reg.metadata.return_value = (
        {"tier": 1, "capabilities": caps} if caps else {"tier": 1})
    return ToolDispatchService(registry=reg), reg


@pytest.mark.asyncio
async def test_dispatch_legacy_path_refuses_and_releases_dedup(monkeypatch):
    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")
    _patch_plan(monkeypatch, _ineligible_plan("cap_x", "bad_tool"))
    service, reg = _service_with_caps(monkeypatch, ["cap_x"])

    executed: set = set()
    result = await service.dispatch(_tc("bad_tool"), "sess-bind", executed)
    assert result.status == "error"
    assert result.error_msg == "CAPABILITY_INELIGIBLE"
    assert result.raw_result["code"] == "CAPABILITY_INELIGIBLE"
    assert result.capability_evidence["action"] == "refused"
    assert result.llm_payload.startswith("Tool 'bad_tool' is INELIGIBLE")
    # dedup 占位已释放：纠正后的重试不会被在飞谎言拦截
    assert not executed
    # registry.dispatch 未被触达（拒绝发生在执行前，零副作用）
    reg.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_allowed_records_evidence_and_executes(monkeypatch):
    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")
    _patch_plan(monkeypatch, _eligible_plan("cap_x", "good_tool"))
    reg = MagicMock(dispatch=AsyncMock(return_value={"success": True}))
    reg.metadata.return_value = {"tier": 1, "capabilities": ["cap_x"]}
    service = ToolDispatchService(registry=reg)

    executed: set = set()
    result = await service.dispatch(_tc("good_tool"), "sess-bind", executed)
    assert result.status == "ok"
    ev = result.capability_evidence
    assert ev and ev["action"] == "allowed"
    assert ev["rank_capability"] == "cap_x"
    reg.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_no_capabilities_skips_bind_untouched(monkeypatch):
    """未声明 capability 的工具：bind 早退，行为逐位既有（零成本路径）。"""
    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "1")
    service, reg = _service_with_caps(monkeypatch, None)
    result = await service.dispatch(_tc("plain_tool"), "sess-bind", set())
    assert result.status == "ok"
    assert result.capability_evidence is None
    reg.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_kill_switch_restores_legacy_behavior(monkeypatch):
    monkeypatch.setenv("GIS_CAPABILITY_DISPATCH_BIND", "0")
    _patch_plan(monkeypatch, _ineligible_plan("cap_x", "bad_tool"))
    service, reg = _service_with_caps(monkeypatch, ["cap_x"])
    result = await service.dispatch(_tc("bad_tool"), "sess-bind", set())
    assert result.status == "ok"
    assert result.capability_evidence is None
    reg.dispatch.assert_awaited_once()


# ── TurnEvidence 证据累加 ────────────────────────────────────────────


def test_turn_evidence_capability_dispatch_bounded():
    from app.lib.runtime.evidence import TurnEvidence

    ev = TurnEvidence(request_id="r", session_id="s", turn_id="t", run_id=None)
    for i in range(20):
        ev.add_capability_dispatch({"tool": f"t{i}", "action": "allowed"})
    entries = ev.capability_dispatches()
    assert len(entries) == 16
    assert entries[0]["tool"] == "t4"  # FIFO
    summary = ev.to_summary()
    assert len(summary["capability_dispatches"]) == 16
    # 非 dict 输入诚实忽略
    ev.add_capability_dispatch("garbage")  # type: ignore[arg-type]
    assert len(ev.capability_dispatches()) == 16
