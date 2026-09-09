"""Chaos 不变量测试（ADR-0119 决策 D10）。

每条测试钉住 chaos corpus（app/evaluation/chaos_corpus.py）声明的一类
系统不变量：锁泄漏、跨 session 污染、重启持久。进程击杀场景用真实
子进程 kill -9 —— flock 的 OS 级释放是契约本身。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from app.evaluation.chaos_corpus import build_chaos_corpus
from app.services.gis_harness import trace_store
from app.services.gis_harness.recovery_ledger import RecoveryLedger


@pytest.fixture()
def store_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(d))
    return d


# ── 锁泄漏（进程击杀）─────────────────────────────────────────────

def _spawn_lock_holder(repo: str, store: str, kind: str) -> subprocess.Popen:
    """子进程：获锁后写标记文件进入长眠（模拟持锁中被 kill -9）。"""
    prog = (
        "import sys, time, os\n"
        "sys.path.insert(0, {repo!r})\n"
        "os.environ['MAPSPEC_STORAGE_DIR'] = {store!r}\n"
        "kind = sys.argv[1]\n"
        "from pathlib import Path\n"
        "if kind == 'trace':\n"
        "    from app.services.gis_harness import trace_store as m\n"
        "    p = m._chains_path('chaos-s')\n"
        "    lock = m._FileLock(p)\n"
        "else:\n"
        "    from app.services.gis_harness.recovery_ledger import RecoveryLedger, _ledger_path, _FileLock\n"
        "    p = _ledger_path('chaos-s')\n"
        "    p.parent.mkdir(parents=True, exist_ok=True)\n"
        "    lock = _FileLock(p)\n"
        "with lock:\n"
        "    Path(os.environ['HOLD_MARKER']).write_text('held')\n"
        "    time.sleep(30)\n"
    ).format(repo=repo, store=store)
    env = dict(os.environ)
    env["HOLD_MARKER"] = os.path.join(store, "hold_marker")
    return subprocess.Popen(
        [sys.executable, "-c", prog, kind],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )


@pytest.mark.skipif(not trace_store._HAS_FCNTL,
                    reason="flock 不可用（非 POSIX）—— 无跨进程锁契约")
@pytest.mark.parametrize("kind", ["trace", "ledger"])
def test_lock_released_after_kill(store_dir, kind, tmp_path):
    """kill -9 持锁进程 → OS 释放 flock → 主进程立即可获锁写。"""
    store = str(store_dir)
    marker = os.path.join(store, "hold_marker")
    proc = _spawn_lock_holder(os.getcwd(), store, kind)
    try:
        for _ in range(100):  # 等子进程拿到锁
            if os.path.exists(marker):
                break
            time.sleep(0.05)
        assert os.path.exists(marker), "子进程未持锁（测试前提失败）"
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)

        # 死进程的锁必须已释放：本进程获锁并完成一次真实写入
        if kind == "trace":
            ok = trace_store.persist_chain(
                {"turn_id": "after-kill", "session_id": "chaos-s",
                 "total_records": 1, "stages": []},
                session_id="chaos-s")
            assert ok, "trace 锁未释放（锁泄漏）"
            assert trace_store.last_seq("chaos-s") == 1
        else:
            led = RecoveryLedger()
            assert led.record_failure("chaos-s", "clip_layer", "tool_error") == 1, \
                "ledger 锁未释放（锁泄漏）"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


# ── 跨 session 隔离 ──────────────────────────────────────────────

def test_durable_state_cross_session_isolation(store_dir):
    """并发多 session 的 ledger / trace 严格按 session 隔离。"""
    led = RecoveryLedger()
    led.record_failure("sess-iso-a", "clip_layer", "tool_error")
    led.record_failure("sess-iso-a", "clip_layer", "tool_error")
    led.record_failure("sess-iso-b", "clip_layer", "timeout")
    # 会话严格隔离（账本按 session 分文件）：A/B 互不可见
    assert led.attempts("sess-iso-b", "clip_layer", "tool_error") == 0
    assert led.attempts("sess-iso-a", "clip_layer", "timeout") == 0
    assert led.attempts("sess-iso-a", "clip_layer", "tool_error") == 2
    # B 成功回写只清 B 自己的账
    led.record_success("sess-iso-b", "clip_layer")
    assert led.attempts("sess-iso-b", "clip_layer", "timeout") == 0
    assert led.attempts("sess-iso-a", "clip_layer", "tool_error") == 2

    # trace 隔离
    trace_store.persist_chain(
        {"turn_id": "ta", "session_id": "sess-iso-a", "total_records": 1,
         "stages": []}, session_id="sess-iso-a")
    trace_store.persist_chain(
        {"turn_id": "tb", "session_id": "sess-iso-b", "total_records": 1,
         "stages": []}, session_id="sess-iso-b")
    ids_a = {r["turn_id"] for r in trace_store.read_chains("sess-iso-a")}
    ids_b = {r["turn_id"] for r in trace_store.read_chains("sess-iso-b")}
    assert ids_a == {"ta"} and ids_b == {"tb"}


# ── resume 预算续接（重启 + 恢复复合场景）────────────────────────

def test_resume_carries_budget_not_rebirth(store_dir):
    """恢复链路复合：旧 session 预算 → 新 session（续接而非复活）。"""
    led = RecoveryLedger()
    old, new = "sess-old", "sess-new"
    for _ in range(2):
        led.record_failure(old, "fly_to_location", "tool_error")
    carried = led.copy_between_sessions(old, new)
    assert carried == 1
    # 恢复后继续失败 → 预算继续累计（不重获满额）
    assert led.record_failure(new, "fly_to_location", "tool_error") == 3
    from app.services.gis_harness.failure_taxonomy import (
        HarnessFailureClass,
        remediation_for,
    )
    decision = remediation_for(
        HarnessFailureClass.TOOL_ERROR,
        attempts=led.attempts(new, "fly_to_location", "tool_error"))
    assert decision.exhausted is True
    assert decision.action == "abort_with_disclosure"


# ── chaos corpus 元门 ─────────────────────────────────────────────

def test_chaos_corpus_deterministic_and_node_ids_exist():
    """语料确定性 + 每个 test_node_id 真实存在于仓库（无假声明）。"""
    a = build_chaos_corpus()
    b = build_chaos_corpus()
    assert [s.scenario_id for s in a] == [s.scenario_id for s in b]
    assert len({s.scenario_id for s in a}) == len(a)
    for s in a:
        assert s.invariant and s.fault, s.scenario_id
        rel, _, node = s.test_node_id.partition("::")
        assert os.path.exists(rel), f"{s.scenario_id}: 文件不存在 {rel}"
        # 节点函数必须在该文件中声明（收集级存在性）
        func = node.split("[")[0].split(".")[-1]
        src = open(rel, encoding="utf-8").read()
        assert f"def {func}(" in src, f"{s.scenario_id}: 节点不存在 {node}"


def test_chaos_corpus_covers_epic_scenarios():
    """Epic V6 目标 8 的八类场景全部入册（scenario_class 封闭）。"""
    required = {
        "client_disconnect", "duplicate_turn", "pi_bridge_cancellation",
        "redis_blip", "worker_restart", "trace_write_interrupt",
        "resume_dangling_ref", "render_telemetry",
    }
    present = {s.scenario_class for s in build_chaos_corpus()}
    missing = required - present
    assert not missing, f"chaos 语料缺场景类: {missing}"
