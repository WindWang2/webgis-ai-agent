"""Regression tests for #589: {"type":"error"}/{"status":"error"|"failed"}
tool returns must be classified as failures end-to-end — the same-family
residue of #529, which only recognized {"error": <str>}.

Pre-fix defect: is_error_like_result only classified the string ``error`` key,
so 21 tool sites returning the error-as-value shapes below bypassed the
classifier and were treated as SUCCESS (dispatch normalized them to status="ok",
marked them completed — same-args retries blocked with a fabricated
"已成功执行" — plans advanced past failures, tracker recorded step_result
instead of step_error, metrics recorded error=None):

  - {"type": "error", "message": ...}   network/temporal/spatial_decision,
  - {"status": "error", ...}            project_tools (save/rerun workflow),
  - {"status": "failed", ...}           rerun_workflow / explorer.

The fix: is_error_like_result (and thus is_tool_error_result) recognizes these
two additional error-as-value key shapes at their canonical values, keeping the
success=True exemption; the dispatch normalization fold then collapses them into
the canonical failure shape, releasing the dedup slot for an honest retry.
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


@pytest.fixture
async def clean_session():
    sid = "test-error-classification-589"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


def _tc(name: str, args: dict) -> dict:
    return {"id": "call_589", "function": {"name": name, "arguments": args}}


# ─── 1. predicate contract: the three #589 shapes classify, saved cases don't ─

_ERROR_SHAPES = [
    {"type": "error", "message": "路网最短路径计算失败: boom"},
    {"type": "error"},
    {"status": "error", "message": "Project not found"},
    {"status": "error"},
    {"status": "failed", "run_id": "r1", "error_message": "step failed"},
]


@pytest.mark.parametrize("shape", _ERROR_SHAPES)
def test_error_like_reclassifies_589_shapes(shape):
    assert is_error_like_result(shape), f"{shape} must classify as an error"
    assert is_tool_error_result(shape)


@pytest.mark.parametrize("shape", _ERROR_SHAPES)
def test_error_dict_reclassifies_after_fold(shape):
    """After the dispatch fold these shapes carry success=False + code, so the
    canonical is_error_dict must classify them too."""
    folded = dict(shape)
    folded.setdefault("code", "tool_error")
    folded.setdefault(
        "message", folded.get("error") or folded.get("error_message") or "x"
    )
    folded["success"] = False
    assert is_error_dict(folded)
    assert is_tool_error_result(folded)


def test_success_true_exemptions_hold():
    """The #529 protection for partial successes is preserved for the new
    shapes: an explicit success=True never reclassifies."""
    assert not is_error_like_result({"success": True, "type": "error", "message": "partial"})
    assert not is_error_like_result({"success": True, "status": "failed", "message": "partial"})
    assert not is_tool_error_result({"success": True, "type": "error"})


def test_business_shapes_not_reclassified():
    """Non-error uses of the same keys stay untouched: type/status carry many
    legitimate values (FeatureCollection, ok, template_applied, ...)."""
    assert not is_error_like_result({"type": "FeatureCollection", "features": []})
    assert not is_error_like_result({"status": "ok"})
    assert not is_error_like_result({"status": "success"})
    assert not is_error_like_result({"status": "started"})
    assert not is_error_like_result({"error": None, "status": "ok"})
    assert not is_error_like_result({"error": 503})


# ─── 2. dispatch: type/status error shapes → status='error', honest retry ─────


@pytest.mark.asyncio
@pytest.mark.parametrize("error_result", _ERROR_SHAPES)
async def test_error_shape_dispatch_is_error_and_retryable(clean_session, error_result):
    """A tool returning any of the #589 shapes must dispatch as status='error'
    with a populated error message, and the dedup slot must be RELEASED so a
    same-args retry is not intercepted with the "已成功执行" success claim."""
    registry = MagicMock(dispatch=AsyncMock(return_value=error_result))
    svc = ToolDispatchService(registry=registry)
    executed: set = set()
    tc = _tc("some_tool", {"q": "x"})

    r1 = await svc.dispatch(tc, clean_session, executed)
    assert isinstance(r1, ToolDispatchResult)
    assert r1.status == "error", f"{error_result} misclassified as {r1.status}"
    assert r1.geojson_ref is None
    assert r1.error_msg, "error_msg must be populated"
    src = error_result.get("message") or error_result.get("error_message")
    if src:
        assert src in r1.error_msg, (
            f"message must survive the fold, got {r1.error_msg!r}"
        )

    # The honest failure channel: tool_failed event.
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
    assert _REPEAT_LLMPAYLOAD.format(tool="some_tool") not in r2.llm_payload


# ─── 3. real network tool site: injected engine failure → step_error ─────────


@pytest.mark.asyncio
async def test_network_tool_exception_classifies_as_error(clean_session, monkeypatch):
    """A network tool whose engine raises returns {"type": "error", ...} — the
    dispatch must classify it as a failure (status='error'), never success, and
    a same-args retry must be allowed."""
    from app.tools import network_tools as nt
    from app.tools.registry import ToolRegistry

    class _BoomEngine:
        calls = 0

        async def solve_shortest_path(self, **kwargs):
            _BoomEngine.calls += 1
            raise RuntimeError("engine exploded")

    monkeypatch.setattr(nt, "NetworkGraphEngine", lambda: _BoomEngine())
    reg = ToolRegistry()
    nt.register_network_tools(reg)
    svc = ToolDispatchService(registry=reg)
    executed: set = set()
    tc = _tc(
        "network_shortest_path",
        {
            "network": {"type": "FeatureCollection", "features": []},
            "origin": {"type": "Point", "coordinates": [0.0, 0.0]},
            "destination": {"type": "Point", "coordinates": [1.0, 1.0]},
        },
    )
    r = await svc.dispatch(tc, clean_session, executed)
    assert r.status == "error"
    assert "engine exploded" in r.error_msg

    # Same-args retry is allowed (the failed call released the dedup slot) and
    # still reports the failure honestly — the engine genuinely re-ran.
    r2 = await svc.dispatch(tc, clean_session, executed)
    assert r2.status == "error"
    assert "[重复调用拦截]" not in r2.llm_payload
    assert _BoomEngine.calls == 2, (
        "failed call must release the dedup slot so a retry re-executes"
    )