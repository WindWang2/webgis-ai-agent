"""ADR-0101 Wave 6: 执行策略审计 + 无进展 V2 + 取消竞态测试。"""
import asyncio

import pytest

from app.services.chat.no_progress import (
    CallPatternTracker,
    canonical_call_signature,
)
from app.tools.policy_audit import (
    Severity,
    audit_registration,
    audit_registry_policies,
    error_findings,
)
from app.tools.registry import ToolExecutionPolicy, ToolRegistry


# ---------------------------------------------------------------------------
# 策略审计
# ---------------------------------------------------------------------------

def _noop():
    return {}


async def _anoop():
    return {}


def test_sync_declared_async_flagged():
    findings = audit_registration("t", _noop, ToolExecutionPolicy.ASYNC, "light", None)
    assert any(f.code == "sync_declared_async" and f.severity is Severity.WARNING
               for f in findings)


def test_heavy_inline_is_error():
    findings = audit_registration("t", _noop, ToolExecutionPolicy.INLINE, "heavy", None)
    assert any(f.code == "heavy_inline" and f.severity is Severity.ERROR
               for f in findings)


def test_inline_timeout_ineffective_info():
    findings = audit_registration("t", _noop, ToolExecutionPolicy.INLINE, "light", 5.0)
    assert any(f.code == "inline_timeout_ineffective" for f in findings)


def test_heavy_without_timeout_info():
    findings = audit_registration("t", _noop, ToolExecutionPolicy.THREAD, "heavy", None)
    assert any(f.code == "heavy_no_explicit_timeout" for f in findings)


def test_clean_tool_has_no_findings():
    assert audit_registration("t", _anoop, ToolExecutionPolicy.ASYNC, "light", None) == []


def test_error_findings_filter():
    findings = audit_registration("t", _noop, ToolExecutionPolicy.INLINE, "heavy", None)
    assert all(f.severity is Severity.ERROR for f in error_findings(findings))


def test_live_registry_policy_scan_clean():
    """活注册表：error 级策略发现必须为零（CI 门）。"""
    from app.tools import init_tools

    reg = ToolRegistry()
    init_tools(reg)
    findings = audit_registry_policies(reg)
    errs = error_findings(findings)
    assert errs == [], f"live registry policy errors: {[e.as_dict() for e in errs]}"


# ---------------------------------------------------------------------------
# canonical 调用签名
# ---------------------------------------------------------------------------

def test_signature_folds_tool_aliases():
    s1 = canonical_call_signature("buffer", {"distance": 100})
    s2 = canonical_call_signature("buffer_analysis", {"distance": 100})
    assert s1 == s2
    assert s1.startswith("buffer_analysis:")


def test_signature_order_and_numeric_insensitive():
    a = canonical_call_signature("t1", {"x": 1, "y": 2})
    b = canonical_call_signature("t1", {"y": 2, "x": 1})
    assert a == b
    # 浮点抖动容差（round 6）
    c = canonical_call_signature("t1", {"x": 1.0000001, "y": 2})
    assert c.startswith("t1:")


def test_signature_large_payload_summarized():
    big_fc = {"geojson": {"type": "FeatureCollection",
                          "features": [{"type": "Feature"} for _ in range(10_000)]}}
    same_shape = {"geojson": {"type": "FeatureCollection",
                              "features": [{"type": "Feature"} for _ in range(10_000)]}}
    assert canonical_call_signature("t", big_fc) == canonical_call_signature("t", same_shape)
    # 长度不同 → 签名不同（数据规模是语义）
    small = {"geojson": {"type": "FeatureCollection", "features": [{"type": "Feature"}]}}
    assert canonical_call_signature("t", big_fc) != canonical_call_signature("t", small)


def test_signature_survives_raw_json_string_args():
    s1 = canonical_call_signature("t1", '{"x": 1}')
    s2 = canonical_call_signature("t1", {"x": 1})
    assert s1 == s2


# ---------------------------------------------------------------------------
# 模式 tracker
# ---------------------------------------------------------------------------

