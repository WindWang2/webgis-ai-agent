"""Wave 8 — 跨进程资源治理（audit 07-resource-governance-gaps.md §6.2 steps 1/2/6）。

- **R1（Critical）**：rows/bytes/nodes 是**并发在飞量衡** —— run 收尾按
  「预留估计 + 实际记账」全额归还；成功/失败/取消三态基线回归、并发 run
  叠加可观察、重复归还钳零。
- **R2**：可选跨进程 advisory 计数器（fakeredis）：reserve INCRBY /
  release DECRBY、TTL 兜底 + stale-sweep、确信超限才拒绝（判定进
  BudgetExceededError.details）、Redis 故障 fail-open + 有界退避（永不
  锁存不可用）、env 关闭 = 零 Redis 交互。
- **R7**：ResourceClass → 槽位权重（重节点 2 单位）在祖先作用域上可观察
  （加权背压），trace 诚实披露单位数；轻节点行为不变；单重节点不自锁。
- **R9**：槽位租约看门狗 —— 忽略取消的非协作节点过 deadline+宽限后槽位
  被强制归还，后续 run 不再被饿死；settle 不二次释放；账簿随 run 清理。
- **ScopeKind.NODE**：枚举成员已删除（决策记录见 budgets.ScopeKind）。

时序契约（与 test_geocompute_chaos.py 一致）：事件同步 + 单调 deadline，
no sleeps > 1s，no flaky timing gates。
"""
from __future__ import annotations

import hashlib
import threading
import time

import fakeredis
import pytest

from app.lib.cancellation import CancellationToken
from app.services.geocompute import (
    BudgetExceededError,
    ExecutionNode,
    ExecutionPlan,
    GeoExecutionEngine,
    NodeCategory,
    ResourceBudget,
    ResourceClass,
    ResourceEstimate,
    ops,
    tracing,
)
from app.services.geocompute.api import run_plan_sync
from app.services.geocompute.budgets import BudgetLimits, ResourceGovernor, ScopeKind
from app.services.geocompute.executor import _RunChargeLedger
from app.services.geocompute.resource_counter import (
    CROSS_PROCESS_FLAG_ENV,
    CrossProcessCounter,
    shared_counter,
)


def _fc(n: int = 3) -> list[dict]:
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]},
            "properties": {"v": i, "kind": "a" if i % 2 == 0 else "b"},
        }
        for i in range(n)
    ]


def _scan_node(node_id: str, *, n: int = 3, rows_estimate: int | None = None,
               inputs: list[str] | None = None, **kw) -> ExecutionNode:
    params = kw.pop("parameters", None) or {"features": _fc(n)}
    est = ResourceEstimate(rows=rows_estimate) if rows_estimate is not None else None
    return ExecutionNode(
        node_id=node_id, category=NodeCategory.SOURCE_SCAN,
        inputs=inputs or [], estimate=est, parameters=params, **kw,
    )


def _filter_node(node_id: str, inputs: list[str] | None = None, n: int = 4) -> ExecutionNode:
    return ExecutionNode(
        node_id=node_id,
        category=NodeCategory.FILTER,
        inputs=inputs or [],
        parameters={
            "predicate": {"op": "eq", "field": "kind", "value": "a"},
            "features": _fc(n),
        },
    )


def _session_path(session_id: str) -> str:
    return "global:root/session:" + hashlib.sha1(
        session_id.encode(), usedforsecurity=False
    ).hexdigest()[:12]


def _counter(**kw) -> tuple[CrossProcessCounter, fakeredis.FakeRedis]:
    fake = fakeredis.FakeRedis()
    return CrossProcessCounter(client=fake, **kw), fake


def _assert_baseline(gov: ResourceGovernor, *paths: str) -> None:
    for path in paths:
        u = gov.usage_full(path)
        assert (u.rows, u.bytes, u.nodes, u.concurrency) == (0, 0, 0, 0), path


