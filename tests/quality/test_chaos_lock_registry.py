"""Chaos：分布式锁续约丢失语义 + artifact_registry 降级互斥（审计 05 #4/#5）。

两个跨 Pod 一致性缺口：

- **LOCK_RENEW_ERROR**：``_renew_loop`` 曾把续约 eval 的**异常**也当
  「继续重试」无限吞掉 —— 只有 eval 返回 0 才置 ``lost``。持续性的
  eval 错误（非抖动）会让 TTL 静默流逝、他 Pod 抢走锁，而
  ``fail_on_lost`` 调用方永远不知道，继续在无锁状态下写共享状态
  （审计 05 #4：数据腐蚀级）。本波次加了**最小修复**：连续失败计数
  覆盖一个 TTL 窗口后如实宣告 lost（预算内瞬时抖动仍容忍）——
  本套件用确定性的 ChaosLockClient 钉死修复后的语义。
- **REGISTRY_DEGRADED_EXCLUSION_DROP**：artifact_registry 全部关键段
  ``fail_on_degraded=False``（:444/:552/:592）—— Redis 不可达时跨 Pod
  互斥被**静默**放弃。这是文档化取舍（注册是增值记录，绝不阻断），
  本套件钉死：操作照常成功、降级可观测（lock.mode=='degraded'）、
  无崩溃 —— 若未来有人改成静默吞掉降级痕迹，这里红。
"""
import asyncio

import pytest

from app.services.distributed_lock import (
    _InProcessLock,
    _ResilientSessionLock,
    LockDegradedError,
    LockLostError,
)
from tests.fixtures.chaos import chaos, reset_journal


@pytest.fixture(autouse=True)
def _reset_journal():
    reset_journal()
    yield
    reset_journal()


# R1 review：默认 1s 上限（500×2ms）在 --cov/负载下会饿死 renew 循环 → 5000 步
async def _poll(condition, *, max_steps: int = 5000, step_s: float = 0.002) -> bool:
    """有界小步长轮询（补丁后时钟 10ms 级；上限 1s，绝不无界等待）。"""
    for _ in range(max_steps):
        if condition():
            return True
        await asyncio.sleep(step_s)
    return condition()


# ── LOCK_RENEW_ERROR：连续异常的有限容忍（最小修复后的钉死）────────────────


def _make_lock(client, *, ttl_ms=30, fail_on_lost=False) -> _ResilientSessionLock:
    """ttl 与补丁后的续约间隔（0.01s）同比例缩小：budget=30//10+1=4。"""
    return _ResilientSessionLock(
        client,
        "chaos:lock-renew",
        _InProcessLock(),
        ttl_ms=ttl_ms,
        fail_on_lost=fail_on_lost,
    )


@pytest.mark.asyncio
async def test_transient_renew_errors_within_budget_keep_lock():
    """瞬时抖动（预算内 2 次连续异常后恢复）→ 锁不丢，继续持有。"""
    with chaos("LOCK_RENEW_ERROR", times=2, interval_s=0.01) as fault:
        lock = _make_lock(fault.client)
        async with lock:
            assert lock.is_redis_backed
            ok = await _poll(lambda: fault.client.renew_calls >= 4)
            assert ok, "续约循环应当继续跑（异常被容忍）"
            assert not lock.lost, "预算内的连续异常不得误报丢失"
            assert fault.fired  # 异常确实注入过
        assert lock.lost is False


@pytest.mark.asyncio
async def test_renew_errors_exceeding_ttl_budget_mark_lost():
    """持续异常跨过整个 TTL 窗口（4 次 × 10ms > 30ms TTL）→ 如实宣告 lost。"""
    with chaos("LOCK_RENEW_ERROR", interval_s=0.01) as fault:
        lock = _make_lock(fault.client)
        async with lock:
            ok = await _poll(lambda: lock.lost)
            assert ok, "跨 TTL 的持续续约失败必须宣告 lost（不再无限吞异常）"
            # 精确性：恰好预算耗尽即停，不多打一次、不少打一次
            assert fault.client.renew_calls == 4
            assert fault.fired
            assert lock._renewer.done(), "宣告丢失后必须停止续约循环"
        # lost=True → 退出时不再 release 别人的锁（token 已易主的前提下）


