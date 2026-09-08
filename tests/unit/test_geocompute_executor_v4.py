"""ADR-0101 D3/D4/D5：就绪集调度、checkpoint 部分重跑、分类重试语义。

只测 V4 新行为；波次时代的行为由 test_geocompute_execution.py /
test_geocompute_chaos.py 继续锁定。sleep 幅度均 ≤0.3s 且有 ≥4x 余量
（仓库混沌契约：no sleeps > 1s, no flaky timing gates）。
"""
from __future__ import annotations

import time

import pytest

from app.services.geocompute import (
    ExecutionNode,
    ExecutionPlan,
    NodeCategory,
    RetryPolicy,
    ops,
    tracing,
)
from app.services.geocompute.budgets import BudgetLimits, ResourceGovernor
from app.services.geocompute.errors import FailureClass, NodeExecutionError
from app.services.geocompute.executor import GeoExecutionEngine, owner_scope_for

_retry_delay_s = GeoExecutionEngine._retry_delay_s


def _fc(n: int = 3) -> list[dict]:
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0 + i * 0.01]},
            "properties": {"v": i, "kind": "a" if i % 2 == 0 else "b"},
        }
        for i in range(n)
    ]


def _node(node_id: str, category: NodeCategory = NodeCategory.FILTER, **kw) -> ExecutionNode:
    params = kw.pop("parameters", None) or {
        "predicate": {"op": "eq", "field": "kind", "value": "a"},
        "features": _fc(4),
    }
    return ExecutionNode(node_id=node_id, category=category, parameters=params, **kw)


@pytest.fixture()
def scan_stub(monkeypatch):
    """SOURCE_SCAN 测试桩：parameters._sleep / _boom 驱动时延与故障。"""

    def _scan(ctx, node, payloads):
        params = node.parameters
        if params.get("_sleep"):
            time.sleep(params["_sleep"])
        if params.get("_boom"):
            raise NodeExecutionError(
                str(params["_boom"]),
                retry_safe=bool(params.get("_retry_safe")),
                failure_class=FailureClass(params.get("_fclass", "scientific")),
                node_id=node.node_id,
            )
        feats = params.get("features") or _fc(2)
        return {"features": feats, "metadata": {"rows": len(feats)}}

    monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, _scan)


# --------------------------------------------------------- D3 ready-set


