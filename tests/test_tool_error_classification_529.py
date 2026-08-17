"""Regression tests for #529: {"error": <str>} tool returns must be classified
as failures end-to-end.

The pre-fix defect: `is_error_dict` recognized only the exception-path shape
`{"success": False, "code": ...}`, while ~139 tool sites return `{"error": ...}`
dicts on the normal path. Those were treated as success: dispatch marked the
key completed, a same-args retry was intercepted with a fabricated
"已成功执行", and plan_mode advanced past the failed step.

The fix: the error-like shape is folded into the canonical failure form at the
dispatch boundary and recognized by the whole predicate family
(`is_error_like_result` / `is_tool_error_result`), plan_mode's step predicate,
the metrics classifier, and spatial_reasoning's error shape.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.llm_result_formatter import (
    is_error_dict,
    is_error_like_result,
    is_tool_error_result,
)
from app.services.session_data import session_data_manager
from app.services.tool_dispatch_service import (
    _REPEAT_LLMPAYLOAD,
    ToolDispatchResult,
    ToolDispatchService,
)
from app.tools.spatial_reasoning import _error_result as sr_error_result


@pytest.fixture
async def clean_session():
    sid = "test-error-classification-529"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


def _tc(name: str, args: dict) -> dict:
    return {"id": "call_529", "function": {"name": name, "arguments": args}}


# ─── 1. predicate contract: which shapes are (and are NOT) errors ───────────


def test_predicate_contract_error_like():
    assert is_error_like_result({"error": "未配置任何地图 API Key"})
    assert is_error_like_result({"success": False, "error": "boom"})
    assert is_error_like_result({"error": "", "summary": "x"})  # empty str still an error channel
    # Canonical shape stays classified by is_error_dict.
    assert is_error_dict({"success": False, "code": "NOT_FOUND", "message": "x"})
    assert is_tool_error_result({"success": False, "code": "NOT_FOUND", "message": "x"})
    assert is_tool_error_result({"error": "no key"})


def test_predicate_contract_non_errors_not_reclassified():
    """Business payloads carrying an error key must NOT be reclassified:
    success=True shields partial-success; None/numeric/nested error values are
    data, not failures."""
    assert not is_error_like_result({"success": True, "error": "partial note"})
    assert not is_error_like_result({"error": None, "status": "ok"})
    assert not is_error_like_result({"error": 503})
    assert not is_error_like_result({"error": {"detail": "nested"}, "status": "ok"})
    assert not is_error_like_result({"status": "ok"})
    assert not is_error_like_result("plain string error")
    assert not is_error_like_result(None)
    # success=True + error must not classify as error through the combined
    # predicate either.
    assert not is_tool_error_result({"success": True, "error": "partial note"})


# ─── 2. dispatch: real tool error shapes → status error, honest retry ───────

_REAL_ERROR_SHAPES = [
    ("chinese_maps", {"error": "未配置任何地图 API Key"}),
    ("chart", {"error": "Invalid chart_type. Must be one of: bar, line"}),
    ("layer_manager", {"error": "Missing session_id context"}),
    ("web_crawler", {"error": "未配置 BAIDU_QIANFAN_TOKEN"}),
    ("remote_sensing", {"error": "No valid bands in the raster"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,error_result", _REAL_ERROR_SHAPES)
async def test_error_dict_dispatch_is_error_and_retryable(
    clean_session, tool_name, error_result
):
    """A tool returning {"error": <str>} must dispatch as status='error' with a
    populated error message, and the dedup slot must be RELEASED so a same-args
    retry is not intercepted with the "已成功执行" success claim."""
    registry = MagicMock(dispatch=AsyncMock(return_value=error_result))
    svc = ToolDispatchService(registry=registry)
    executed: set = set()
    tc = _tc(tool_name, {"q": "x"})

    r1 = await svc.dispatch(tc, clean_session, executed)
    assert isinstance(r1, ToolDispatchResult)
    assert r1.status == "error", f"{tool_name} error dict misclassified as {r1.status}"
    assert r1.geojson_ref is None
    assert r1.error_msg, "error_msg must be populated from the error value"
    assert error_result["error"] in r1.error_msg

    # tool_failed event recorded (the honest failure channel).
    log = await session_data_manager.get_event_log(clean_session)
    assert any(e["event"] == "tool_failed" for e in log)

    # Dedup slot released → the same-args retry EXECUTES again and reports the
    # failure honestly — never the fabricated success claim.
    r2 = await svc.dispatch(tc, clean_session, executed)
    assert r2.status == "error"
    assert registry.dispatch.call_count == 2, (
        "failed call must release the dedup slot so a retry is not blocked"
    )
    assert "[重复调用拦截]" not in r2.llm_payload
    assert _REPEAT_LLMPAYLOAD.format(tool=tool_name) not in r2.llm_payload


@pytest.mark.asyncio
async def test_canonical_error_dict_unchanged(clean_session):
    """The canonical std_error_response shape keeps its code/message intact."""
    registry = MagicMock(dispatch=AsyncMock(return_value={
        "success": False,
        "code": "NOT_FOUND",
        "message": "区域不存在",
        "error_type": "KeyError",
    }))
    svc = ToolDispatchService(registry=registry)
    r = await svc.dispatch(_tc("get_district", {}), clean_session, set())
    assert r.status == "error"
    assert "区域不存在" in r.error_msg


@pytest.mark.asyncio
async def test_success_true_with_error_note_stays_ok(clean_session):
    """A result that explicitly claims success while carrying an error note
    (partial-success, e.g. geocode) must remain status='ok' — never a failure."""
    registry = MagicMock(dispatch=AsyncMock(return_value={
        "success": True,
        "error": "2 of 10 addresses failed",
        "results": [{"ok": 1}],
    }))
    svc = ToolDispatchService(registry=registry)
    r = await svc.dispatch(_tc("geocode_cn", {}), clean_session, set())
    assert r.status == "ok"
    assert r.error_msg is None


@pytest.mark.asyncio
async def test_error_none_value_stays_ok(clean_session):
    registry = MagicMock(dispatch=AsyncMock(return_value={
        "status": "ok", "error": None, "data": [1, 2],
    }))
    svc = ToolDispatchService(registry=registry)
    r = await svc.dispatch(_tc("some_tool", {}), clean_session, set())
    assert r.status == "ok"


@pytest.fixture
def registry():
    from app.tools.registry import ToolRegistry

    return ToolRegistry()


# ─── 3. plan_mode: {"error": ...} step must fail the plan, not advance ──────


@pytest.mark.asyncio
async def test_plan_step_error_dict_fails_plan(registry):
    """A plan step whose tool returns {"error": ...} must be recorded as the
    failure: the plan reaches failed, and the dependent later step does NOT
    execute (no advance-past-failure)."""
    from app.services import plan_mode as pm

    calls = {"s2": 0}

    @registry.tool(name="failing_tool", description="fails with error dict")
    def _fail() -> dict:
        return {"error": "无法获取数据源"}

    @registry.tool(name="later_tool", description="must never run")
    def _later() -> dict:
        calls["s2"] += 1
        return {"success": True}

    sid = "sess-529-plan"
    plan = pm.PlanProposal(
        title="529",
        steps=[
            pm.PlanStep(id="s1", tool="failing_tool"),
            pm.PlanStep(id="s2", tool="later_tool", depends_on=["s1"]),
        ],
    )
    plan_id = await pm.store_plan(sid, plan)
    result = await pm.execute_plan_async(sid, plan_id, registry)

    assert result.get("success") is False
    assert result.get("failed_step") == "s1"
    # The persisted plan converges to the failed terminal state.
    stored = await pm.load_plan(sid, plan_id)
    assert (stored or {}).get("__status__") == "failed", (
        f"plan must reach a failed status, got {(stored or {}).get('__status__')!r}"
    )
    assert calls["s2"] == 0, (
        "later step must not execute after a step failed with an error dict "
        "(plan must not advance past failures)"
    )


# ─── 4. spatial_reasoning error shape is canonical ─────────────────────────


def test_spatial_reasoning_error_result_is_classified_error():
    result = sr_error_result("LLM 调用失败: ConnectionError")
    assert is_error_dict(result), (
        "spatial_reasoning error shape must carry success=False + code so the "
        "dispatch error path classifies it as a failure"
    )
    assert result["code"] == "spatial_reasoning_error"
    assert "LLM 调用失败" in result["message"]


# ─── 5. metrics classifier sees the error ──────────────────────────────────


@pytest.mark.asyncio
async def test_registry_metrics_classifies_error_like(monkeypatch):
    """The registry's dispatch metrics must classify {"error": <str>} results
    with an error class (not silently 'no error class')."""
    from app.tools.registry import ToolRegistry
    from app.services import tool_metrics

    reg = ToolRegistry()
    calls: list[dict] = []

    def _fake_record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(tool_metrics, "record_tool_call", _fake_record)

    @reg.tool(name="err_like", description="d")
    def _tool() -> dict:
        return {"error": "boom"}

    res = await reg.dispatch("err_like", "{}")
    assert res == {"error": "boom"}
    assert calls and calls[-1].get("error") == "tool_error", (
        f"metrics must record an error class for the error-like result, got {calls[-1] if calls else None}"
    )
