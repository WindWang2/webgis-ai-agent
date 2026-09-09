"""Harness V5 统一失败分类与 typed remediation 契约（ADR-0118 D2）。

验收锚点（Epic V5）：失败进入 typed diagnose/repair 而不是 generic
TOOL_ERROR；retry/replan 有严格预算（全表 max_attempts ≤ 3）；预算
耗尽 → abort_with_disclosure（无无限循环）；CRS 失败不再逃逸为泛化
TOOL_ERROR；planning/geocompute 原枚举经适配器映射、零破坏。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.failure_taxonomy import (
    REMEDIATION_POLICY,
    HarnessFailureClass,
    RemediationLedger,
    classify_and_remediate,
    classify_harness_failure,
    from_geocompute_failure_class,
    from_planning_failure_class,
    remediation_for,
)
from app.services.session_data import session_data_manager


# ---------------------------------------------------------------- 分类

@pytest.mark.parametrize("kwargs,expected", [
    ({"message": "operation cancelled by user"},
     HarnessFailureClass.CANCELLED),
    ({"code": "cancelled"}, HarnessFailureClass.CANCELLED),
    ({"message": "pyproj CRSError: EPSG:99999999 not found"},
     HarnessFailureClass.CRS_ERROR),
    ({"message": "CRS mismatch: cannot reproject to EPSG:3857"},
     HarnessFailureClass.CRS_ERROR),
    ({"message": "request timed out after 30s"},
     HarnessFailureClass.TIMEOUT),
    ({"exception": TimeoutError("t"), "message": "x"},
     HarnessFailureClass.TIMEOUT),
    ({"message": "ref stale: 已过期引用 ref_abc"},
     HarnessFailureClass.STALE_REF),
    ({"message": "tile source load failed for layer"},
     HarnessFailureClass.RENDERER_FAILURE),
    ({"message": "渲染失败：图层源加载错误"},
     HarnessFailureClass.RENDERER_FAILURE),
    ({"message": "partial materialization: 半写产物需清理"},
     HarnessFailureClass.PARTIAL_COMPLETION),
    ({"code": "VALIDATION_ERROR", "message": "missing required arg 'column'"},
     HarnessFailureClass.TOOL_ERROR),
    ({"code": "UNKNOWN_TOOL", "message": "no such tool"},
     HarnessFailureClass.TOOL_ERROR),
])
def test_classification_precedence(kwargs, expected):
    assert classify_harness_failure(**kwargs) is expected


def test_crs_beats_generic_validation_delegation():
    """CRS 标记必须优先于 planning 委托（否则 pyproj 错被折叠进 validation）。"""
    fc = classify_harness_failure(
        code="TOOL_ERROR",
        message="CRSError: invalid projection 0-360",
        exception=Exception("pyproj boom"),
    )
    assert fc is HarnessFailureClass.CRS_ERROR


def test_exception_type_crs_error():
    class CRSError(Exception):
        pass

    fc = classify_harness_failure(
        exception=CRSError("bad crs"), message="reproject failed")
    # 类型名不在已知表 → 走标记；消息含 crs 标记命中
    assert fc is HarnessFailureClass.CRS_ERROR


def test_empty_result_via_planning_markers():
    fc = classify_harness_failure(
        status="ok", message="query returned no features")
    assert fc is HarnessFailureClass.EMPTY_RESULT


def test_deterministic_classification():
    kw = {"code": "TOOL_ERROR", "message": "连接超时 timeout"}
    first = classify_harness_failure(**kw)
    for _ in range(5):
        assert classify_harness_failure(**kw) is first


# ---------------------------------------------------------------- 适配器

def test_planning_adapter_totality():
    from app.services.planning.models import FailureClass

    for fc in FailureClass:
        assert from_planning_failure_class(fc) in HarnessFailureClass


def test_geocompute_adapter_totality():
    from app.services.geocompute.errors import FailureClass

    mapping = {
        FailureClass.TRANSIENT_REMOTE: HarnessFailureClass.TIMEOUT,
        FailureClass.PARTIAL_MATERIALIZATION:
            HarnessFailureClass.PARTIAL_COMPLETION,
        FailureClass.INVALID_DATA: HarnessFailureClass.DATA_ERROR,
        FailureClass.CANCELLED: HarnessFailureClass.CANCELLED,
        FailureClass.SCIENTIFIC: HarnessFailureClass.DATA_ERROR,
        FailureClass.BUDGET_EXCEEDED: HarnessFailureClass.BUDGET_EXHAUSTED,
    }
    for fc in FailureClass:
        got = from_geocompute_failure_class(fc)
        assert got in HarnessFailureClass
        if fc in mapping:
            assert got is mapping[fc]


# ---------------------------------------------------------------- 预算纪律

def test_policy_table_bounded():
    """全表 max_attempts ≤ 3（严格预算 —— 无无限循环的结构保证）。"""
    assert REMEDIATION_POLICY, "policy 表非空"
    for fc, (action, max_attempts) in REMEDIATION_POLICY.items():
        assert max_attempts <= 3, fc
        assert max_attempts >= 0
    non_abort = [fc for fc in REMEDIATION_POLICY
                 if fc not in (HarnessFailureClass.CANCELLED,
                               HarnessFailureClass.BUDGET_EXHAUSTED)]
    assert all(REMEDIATION_POLICY[fc][1] > 0 for fc in non_abort)


def test_retry_budget_exhaustion_forces_abort():
    d = remediation_for(HarnessFailureClass.TOOL_ERROR, attempts=1)
    assert d.retry_allowed and d.action == "retry_with_backoff"
    d2 = remediation_for(HarnessFailureClass.TOOL_ERROR, attempts=3)
    assert not d2.retry_allowed
    assert d2.action == "abort_with_disclosure"
    # 取消/预算类永不允许重试
    assert not remediation_for(
        HarnessFailureClass.CANCELLED, attempts=0).retry_allowed
    assert not remediation_for(
        HarnessFailureClass.BUDGET_EXHAUSTED, attempts=0).retry_allowed


def test_ledger_counts_and_budget_closure():
    led = RemediationLedger(cap=8)
    p1 = classify_and_remediate(
        message="timed out", tool_name="buffer", session_id="s1", ledger=led)
    assert p1["attempts"] == 1 and p1["retry_allowed"] is True
    for i in range(2, 5):
        p = classify_and_remediate(
            message="timed out", tool_name="buffer", session_id="s1",
            ledger=led)
        assert p["attempts"] == i
    assert p["retry_allowed"] is False
    assert p["remediation"] == "abort_with_disclosure"

    # 不同 session/tool 独立记账
    p_other = classify_and_remediate(
        message="timed out", tool_name="buffer", session_id="s2", ledger=led)
    assert p_other["attempts"] == 1


def test_ledger_bounded():
    led = RemediationLedger(cap=4)
    for i in range(10):
        led.record((f"s{i}", "t", "timeout"))
    assert len(led._counts) == 4


def test_classify_and_remediate_never_raises(monkeypatch):
    def _boom(**_):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.services.gis_harness.failure_taxonomy.classify_harness_failure",
        _boom)
    payload = classify_and_remediate(message="x", tool_name="t")
    assert payload["class"] == "unknown"
    assert "remediation" in payload


# ---------------------------------------------------------------- dispatch seam 集成

def _tc(name: str, args: dict | None = None, tc_id: str = "call_v5") -> dict:
    return {"id": tc_id, "function": {"name": name,
                                      "arguments": args or {}}}


@pytest.fixture
def failing_registry():
    from unittest.mock import AsyncMock, MagicMock

    reg = MagicMock(dispatch=AsyncMock())
    return reg


@pytest.mark.asyncio
async def test_dispatch_error_carries_typed_harness_failure(
        failing_registry, monkeypatch, tmp_path):
    """真实 ToolDispatchService 错误路径 → result.harness_failure typed
    分类；CRS → substitute_operator 而非泛化 TOOL_ERROR 语义。"""
    from app.services.tool_dispatch_service import ToolDispatchService

    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    sid = "v5-taxonomy-seam"
    svc = ToolDispatchService(registry=failing_registry)

    # 1) CRS 失败（registry 返回失败形状 dict，走 is_error_like 折叠 +
    #    is_error_dict 统一错误分支 —— 与生产同一路径）
    failing_registry.dispatch.return_value = {
        "success": False,
        "error": "CRSError: unsupported projection EPSG:99999999",
    }
    from app.services.gis_harness.failure_taxonomy import RemediationLedger

    monkeypatch.setattr(
        "app.services.gis_harness.failure_taxonomy._global_ledger",
        RemediationLedger())
    result = await svc.dispatch(
        _tc("reproject_tool"), sid, set())
    assert result.status == "error"
    hf = result.raw_result.get("harness_failure")
    assert hf, "dispatch 错误路径未附带 harness_failure"
    assert hf["class"] == "crs_error"
    assert hf["remediation"] == "substitute_operator"
    assert hf["max_attempts"] <= 3

    # 2) 同 (session, tool) 重试 → 账本计数递增
    result2 = await svc.dispatch(_tc("reproject_tool"), sid, set())
    hf2 = result2.raw_result["harness_failure"]
    assert hf2["attempts"] == hf["attempts"] + 1

    # 3) 超时类失败 → TIMEOUT
    failing_registry.dispatch.return_value = {
        "success": False,
        "error": "request timed out connecting to upstream",
    }
    result3 = await svc.dispatch(_tc("fetch_tool", {}, "call_v5_2"), sid, set())
    assert result3.raw_result["harness_failure"]["class"] == "timeout"

    await session_data_manager.clear_session(sid)