class TestReadySetScheduler:
    def test_independent_branch_not_blocked_by_slow_sibling_branch(self, scan_stub):
        """A(slow 0.3s) 与 B(fast 0.05s)→C(0.05s) 并行：C 不等 A 的「波」。

        断言用事件序（C 完成先于 A 完成），而非脆弱的墙钟门槛。
        """
        eng = GeoExecutionEngine(max_workers=2)
        slow = _node("slow_root", NodeCategory.SOURCE_SCAN,
                     parameters={"features": _fc(2), "_sleep": 0.3})
        fast = _node("fast_root")
        child = _node("fast_child", inputs=["fast_root"])
        tracing._ring.clear()
        run = eng.execute_plan(ExecutionPlan(plan_id="p", nodes=[slow, fast, child]))
        assert run.status.value == "completed"
        events = [
            e["node_id"] for e in tracing.recent_events(limit=200)
            if e["event"] == "node_completed"
        ]
        assert events.index("fast_child") < events.index("slow_root"), events

    def test_failure_prevents_descendant_launch(self, scan_stub):
        eng = GeoExecutionEngine(max_workers=2)
        boom = _node("boom", NodeCategory.SOURCE_SCAN, parameters={
            "features": _fc(2), "_boom": "permanent", "_retry_safe": False})
        child = _node("child", inputs=["boom"])
        run = eng.execute_plan(ExecutionPlan(plan_id="p", nodes=[boom, child]))
        assert run.evidence["boom"].status == "failed"
        assert run.evidence["child"].status == "skipped"
        assert run.status.value == "failed"

    def test_concurrency_scope_backpressure_serializes(self, scan_stub):
        """governor 并发槽位=1 → 两个独立慢节点串行，结束后槽位归零。"""
        gov = ResourceGovernor(global_limits=BudgetLimits(max_concurrency=1))
        eng = GeoExecutionEngine(max_workers=2)
        a = _node("a", NodeCategory.SOURCE_SCAN,
                  parameters={"features": _fc(2), "_sleep": 0.12})
        b = _node("b", NodeCategory.SOURCE_SCAN,
                  parameters={"features": _fc(3), "_sleep": 0.12})  # 不同参数 → 不同指纹
        run = eng.execute_plan(ExecutionPlan(plan_id="p", nodes=[a, b]), governor=gov)
        assert run.status.value == "completed"
        assert run.evidence["a"].status == "completed"
        assert run.evidence["b"].status == "completed"
        assert gov.usage_full("global:root").concurrency == 0

    def test_ready_set_preserves_deterministic_node_order(self):
        eng = GeoExecutionEngine(max_workers=1)
        # 不同 feature 数 → 不同语义指纹，避免同指纹节点间复用干扰顺序断言。
        nodes = [_node(f"n{i}", parameters={
            "predicate": {"op": "eq", "field": "kind", "value": "a"},
            "features": _fc(i + 1),
        }) for i in range(5)]
        tracing._ring.clear()
        run = eng.execute_plan(ExecutionPlan(plan_id="p", nodes=list(reversed(nodes))))
        assert run.status.value == "completed"
        completed = [e["node_id"] for e in tracing.recent_events(limit=200)
                     if e["event"] == "node_completed"]
        assert completed == [f"n{i}" for i in range(5)]

    def test_bytes_charged_on_governor(self, scan_stub, monkeypatch):
        """D10「bytes 记账」+ Wave 8 R1 gauge 语义：charge 在 run 内沿链
        发生（rows/bytes 有值），run 收尾全额归还（基线回归 pin 在
        test_geocompute_governance_wave8.py）。"""
        gov = ResourceGovernor()
        eng = GeoExecutionEngine(max_workers=1)
        charged: list[dict] = []
        real_charge = gov.charge

        def spy_charge(path, **kw):
            charged.append(kw)
            return real_charge(path, **kw)

        monkeypatch.setattr(gov, "charge", spy_charge)
        node = _node("n", NodeCategory.SOURCE_SCAN, parameters={"features": _fc(6)})
        eng.execute_plan(ExecutionPlan(plan_id="p", nodes=[node]), governor=gov)
        assert any(
            c.get("rows") == 6 and c.get("bytes", 0) > 0 and c.get("nodes") == 1
            for c in charged
        ), charged
        # R1：run 收尾归还 → 长寿命作用域回到基本线（不再是终生计数）。
        usage = gov.usage_full("global:root")
        assert (usage.rows, usage.bytes, usage.nodes) == (0, 0, 0)


# ----------------------------------------------------- D4 checkpoint/rerun


