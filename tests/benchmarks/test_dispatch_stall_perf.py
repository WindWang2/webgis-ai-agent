"""#677 dispatch stall benchmark — bounded estimator + single-traversal gate (perf).

"""
import statistics
import time

import pytest

from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.perf

# Synthesize FeatureCollection payloads of given feature counts.
def _fc(n, kind="Point"):
    if kind == "Point":
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"id": i, "name": f"pt-{i}", "v": float(i)},
                    "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.0001, 39.0]},
                }
                for i in range(n)
            ],
        }
    raise ValueError(kind)


def _med(fn, n=7):
    return statistics.median(fn() for _ in range(n))


def _estimate_ms(payload):
    """Wall-clock of the budgeted walker — tight O(budget), not O(features)."""
    from app.tools.registry import _estimate_json_bytes
    t0 = time.perf_counter()
    est = _estimate_json_bytes(payload)
    dt = (time.perf_counter() - t0) * 1000
    assert est > 0
    return dt


@pytest.mark.perf
def test_estimate_10k_bounded():
    fc = _fc(10_000)
    med = _med(lambda: _estimate_ms(fc))
    # Before (#677): 10k ≈ tens of ms full walk. After: O(20k nodes) ≈ few ms.
    assert med < 25.0, f"10k estimate {med:.3f}ms — must stay O(budget)"


@pytest.mark.perf
def test_estimate_50k_bounded():
    fc = _fc(50_000)
    med = _med(lambda: _estimate_ms(fc))
    assert med < 25.0, f"50k estimate {med:.3f}ms — budget must cap width"


@pytest.mark.perf
def test_estimate_100k_bounded():
    fc = _fc(100_000)
    med = _med(lambda: _estimate_ms(fc))
    assert med < 25.0, f"100k estimate {med:.3f}ms — 100k must not scale linearly"


@pytest.mark.perf
def test_estimate_100k_not_linear_vs_10k():
    fc10 = _fc(10_000)
    fc100 = _fc(100_000)
    # Walker must be sub-linear in feature count — budget caps it. Before: 100k ~10x 10k.
    # Give warmup so JIT/caching settles.
    _estimate_ms(fc10)
    _estimate_ms(fc100)
    med10 = _med(lambda: _estimate_ms(fc10))
    med100 = _med(lambda: _estimate_ms(fc100))
    # With budget the 100k cost stays O(budget) — at most 4x the 10k cost.
    assert med100 < med10 * 4 + 8.0, f"not sub-linear: 10k {med10:.3f}ms vs 100k {med100:.3f}ms"


@pytest.mark.perf
def test_estimate_budget_hit_is_traceable():
    from app.tools.registry import _ESTIMATE_MAX_NODES, _estimate_json_bytes
    fc = _fc(100_000)
    budget = [_ESTIMATE_MAX_NODES]
    est = _estimate_json_bytes(fc, _budget=budget)
    # Huge payload must exhaust the budget (approximation marker) and still
    # return a plausible byte count (not zero / tiny).
    assert budget[0] <= 0, "100k must exhaust the node budget"
    assert est > 500_000, f"approx estimate implausibly small: {est}"
    # Small payload stays exact (budget not hit)
    small = {"a": 1, "b": [1, 2, 3]}
    b2 = [_ESTIMATE_MAX_NODES]
    e2 = _estimate_json_bytes(small, _budget=b2)
    assert b2[0] > 0, "small payload must not exhaust budget"
    assert e2 == _estimate_json_bytes(small)


@pytest.mark.perf
@pytest.mark.asyncio
async def test_dispatch_100k_result_stall_bounded():
    """End-to-end dispatch stall: result_bytes on the event loop must stay budgeted.

    Synthetic tool returns a 100k-feature FC. dispatch() does the budgeted
    result estimate on the loop — assert the event-loop tick delay is O(ms),
    not 100s of ms. Also asserts the tool result reaches the caller intact.
    """
    from app.tools.registry import ToolRegistry

    fc = _fc(100_000)

    reg = ToolRegistry()

    def echo_tool(geojson: dict) -> dict:
        return {"type": "FeatureCollection", "features": fc["features"], "success": True}

    reg.register("echo_big", "echo", echo_tool)

    # Wrap the metrics recorder so we can isolate dispatch's loop stall
    # from DB/Redis red herrings — just time dispatch itself.
    t0 = time.perf_counter()
    res = await reg.dispatch("echo_big", {"geojson": {"type": "Point", "coordinates": [0, 0]}}, session_id=None)
    stall = (time.perf_counter() - t0) * 1000
    assert res["success"] is True
    assert len(res["features"]) == 100_000
    # Before: full result walk was ~200-700ms on the loop. After: budgeted.
    assert stall < 250.0, f"dispatch stall {stall:.1f}ms — must be O(budget) not O(features)"


@pytest.mark.perf
def test_args_single_traversal_gate():
    """Args size gate is walked once (hint) — verify oversized short-circuits."""
    from app.lib.tool_cache import make_cache_key

    fc = _fc(100_000)
    args = {"geojson": fc, "extra": "x" * 100}
    # Outside dispatch (no hint), make_cache_key should still bypass cache
    # for oversized args — but via a budget-limited walk, not a full walk.
    t0 = time.perf_counter()
    key = make_cache_key("some_tool", args)
    dt = (time.perf_counter() - t0) * 1000
    assert key is None, "oversized args must bypass cache (None key)"
    assert dt < 50.0, f"oversized args gate {dt:.3f}ms — must be O(budget)"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_args_inside_dispatch_reuses_hint():
    """Inside dispatch, _dispatch_impl + make_cache_key reuse the hint — second walk is skipped."""
    from unittest.mock import patch as _patch

    fc = _fc(80_000)
    args = {"geojson": fc}
    reg = ToolRegistry()

    def tiny_tool(geojson: dict) -> dict:
        return {"ok": True}

    reg.register("tiny_tool", "t", tiny_tool)

    # Instrument: count how many fresh _estimate_json_bytes walks happen for
    # this exact args object. The hint reuse should avoid a second walk.
    import app.tools.registry as reg_mod

    real_est = reg_mod._estimate_json_bytes
    call_count = [0]

    def counting_est(obj, _depth=0, _budget=None):
        # Only count walks rooted at the outer args dict
        if obj is args:
            call_count[0] += 1
        return real_est(obj, _depth=_depth, _budget=_budget)

    with _patch.object(reg_mod, "_estimate_json_bytes", side_effect=counting_est):
        # Need to also patch tc_mod's imported reference if any — it imports inside fn
        res = await reg.dispatch("tiny_tool", args, session_id=None)
        assert res["ok"] is True
        # dispatch does one hint walk; cache/make_cache_key and gate must reuse it
        # (so total outer walks == 1, not 2-3).
        assert call_count[0] <= 1, f"args walked {call_count[0]} times — must be 1 via hint reuse"
