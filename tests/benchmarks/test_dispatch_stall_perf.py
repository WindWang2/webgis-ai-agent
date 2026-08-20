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

    # Warm up the executor / machinery first — first-call thread-pool spin-up
    # costs ~150-200ms one-time and is not the loop stall this test pins.
    # Steady state (measured on the fix): ~8.5ms marginal vs small-tool ~0.3ms.
    await reg.dispatch("echo_big", {"geojson": {"type": "Point", "coordinates": [0, 0]}}, session_id=None)

    t0 = time.perf_counter()
    res = await reg.dispatch("echo_big", {"geojson": {"type": "Point", "coordinates": [0, 0]}}, session_id=None)
    stall = (time.perf_counter() - t0) * 1000
    assert res["success"] is True
    assert len(res["features"]) == 100_000
    # Before: full result walk was ~360-430ms on the loop (flip-red). After:
    # budgeted — steady-state marginal cost measured ~8.5ms; 30ms cap keeps
    # ~3.5x headroom for CI jitter while still failing loudly on O(features).
    assert stall < 30.0, f"dispatch stall {stall:.1f}ms — must be O(budget) not O(features)"


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
        # dispatch 的 hint 探测是 args 的唯一一次外层遍历：oversized hint 命中后
        # _dispatch_impl 校验门与 make_cache_key 都零遍历。任何回归（hint 丢失
        # 回退全量走）都会数出 2+ 次而红。精确 == 1，不接受恒真的小于等于。
        assert call_count[0] == 1, f"args walked {call_count[0]} times — must be exactly 1 via hint reuse"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_result_bytes_approx_traceable():
    """预算外推的估计值必须可追溯：tool_metrics 收到 result_bytes_approx=True。"""
    from unittest.mock import patch as _patch
    import app.services.tool_metrics as tm

    fc = _fc(100_000)
    reg = ToolRegistry()

    def echo_tool(geojson: dict) -> dict:
        return {"type": "FeatureCollection", "features": fc["features"], "success": True}

    reg.register("echo_tr", "e", echo_tool)

    captured = []
    real_rec = tm.record_tool_call

    def capture(**kw):
        captured.append(kw)
        return real_rec(**kw)

    with _patch.object(tm, "record_tool_call", side_effect=capture):
        await reg.dispatch("echo_tr", {"geojson": {"type": "Point", "coordinates": [0, 0]}}, session_id=None)

    assert captured, "record_tool_call must have been invoked"
    row = captured[-1]
    assert row["result_bytes"] > 100_000, "100k-feature result estimate must be non-trivial"
    assert row["result_bytes_approx"] is True, "budget-exhausted estimate must be flagged approximate"
    assert row["arg_bytes_approx"] is False, "tiny args stay exact"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_dispatch_impl_no_hint_gate_is_budgeted():
    """直调 _dispatch_impl（无 ContextVar hint）时校验门同样预算化：
    大 args 走保守 oversized 分支跳过 validate_geojson_structure，
    估计 walker 本身有界（<15ms）。注意：本测试不断言直调总时长 —
    Pydantic 对 10 万要素 args 的 model_dump/校验是另一笔 ~200ms 的
    O(features) 开销（票面之外的相邻问题，另立票跟踪），不应在此误判
    为 walker 回归。"""
    from unittest.mock import patch as _patch
    import app.tools.registry as reg_mod
    from app.tools.registry import _arg_size_hint_var, _ESTIMATE_MAX_NODES

    fc = _fc(100_000)
    reg = ToolRegistry()

    def tiny_tool(geojson: dict) -> dict:
        return {"ok": True}

    reg.register("tiny_direct", "t", tiny_tool)

    # walker 本身有界：一次预算化估计远低于无界全量走（flip-red ~360ms）
    t0 = time.perf_counter()
    _estimate_json_bytes = reg_mod._estimate_json_bytes
    est = _estimate_json_bytes({"geojson": fc}, _budget=[_ESTIMATE_MAX_NODES])
    dt = (time.perf_counter() - t0) * 1000
    assert est > 100_000
    assert dt < 15.0, f"budgeted walk {dt:.1f}ms — must be O(budget)"

    # 门槛语义（确定性断言，不依赖计时）：无 hint 分支的顶层估计调用必须
    # 显式携带预算对象 — 修复前这里是 `_estimate_json_bytes(arguments)`
    # 无预算全量走（~360ms），本断言红。
    real_est = _estimate_json_bytes
    top_level_budgeted = []

    def budget_probe(obj, _depth=0, _budget=None):
        if _depth == 0:
            top_level_budgeted.append(_budget is not None)
        return real_est(obj, _depth=_depth, _budget=_budget)

    with _patch.object(reg_mod, "_estimate_json_bytes", side_effect=budget_probe), _patch.object(
        reg_mod, "validate_geojson_structure", side_effect=lambda a: None
    ):
        token = _arg_size_hint_var.set(None)  # 强制无 hint 的 else 分支
        try:
            res = await reg._dispatch_impl("tiny_direct", {"geojson": fc})
        finally:
            _arg_size_hint_var.reset(token)
    assert res["ok"] is True
    assert top_level_budgeted == [True], (
        f"no-hint gate estimate must pass an explicit budget (got {top_level_budgeted})"
    )
