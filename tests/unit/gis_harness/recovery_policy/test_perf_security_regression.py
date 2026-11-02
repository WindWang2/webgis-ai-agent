"""M8 性能与安全回归（GOAL 05 / ADR-0279）—— 确定性、可复跑两次。

性能面：
- 诊断+裁决纯函数吞吐（中位数/ P95 阈值宽松防 flaky）；
- recovery payload 与 LLM note 有界（上下文预算零膨胀超限）；
- 重复裁决无状态增长（重试风暴上限 —— 预算钳死）。

安全面：
- 诊断/LLM note 不携带他会话 ID（跨租户零泄漏）；
- 不从 map_state 读身份字段（#1843 类信任边界不复制：本层签名面
  只有服务端解析后的 ref_owner_sessions，不可证明即 fail-closed）；
- last_recovery 形状门：伪造 reason 拒绝落账（计划存储不接受任意结构）。
"""
from __future__ import annotations

import json
import statistics
import time

import pytest

from app.services.gis_harness.recovery_policy.contracts import (
    REASON_REGISTRY,
)
from app.services.gis_harness.recovery_policy.continuation import (
    resolve_retry_directive,
)
from app.services.gis_harness.recovery_policy.wiring import (
    build_recovery_payload,
)

_REPEATS = 200


class TestPerformanceRegression:
    def test_diagnose_decide_throughput(self):
        """诊断+裁决端到端中位数 < 2ms、P95 < 8ms（纯函数）。"""
        timings = []
        for i in range(_REPEATS):
            t0 = time.perf_counter()
            build_recovery_payload(
                message=f"upstream 503 attempt {i}", tool_name="query_pois",
                harness_failure={"class": "timeout", "attempts": 0,
                                 "max_attempts": 2, "retry_allowed": True},
                session_id="s")
            timings.append((time.perf_counter() - t0) * 1000.0)
        assert statistics.median(timings) < 2.0
        assert statistics.quantiles(timings, n=20)[18] < 8.0  # p95

    def test_payload_size_bounded(self):
        payload = build_recovery_payload(
            message="x" * 5000, tool_name="t" * 300,
            harness_failure={"class": "timeout", "attempts": 1,
                             "max_attempts": 2, "retry_allowed": True},
            session_id="s")
        blob = json.dumps(payload, default=str)
        assert len(blob) < 2500  # 有界（诊断+裁决全量序列化）

    def test_llm_note_bounded_240(self):
        payload = build_recovery_payload(
            message="m" * 3000, tool_name="t",
            harness_failure={"class": "timeout", "attempts": 0,
                             "max_attempts": 3, "retry_allowed": True},
            session_id="s")
        assert len(payload["llm_note"]) <= 240

    def test_repeated_decisions_do_not_grow_state(self):
        """同一失败重复裁决：输出尺寸恒定（无累计状态 → 无重试风暴）。"""
        first = None
        for i in range(50):
            payload = build_recovery_payload(
                message="HTTP 429", tool_name="query_pois",
                harness_failure={"class": "timeout", "attempts": i % 3,
                                 "max_attempts": 3,
                                 "retry_allowed": i % 3 < 3},
                session_id="s")
            blob = json.dumps(payload, default=str)
            if first is None:
                first = len(blob)
            # attempts 数位变化允许 ±8 字节，但不允许结构增长
            assert abs(len(blob) - first) < 8

    def test_deterministic_repeatable_baseline(self):
        """M8 基线可复跑：双跑结果一致（非计时项）。"""
        kw = dict(message="HTTP 429", tool_name="query_pois",
                  harness_failure={"class": "timeout", "attempts": 0,
                                   "max_attempts": 2, "retry_allowed": True},
                  session_id="s")
        r1 = build_recovery_payload(**kw)
        r2 = build_recovery_payload(**kw)
        assert r1 == r2


class TestSecurityRegression:
    def test_cross_session_id_never_in_note(self):
        """他会话 ID（ref_origin）不得泄漏进 LLM note / detail。"""
        payload = build_recovery_payload(
            message="ref not found", code="NOT_FOUND", tool_name="t",
            harness_failure={"class": "stale_ref", "attempts": 0,
                             "max_attempts": 2, "retry_allowed": True},
            session_id="sess-current", ref_origin_session="sess-SECRET")
        note = payload["llm_note"]
        diag_detail = payload["diagnostic"]["detail"]
        assert "sess-SECRET" not in note
        assert "sess-SECRET" not in diag_detail
        # 归因面只携带本会话 ID
        assert payload["diagnostic"]["attribution"]["session_id"] == \
            "sess-current"

    def test_unknown_ref_owner_fail_closed(self):
        """#1843 类边界：身份/归属不可证明 → fail-closed，不猜。"""
        d = resolve_retry_directive(
            progress_rows=[{"capability": "a", "status": "failed"}],
            last_recovery=None, chapter=None,
            user_ref_hints=["ref:x"], ref_owner_sessions=None,
            current_session_id="sess-cur")
        assert d.verdict == "fail_closed"

    def test_continuation_has_no_map_state_param(self):
        """签名面纪律：恢复层不接受客户端可写的 map_state 身份字段。"""
        import inspect
        sig = inspect.signature(resolve_retry_directive)
        assert "map_state" not in sig.parameters
        assert "_gis_memory_org" not in sig.parameters
        assert "_mission_binding" not in sig.parameters

    @pytest.mark.asyncio
    async def test_forged_recovery_reason_rejected(self, monkeypatch, tmp_path):
        """last_recovery 形状门：未知 reason 码的伪造 payload 不落账。"""
        monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "d"))
        from app.services.session_data import session_data_manager

        sid = "m8-forged"
        await session_data_manager.clear_session(sid)
        from app.services.session_plan import (
            apply_tool_result,
            load_session_plan,
        )

        await apply_tool_result(
            sid, "webgis_map_intent",
            {"plan": {"query": "q", "capabilities": [
                {"capability": "c", "tools": ["tool_x"]}],
                "data_requirements": []}},
            success=True)
        await apply_tool_result(
            sid, "tool_x",
            {"success": False, "error": "boom",
             "recovery": {"diagnostic": {
                 "reason": "totally_fake_reason",
                 "disposition": "retryable"},
                 "decision": {}}},
            success=False)
        plan = await load_session_plan(sid, store=session_data_manager)
        assert plan.last_recovery is None  # 伪造 → 拒绝落账

        await session_data_manager.clear_session(sid)

    def test_reason_registry_closure_is_the_only_gate(self):
        """封闭词表就是形状门的事实源（新码必须登记，未登记即拒）。"""
        assert "totally_fake_reason" not in REASON_REGISTRY
        assert len(REASON_REGISTRY) >= 25


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
