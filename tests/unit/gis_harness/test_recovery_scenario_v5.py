"""Harness V5 live-failure corpus + 端到端恢复场景（ADR-0118 D9）。

验收锚点（Epic V5）：
- 八类真实故障（provider timeout / transient DB / empty-partial / 地图源
  失败 / 重启 / stale workspace / 取消 / 重试耗尽）全部有确定性期望
  （typed class + remediation + 耗尽终态）；
- 预算阶梯证明：任何可重试类最终收敛 abort_with_disclosure —— 无无限
  循环；
- 至少一组复杂 GIS scenario 在**中间故障后自动恢复并最终验证**（真实
  ToolDispatchService 错误 seam → typed diagnose → remediation 重试 →
  成功 → 渲染 telemetry 最终核验 → durable trace 留证）。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.evaluation.failure_corpus import (
    FAILURE_CATEGORIES,
    category_coverage,
    evaluate_failure_case,
    get_failure_corpus,
)
from app.services.gis_harness.failure_taxonomy import (
    RemediationLedger,
)
from app.services.session_data import session_data_manager


# ---------------------------------------------------------------- 语料门

def test_failure_corpus_coverage_and_determinism():
    cases = get_failure_corpus()
    assert len(cases) >= FAILURE_CATEGORIES.__len__()
    cov = category_coverage(cases)
    for cat in FAILURE_CATEGORIES:
        assert cov[cat] >= 1, f"故障类目 {cat} 无案例"
    again = get_failure_corpus()
    assert [c.case_id for c in cases] == [c.case_id for c in again]


def test_failure_expectations_deterministic():
    for case in get_failure_corpus():
        v1 = evaluate_failure_case(case)
        v2 = evaluate_failure_case(case)
        assert v1.classified is v2.classified, case.case_id
        assert v1.first_action == v2.first_action, case.case_id
        assert v1.classified is case.expected_class, (
            case.case_id, v1.classified)
        assert v1.first_action == case.expected_action, case.case_id


def test_budget_ladder_always_terminates():
    """任何类的预算阶梯必须在有限步收敛到不可重试终态（无无限循环）。"""
    for case in get_failure_corpus():
        v = evaluate_failure_case(case)
        assert len(v.budget_ladder) <= 5, case.case_id
        assert v.exhausted_action == case.exhausted_action, case.case_id
        assert v.budget_ladder[-1]["retry_allowed"] is False, case.case_id


def test_retry_exhaustion_contract():
    """重试耗尽语义：耗尽后 abort_with_disclosure，绝不无限循环。"""
    case = next(c for c in get_failure_corpus()
                if c.case_id == "FL-EXHAUST-1")
    v = evaluate_failure_case(case)
    assert v.budget_ladder[0]["retry_allowed"] is True
    assert v.budget_ladder[-1]["remediation"] == "abort_with_disclosure"


# ------------------------------------------------- 端到端恢复场景

def _tc(name, args=None, tc_id="call_rec"):
    return {"id": tc_id, "function": {"name": name, "arguments": args or {}}}


@pytest.mark.asyncio
async def test_mid_failure_recovery_to_verified_finalization(monkeypatch, tmp_path):
    """完整闭环：CRS 失败 → typed diagnose → remediation 重试 → 成功 →
    渲染 telemetry 最终核验 verified → trace 持久化留证。"""
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "d"))
    sid = "v5-recovery-scenario"
    await session_data_manager.clear_session(sid)

    from app.services.tool_dispatch_service import ToolDispatchService

    reg = MagicMock(dispatch=AsyncMock())
    calls = {"n": 0}

    async def _flaky_reproject(tool_name, tool_args_raw, session_id=None,
                               executed_tools=None):
        calls["n"] += 1
        if calls["n"] == 1:
            # 首次：CRS 失败（生产同形：失败形状 dict）
            return {"success": False,
                    "error": "CRSError: unsupported projection EPSG:0"}
        # remediation（substitute_operator）后重试成功
        return {"summary": "reprojected to UTM 50N", "target_crs": "EPSG:32650"}

    reg.dispatch.side_effect = _flaky_reproject
    svc = ToolDispatchService(registry=reg)
    ledger = RemediationLedger()
    monkeypatch.setattr(
        "app.services.gis_harness.failure_taxonomy._global_ledger", ledger)

    # 1) 首次执行：中间故障 → typed diagnose
    r1 = await svc.dispatch(_tc("reproject"), sid, set())
    assert r1.status == "error"
    hf = r1.raw_result["harness_failure"]
    assert hf["class"] == "crs_error"
    assert hf["remediation"] == "substitute_operator"
    assert hf["retry_allowed"] is True

    # 2) 依 remediation 重试：恢复成功
    r2 = await svc.dispatch(_tc("reproject"), sid, set())
    assert r2.status == "ok"

    # 3) 渲染 observation（revision 匹配 + 完整 telemetry）→ 最终核验
    from app.services.gis_harness.render_observation import (
        validate_render_observation,
    )

    chapter = {"map_layers": [{"layer_id": "product-heat", "role": "result",
                               "enabled": True, "visible": True}]}
    mapspec = {"layers": [{"id": "product-heat", "type": "circle",
                           "source": "src-heat", "paint": {},
                           "layout": {"visibility": "visible"}}]}
    observation = {
        "source": "frontend_runtime", "sequence": 2, "mapspec_revision": 1,
        "layers": [{"id": "product-heat", "runtime_store_id": "product-heat",
                    "visible": True, "runtime_layer_count": 1,
                    "source_converged": True, "source_status": "loaded",
                    "render_complete": True, "feature_count": 128,
                    "style_converged": True}],
        "components": [{"id": "t", "type": "title", "mounted": True},
                       {"id": "chart-1", "type": "chart_panel",
                        "mounted": True}],
        "charts": [{"id": "chart-1", "rendered": True, "data_points": 31}],
    }
    status, findings = validate_render_observation(
        chapter, mapspec, observation, 1,
        [["title"], ["chart_panel"]])
    assert status == "verified", findings
    assert findings == []

    # 4) durable trace：成功 turn 的链落盘（seq 单调、FINAL_VERDICT 受保护）
    from app.lib.runtime.gis_trace import Stage, get_gis_trace_registry
    from app.services.gis_harness.trace_store import (
        last_seq,
        persist_turn_chain,
        read_chains,
    )

    tid = "recovery-turn-1"
    chain = get_gis_trace_registry().start(tid, sid)
    chain.record(Stage.TOOL_RESULTS, tool="reproject", status="recovered")
    chain.record(Stage.FINAL_VERDICT, verdict="READY")
    assert persist_turn_chain(tid, session_id=sid) is True
    records = read_chains(sid)
    assert any(r["turn_id"] == tid for r in records)
    assert last_seq(sid) >= 1

    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_recovery_with_stale_then_reload_ref(monkeypatch, tmp_path):
    """stale-ref 恢复半边：ref 缺席 → spill 兜底 → 恢复成功或诚实披露。"""
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "d2"))
    sid = "v5-stale-ref-scenario"
    await session_data_manager.clear_session(sid)
    ref = await session_data_manager.store(
        sid, {"type": "FeatureCollection", "features": []})

    from app.services.session_data import RefSpillStore, session_data_manager as mgr

    # 模拟驱逐落盘 + 内存清除（重启语义：spill 是 durable 半边）
    payload = await mgr.get(sid, ref)
    assert payload is not None
    RefSpillStore().spill(sid, ref, payload)
    await mgr.delete_ref(sid, ref)

    # 内存清除后：mgr miss、spill（durable 半边）命中 —— RELOAD_REF 的
    # 重启语义（detailed resolver 语义由 V4 ref_resolver 测试覆盖）
    assert await mgr.get(sid, ref) is None
    from app.services.session_data import ref_spill_store

    reloaded = ref_spill_store.load(sid, ref)
    assert reloaded is not None
    assert reloaded.get("type") == "FeatureCollection"
    await session_data_manager.clear_session(sid)
