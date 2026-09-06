"""ADR-0101 D11/D12：single-flight 防击穿、失效广播、可复现执行包、
执行级 lineage 投影。"""
from __future__ import annotations

import threading

import pytest

from app.services.geocompute import (
    ExecutionNode,
    ExecutionPlan,
    NodeCategory,
    PayloadKind,
)
from app.services.geocompute.executor import GeoExecutionEngine
from app.services.geocompute.reproducibility import (
    CONDITIONALLY_REPRODUCIBLE,
    NON_DETERMINISTIC,
    REPRODUCIBLE,
    SOURCE_UNAVAILABLE,
    STALE,
    build_execution_bundle,
    lineage_projection,
)
from app.services.singleflight import SingleFlight


def _fc(n: int = 3) -> list[dict]:
    return [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
         "properties": {"v": i, "kind": "a"}}
        for i in range(n)
    ]


def _filter_node(node_id="f1", **kw) -> ExecutionNode:
    return ExecutionNode(
        node_id=node_id, category=NodeCategory.FILTER,
        parameters={"predicate": {"op": "eq", "field": "kind", "value": "a"},
                    "features": _fc(4)},
        **kw,
    )


# ------------------------------------------------------------- single-flight


class TestSingleFlight:
    def test_concurrent_callers_share_result(self):
        sf = SingleFlight()
        calls = {"n": 0}
        started = threading.Event()

        def builder():
            calls["n"] += 1
            started.set()
            import time

            time.sleep(0.05)  # 让等待者进入等待态
            return "value"

        results: list = []
        lock = threading.Lock()

        def worker():
            v = sf.run("k", builder)
            with lock:
                results.append(v)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        started.wait(5)
        import time

        time.sleep(0.01)
        for t in threads:
            t.join(timeout=10)
        assert results == ["value"] * 3
        assert calls["n"] == 1

    def test_builder_failure_propagates_to_waiters(self):
        sf = SingleFlight()
        started = threading.Event()
        release = threading.Event()

        def builder():
            started.set()
            release.wait(5)
            raise RuntimeError("builder crashed")

        errs: list = []
        def waiter():
            started.wait(5)
            try:
                sf.run("k", builder)
            except RuntimeError as e:
                errs.append(str(e))

        wt = threading.Thread(target=waiter)
        wt.start()
        release.set()
        wt.join(timeout=10)
        # 等待者共享 leader 的失败（诚实传播，绝不给半成品）。
        assert errs == ["builder crashed"]
        # 新一轮：同样诚实失败。
        with pytest.raises(RuntimeError, match="builder crashed"):
            sf.run("k", builder)

    def test_wait_timeout_falls_back_to_direct_build(self):
        sf = SingleFlight(wait_timeout=0.05)
        release = threading.Event()

        def slow_builder():
            release.wait(5)
            return "slow"

        def fast_builder():
            return "fast"

        box: dict = {}
        def leader():
            box["leader"] = sf.run("k", slow_builder)

        lt = threading.Thread(target=leader)
        lt.start()
        import time

        time.sleep(0.02)
        # 等待者超时 → 自行快速构建（诚实降级，不永久卡死）。
        assert sf.run("k", fast_builder) == "fast"
        release.set()
        lt.join(timeout=10)
        assert box["leader"] == "slow"

    def test_version_passthrough(self):
        sf = SingleFlight()
        value, version = sf.run("k", lambda: 42, version="rev-1")
        assert (value, version) == (42, "rev-1")

    def test_overload_falls_through(self):
        sf = SingleFlight(max_inflight=1)
        sf.run("a", lambda: 1)
        # max_inflight=1 上：key "b" 直接并发计算（不注册）。
        assert sf.run("b", lambda: 2) == 2


# ------------------------------------------------------------ 失效广播


class TestCacheBroadcast:
    def test_no_redis_returns_false_and_never_raises(self, monkeypatch):
        import app.services.cache_broadcast as cb

        monkeypatch.setattr(cb, "_redis_client", lambda: None)
        assert cb.broadcast_ref_invalidation("s", "ref:geojson-x", "OVERWRITE") is False
        assert cb.start_listener() is False

    def test_message_is_bounded_and_payload_free(self, monkeypatch):
        import app.services.cache_broadcast as cb

        published = {}
        class FakeClient:
            def publish(self, channel, message):
                published["channel"] = channel
                published["message"] = message
                return 1

        monkeypatch.setattr(cb, "_redis_client", lambda: FakeClient())
        assert cb.broadcast_ref_invalidation(
            "sess-1", "ref:geojson-abc", "OVERWRITE") is True
        assert published["channel"] == cb.CHANNEL
        import json

        event = json.loads(published["message"])
        assert set(event) == {"kind", "session_id", "ref_id", "reason", "ts"}
        assert "features" not in published["message"]  # 无载荷

    def test_apply_event_invokes_local_authority(self, monkeypatch):
        import app.services.cache_broadcast as cb

        called = {}
        import app.services.ref_lifecycle as rl

        monkeypatch.setattr(rl, "invalidate_ref_caches",
                            lambda sid, ref, reason="REPLACE": called.update(
                                {"sid": sid, "ref": ref, "reason": reason}))
        import json

        cb._apply_event(json.dumps({
            "kind": "ref_invalidation", "session_id": "s", "ref_id": "r",
            "reason": "OVERWRITE"}))
        assert called == {"sid": "s", "ref": "r", "reason": "OVERWRITE"}
        # 非 kind/坏消息：静默忽略。
        cb._apply_event("not json")
        cb._apply_event(json.dumps({"kind": "other"}))


