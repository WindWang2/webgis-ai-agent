"""#437: spatial_reasoning must not return an unmarked canned conclusion.

Before the fix, with default env (SPATIAL_REASONING_USE_REAL_LLM unset) the
tool returned a hardcoded mock — conclusion "基于现有规则库，该位置适合商业
选址…" with a fabricated confidence of 0.75 — regardless of the query. It is
reachable through plan-mode execute_plan and the admin /tools/execute route
(tier-3 gated paths), where the fabricated result is persisted into history
and can drive user-facing conclusions, and is_suspicious_result could not
flag it.

Contract after the fix: with default env the tool returns an explicitly
marked unavailable result (success=False, honest message, correction_hint —
Exception As Thought); real reasoning only when the feature flag is set.

Deterministic: no network — the real-LLM path is exercised with a mocked
provider.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.tool_dispatch_service import is_suspicious_result
from app.tools.registry import ToolRegistry, confirm_tier3
from app.tools.spatial_reasoning import register_spatial_reasoning


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_spatial_reasoning(r)
    return r


_VALID_LLM_JSON = (
    '{"type": "spatial_reasoning", "conclusion": "真实推演结论", '
    '"reasoning_chain": [{"step": 1, "fact": "规则事实", "source": "commercial"}], '
    '"confidence": 0.8, "uncertainty": "有限", "recommendations": ["建议"]}'
)


# ─── default env: honest unavailability, never a canned conclusion ───────────


@pytest.mark.asyncio
async def test_default_env_dispatch_returns_honest_unavailable(registry, monkeypatch):
    monkeypatch.delenv("SPATIAL_REASONING_USE_REAL_LLM", raising=False)
    with confirm_tier3():
        result = await registry.dispatch(
            "spatial_reasoning",
            {"query": "某位置是否适合建地铁站", "reasoning_depth": "standard"},
        )
    assert isinstance(result, dict)
    # Explicitly marked as not-a-result (never an unmarked canned conclusion).
    assert result.get("success") is False, result
    # No fabricated confidence or canned conclusion text.
    assert result.get("confidence") != 0.75
    assert "商业选址" not in str(result.get("message", result.get("error", "")))
    # is_suspicious_result must flag it so plan-mode steps do not tick off it.
    assert is_suspicious_result(result) is True


@pytest.mark.asyncio
async def test_default_env_result_carries_correction_hint(registry, monkeypatch):
    """Exception As Thought: the unavailable result guides the LLM to a next
    action (enable the flag / use real data tools) instead of dead-ending."""
    monkeypatch.delenv("SPATIAL_REASONING_USE_REAL_LLM", raising=False)
    with confirm_tier3():
        result = await registry.dispatch(
            "spatial_reasoning", {"query": "任意问题"}
        )
    hint = result.get("correction_hint", "")
    assert hint, "unavailable result must carry a correction_hint"
    assert "SPATIAL_REASONING_USE_REAL_LLM" in hint


@pytest.mark.asyncio
async def test_default_env_result_independent_of_query(registry, monkeypatch):
    """The unavailable result must be the same honest marker for any query —
    in particular it must not fabricate query-specific conclusions."""
    monkeypatch.delenv("SPATIAL_REASONING_USE_REAL_LLM", raising=False)
    outs = []
    with confirm_tier3():
        for q in ("医院选址分析", "暴雨内涝推演", "学区房溢价"):
            outs.append(
                await registry.dispatch("spatial_reasoning", {"query": q})
            )
    assert outs[0].get("success") is False
    assert all(o.get("message") == outs[0].get("message") for o in outs)


# ─── feature flag set: real reasoning path intact ────────────────────────────


@pytest.mark.asyncio
async def test_flag_set_dispatch_returns_real_llm_result(registry, monkeypatch):
    monkeypatch.setenv("SPATIAL_REASONING_USE_REAL_LLM", "true")
    mock_response = {
        "choices": [{"message": {"content": _VALID_LLM_JSON}}]
    }
    with patch(
        "app.tools.spatial_reasoning.call_llm", return_value=mock_response
    ), confirm_tier3():
        result = await registry.dispatch(
            "spatial_reasoning", {"query": "真实推演问题"}
        )
    assert result["type"] == "spatial_reasoning"
    assert result["conclusion"] == "真实推演结论"
    assert result["confidence"] == 0.8
    assert result.get("success") is not False


@pytest.mark.asyncio
async def test_flag_set_llm_failure_returns_error_not_conclusion(registry, monkeypatch):
    """With the flag on but the provider failing, the result is an explicit
    error (confidence 0.0), never a canned success."""
    monkeypatch.setenv("SPATIAL_REASONING_USE_REAL_LLM", "true")
    with patch(
        "app.tools.spatial_reasoning.call_llm",
        side_effect=RuntimeError("provider down"),
    ), confirm_tier3():
        result = await registry.dispatch(
            "spatial_reasoning", {"query": "q"}
        )
    assert result.get("confidence") == 0.0
    assert "商业选址" not in str(result)