@pytest.fixture()
def scan_stub(monkeypatch):
    """SOURCE_SCAN 测试桩（parameters._sleep / _boom 驱动，同 v4 惯例）。"""

    def _scan(ctx, node, payloads):
        params = node.parameters
        if params.get("_sleep"):
            time.sleep(params["_sleep"])
        if params.get("_boom"):
            from app.services.geocompute.errors import FailureClass, NodeExecutionError

            raise NodeExecutionError(
                str(params["_boom"]),
                retry_safe=bool(params.get("_retry_safe")),
                failure_class=FailureClass(params.get("_fclass", "scientific")),
                node_id=node.node_id,
            )
        feats = params.get("features") or _fc(2)
        return {"features": feats, "metadata": {"rows": len(feats)}}

    monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, _scan)


# ═══════════════════════════════ R1：并发在飞量衡（基线回归）═════════════════


class TestR1BaselineReturn:
    def test_usage_returns_to_baseline_after_success(self, scan_stub):
        """成功 run：global/session 链上 rows/bytes/nodes/concurrency 全部
        回到基本线（预留估计 + 实际记账都被归还）。"""
        gov = ResourceGovernor()
        sid = "w8-ok-sess"
        plan = ExecutionPlan(
            plan_id="w8-ok",
            nodes=[
                _scan_node("a", n=4, rows_estimate=100),
                _scan_node("b", n=2, rows_estimate=50),
            ],
        )
        run = run_plan_sync(plan, session_id=sid, governor=gov)
        assert run.status.value == "completed"
        _assert_baseline(gov, "global:root", _session_path(sid))

    def test_usage_returns_to_baseline_after_failure(self, scan_stub):
        """失败 run：失败节点不记账、后代 skipped，预留仍全额归还。"""
        gov = ResourceGovernor()
        sid = "w8-fail-sess"
        boom = _scan_node(
            "boom",
            parameters={"features": _fc(2), "_boom": "permanent",
                        "_retry_safe": False},
            rows_estimate=80,
        )
        child = _scan_node("child", n=3, rows_estimate=40, inputs=["boom"])
        run = run_plan_sync(
            ExecutionPlan(plan_id="w8-fail", nodes=[boom, child]),
            session_id=sid, governor=gov,
        )
        assert run.status.value == "failed"
        assert run.evidence["boom"].status == "failed"
        assert run.evidence["child"].status == "skipped"
        _assert_baseline(gov, "global:root", _session_path(sid))

    def test_usage_returns_to_baseline_after_cancellation(self, monkeypatch):
        """取消 run：已完成节点的 charge + 未启动节点的收敛之后，预留与
        记账全额归还。"""
        gov = ResourceGovernor()
        sid = "w8-cancel-sess"
        token = CancellationToken()

        def dispatcher(ctx, node, payloads):
            if node.node_id == "a":
                token.cancel("w8 cancel")  # deterministic：wave 1 内触发
                return {"features": _fc(4), "metadata": {}}
            return {"features": _fc(1), "metadata": {}}

        monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, dispatcher)
        node_a = _scan_node("a", rows_estimate=30)
        node_b = _scan_node("b", inputs=["a"], rows_estimate=10)
        run = run_plan_sync(
            ExecutionPlan(plan_id="w8-cancel", nodes=[node_a, node_b]),
            session_id=sid, governor=gov, cancel_token=token,
        )
        assert run.status.value == "cancelled"
        assert run.evidence["a"].status == "completed"
        assert run.evidence["b"].status == "cancelled"
        _assert_baseline(gov, "global:root", _session_path(sid))

    def test_concurrent_runs_sum_while_inflight_then_baseline(self, monkeypatch):
        """两个重叠 run：在飞期间用量 = 两者之和（估计预留叠加）；都结束后
        回到基本线（事件同步，无时序门槛）。"""
        gov = ResourceGovernor()
        sid = "w8-concurrent-sess"
        started = {"r1": threading.Event(), "r2": threading.Event()}
        release = threading.Event()

        def gated(ctx, node, payloads):
            started[node.node_id].set()
            release.wait(5.0)
            return {"features": _fc(3), "metadata": {}}

        monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, gated)
        p1 = ExecutionPlan(
            plan_id="w8-c1", nodes=[_scan_node("r1", n=3, rows_estimate=100)])
        p2 = ExecutionPlan(
            plan_id="w8-c2", nodes=[_scan_node("r2", n=4, rows_estimate=60)])
        holder: dict[str, object] = {}

        def _run(key: str, plan: ExecutionPlan) -> None:
            holder[key] = run_plan_sync(plan, session_id=sid, governor=gov)

        t1 = threading.Thread(target=_run, args=("r1", p1), daemon=True)
        t2 = threading.Thread(target=_run, args=("r2", p2), daemon=True)
        t1.start()
        t2.start()
        try:
            assert started["r1"].wait(5.0) and started["r2"].wait(5.0)
            # 在飞：两个 run 的估计预留沿共享祖先链叠加（gauge，非终身计数）。
            peak_root = gov.usage_full("global:root")
            assert (peak_root.rows, peak_root.nodes, peak_root.concurrency) == (
                160, 2, 2)
            peak_sess = gov.usage_full(_session_path(sid))
            assert (peak_sess.rows, peak_sess.nodes, peak_sess.concurrency) == (
                160, 2, 2)
        finally:
            release.set()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not t1.is_alive() and not t2.is_alive()
        _assert_baseline(gov, "global:root", _session_path(sid))
        assert holder["r1"].status.value == "completed"
        assert holder["r2"].status.value == "completed"

    def test_double_release_clamps_at_zero(self):
        """重复归还钳零：不得把用量记负、变相抬高其他作用域的可用容量。"""
        gov = ResourceGovernor(global_limits=BudgetLimits(max_rows=100))
        gov.reserve("global:root", rows=50)
        gov.release("global:root", rows=50)
        gov.release("global:root", rows=50)  # duplicate
        assert gov.usage("global:root") == (0, 0, 0)
        gov.reserve("global:root", rows=100)  # 满额仍可预留（没有被虚增）
        with pytest.raises(BudgetExceededError):
            gov.reserve("global:root", rows=1)

    def test_charge_ledger_thread_safe(self):
        """R1 账簿：并发 add 确定性求和（节点线程并发记账竞免）。"""
        ledger = _RunChargeLedger()

        def worker() -> None:
            for _ in range(500):
                ledger.add(3, 7, 1)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert ledger.snapshot() == (8 * 500 * 3, 8 * 500 * 7, 8 * 500)


