"""F06 — chokepoint situation 统一供给 + denial 决策记录契约测试
（ADR-0215 D3/D7）.

覆盖：
- dispatch 期 situation=None → auto-supply（4 条调用面等价 —— DoD 3）；
- kill switch / 供给关闭 → bare context 既有语义；
- caller dict/ctx 合并（caller facts win）；
- 拒绝路径：denial decision record（TOOL_CALLS riding）+ canonical
  reason codes + dedup 槽释放 + **no-secret 不变量**。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.tool_dispatch_service import ToolDispatchService


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("GIS_CAPABILITY_DISPATCH_BIND", "GIS_SITUATION_SUPPLY",
                 "GIS_TOOL_CREDENTIALS", "GIS_TOOL_PERMISSIONS",
                 "GIS_RUNTIME_OFFLINE"):
        monkeypatch.delenv(name, raising=False)
    from app.lib.tool_security import set_credential_presence_provider

    set_credential_presence_provider(None)
    yield
    set_credential_presence_provider(None)


def _patch_plan_capture(monkeypatch, plan):
    import app.services.gis_harness.candidate_planner_v8 as cp

    captured = {}

    def fake(cap, ctx, **kw):
        captured["ctx"] = ctx
        captured["session_id"] = kw.get("session_id", "")
        return plan

    monkeypatch.setattr(cp, "plan_candidates_v8", fake)
    return captured


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
            Candidate(kind="tool", id=tool_id,
                      qualification=QualificationResult(
                          status=QualificationStatus.ELIGIBLE),
                      latency_class="fast", reliability_penalty=0.0,
                      score=0.0),
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
                    check="credentials", observed="missing: smtp",
                    expected="smtp present", hint="configure credential smtp")],
            ).to_dict(),
        }],
    )


def _tc(name: str) -> dict:
    return {"id": "call_1", "function": {"name": name, "arguments": {}}}


def _service_with_cap_tool():
    reg = MagicMock()
    reg.metadata.return_value = {"capabilities": ["cap_x"], "tier": 1}
    service = ToolDispatchService(registry=reg)
    # bypass governor（单测面：无 governor 依赖）
    service._registry.dispatch = AsyncMock(return_value={"success": True})
    return service, reg


@pytest.mark.asyncio
async def test_auto_supply_reaches_bind(monkeypatch):
    monkeypatch.setenv("GIS_RUNTIME_OFFLINE", "1")
    captured = _patch_plan_capture(monkeypatch, _eligible_plan("cap_x", "t1"))
    service, _ = _service_with_cap_tool()

    result = await service.dispatch(_tc("t1"), "sess-supply", set())

    assert result.status == "ok"
    ctx = captured["ctx"]
    from app.services.gis_harness.qualification_v8 import QualificationContext

    assert isinstance(ctx, QualificationContext)
    assert ctx.offline is True  # 运行时事实进入 bind 资格判断


@pytest.mark.asyncio
async def test_supply_disabled_keeps_bare_context(monkeypatch):
    monkeypatch.setenv("GIS_SITUATION_SUPPLY", "0")
    monkeypatch.setenv("GIS_RUNTIME_OFFLINE", "1")
    captured = _patch_plan_capture(monkeypatch, _eligible_plan("cap_x", "t1"))
    service, _ = _service_with_cap_tool()

    await service.dispatch(_tc("t1"), "sess-supply", set())

    ctx = captured["ctx"]
    assert ctx.offline is None  # bare context：缺席面不裁决


@pytest.mark.asyncio
async def test_caller_dict_merged_at_chokepoint(monkeypatch):
    monkeypatch.setenv("GIS_RUNTIME_OFFLINE", "1")
    captured = _patch_plan_capture(monkeypatch, _eligible_plan("cap_x", "t1"))
    service, _ = _service_with_cap_tool()

    await service.dispatch(
        _tc("t1"), "sess-supply", set(), situation={"task_hint": "heatmap"})

    ctx = captured["ctx"]
    assert ctx.task_hint == "heatmap"  # caller 事实保留
    assert ctx.offline is True  # 运行时事实补齐


@pytest.mark.asyncio
async def test_all_faces_equivalent_decisions(monkeypatch):
    """DoD 3：Pi 面 / legacy 面同样传 None → 同一 session 同一裁决。"""
    plan = _ineligible_plan("cap_x", "t1")
    _patch_plan_capture(monkeypatch, plan)
    service_pi, _ = _service_with_cap_tool()
    service_legacy, _ = _service_with_cap_tool()

    r_pi = await service_pi.dispatch(_tc("t1"), "sess-eq", set())
    r_legacy = await service_legacy.dispatch(_tc("t1"), "sess-eq", set())

    assert r_pi.status == r_legacy.status == "error"
    assert r_pi.error_msg == r_legacy.error_msg == "CAPABILITY_INELIGIBLE"
    assert r_pi.raw_result["reason"] == r_legacy.raw_result["reason"]


@pytest.mark.asyncio
async def test_denial_record_rides_chain(monkeypatch):
    from app.lib.runtime.context import bind_runtime_context
    from app.lib.runtime.decision_record import (
        DECISION_KIND_CAPABILITY_DISPATCH_DENIAL,
    )
    from app.lib.runtime.gis_trace import Stage, get_gis_trace_registry

    _patch_plan_capture(monkeypatch, _ineligible_plan("cap_x", "t1"))
    service, _ = _service_with_cap_tool()

    with bind_runtime_context(session_id="sess-rec", turn_id="turn-rec"):
        result = await service.dispatch(_tc("t1"), "sess-rec", set())
    assert result.status == "error"

    chain = get_gis_trace_registry().get("turn-rec")
    assert chain is not None
    denial_records = []
    for rec in chain.stage_records(Stage.TOOL_CALLS):
        decision = rec.payload.get("decision")
        if isinstance(decision, dict) and decision.get(
                "kind") == DECISION_KIND_CAPABILITY_DISPATCH_DENIAL:
            denial_records.append(decision)
    assert denial_records, "denial decision record must ride the chain"
    d = denial_records[0]
    assert d["selected"] == "t1"
    assert d["inputs"]["capability"] == "cap_x"
    assert d["policy_version"] == "capability_dispatch_bind.v1"
    assert d["decision_id"].startswith("dec_")
    alternatives = d["alternatives"]
    assert alternatives and alternatives[0]["id"].startswith("tool:")
    codes = [rc["check"] for rc in d["reason_codes"]]
    assert codes == ["credentials_missing"]  # canonical 词表投影


@pytest.mark.asyncio
async def test_denial_no_secret_material_anywhere(monkeypatch):
    """拒绝面（evidence/details/记录）只见凭证 id，绝不见 secret 值。"""
    monkeypatch.setenv("GIS_TOOL_CREDENTIALS",
                       "smtp:secret:hunter2-value,upstream:token")
    _patch_plan_capture(monkeypatch, _ineligible_plan("cap_x", "t1"))
    service, _ = _service_with_cap_tool()

    result = await service.dispatch(_tc("t1"), "sess-sec", set())

    assert result.status == "error"
    blob = json.dumps({
        "details": result.raw_result,
        "evidence": result.capability_evidence,
        "llm": result.llm_payload,
    }, default=str)
    assert "hunter2" not in blob
    assert "token:" not in blob.replace("upstream:token", "")
    # 凭证 id 允许出现在资格解释面（presence 级）
    assert "smtp" in blob


@pytest.mark.asyncio
async def test_denial_releases_dedup_slot(monkeypatch):
    _patch_plan_capture(monkeypatch, _ineligible_plan("cap_x", "t1"))
    service, _ = _service_with_cap_tool()
    executed: set = set()

    first = await service.dispatch(_tc("t1"), "sess-dedup", executed)
    second = await service.dispatch(_tc("t1"), "sess-dedup", executed)
    assert first.status == "error"
    assert second.status == "error"  # 重试不再被「在飞」谎言拦住


@pytest.mark.asyncio
async def test_evidence_carries_canonical_reason_codes(monkeypatch):
    _patch_plan_capture(monkeypatch, _ineligible_plan("cap_x", "t1"))
    service, _ = _service_with_cap_tool()

    result = await service.dispatch(_tc("t1"), "sess-ev", set())

    ev = result.capability_evidence
    assert ev["action"] == "refused"
    assert ev["reason_codes"] == ["credentials_missing"]
    assert result.raw_result["reason_codes"][0]["check"] == \
        "credentials_missing"
    assert result.raw_result["reason_codes"][0]["hint"] == \
        "configure credential smtp"


def test_reason_code_vocabulary_closed_and_stable():
    from app.services.gis_harness.hotpath_convergence.capability_reasons import (
        REASON_CODES,
        canonical_reason_code,
        reason_codes_from_qualification,
    )
    from app.services.gis_harness.qualification_v8 import (
        QualificationReason,
        QualificationResult,
    )

    # 词表值封闭：无自由文本投影
    assert all(isinstance(v, str) and v for v in REASON_CODES.values())
    # 未登记 check → unmapped: 前缀（不静默丢失，不发明新词）
    assert canonical_reason_code("totally_new_check") == \
        "unmapped:totally_new_check"
    qual = QualificationResult(status="ineligible", reasons=[
        QualificationReason(check="offline", observed="1", expected="0"),
        QualificationReason(check="budget", observed="heavy", expected="light"),
    ])
    codes = reason_codes_from_qualification(qual)
    assert [c["check"] for c in codes] == [
        "budget_exceeded", "offline_network_required"]  # 确定性排序