def test_tracker_exact_repeat_failure():
    t = CallPatternTracker()
    r1 = t.record("buffer_analysis", {"distance": 1}, "error")
    r2 = t.record("buffer_analysis", {"distance": 1}, "error")
    assert r1 == []
    assert "exact_repeat_failure" in r2


def test_tracker_alias_oscillation_detected():
    t = CallPatternTracker()
    t.record("buffer", {"distance": 1}, "error")
    reasons = t.record("buffer_layer", {"distance": 1}, "error")
    assert "alias_oscillation" in reasons


def test_tracker_no_false_positive_on_state_change():
    t = CallPatternTracker()
    t.record("buffer_analysis", {"distance": 1}, "error", state_epoch=1)
    reasons = t.record("buffer_analysis", {"distance": 1}, "error", state_epoch=2)
    assert "exact_repeat_failure" not in reasons  # 上游状态真的变了


def test_tracker_repeated_read():
    t = CallPatternTracker(repeated_read_threshold=3)
    t.record("list_refs", {}, "ok", is_read_only=True, state_epoch=5)
    t.record("list_refs", {}, "ok", is_read_only=True, state_epoch=5)
    reasons = t.record("list_refs", {}, "ok", is_read_only=True, state_epoch=5)
    assert any(r.startswith("repeated_read:") for r in reasons)


def test_tracker_mutation_without_state_change():
    t = CallPatternTracker()
    t.record("webgis_component_update", {"id": "x"}, "ok", is_mutation=True, state_epoch=3)
    reasons = t.record("webgis_component_update", {"id": "x"}, "ok", is_mutation=True, state_epoch=3)
    assert "repeated_mutation_no_state_change" in reasons


def test_tracker_bounded():
    t = CallPatternTracker(max_records=8)
    for i in range(50):
        t.record("t", {"i": i}, "ok")
    assert len(t.records) <= 8


# ---------------------------------------------------------------------------
# 取消竞态（§26）：并行波取消风暴下的信号量不变量
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancellation_storm_keeps_semaphore_invariant():
    """N 个并行 sync 工具 + 取消风暴 → 泄漏线程计数单调、无未归还槽位。"""
    from app.tools import registry as reg_mod

    reg = ToolRegistry()
    started = asyncio.Event()

    def slow_tool(seconds: float = 2.0) -> dict:
        started.set()
        import time as _t

        _t.sleep(seconds)
        return {"success": True}

    reg.register(name="slow_probe", description="probe", func=slow_tool,
                 execution_policy=ToolExecutionPolicy.THREAD)

    leaked_before = reg_mod._tool_thread_leaked_count

    async def one_call():
        try:
            await asyncio.wait_for(reg.dispatch("slow_probe", {"seconds": 2.0}), timeout=0.15)
        except (asyncio.TimeoutError, TimeoutError):
            pass

    await asyncio.gather(*(one_call() for _ in range(8)))
    # 全部超时放弃 → 每个放弃的调用都计一次泄漏（线程继续跑完）
    assert reg_mod._tool_thread_leaked_count - leaked_before >= 8
    # 信号量最终全额归还：再跑一批快速调用，若槽位泄漏会在这里饿死
    done = await asyncio.wait_for(
        asyncio.gather(*(reg.dispatch("slow_probe", {"seconds": 0.01}) for _ in range(16))),
        timeout=30,
    )
    assert all(r.get("success") for r in done)


@pytest.mark.asyncio
async def test_parallel_dispatch_result_order_independent():
    """并行工具波：完成顺序不影响各自结果正确性（§28 确定性）。"""
    reg = ToolRegistry()

    def echo(val: int) -> dict:
        import time as _t

        _t.sleep(0.01 * (10 - val % 10))  # 故意错开完成顺序
        return {"success": True, "val": val}

    reg.register(name="echo_probe", description="probe", func=echo)
    results = await asyncio.gather(
        *(reg.dispatch("echo_probe", {"val": v}) for v in range(10))
    )
    assert [r["val"] for r in results] == list(range(10))