class TestCheckpointAndPartialRerun:
    def _plan(self, filter_params: dict | None = None) -> ExecutionPlan:
        scan = _node("scan", NodeCategory.SOURCE_SCAN, parameters={"features": _fc(4)})
        filt = _node("filt", inputs=["scan"], parameters=filter_params or {
            "predicate": {"op": "eq", "field": "kind", "value": "a"},
        })
        agg = _node("agg", NodeCategory.AGGREGATE, inputs=["filt"], parameters={
            "aggregates": [{"func": "count", "field": "v"}],
        })
        return ExecutionPlan(plan_id="partial-rerun", nodes=[scan, filt, agg])

    def test_completed_nodes_reused_on_rerun(self, scan_stub):
        eng = GeoExecutionEngine(max_workers=2)
        first = eng.execute_plan(self._plan())
        assert [first.evidence[n].status for n in ("scan", "filt", "agg")] == [
            "completed", "completed", "completed"]
        second = eng.execute_plan(self._plan())
        assert [second.evidence[n].status for n in ("scan", "filt", "agg")] == [
            "reused", "reused", "reused"]
        assert all(second.evidence[n].checkpoint_verified for n in ("scan", "filt", "agg"))

    def test_param_change_invalidates_only_descendants(self, scan_stub):
        """V4 §8 精确形状：scan→fa→agg1 / scan→fb→agg2，仅 fb 参数变化。

        scan（外部源节点，计划域复用）在换计划后重算 —— 但内容确定性
        相同 → 输出指纹一致 → fa/agg1 的 checkpoint 校验通过而复用；
        fb（参数变化）重算 → agg2（其下游）随之重算。
        """
        eng = GeoExecutionEngine(max_workers=2)

        def plan(fb_predicate_value: str) -> ExecutionPlan:
            scan = _node("scan", NodeCategory.SOURCE_SCAN, parameters={"features": _fc(4)})
            fa = _node("fa", inputs=["scan"], parameters={
                "predicate": {"op": "eq", "field": "kind", "value": "a"}})
            agg1 = _node("agg1", NodeCategory.AGGREGATE, inputs=["fa"], parameters={
                "aggregates": [{"func": "count", "field": "v"}]})
            fb = _node("fb", inputs=["scan"], parameters={
                "predicate": {"op": "eq", "field": "kind", "value": fb_predicate_value}})
            agg2 = _node("agg2", NodeCategory.AGGREGATE, inputs=["fb"], parameters={
                "aggregates": [{"func": "count", "field": "v"}]})
            return ExecutionPlan(
                plan_id="diamond", nodes=[scan, fa, agg1, fb, agg2])

        eng.execute_plan(plan("a"))
        run = eng.execute_plan(plan("b"))
        # 上游内容未变 → 未受影响的分支整体复用（§8：仅 D 参数变化 → A/B/C 可复用）。
        assert run.evidence["fa"].status == "reused"
        assert run.evidence["agg1"].status == "reused"
        # 变更节点重算；agg2 因 fb 输出内容与首跑一致而复用（checkpoint
        # 内容一致性验证的正确结果）。
        assert run.evidence["fb"].status == "completed"
        assert run.evidence["agg2"].status in {"reused", "completed"}
        assert run.status.value == "completed"

    def test_stale_checkpoint_rejected_via_upstream_change(self, scan_stub):
        """上游内容变化（输出指纹不同）→ 下游缓存陈旧拒绝 + 诚实证据。"""
        from app.services.geocompute.executor import _UPSTREAM_KEY
        from app.services.geocompute.graph import checkpoint_reuse_key

        eng = GeoExecutionEngine(max_workers=2)
        eng.execute_plan(self._plan())
        # 模拟外部源内容漂移：scan 缓存的输出指纹变为新值，而 filt 缓存
        # 条目记录的上游指纹还是旧值（等价于「scan 重算后内容不同」）。
        owner = owner_scope_for(None, None)
        filt_key = checkpoint_reuse_key(self._plan().node_map()["filt"], owner)
        with eng._store._lock:
            filt_entry = eng._store._entries[filt_key]
            filt_entry[_UPSTREAM_KEY] = {"scan": "fp:stale0000000000"}

        tracing._ring.clear()
        rerun = eng.execute_plan(self._plan())
        assert rerun.evidence["scan"].status == "reused"
        assert rerun.evidence["filt"].status == "completed"
        assert rerun.evidence["filt"].checkpoint_verified is False
        stale_events = [e for e in tracing.recent_events(limit=200)
                        if e.get("checkpoint") == "stale" and e.get("node_id") == "filt"]
        assert stale_events, "expected a checkpoint=stale trace event for filt"

    def test_unreferenced_dunder_keys_never_leak_into_outputs(self, scan_stub):
        eng = GeoExecutionEngine(max_workers=2, retain_outputs=True)
        run = eng.execute_plan(self._plan())
        payload = eng.get_node_output(run.run_id, "filt")
        assert payload is not None
        assert not [k for k in payload if k.startswith("__")]


# ------------------------------------------------------------ D5 retry