# ═══════════════════════ R2：跨进程 advisory 计数器（默认关闭）══════════════


class TestCrossProcessCounter:
    def test_reserve_increments_release_decrements_with_ttl(self):
        counter, fake = _counter()
        chain = "global:root/session:s1"
        decision = counter.reserve(
            chain, rows=30, nodes=1, limits={"rows": 1000, "bytes": None, "nodes": None},
        )
        assert decision.allowed
        key = counter.key_for(chain, "rows")
        assert int(fake.get(key)) == 30
        assert int(fake.get(counter.key_for(chain, "nodes"))) == 1
        assert 0 < fake.ttl(key) <= 3600, "entries must carry a TTL (crash bound)"
        counter.release(chain, rows=30, nodes=1)
        assert int(fake.get(key) or 0) == 0
        assert int(fake.get(counter.key_for(chain, "nodes")) or 0) == 0

    def test_increase_mirrors_charge(self):
        counter, fake = _counter()
        counter.increase("global:root", rows=5, bytes_=123)
        assert int(fake.get(counter.key_for("global:root", "rows"))) == 5
        assert int(fake.get(counter.key_for("global:root", "bytes"))) == 123

    def test_advisory_deny_only_when_confidently_over_limit(self):
        counter, fake = _counter()
        chain = "global:root"
        rows_key = counter.key_for(chain, "rows")
        fake.incrby(rows_key, 80)  # 另一 pod 的在飞占用（带 TTL，符合协议）
        fake.expire(rows_key, 3600)
        within = counter.reserve(chain, rows=19, limits={"rows": 100})
        assert within.allowed  # 80+19 ≤ 100 → 放行（保留自己的增量）
        decision = counter.reserve(chain, rows=30, limits={"rows": 100})
        assert not decision.allowed
        assert decision.details["counter"]["rows"] == 129
        assert decision.details["over"] == ["rows 129 > 100"]
        # 拒绝时自我回滚：计数器回到「放行增量 + 另一 pod 占用」。
        assert int(fake.get(rows_key)) == 99

    def test_no_limit_dimension_never_denies(self):
        counter, _ = _counter()
        decision = counter.reserve("global:root", rows=10**12, limits={"rows": None})
        assert decision.allowed

    def test_executor_budget_error_exposes_cross_process_decision(self):
        """拒绝经 governor.reserve 透出：BudgetExceededError.details 携带
        advisory 判定依据；execution 作用域不泄漏（teardown 兜底）。"""
        counter, fake = _counter()
        gov = ResourceGovernor(
            global_limits=BudgetLimits(max_rows=100), cross_process=counter,
        )
        rows_key = counter.key_for("global:root", "rows")
        fake.incrby(rows_key, 80)  # 另一 pod 的在飞占用（带 TTL，符合协议）
        fake.expire(rows_key, 3600)
        plan = ExecutionPlan(
            plan_id="w8-xp-deny",
            nodes=[_scan_node("n", n=2, rows_estimate=30)],
        )
        with pytest.raises(BudgetExceededError) as ei:
            GeoExecutionEngine(max_workers=1).execute_plan(plan, governor=gov)
        xp = ei.value.details["cross_process"]
        assert xp["counter"]["rows"] == 110
        assert ei.value.details["scope"] == "global:root"
        assert int(fake.get(rows_key)) == 80
        assert gov._find("global:root").children == [], "execution scope torn down"

    def test_counter_nets_to_zero_after_full_run(self, scan_stub):
        """整 run 净和为零：reserve INCRBY（估计）+ charge INCRBY（实际）
        与收尾 release DECRBY 严格配对（crash 残留由 TTL 兜底）。"""
        counter, fake = _counter()
        gov = ResourceGovernor(
            global_limits=BudgetLimits(max_rows=1000), cross_process=counter,
        )
        sid = "w8-netzero-sess"
        run = run_plan_sync(
            ExecutionPlan(
                plan_id="w8-netzero",
                nodes=[_scan_node("a", n=3, rows_estimate=40)],
            ),
            session_id=sid, governor=gov,
        )
        assert run.status.value == "completed"
        chain = _session_path(sid)  # 稳定链键：EXECUTION 段被剥掉
        for dim in ("rows", "bytes", "nodes"):
            key = counter.key_for(chain, dim)
            assert int(fake.get(key) or 0) == 0, dim

    def test_stale_sweep_discards_ttlless_crash_residue(self):
        counter, fake = _counter()
        chain = "global:root/session:s"
        assert counter.reserve(chain, rows=5).allowed
        key = counter.key_for(chain, "rows")
        assert 0 < fake.ttl(key) <= 3600
        fake.persist(key)  # 模拟 INCRBY 后 EXPIRE 前崩溃的无 TTL 残留
        assert fake.ttl(key) == -1
        counter.increase(chain, rows=3)
        # 旧读数不可信 → 删除重建（宁可少信不多信）。
        assert int(fake.get(key)) == 3
        assert fake.ttl(key) > 0

    def test_release_clamps_negative_counts(self):
        counter, fake = _counter()
        chain = "global:root"
        fake.incrby(counter.key_for(chain, "rows"), 4)
        counter.release(chain, rows=10)  # TTL 过期后的迟到 release
        assert int(fake.get(counter.key_for(chain, "rows")) or 0) == 0

    def test_outage_fails_open_with_bounded_backoff(self):
        """Redis 故障 → 放行 + 有界退避（退避期内零命令）；到期重新探测
        （永不锁存「不可用」—— distributed_lock 60s 重验同纪律）。"""
        calls = {"n": 0}

        class Exploding:
            def incrby(self, *a, **kw):
                calls["n"] += 1
                raise ConnectionError("redis down")

            def ttl(self, *a, **kw):
                calls["n"] += 1
                raise ConnectionError("redis down")

        counter = CrossProcessCounter(client=Exploding(), backoff_s=0.25)
        for _ in range(5):
            assert counter.reserve("global:root", rows=10, limits={"rows": 1}).allowed
        assert calls["n"] == 1, "backoff window must suppress further commands"
        time.sleep(0.3)
        assert counter.reserve("global:root", rows=10, limits={"rows": 1}).allowed
        assert calls["n"] == 2, "counter must re-probe after backoff expires"

    def test_default_off_zero_redis_interaction(self, monkeypatch):
        """默认（无 flag）= 零 Redis 交互：不建客户端、不发命令、不检查
        限额 —— L1 语义与纯进程内完全一致。"""
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.delenv(CROSS_PROCESS_FLAG_ENV, raising=False)
        counter = CrossProcessCounter()
        assert counter.enabled is False
        assert counter.reserve("global:root", rows=10, limits={"rows": 1}).allowed
        counter.increase("global:root", rows=5)
        counter.release("global:root", rows=5)
        assert counter._client is None, "no Redis client may be constructed"
        # 装配进 governor（api.GOVERNOR 同款）也不产生任何 Redis 交互。
        shared = shared_counter()
        assert shared.enabled is False
        gov = ResourceGovernor(
            global_limits=BudgetLimits(max_rows=10), cross_process=shared,
        )
        gov.reserve("global:root", rows=5)
        gov.charge("global:root", rows=2)
        gov.release("global:root", rows=7)
        assert gov.usage("global:root") == (0, 0, 0)
        assert shared._client is None

    def test_flag_on_requires_redis_url(self, monkeypatch):
        from app.core import config as config_mod

        monkeypatch.setenv(CROSS_PROCESS_FLAG_ENV, "1")
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setattr(config_mod.settings, "REDIS_URL", "", raising=False)
        assert CrossProcessCounter().enabled is False
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/9")
        assert CrossProcessCounter().enabled is True
        # flag 关闭时即便有 URL 也不启用（默认关）。
        monkeypatch.delenv(CROSS_PROCESS_FLAG_ENV, raising=False)
        assert CrossProcessCounter().enabled is False


