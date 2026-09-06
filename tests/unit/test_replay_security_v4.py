"""ADR-0101 D12/D13（V4 §31/§32/§33/§34/§36）：trace 重放不变式、
结构化追踪披露、安全传播与物化对抗、确定性混沌注入。"""
from __future__ import annotations

import threading

import pytest

from app.services.geocompute import (
    ExecutionNode,
    ExecutionPlan,
    NodeCategory,
    RetryPolicy,
    tracing,
)
from app.services.geocompute.executor import GeoExecutionEngine
from app.services.geocompute.replay import replay_trace


def _fc(n: int = 4) -> list[dict]:
    return [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
         "properties": {"v": i, "kind": "a" if i % 2 == 0 else "b"}}
        for i in range(n)
    ]


def _node(node_id="f1", **kw) -> ExecutionNode:
    params = kw.pop("parameters", None) or {
        "predicate": {"op": "eq", "field": "kind", "value": "a"},
        "features": _fc(4),
    }
    return ExecutionNode(
        node_id=node_id, category=kw.pop("category", NodeCategory.FILTER),
        parameters=params, **kw,
    )


# ---------------------------------------------------------- trace 重放（§32）


class TestTraceReplay:
    def _record(self, plan, **kw):
        engine = GeoExecutionEngine(max_workers=2, **kw)
        tracing._ring.clear()
        run = engine.execute_plan(plan)
        events = tracing.recent_events(limit=1024)
        return run, [e for e in events if e.get("run_id") == run.run_id]

    def test_happy_path_trace_is_valid(self):
        plan = ExecutionPlan(plan_id="r1", nodes=[
            _node("a"), _node("b", inputs=["a"])])
        _, events = self._record(plan)
        report = replay_trace(events)
        assert report["valid"], report["violations"]
        assert report["nodes"]["a"]["terminal"] == "node_completed"

    def test_failure_and_skip_trace_is_valid(self):
        # NETWORK_OPERATION 未接线 → 诚实 OPERATION_UNSUPPORTED 失败。
        boom = _node("boom", category=NodeCategory.NETWORK_OPERATION)
        plan = ExecutionPlan(plan_id="r2", nodes=[
            boom, _node("child", inputs=["boom"])])
        _, events = self._record(plan)
        report = replay_trace(events)
        assert report["valid"], report["violations"]
        assert report["nodes"]["child"]["terminal"] == "node_skipped"

    def test_cancellation_trace_is_valid(self):
        from app.lib.cancellation import CancellationToken

        token = CancellationToken(job_id="t")
        token.cancel("pre-cancelled")
        engine = GeoExecutionEngine(max_workers=1)
        tracing._ring.clear()
        plan = ExecutionPlan(plan_id="r3", nodes=[_node("a")])
        run = engine.execute_plan(plan, cancel_token=token)
        events = [e for e in tracing.recent_events(limit=1024)
                  if e.get("run_id") == run.run_id]
        report = replay_trace(events)
        assert report["valid"], report["violations"]
        assert report["nodes"]["a"]["terminal"] in {"node_cancelled", "node_marked"}

    def test_fabricated_completed_after_cancelled_is_caught(self):
        events = [
            {"event": "run_started", "run_id": "r"},
            {"event": "node_admitted", "run_id": "r", "node_id": "a"},
            {"event": "node_cancelled", "run_id": "r", "node_id": "a"},
            {"event": "node_completed", "run_id": "r", "node_id": "a"},
            {"event": "run_finished", "run_id": "r"},
        ]
        report = replay_trace(events)
        assert not report["valid"]
        assert any("second terminal" in v for v in report["violations"])

    def test_event_after_run_finished_is_caught(self):
        events = [
            {"event": "run_started", "run_id": "r"},
            {"event": "run_finished", "run_id": "r"},
            {"event": "node_completed", "run_id": "r", "node_id": "a"},
        ]
        report = replay_trace(events)
        assert not report["valid"]
        assert any("after run_finished" in v for v in report["violations"])

    def test_retry_ceiling_violation(self):
        events = [{"event": "run_started", "run_id": "r"}]
        for _ in range(5):
            events.append({"event": "node_attempt_failed", "run_id": "r", "node_id": "a"})
        events.append({"event": "run_finished", "run_id": "r"})
        report = replay_trace(events)
        assert not report["valid"]
        assert any("retry ceiling" in v for v in report["violations"])

    def test_admitted_twice_violation(self):
        events = [
            {"event": "run_started", "run_id": "r"},
            {"event": "node_admitted", "run_id": "r", "node_id": "a"},
            {"event": "node_admitted", "run_id": "r", "node_id": "a"},
            {"event": "node_completed", "run_id": "r", "node_id": "a"},
            {"event": "run_finished", "run_id": "r"},
        ]
        report = replay_trace(events)
        assert not report["valid"]

    def test_retry_run_replay_records_attempt_failures(self):
        plan = ExecutionPlan(plan_id="r4", nodes=[
            _node("boom", parameters={
                "predicate": {"op": "eq", "field": "kind", "value": "a"},
                "features": _fc(2), "_boom": "remote", "_retry_safe": True,
                "_fclass": "transient_remote"},
                retry=RetryPolicy(max_attempts=2, backoff_s=0.01))])
        # SOURCE_SCAN 需要桩：借用 executor_v4 的思路直接 patch。
        from app.services.geocompute import ops
        from app.services.geocompute.errors import FailureClass, NodeExecutionError

        def _scan(ctx, node, payloads):
            if node.parameters.get("_boom"):
                raise NodeExecutionError(
                    "remote hiccup", retry_safe=True,
                    failure_class=FailureClass.TRANSIENT_REMOTE)
            return {"features": node.parameters.get("features") or []}

        ops.REGISTRY[NodeCategory.SOURCE_SCAN] = _scan
        try:
            plan = ExecutionPlan(plan_id="r4b", nodes=[
                ExecutionNode(node_id="boom", category=NodeCategory.SOURCE_SCAN,
                              parameters={"_boom": "x"},
                              retry=RetryPolicy(max_attempts=2, backoff_s=0.01))])
            engine = GeoExecutionEngine(max_workers=1)
            tracing._ring.clear()
            run = engine.execute_plan(plan)
            events = [e for e in tracing.recent_events(limit=1024)
                      if e.get("run_id") == run.run_id]
        finally:
            ops.REGISTRY.pop(NodeCategory.SOURCE_SCAN, None)
        report = replay_trace(events)
        assert report["valid"], report["violations"]
        assert report["nodes"]["boom"]["attempt_failures"] == 2


