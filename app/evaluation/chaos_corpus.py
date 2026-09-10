"""Chaos / Recovery Corpus（ADR-0119 决策 D10）。

Epic V6 目标 8 要求的 chaos 场景**确定性语料**：每条场景声明
（注入故障 → 必须成立的不变量 → 实际钉住它的测试节点 id）。

诚实纪律：
- ``test_node_id`` 必须指向**真实存在**的 pytest 节点 —— ``corpus`` 门
  测试扫描仓库断言节点可收集（声明假能力的语料行即失败）；
- 场景集封闭有界（无生成式展开）；同输入同输出。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class ChaosScenario:
    """一条 chaos 场景：故障注入 → 不变量 → 钉住它的测试节点。"""

    scenario_id: str
    scenario_class: str   # Epic 目标 8 场景类（封闭八类）
    fault: str            # 注入的故障（封闭描述）
    invariant: str        # 必须成立的系统不变量
    test_node_id: str     # 钉住该不变量的 pytest 节点（必须真实可收集）


def build_chaos_corpus() -> List[ChaosScenario]:
    """确定性场景集（V6 Epic 目标 8 全覆盖）。"""
    scenarios: List[ChaosScenario] = []
    add = scenarios.append

    # ── 锁与资源 ────────────────────────────────────────────────
    add(ChaosScenario(
        "CH-lock-trace-kill", "worker_restart",
        "持有 trace flock 的 worker 被 kill -9",
        "OS 释放 flock：另一进程立即可获锁（无全局锁泄漏）",
        "tests/unit/gis_harness/test_chaos_invariants_v6.py::test_lock_released_after_kill[trace]",
    ))
    add(ChaosScenario(
        "CH-lock-ledger-kill", "worker_restart",
        "持有 recovery ledger flock 的进程被 kill -9",
        "OS 释放 flock：账本立即可写（无锁泄漏、无预算写丢）",
        "tests/unit/gis_harness/test_chaos_invariants_v6.py::test_lock_released_after_kill[ledger]",
    ))
    add(ChaosScenario(
        "CH-trace-torn-write", "trace_write_interrupt",
        "trace 写中断（无换行截断尾巴）",
        "torn-tail 自愈：下次 append 修补尾巴，记录零黏连损坏",
        "tests/unit/gis_harness/test_trace_store_v6.py::test_corrupt_segment_tolerated",
    ))

    # ── 重启 / 持久 ─────────────────────────────────────────────
    add(ChaosScenario(
        "CH-restart-ledger-budget", "worker_restart",
        "worker 重启（全新进程实例）",
        "durable 恢复预算不归零：重启后 attempts 可见、可继续耗尽",
        "tests/unit/test_recovery_ledger_v6.py::test_failure_recording_and_write_through",
    ))
    add(ChaosScenario(
        "CH-restart-trace-window", "worker_restart",
        "worker 重启后读 trace",
        "分段窗口与 seq 游标完整（manifest 驱动，不依赖进程内状态）",
        "tests/unit/gis_harness/test_trace_store_v6.py::test_segment_roll_and_compression",
    ))
    add(ChaosScenario(
        "CH-restart-resume-anchor", "worker_restart",
        "session 过期后从锚点恢复（DB 锚点 + 新进程）",
        "恢复成功且授权 fail-closed；旧 id 不复活；dangling 诚实披露",
        "tests/unit/gis_harness/test_resume_anchor_v5.py::test_save_and_resume_roundtrip_with_restart",
    ))

    # ── 隔离 / 重复 ─────────────────────────────────────────────
    add(ChaosScenario(
        "CH-duplicate-turn-settle", "duplicate_turn",
        "同 turn 重复 settle（幂等窗口）",
        "重复持久化零重复行；(turn, total) 变化 → 新证据追加",
        "tests/unit/gis_harness/test_trace_store_v5.py::test_persist_idempotent_on_settle_retry",
    ))
    add(ChaosScenario(
        "CH-cross-session-isolation", "duplicate_turn",
        "并发多 session 写 durable 状态",
        "ledger/trace 按 session 严格隔离（无跨 session 污染）",
        "tests/unit/gis_harness/test_chaos_invariants_v6.py::test_durable_state_cross_session_isolation",
    ))
    add(ChaosScenario(
        "CH-resume-dangling-ref", "resume_dangling_ref",
        "resume 时旧 ref 已过期（dangling）",
        "ref 重写为空 + missing_refs 披露 —— 绝不悬空谎报绑定",
        "tests/unit/gis_harness/test_resume_anchor_v5.py::test_resume_rehydrates_live_refs",
    ))

    # ── 取消 / 预算 ─────────────────────────────────────────────
    add(ChaosScenario(
        "CH-cancel-no-retry", "pi_bridge_cancellation",
        "Pi bridge cancellation（OperationCancelled）",
        "cancelled 分类为不可恢复：retry_allowed=False，不进重试循环",
        "tests/unit/gis_harness/test_durable_context_continuation_v6.py::test_continuation_unrecoverable_failure_aborts",
    ))
    add(ChaosScenario(
        "CH-retry-exhaustion-terminates", "pi_bridge_cancellation",
        "失败预算阶梯（1..max）",
        "预算阶梯必然终止于 abort_with_disclosure（无无限重试）",
        "tests/unit/gis_harness/test_recovery_scenario_v5.py::test_budget_ladder_always_terminates",
    ))
    add(ChaosScenario(
        "CH-ledger-budget-cross-worker", "worker_restart",
        "双进程并发记同一失败 key",
        "跨 worker 预算一致（2 进程 ×5 = 10，flock 串行化无丢账）",
        "tests/unit/test_recovery_ledger_v6.py::test_two_process_concurrent_accounting",
    ))
    add(ChaosScenario(
        "CH-repair-loop-terminates", "render_telemetry",
        "渲染发散反复触发修复回路",
        "continuation 裁决在 repair 预算耗尽后转 reobserve/abort",
        "tests/unit/gis_harness/test_durable_context_continuation_v6.py::test_continuation_render_failure_repair_then_reobserve",
    ))

    # ── 断连 / 中间件 / 遥测 ────────────────────────────────────
    add(ChaosScenario(
        "CH-client-disconnect", "client_disconnect",
        "client_disconnect：SSE 客户端断连风暴",
        "断连不泄漏：turn 上下文/登记最终清空，无悬挂 active turn",
        "tests/unit/test_v5_acceptance_scenarios.py::test_s2_random_disconnect_storm_no_leak",
    ))
    add(ChaosScenario(
        "CH-redis-blip", "redis_blip",
        "redis 短故障：cancel 期间 Redis 登记/注销失败",
        "降级本地登记，下一 session 正常继续（无中间件强依赖）",
        "tests/unit/test_v5_acceptance_scenarios.py::test_s3_cancel_during_redis_register_next_session_proceeds",
    ))
    add(ChaosScenario(
        "CH-late-render-telemetry", "render_telemetry",
        "render telemetry 迟到（旧 revision 观察）",
        "迟到观察不得验证更新的 spec：stale 诚实披露，留给新观察重验",
        "tests/unit/gis_harness/test_render_observation.py::test_validate_stale_observation_cannot_validate_newer_spec",
    ))
    add(ChaosScenario(
        "CH-pi-bridge-cancellation", "pi_bridge_cancellation",
        "pi_bridge_cancellation（OperationCancelled）",
        "取消分类为不可恢复失败类：不重试、不耗 remediation 预算",
        "tests/unit/gis_harness/test_durable_context_continuation_v6.py::test_continuation_unrecoverable_failure_aborts",
    ))

    return scenarios


def corpus_node_ids() -> Tuple[str, ...]:
    return tuple(s.test_node_id for s in build_chaos_corpus())


__all__ = ["ChaosScenario", "build_chaos_corpus", "corpus_node_ids"]