# ═══════════════════ R7：ResourceClass → 槽位权重（祖先作用域可观察）═════════


class TestResourceClassWiring:
    def test_slot_units_mapping_is_deterministic(self):
        units = GeoExecutionEngine.slot_units_for
        heavy_mem = _scan_node("h", resource_class=ResourceClass(memory=4))
        heavy_cpu = _scan_node("c", resource_class=ResourceClass(cpu=5))
        heavy_max = _scan_node("m", resource_class=ResourceClass(memory=5, cpu=5))
        light = _scan_node("l")
        boundary = _scan_node("b", resource_class=ResourceClass(memory=3, cpu=4))
        assert units(heavy_mem) == 2
        assert units(heavy_cpu) == 2
        assert units(heavy_max) == 2
        assert units(light) == 1
        assert units(boundary) == 1

    def test_heavy_node_consumes_two_ancestor_slots(self, monkeypatch):
        """重节点占 2 个祖先槽位单位：并发预算 3 时第二个重节点被加权准入
        拒绝（背压），直到第一个落定 —— ResourceClass 可观察地参与调度。"""
        gov = ResourceGovernor(global_limits=BudgetLimits(max_concurrency=3))
        started = {"a": threading.Event(), "b": threading.Event()}
        release_a = threading.Event()

        def gated(ctx, node, payloads):
            started[node.node_id].set()
            if node.node_id == "a":
                release_a.wait(5.0)
            return {"features": _fc(2), "metadata": {}}

        monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, gated)
        heavy_a = _scan_node("a", rows_estimate=10,
                             resource_class=ResourceClass(memory=4))
        heavy_b = _scan_node("b", rows_estimate=10,
                             resource_class=ResourceClass(memory=4))
        eng = GeoExecutionEngine(max_workers=2)
        holder: dict[str, object] = {}

        def _execute() -> None:
            holder["run"] = eng.execute_plan(
                ExecutionPlan(plan_id="w8-heavy", nodes=[heavy_a, heavy_b]),
                governor=gov,
            )

        worker = threading.Thread(target=_execute, daemon=True)
        worker.start()
        try:
            assert started["a"].wait(5.0)
            assert gov.usage_full("global:root").concurrency == 2, (
                "heavy node must hold 2 slot units")
            assert not started["b"].is_set(), (
                "second heavy node must be backpressured (2+2 > 3)")
        finally:
            release_a.set()
        worker.join(timeout=10)
        assert not worker.is_alive()
        run = holder["run"]
        assert run.status.value == "completed"
        # a/b 同指纹（同参数）：b 落定可能是 completed 或复用 a 的结果 ——
        # 本测试只关心加权准入时序。
        assert run.evidence["b"].status in {"completed", "reused"}
        assert gov.usage_full("global:root").concurrency == 0
        # trace 诚实披露单位数。
        admitted = {
            e["node_id"]: e.get("concurrency")
            for e in tracing.recent_events(limit=400)
            if e["event"] == "node_admitted" and e["run_id"] == run.run_id
        }
        assert admitted == {"a": 2, "b": 2}

    def test_light_nodes_keep_single_slot_semantics(self, monkeypatch):
        """轻节点照旧占 1 个槽位：R7 加权不改变既有调度语义。"""
        gov = ResourceGovernor(global_limits=BudgetLimits(max_concurrency=2))
        started = {"a": threading.Event(), "b": threading.Event()}
        release = threading.Event()

        def gated(ctx, node, payloads):
            started[node.node_id].set()
            release.wait(5.0)
            return {"features": _fc(2), "metadata": {}}

        monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, gated)
        eng = GeoExecutionEngine(max_workers=2)
        holder: dict[str, object] = {}

        def _execute() -> None:
            holder["run"] = eng.execute_plan(
                ExecutionPlan(plan_id="w8-light",
                              nodes=[_scan_node("a", rows_estimate=5),
                                     _scan_node("b", n=4, rows_estimate=5)]),
                governor=gov,
            )

        worker = threading.Thread(target=_execute, daemon=True)
        worker.start()
        try:
            assert started["a"].wait(5.0) and started["b"].wait(5.0)
            assert gov.usage_full("global:root").concurrency == 2
        finally:
            release.set()
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert holder["run"].status.value == "completed"
        assert gov.usage_full("global:root").concurrency == 0
        admitted = {
            e["node_id"]: e.get("concurrency")
            for e in tracing.recent_events(limit=400)
            if e["event"] == "node_admitted" and e["run_id"] == holder["run"].run_id
        }
        assert admitted == {"a": 1, "b": 1}

    def test_single_heavy_node_never_self_deadlocks(self, scan_stub):
        """EXECUTION 上界以单位计（2×max_workers）：max_workers=1 的单重
        节点（2 单位 ≤ 2）绝不因自身权重被永久拒绝。"""
        gov = ResourceGovernor()
        eng = GeoExecutionEngine(max_workers=1)
        run = eng.execute_plan(
            ExecutionPlan(
                plan_id="w8-selflock",
                nodes=[_scan_node("h", rows_estimate=5,
                                  resource_class=ResourceClass(memory=4))],
            ),
            governor=gov,
        )
        assert run.status.value == "completed"
        assert gov.usage_full("global:root").concurrency == 0