# ------------------------------------------------------- 安全传播（§33/§34）


class TestSecurityPropagation:
    def test_trace_events_drop_secret_like_fields(self):
        tracing._ring.clear()
        tracing.emit("node_completed", run_id="r", node_id="n",
                     access_token="secret-token", password="hunter2",
                     sql_text="select * from secrets")
        events = tracing.recent_events(limit=10)
        ev = events[-1]
        assert "access_token" not in ev
        assert "password" not in ev
        assert "sql_text" not in ev

    def test_error_messages_scrub_paths(self):
        from app.services.geocompute.executor import _scrub_error_message

        msg = _scrub_error_message("failed to read /home/kevin/secret/data.tif: boom")
        assert "/home/kevin" not in msg
        assert "<path>" in msg

    def test_reuse_keys_never_leak_cross_owner(self):
        from app.services.geocompute.executor import owner_scope_for
        from app.services.geocompute.graph import checkpoint_reuse_key

        node = _node("x")
        key_u1 = checkpoint_reuse_key(node, owner_scope_for({"user_id": "u1"}, None))
        key_u2 = checkpoint_reuse_key(node, owner_scope_for({"user_id": "u2"}, None))
        key_s = checkpoint_reuse_key(node, owner_scope_for(None, "sess-1"))
        assert len({key_u1, key_u2, key_s}) == 3
        assert "u1" not in key_u1 and "sess-1" not in key_s  # 哈希域，不落明文

    def test_broadcast_messages_carry_no_payloads(self):
        """已在 Wave 8 覆盖字段集合；这里锁「值有界」这一条。"""
        import json

        import app.services.cache_broadcast as cb

        captured = {}

        class FakeClient:
            def publish(self, channel, message):
                captured["m"] = message

        # patch _client_cached（不是 _redis_client）：_client_cached 有
        # 失败退避缓存，patch 底层可能被缓存短路（round-2 评审发现）。
        orig = cb._client_cached
        cb._client_cached = lambda: FakeClient()
        try:
            cb.broadcast_ref_invalidation("s" * 500, "r" * 500, "OVERWRITE")
        finally:
            cb._client_cached = orig
        event = json.loads(captured["m"])
        assert len(event["session_id"]) <= 128
        assert len(event["ref_id"]) <= 128

    def test_materialize_rejects_missing_session_and_data(self):
        from app.services.geocompute import ops
        from app.services.geocompute.errors import NodeExecutionError

        ctx = ops.OperatorContext(run_id="r", node_id="m", session_id=None)
        node = _node("m", category=NodeCategory.MATERIALIZE, inputs=["a"])
        with pytest.raises(NodeExecutionError, match="session"):
            ops.execute_node(ctx, node, {"a": {"features": _fc(2)}})