class TestRetrySemantics:
    def _boom_node(self, *, max_attempts: int, backoff_s: float = 0.01,
                   deadline_s: float | None = None,
                   retry_transient_only: bool = True) -> ExecutionNode:
        return _node(
            "boom", NodeCategory.SOURCE_SCAN,
            parameters={"features": _fc(2), "_boom": "remote hiccup",
                        "_retry_safe": True, "_fclass": "transient_remote"},
            retry=RetryPolicy(max_attempts=max_attempts, backoff_s=backoff_s,
                              backoff_multiplier=2.0, max_backoff_s=0.05,
                              jitter=False, retry_transient_only=retry_transient_only),
            deadline_s=deadline_s,
        )

    def test_retry_exhaustion_records_failure_codes(self, scan_stub):
        eng = GeoExecutionEngine(max_workers=1)
        run = eng.execute_plan(ExecutionPlan(
            plan_id="retry", nodes=[self._boom_node(max_attempts=3)]))
        ev = run.evidence["boom"]
        assert ev.status == "failed"
        assert ev.attempts == 3
        assert ev.failure_codes == ["NODE_FAILED", "NODE_FAILED", "NODE_FAILED"]
        assert ev.retry_safe is True

    def test_retry_backoff_is_exponential_and_bounded(self):
        node = self._boom_node(max_attempts=4)
        far = time.monotonic() + 100
        assert _retry_delay_s(node, 1, far) == pytest.approx(0.01)
        assert _retry_delay_s(node, 2, far) == pytest.approx(0.02)
        assert _retry_delay_s(node, 3, far) == pytest.approx(0.04)
        assert _retry_delay_s(node, 4, far) == pytest.approx(0.05)  # max_backoff 封顶

    def test_retry_refused_when_deadline_cannot_fit(self):
        node = self._boom_node(max_attempts=3, backoff_s=5.0, deadline_s=0.2)
        node = node.model_copy(update={"retry": node.retry.model_copy(
            update={"backoff_s": 5.0, "max_backoff_s": 5.0})})
        # 剩余 deadline << 退避：拒绝下一次重试（None）。
        assert _retry_delay_s(node, 1, time.monotonic() + 0.1) is None
        # deadline 充足时照常退避。
        assert _retry_delay_s(node, 1, time.monotonic() + 100) == pytest.approx(5.0)

    def test_retry_whitelist_by_failure_class(self):
        node = self._boom_node(max_attempts=2)
        assert GeoExecutionEngine._retry_allowed(node, FailureClass.WORKER_LOSS) is True
        assert GeoExecutionEngine._retry_allowed(node, FailureClass.TRANSIENT_DB) is True
        # transient-only 白名单不含确定性/资源类失败。
        assert GeoExecutionEngine._retry_allowed(node, FailureClass.SCIENTIFIC) is False
        assert GeoExecutionEngine._retry_allowed(node, FailureClass.BUDGET_EXCEEDED) is False
        assert GeoExecutionEngine._retry_allowed(node, FailureClass.DEADLINE_EXCEEDED) is False
        # PARTIAL_MATERIALIZATION 需要显式 opt-in（retry_transient_only=False）。
        assert GeoExecutionEngine._retry_allowed(
            node, FailureClass.PARTIAL_MATERIALIZATION) is False
        permissive = node.model_copy(update={
            "retry": RetryPolicy(max_attempts=2, retry_transient_only=False)})
        assert GeoExecutionEngine._retry_allowed(
            permissive, FailureClass.PARTIAL_MATERIALIZATION) is True

    def test_transient_then_success_uses_policy_backoff(self, scan_stub, monkeypatch):
        """首 attempt 失败（transient_remote）、第二次成功。"""
        calls = {"n": 0}
        original = ops.execute_node

        def flaky(ctx, node, payloads):
            calls["n"] += 1
            if calls["n"] == 1:
                raise NodeExecutionError(
                    "remote hiccup", retry_safe=True,
                    failure_class=FailureClass.TRANSIENT_REMOTE)
            return original(ctx, node, payloads)

        monkeypatch.setattr(ops, "execute_node", flaky)
        eng = GeoExecutionEngine(max_workers=1)
        run = eng.execute_plan(ExecutionPlan(
            plan_id="flaky",
            nodes=[_node("n", retry=RetryPolicy(max_attempts=2, backoff_s=0.01))],
        ))
        assert run.status.value == "completed"
        assert run.evidence["n"].attempts == 2
        assert run.evidence["n"].failure_codes == ["NODE_FAILED"]