@pytest.mark.asyncio
async def test_fail_on_lost_caller_gets_typed_error_after_budget():
    """fail_on_lost=True 的调用方在临界段内即拿到 LockLostError（可停止写）。"""
    with chaos("LOCK_RENEW_ERROR", interval_s=0.01) as fault:
        lock = _make_lock(fault.client, fail_on_lost=True)
        wrote_after_lost = []
        with pytest.raises(LockLostError):
            async with lock:
                assert await _poll(lambda: lock.lost)
                wrote_after_lost.append("would-be-write")  # 现实中调用方此时应中止
        assert wrote_after_lost  # 修复前：这里永远到不了（异常被无限吞）


@pytest.mark.asyncio
async def test_budget_boundary_recovery_resets_failure_counter():
    """预算边界（budget-1 次失败后恢复）→ 计数归零，锁照常持有。"""
    with chaos("LOCK_RENEW_ERROR", times=3, interval_s=0.01) as fault:
        lock = _make_lock(fault.client)
        async with lock:
            ok = await _poll(lambda: fault.client.renew_calls >= 5)
            assert ok
            assert not lock.lost, "恢复成功必须重置连续失败计数"
    # 退出时 release 走真实 token 语义（未丢失 → 正常释放）


# ── LOCK_ACQUIRE_DEGRADE：获取相位的降级策略矩阵 ──────────────────────────


@pytest.mark.asyncio
async def test_acquire_degrade_policy_matrix():
    """获取相位 Redis 失败：宽松策略降级可观测；严格策略类型化抛错。"""
    with chaos("LOCK_ACQUIRE_DEGRADE") as fault:
        lenient = _ResilientSessionLock(
            fault.client, "chaos:degrade-lenient", _InProcessLock()
        )
        async with lenient:
            assert lenient.mode == "degraded"
            assert lenient.is_degraded and not lenient.is_redis_backed

        strict = _ResilientSessionLock(
            fault.client,
            "chaos:degrade-strict",
            _InProcessLock(),
            fail_on_degraded=True,
        )
        with pytest.raises(LockDegradedError):
            async with strict:
                pass

    assert fault.fired  # SET NX 的连接错误确实注入过


# ── REGISTRY_DEGRADED_EXCLUSION_DROP：registry 写路径的静默降级 ───────────


@pytest.mark.asyncio
async def test_registry_write_succeeds_while_cross_pod_exclusion_dropped():
    """Redis 不可达 → registry 注册照常成功，但锁降级可观测（文档化取舍）。"""
    from app.services.artifact_registry import (
        get_artifact,
        register_artifact,
        update_record_metadata,
    )

    sid = "chaos-registry-degraded"
    with chaos("REGISTRY_DEGRADED_EXCLUSION_DROP") as fault:
        rec = await register_artifact(
            sid,
            artifact_id="ref:chaos-degraded",
            artifact_type="feature_collection",
            producer_tool="chaos_test",
            metadata={"origin": "chaos"},
        )
        assert rec is not None, "降级路径不得阻断注册（fail_on_degraded=False 的取舍）"
        assert fault.handles, "registry 必须真实走过 session_lock_registry.lock()"
        for lock_handle in fault.handles:
            assert lock_handle.mode == "degraded", (
                "跨 Pod 互斥被静默放弃 —— 这正是要钉死的可观测降级证据"
            )
            assert lock_handle.is_redis_backed is False
        # 同一降级会话内的后续更新同样成功（无崩溃、无部分状态）
        assert await update_record_metadata(
            sid, "ref:chaos-degraded", metadata={"extra": "1"}
        )

    assert fault.fired  # SET NX 确实抛了连接错误
    rec_after = await get_artifact(sid, "ref:chaos-degraded")
    assert rec_after is not None
    assert rec_after.metadata["origin"] == "chaos"