# ═══════════════════ R9：槽位租约看门狗（非协作节点不再永久占槽）═════════════


class TestSlotLeaseWatchdog:
    def test_hung_node_slot_reclaimed_and_later_run_not_starved(self, monkeypatch):
        """忽略取消的僵尸节点（线程不可强杀）过 deadline+宽限后：并发槽位
        被强制归还 → 后续 run 不被饿死；僵尸落定时 settle 跳过二次释放；
        run 收尾后全链回基线（账簿清理 + 钳零兜底）。"""
        gov = ResourceGovernor()
        sid = "w8-lease-sess"
        session_path = gov.ensure_scope(
            "global:root", ScopeKind.SESSION,
            hashlib.sha1(sid.encode(), usedforsecurity=False).hexdigest()[:12],
            limits=BudgetLimits(max_concurrency=1),  # 唯一槽位 → 饿死可见
        )
        started = threading.Event()
        stop = threading.Event()
        seen: dict[str, str] = {}

        def zombie(ctx, node, payloads):
            seen["run_id"] = ctx.run_id
            started.set()
            for _ in range(150):  # ≤3s 上界；测试在 reclaim 后提前打断
                if stop.is_set():
                    break
                time.sleep(0.02)  # 故意不调用 checkpoint：非协作节点
            return {"features": _fc(2), "metadata": {}}

        monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, zombie)
        zombie_node = ExecutionNode(
            node_id="z", category=NodeCategory.SOURCE_SCAN,
            estimate=ResourceEstimate(rows=10), parameters={"features": _fc(2)},
        )
        child = _filter_node("c", inputs=["z"])
        eng_a = GeoExecutionEngine(max_workers=1, slot_lease_grace_s=0.05)
        releases: list[int] = []
        real_release = gov.release

        def spy_release(path, **kw):
            if kw.get("concurrency"):
                releases.append(int(kw["concurrency"]))
            return real_release(path, **kw)

        gov.release = spy_release  # noqa: B010 - 观测点（测试内 monkey 不便）
        holder: dict[str, object] = {}

        def _run_a() -> None:
            holder["run"] = eng_a.execute_plan(
                ExecutionPlan(
                    plan_id="w8-lease-a",
                    nodes=[zombie_node, child],
                    budget=ResourceBudget(max_rows=1000, deadline_s=0.3),
                ),
                session_id=sid, governor=gov, governor_parent_path=session_path,
            )

        thread_a = threading.Thread(target=_run_a, daemon=True)
        thread_a.start()
        try:
            assert started.wait(5.0), "zombie node never started"
            assert eng_a.cancel_run(seen["run_id"], "w8 cancel")
            deadline = time.monotonic() + 3.0
            reclaimed: list[dict] = []
            while time.monotonic() < deadline and not reclaimed:
                reclaimed = [
                    e for e in tracing.recent_events(limit=400)
                    if e["event"] == "slot_lease_reclaimed"
                    and e["run_id"] == seen["run_id"]
                ]
                time.sleep(0.02)
            assert reclaimed, "slot lease must be reclaimed past deadline+grace"
            assert reclaimed[0]["node_id"] == "z"
            assert reclaimed[0]["concurrency"] == 1
            # 槽位已归还：唯一 session 槽位空闲 → 后续 run 不被饿死。
            assert gov.usage_full(session_path).concurrency == 0
            eng_b = GeoExecutionEngine(max_workers=1)
            run_b = eng_b.execute_plan(
                ExecutionPlan(
                    plan_id="w8-lease-b",
                    nodes=[_filter_node("quick", n=2)],
                    budget=ResourceBudget(max_rows=1000, deadline_s=10),
                ),
                session_id=sid, governor=gov, governor_parent_path=session_path,
            )
            assert run_b.status.value == "completed"
            assert not stop.is_set(), "zombie thread indeed still alive"
        finally:
            stop.set()
        thread_a.join(timeout=10)
        assert not thread_a.is_alive(), "run must converge after zombie settles"
        run_a = holder["run"]
        assert run_a.status.value == "cancelled"
        assert run_a.evidence["c"].status == "cancelled"
        # settle 路径对被回收槽位跳过二次释放：concurrency=1 的归还恰好
        # 2 次（看门狗 1 + run_b 节点 1），僵尸落定不重复归还。
        assert releases == [1, 1], releases
        # 全链基线：R1 归还 + 账簿清理（钳零兜底未被需要）。
        _assert_baseline(gov, "global:root", session_path)

    def test_node_level_deadline_reclaim_under_long_plan_deadline(
        self, monkeypatch,
    ):
        """plan deadline 很长（30s）、节点自身 deadline 短（0.2s）：wait()
        以巡检间隔为上界周期唤醒 → 看门狗在节点 deadline+宽限回收，而非
        拖到 run deadline。"""
        gov = ResourceGovernor()
        sid = "w8-lease-nodedl"
        session_path = gov.ensure_scope(
            "global:root", ScopeKind.SESSION,
            hashlib.sha1(sid.encode(), usedforsecurity=False).hexdigest()[:12],
            limits=BudgetLimits(max_concurrency=1),
        )
        started = threading.Event()
        stop = threading.Event()
        seen: dict[str, str] = {}

        def zombie(ctx, node, payloads):
            seen["run_id"] = ctx.run_id
            started.set()
            for _ in range(150):  # ≤3s 上界
                if stop.is_set():
                    break
                time.sleep(0.02)
            return {"features": _fc(2), "metadata": {}}

        monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, zombie)
        znode = ExecutionNode(
            node_id="z", category=NodeCategory.SOURCE_SCAN,
            estimate=ResourceEstimate(rows=10), parameters={"features": _fc(2)},
            deadline_s=0.2,
        )
        eng_a = GeoExecutionEngine(max_workers=1, slot_lease_grace_s=0.05)
        holder: dict[str, object] = {}

        def _run_a() -> None:
            holder["run"] = eng_a.execute_plan(
                ExecutionPlan(
                    plan_id="w8-lease-nodedl-a", nodes=[znode],
                    budget=ResourceBudget(max_rows=1000, deadline_s=30),
                ),
                session_id=sid, governor=gov, governor_parent_path=session_path,
            )

        thread_a = threading.Thread(target=_run_a, daemon=True)
        thread_a.start()
        try:
            assert started.wait(5.0)
            t0 = time.monotonic()
            reclaimed: list[dict] = []
            while time.monotonic() < t0 + 4.0 and not reclaimed:
                reclaimed = [
                    e for e in tracing.recent_events(limit=400)
                    if e["event"] == "slot_lease_reclaimed"
                    and e["run_id"] == seen["run_id"]
                ]
                time.sleep(0.02)
            assert reclaimed, (
                "watchdog must reclaim on node deadline + grace, "
                "not at the (far) run deadline"
            )
            elapsed = time.monotonic() - t0
            assert elapsed < 5.0, f"reclaim took {elapsed:.2f}s (run deadline=30s)"
            assert gov.usage_full(session_path).concurrency == 0
        finally:
            stop.set()
        thread_a.join(timeout=10)
        assert not thread_a.is_alive()
        _assert_baseline(gov, "global:root", session_path)


# ═══════════════════════════ ScopeKind.NODE 决策记录 ═══════════════════════


def test_scope_kind_node_member_deleted():
    """Wave 8 决策（audit 07 §1.1/R7）：NODE 从未实例化 → 删除成员，
    避免「声明未接线」的假面（理由记录在 budgets.ScopeKind docstring）。"""
    assert not hasattr(ScopeKind, "NODE")
    assert {k.value for k in ScopeKind} == {
        "global", "tenant", "project", "session", "execution",
    }