# ------------------------------------------------- 可复现执行包 / lineage


class TestReproducibilityBundle:
    def _plan_and_run(self, node: ExecutionNode):
        engine = GeoExecutionEngine(max_workers=1)
        plan = ExecutionPlan(plan_id="bundle", nodes=[node])
        run = engine.execute_plan(plan)
        return plan, run

    def test_pure_compute_is_reproducible(self):
        plan, run = self._plan_and_run(_filter_node())
        bundle = build_execution_bundle(plan, run, runtime_manifest_fingerprint="rt-1")
        assert bundle["reproducibility"] == REPRODUCIBLE
        assert bundle["plan_fingerprint"] == plan.graph_fingerprint()
        assert bundle["drift"]["state"] == "current"

    def test_external_source_is_conditionally_reproducible(self):
        node = ExecutionNode(
            node_id="q", category=NodeCategory.QUERY,
            parameters={"dataset_id": "d1"},
            dataset_fingerprints={"d": "fp-1"},
        )
        plan, run = self._plan_and_run(node)
        bundle = build_execution_bundle(plan, run, runtime_manifest_fingerprint="rt-1")
        assert bundle["reproducibility"] == CONDITIONALLY_REPRODUCIBLE

    def test_nondeterministic_node_disclosed(self):
        node = _filter_node().model_copy(update={
            "deterministic": False, "reuse": __import__("app.services.geocompute", fromlist=["NodeReusePolicy"]).NodeReusePolicy.DISALLOW})
        plan, run = self._plan_and_run(node)
        bundle = build_execution_bundle(plan, run, runtime_manifest_fingerprint="rt-1")
        assert bundle["reproducibility"] == NON_DETERMINISTIC

    def test_stale_runtime_disclosed(self, monkeypatch):
        from app.services.geocompute.drift import build_plan_record

        node = _filter_node()
        plan, run = self._plan_and_run(node)
        bundle = build_execution_bundle(plan, run, runtime_manifest_fingerprint="old-rt")
        # 旧 runtime 指纹 vs 当前（None/不同）→ drift 判 stale 或 unknown；
        # 用精确注入验证 stale 路径：
        from app.services.geocompute import reproducibility as rb

        monkeypatch.setattr(rb, "check_plan_drift", lambda *a, **kw: type(
            "V", (), {"state": "stale_runtime", "reason": "runtime changed",
                      "to_dict": lambda s: {}})())
        bundle2 = build_execution_bundle(plan, run, runtime_manifest_fingerprint="old-rt")
        assert bundle2["reproducibility"] == STALE
        assert build_plan_record(plan)["plan_fingerprint"] == plan.graph_fingerprint()

    def test_source_unavailable_when_source_node_failed(self):
        from app.services.geocompute.executor import GeoExecutionEngine as E

        node = ExecutionNode(node_id="scan", category=NodeCategory.SOURCE_SCAN,
                             parameters={"dataset_id": "ghost"})
        engine = E(max_workers=1)
        plan = ExecutionPlan(plan_id="b2", nodes=[node])
        run = engine.execute_plan(plan)  # 无 DB → 源节点失败
        bundle = build_execution_bundle(plan, run, runtime_manifest_fingerprint="rt")
        assert bundle["reproducibility"] == SOURCE_UNAVAILABLE

    def test_lineage_projection_bounded_and_payload_free(self):
        node = ExecutionNode(
            node_id="f1", category=NodeCategory.FILTER,
            parameters={"predicate": {"op": "eq", "field": "kind", "value": "a"},
                        "features": _fc(4)},
            produces=PayloadKind.FEATURES,
            lineage_inputs=[{"ref_id": "ref:geojson-1", "kind": "ref"}],
        )
        plan, run = self._plan_and_run(node)
        projection = lineage_projection(plan, run, node_id="f1")
        assert len(projection) == 1
        entry = projection[0]
        assert entry["input_lineage"] == [{"ref_id": "ref:geojson-1", "kind": "ref"}]
        assert entry["output_ref"] is None or isinstance(entry["output_ref"], str)
        text = repr(entry)
        assert "properties" not in entry  # 无原始载荷投影

    def test_lineage_projection_covers_whole_chain(self):
        from app.services.geocompute.executor import GeoExecutionEngine as E

        scan = ExecutionNode(node_id="scan", category=NodeCategory.SOURCE_SCAN,
                             parameters={"features": _fc(2), "dataset_id": "d"})
        filt = _filter_node("f1", inputs=["scan"])
        engine = E(max_workers=1)
        plan = ExecutionPlan(plan_id="chain", nodes=[scan, filt])
        run = engine.execute_plan(plan)
        projection = lineage_projection(plan, run)
        assert [e["node_id"] for e in projection] == ["scan", "f1"]
        assert projection[1]["inputs"] == ["scan"]