class TestChaosInjections:
    """确定性故障注入（§36）：无 sleep 重、无网络、无 docker。"""

    def test_corrupt_cache_entry_treated_as_miss(self):
        """缓存条目损坏（无 __size__ 标记）→ 视为 miss，诚实重算。"""
        engine = GeoExecutionEngine(max_workers=1)
        plan = ExecutionPlan(plan_id="c1", nodes=[_node("a")])
        engine.execute_plan(plan)
        # 毒化缓存条目（无输入源节点 → 计划域复用键）。
        from app.services.geocompute.graph import node_reuse_key
        from app.services.geocompute.executor import owner_scope_for

        plan_fp = plan.graph_fingerprint()
        key = node_reuse_key(plan_fp, _node("a"), owner_scope_for(None, None))
        with engine._store._lock:
            engine._store._entries[key] = {"garbage": True}  # 丢 __size__
        tracing._ring.clear()
        run2 = engine.execute_plan(plan)
        assert run2.evidence["a"].status == "completed"  # 重算而非使用垃圾

    def test_cancel_during_retry_backoff(self):
        """重试退避期间取消 → 节点收敛为 cancelled，不完成。"""
        from app.lib.cancellation import CancellationToken
        from app.services.geocompute import ops
        from app.services.geocompute.errors import FailureClass, NodeExecutionError

        calls = {"n": 0}
        started = threading.Event()

        def flaky(ctx, node, payloads):
            calls["n"] += 1
            started.set()
            import time

            time.sleep(0.05)
            raise NodeExecutionError("transient", retry_safe=True,
                                     failure_class=FailureClass.TRANSIENT_REMOTE)

        ops.REGISTRY[NodeCategory.FILTER] = flaky
        token = CancellationToken(job_id="c2")
        try:
            def canceller():
                started.wait(5)
                token.cancel("cancelled mid-retry")

            ct = threading.Thread(target=canceller)
            ct.start()
            engine = GeoExecutionEngine(max_workers=1)
            plan = ExecutionPlan(plan_id="c2", nodes=[
                _node("a", retry=RetryPolicy(max_attempts=4, backoff_s=0.4,
                                             jitter=False))])
            run = engine.execute_plan(plan, cancel_token=token)
            ct.join(timeout=10)
            assert run.evidence["a"].status == "cancelled"
        finally:
            ops.REGISTRY.pop(NodeCategory.FILTER, None)

    def test_deadline_during_retry_backoff_converges(self):
        """退避长于剩余 deadline → 拒绝重试，快速失败（不空转）。"""
        import time

        from app.services.geocompute import ops
        from app.services.geocompute.errors import FailureClass, NodeExecutionError

        def flaky(ctx, node, payloads):
            raise NodeExecutionError("transient", retry_safe=True,
                                     failure_class=FailureClass.TRANSIENT_REMOTE)

        ops.REGISTRY[NodeCategory.FILTER] = flaky
        try:
            engine = GeoExecutionEngine(max_workers=1)
            t0 = time.monotonic()
            plan = ExecutionPlan(
                plan_id="c3",
                nodes=[_node("a", retry=RetryPolicy(max_attempts=3, backoff_s=9.0,
                                                    jitter=False, max_backoff_s=9.0),
                              deadline_s=2.0)],
                budget=__import__("app.services.geocompute", fromlist=["ResourceBudget"]).ResourceBudget(deadline_s=30),
            )
            run = engine.execute_plan(plan)
            elapsed = time.monotonic() - t0
            assert run.evidence["a"].status == "failed"
            assert run.evidence["a"].attempts == 1  # deadline 感知拒绝重试
            assert elapsed < 5  # 没有真的睡 9s
        finally:
            ops.REGISTRY.pop(NodeCategory.FILTER, None)

    def test_cancelled_node_never_registers_output(self):
        """取消节点不产 output_ref / 不落 artifact（§31 红线）。"""
        from app.lib.cancellation import CancellationToken

        token = CancellationToken(job_id="c4")
        token.cancel("pre")
        engine = GeoExecutionEngine(max_workers=1)
        plan = ExecutionPlan(plan_id="c4", nodes=[
            _node("a"), _node("m", category=NodeCategory.MATERIALIZE, inputs=["a"])])
        run = engine.execute_plan(plan, cancel_token=token)
        for ev in run.evidence.values():
            assert ev.output_ref is None
            assert ev.status in {"cancelled", "skipped", "pending"}
